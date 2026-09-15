"""KAYTRADE V1.6.5 production runtime overlay — Build 1650.

- latest CLOSED candle only for 1m/5m/15m/1H/4H inputs;
- 5m BOLL/RSI/Volume/KDJ signal package expires at the next 5m close;
- dynamic timeframe freshness is checked again immediately before submit;
- existing audited LIMIT / isolated leverage / structure / cost protections remain.
"""
from __future__ import annotations

import time

from v164_update_patch import apply as apply_previous
apply_previous()

import app
import engine
from candles import expected_bar
import v138_strategy_patch as v138
import v152_strategy_patch as v152_runtime
import v153_runtime_patch as v153_runtime
import v153_backtest_core as v153_bt
import v154_runtime_patch as v154_runtime
import v154_limit_log_fix as v154_limit
import v154_record_card_boll_fix as v154_record
import v160_update_patch as v160
import v161_update_patch as v161
import v162_update_patch as v162
import v163_update_patch as v163
import v164_update_patch as v164
import v165_model as model

VERSION = "1.6.5"
BUILD = "1650"

# Route every production facade to the same V1.6.5 model.
for module in (v160, v161, v162, v163, v164, v152_runtime, v153_runtime, v153_bt, v154_runtime, v154_limit):
    if hasattr(module, "model"):
        module.model = model
if hasattr(v154_record, "model"):
    v154_record.model = model
v162.VERSION = VERSION
v162.BUILD = BUILD
v152_runtime.V152_VERSION = VERSION
v153_runtime.VERSION = VERSION

# Preserve the confirmed execution model: first entry 1x LIMIT, 1H ATR stop,
# full-position 2R target, BE disabled.
v138._prepare_order = v162._prepare_order_v162
v161._maybe_trigger_be = lambda owner: None

_PREVIOUS_ARM = engine.Engine.arm
_PREVIOUS_SUBMIT = v138._submit_initial


def _owner_now_ms(owner):
    try:
        return int(float(owner.market_now()) * 1000.0)
    except Exception:
        return int(time.time() * 1000.0)


def _opportunity_from(market, score):
    for source in (
        (market or {}).get("opportunity") if isinstance(market, dict) else None,
        (score or {}).get("opportunity") if isinstance(score, dict) else None,
    ):
        if isinstance(source, dict) and source:
            return source
    return None


def _market_timeframe_freshness(owner, market):
    """Fail closed if a candle boundary passed after the current score snapshot."""
    if not isinstance(market, dict):
        return False, ["缺少市场周期快照"]
    now_s = _owner_now_ms(owner) / 1000.0
    specs = (
        ("bar1m", "1m", 60_000),
        ("bar5m", "5m", 300_000),
        ("bar15", "15m", 900_000),
        ("bar1h", "1H", 3_600_000),
        ("bar4h", "4H", 14_400_000),
    )
    problems = []
    for key, label, step in specs:
        try:
            actual = int(market.get(key))
        except (TypeError, ValueError):
            problems.append(f"{label}最新已收盘K线时间缺失")
            continue
        expected = int(expected_bar(now_s, step))
        if actual != expected:
            problems.append(f"{label}已出现新的收盘K线，必须先刷新指标")
    return not problems, problems


def _clear_stale_opportunity(owner, *, emit_log=False):
    store = getattr(owner, "store", None)
    if store is None:
        return False
    opp = store.data.get("v152_opportunity")
    if not isinstance(opp, dict):
        return False
    now_ms = _owner_now_ms(owner)
    opened, _age, _remaining, _end = model._signal_window(opp, now_ms)
    start = model._signal_close_ms(opp)
    expired = bool(start > 0 and not opened and now_ms >= start + model.ENTRY_WINDOW_MS)
    if not expired:
        return False
    old_id = str(opp.get("id") or "")
    store.data["v152_opportunity"] = None
    store.data["v152_rearm_zone"] = None
    store.save()
    try:
        store.record("V1.6.5清理过期5m外轨信号", {
            "opportunity_id": old_id,
            "signal_close_ms": start,
            "entry_window_ms": model.ENTRY_WINDOW_MS,
        })
    except Exception:
        pass
    if emit_log:
        owner.emit("log", "V1.6.5：旧5m外轨信号已到下一根5m收盘并清除；等待最新已收盘5m重新判断")
    return True


def _pre_submit_guard(owner, market, score):
    blockers = []
    if not isinstance(market, dict) or not isinstance(score, dict):
        return ["缺少有效市场/评分状态"]

    if not bool(getattr(owner, "enabled", False)) or bool(getattr(owner, "stopped", False)):
        blockers.append("自动交易当前未处于可开仓状态")
    store = getattr(owner, "store", None)
    if store is not None:
        if str(store.data.get("halt") or "").strip():
            blockers.append("存在故障锁/人工停止状态")
        if isinstance(store.data.get("active"), dict):
            blockers.append("本地已有活动订单/仓位状态")
    if bool(getattr(getattr(owner, "x", None), "clock_sync_degraded", False)):
        blockers.append("时钟同步不稳定")

    fresh, stale_reasons = _market_timeframe_freshness(owner, market)
    if not fresh:
        blockers.extend(stale_reasons)

    side = str(market.get("side") or "")
    if side not in ("做多", "做空"):
        blockers.append("当前没有可执行方向")
    if not score.get("gate") or not score.get("eligible"):
        blockers.append("评分/Hard Gate当前未全部通过")
    try:
        if float(score.get("total") or 0.0) < float(model.THRESHOLD):
            blockers.append("评分未达到6.0")
    except Exception:
        blockers.append("评分数据无效")

    opp = _opportunity_from(market, score)
    if not isinstance(opp, dict):
        blockers.append("缺少最新已收盘5m BOLL外轨信号")
        return model._dedupe(blockers)
    path = str(opp.get("signal_path") or opp.get("path") or "")
    if path not in model.OUTER_PATHS:
        blockers.append("仅允许5m BOLL上下外轨路径")

    opened, _age, _remaining, _end = model._signal_window(opp, _owner_now_ms(owner))
    if not opened:
        blockers.append("5m外轨信号已到下一根5m收盘，必须刷新后重新判断")

    ratio = model._finite(opp.get("five_signal_volume_ratio"))
    if ratio is None:
        blockers.append("5m信号K量能数据不可用")
    elif ratio >= model.VOLUME_HARD_GATE:
        blockers.append(f"5m信号K量能 {ratio:.2f}× ≥ 1.20×")

    confirmations = score.get("confirmations") or {}
    required = confirmations.get("required") or {}
    # Legacy key names may still exist internally, but their booleans must all
    # reflect the current V1.6.5 strategy state before a write is allowed.
    if required and not all(bool(value) for value in required.values()):
        blockers.append("至少一个开仓必要条件已失效")
    if str(confirmations.get("4H_trend_state") or "") != "aligned":
        blockers.append("4H未与交易方向同向")
    rsi = model._finite(confirmations.get("rsi5"))
    if rsi is None or not (model.RSI_MIN <= rsi <= model.RSI_MAX):
        blockers.append("5m RSI不在30-70")
    if not model._macd_improving(score):
        blockers.append("5m MACD未连续向交易方向改善")

    return model._dedupe(blockers)


def _rewrite_runtime_text(data):
    if not isinstance(data, str):
        return data
    text = data
    for old in ("V1.6.4", "V1.6.3", "V1.6.2"):
        text = text.replace(old, "V1.6.5")
    text = text.replace("4分钟执行窗口", "当前5m闭合周期")
    text = text.replace("4分钟窗口", "5m闭合周期")
    text = text.replace("4分钟内", "下一根5m收盘前")
    text = text.replace("超过4分钟", "已到下一根5m收盘")
    if text.startswith("自动交易启动；"):
        return (
            "自动交易启动；V1.6.5 Build1650：所有指标仅使用各自周期最新已收盘K线；"
            "1H/15m定方向→最新5m BOLL外轨信号2.5分→5m RSI30-70 / MACD改善 / Volume<1.20×→4H同向；"
            "5m BOLL及同根5m RSI/Volume/KDJ有效至下一根5m收盘，随后整组刷新；"
            "15m/1H/4H在各自新K线收盘时自动刷新；评分≥6，前方结构≥1.5R、成本≤0.30R及止损结构继续限制；"
            "下单前Final Entry Guard再次检查各周期新鲜度和全部动态Hard Gate；"
            "首次开仓固定1× LIMIT；1×1H ATR止损、2R整仓止盈、No-BE"
        )
    return text


def _arm_v165(self, settings):
    _clear_stale_opportunity(self, emit_log=True)
    original_emit = self.emit

    def emit(kind, data):
        return original_emit(kind, _rewrite_runtime_text(data))

    self.emit = emit
    try:
        return _PREVIOUS_ARM(self, settings)
    finally:
        self.emit = original_emit


def _submit_v165(self, market, score, equity, available, remaining):
    blockers = _pre_submit_guard(self, market, score)
    if blockers:
        self.emit("log", "V1.6.5 Final Entry Guard禁止开仓：" + "；".join(blockers))
        # Force a fresh market evaluation on the next cycle if a timeframe rolled.
        if any("必须先刷新指标" in item for item in blockers):
            self.market = None
        return None

    original_emit = self.emit

    def emit(kind, data):
        return original_emit(kind, _rewrite_runtime_text(data))

    self.emit = emit
    try:
        # V1.6.4's audited submitter remains underneath; its final server-time
        # BOLL window check now reads the V1.6.5 5-minute lifecycle through the
        # shared model route, immediately before the OKX Place Order write.
        return _PREVIOUS_SUBMIT(self, market, score, equity, available, remaining)
    finally:
        self.emit = original_emit


def apply():
    if getattr(engine.Engine, "_kaytrade_v165_applied", False):
        return
    engine.Engine.arm = _arm_v165
    v138._submit_initial = _submit_v165
    engine.Engine._kaytrade_v165_applied = True
    app.App._kaytrade_v165_runtime_applied = True


apply()

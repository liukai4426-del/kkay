"""KAYTRADE V1.6.4 production runtime overlay — Build 1641.

Build 1641 keeps the verified V1.6.3 order lifecycle and fixes the final V1.6.4
execution boundary:
- outer BOLL opportunities are valid for four minutes only;
- stale persisted opportunities are cleared before live trading;
- a final V1.6.4 entry guard runs before the inherited LIMIT submit path;
- the inherited submitter re-checks the same four-minute window immediately
  before the OKX Place Order write;
- Volume >=1.20x is a hard gate, Volume scores zero, outer BOLL scores 2.5;
- RSI 30-70 / 5m MACD improvement / 4H aligned remain mandatory.
"""
from __future__ import annotations

import time

from v163_update_patch import apply as apply_previous
apply_previous()

import app
import engine
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
import v164_model as model

VERSION = "1.6.4"
BUILD = "1641"

# Route every live/backtest facade that the production order lifecycle calls
# through the exact same V1.6.4 model.
v160.model = model
v161.model = model
v162.model = model
v162.VERSION = VERSION
v162.BUILD = BUILD
v163.model = model
v152_runtime.model = model
v152_runtime.V152_VERSION = VERSION
v153_runtime.model = model
v153_runtime.VERSION = VERSION
v153_bt.model = model
v154_runtime.model = model
v154_limit.model = model
if hasattr(v154_record, "model"):
    v154_record.model = model

# Preserve V1.6.2/V1.6.3 execution semantics: 1x LIMIT entry, 1H ATR stop,
# 2R full take-profit and BE disabled.
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


def _clear_stale_opportunity(owner, *, emit_log=False):
    """Remove a persisted pre-Build1641 signal that is already older than 4m."""
    store = getattr(owner, "store", None)
    if store is None:
        return False
    state = store.data
    opp = state.get("v152_opportunity")
    if not isinstance(opp, dict):
        return False
    opened, _age, _remaining, _end = model._signal_window(opp, _owner_now_ms(owner))
    start = model._signal_close_ms(opp)
    expired = bool(start > 0 and not opened and _owner_now_ms(owner) >= start + model.ENTRY_WINDOW_MS)
    if not expired:
        return False
    old_id = str(opp.get("id") or "")
    state["v152_opportunity"] = None
    # Do not preserve a stale zone lock across a software upgrade. The model
    # itself refuses to recreate an old >4m signal from the current closed bar.
    state["v152_rearm_zone"] = None
    store.save()
    try:
        store.record("V1.6.4 Build1641清理过期外轨信号", {
            "opportunity_id": old_id,
            "signal_close_ms": start,
            "entry_window_ms": model.ENTRY_WINDOW_MS,
        })
    except Exception:
        pass
    if emit_log:
        owner.emit("log", "V1.6.4 Build1641：已清除启动前残留的过期5m外轨信号；等待新的已收盘5m外轨信号")
    return True


def _pre_submit_guard(owner, market, score):
    """Cheap final strategy-state guard before entering the audited submitter.

    Plan/price/cost/stop checks still run inside v152/v162 preparation. The
    inherited LIMIT submitter then performs a second server-time window check
    after leverage reads and immediately before /api/v5/trade/order.
    """
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
        blockers.append("缺少5m BOLL外轨机会")
        return model._dedupe(blockers)
    path = str(opp.get("signal_path") or opp.get("path") or "")
    if path not in model.OUTER_PATHS:
        blockers.append("仅允许5m BOLL上下外轨路径")

    opened, _age, _remaining, _end = model._signal_window(opp, _owner_now_ms(owner))
    if not opened:
        blockers.append("5m BOLL外轨信号已超过4分钟执行窗口")

    ratio = model._finite(opp.get("five_signal_volume_ratio"))
    if ratio is None:
        blockers.append("5m信号K量能数据不可用")
    elif ratio >= model.VOLUME_HARD_GATE:
        blockers.append(f"5m信号K量能 {ratio:.2f}× ≥ 1.20×")

    confirmations = score.get("confirmations") or {}
    required = confirmations.get("required") or {}
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
    text = data.replace("V1.6.3", "V1.6.4").replace("V1.6.2", "V1.6.4")
    if text.startswith("自动交易启动；"):
        return (
            "自动交易启动；V1.6.4 Build1641：仅5m BOLL外轨；外轨信号2.5分且仅在收盘后4分钟内有效；"
            "5m信号K量能≥前20根5m均量1.20×时Hard Gate禁止开仓；Volume不再计分；"
            "做多下轨/做空上轨均要求5m RSI 30-70、5m MACD连续向交易方向改善、4H同向；"
            "下单前执行Final Entry Guard，且LIMIT写入前再次核对4分钟窗口；"
            "全部首次开仓1× LIMIT；1×1H ATR止损、2R整仓止盈、No-BE；"
            "OKX 51290沿用V1.6.3软暂停与安全迁移"
        )
    return text


def _arm_v164(self, settings):
    _clear_stale_opportunity(self, emit_log=True)
    original_emit = self.emit

    def emit(kind, data):
        return original_emit(kind, _rewrite_runtime_text(data))

    self.emit = emit
    try:
        return _PREVIOUS_ARM(self, settings)
    finally:
        self.emit = original_emit


def _submit_v164(self, market, score, equity, available, remaining):
    blockers = _pre_submit_guard(self, market, score)
    if blockers:
        self.emit("log", "V1.6.4 Final Entry Guard禁止开仓：" + "；".join(blockers))
        return None

    original_emit = self.emit

    def emit(kind, data):
        return original_emit(kind, _rewrite_runtime_text(data))

    self.emit = emit
    try:
        # The inherited submitter performs plan/structure/cost validation, the
        # idempotent isolated-leverage preflight, and a second server-time BOLL
        # window check immediately before the OKX Place Order POST.
        return _PREVIOUS_SUBMIT(self, market, score, equity, available, remaining)
    finally:
        self.emit = original_emit


def apply():
    if getattr(engine.Engine, "_kaytrade_v164_applied", False):
        return
    engine.Engine.arm = _arm_v164
    v138._submit_initial = _submit_v164
    engine.Engine._kaytrade_v164_applied = True
    app.App._kaytrade_v164_runtime_applied = True


apply()

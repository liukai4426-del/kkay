"""KAYTRADE V1.6.7 Build1671 hotfix: 15m BOLL is the only BOLL signal source.

Rules locked by this patch:
- BOLL trigger/scoring comes ONLY from the latest CLOSED 15m candle.
- short requires 15m high >= frozen 15m upper band -> upper_band.
- long requires 15m low <= frozen 15m lower band -> lower_band.
- 5m is used only for RSI/MACD/Volume confirmation and the five-minute execution window.
- there is no 5m BOLL trigger, score, opportunity, or log semantic.
- one closed 15m trigger may open only one five-minute execution window; when that
  window expires the same 15m candle cannot create another opportunity.
"""
from __future__ import annotations

import re

from core import indicators
from v167_strategy_patch import apply as apply_previous
apply_previous()

import app
import v153_model as signal_core
import v164_model as v164
import v165_model as model
import v165_update_patch as runtime
import v165_15m_outer_patch as outer15
import v166_ui_patch as ui166
import v167_strategy_patch as s167
import v167_algo_sync_patch as a167

VERSION = "1.6.7"
BUILD = "1671"

_PREVIOUS_EVALUATE = model.evaluate
_PREVIOUS_EXECUTION_CHECKS = model.execution_checks
_PREVIOUS_PRE_SUBMIT_GUARD = runtime._pre_submit_guard
_PREVIOUS_RUNTIME_TEXT = runtime._rewrite_runtime_text
_PREVIOUS_CLEAR_STALE = runtime._clear_stale_opportunity
_PREVIOUS_APP_INIT = app.App.__init__


def _boll_entry_signal_15m_only(quarter, five, side):
    """Strict 15m-only BOLL trigger; 5m contributes confirmations, never BOLL."""
    if len(quarter) < 2 or len(five) < 2:
        return None
    q = indicators(quarter)
    f = indicators(five)
    bar15 = quarter[-1]
    bar5 = five[-1]
    upper = float(q["upper"])
    lower = float(q["lower"])
    middle = float(q["middle"])
    rsi5 = float(f["rsi"])
    atr5 = float(f["atr"])
    if not (model.RSI_MIN <= rsi5 <= model.RSI_MAX):
        return None
    high15 = float(bar15["h"])
    low15 = float(bar15["l"])
    if side == "做多":
        if low15 > lower:
            return None
        path, reference = "lower_band", lower
    elif side == "做空":
        if high15 < upper:
            return None
        path, reference = "upper_band", upper
    else:
        return None
    bar_t = int(bar15["t"])
    return {
        "path": path,
        "bar_t": bar_t,
        "signal_close_ms": bar_t + 15 * 60 * 1000,
        "reference": float(reference),
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "rsi5": rsi5,
        "atr5": atr5,
        # 5m extremes remain only for inherited stop/entry-drift mechanics.
        "bar_low": float(bar5["l"]),
        "bar_high": float(bar5["h"]),
        # Immutable proof that the BOLL trigger itself came from 15m.
        "boll15_signal_low": low15,
        "boll15_signal_high": high15,
        "boll_timeframe": "15m",
    }


def _signal_adapter_15m_only(five, side):
    quarter = outer15._ACTIVE_QUARTER
    if not isinstance(quarter, (list, tuple)):
        return None
    return _boll_entry_signal_15m_only(quarter, five, side)


def _new_opportunity_15m_only(hour, quarter, five, one, side, signal):
    opp = outer15._new_opportunity_15m(hour, quarter, five, one, side, signal)
    if isinstance(opp, dict) and isinstance(signal, dict) and signal.get("boll_timeframe") == "15m":
        detail = opp.setdefault("trigger_detail", {})
        detail["boll_timeframe"] = "15m"
        detail["boll15_signal_low"] = float(signal["boll15_signal_low"])
        detail["boll15_signal_high"] = float(signal["boll15_signal_high"])
        detail["boll15_upper"] = float(signal["upper"])
        detail["boll15_lower"] = float(signal["lower"])
        opp["boll_timeframe"] = "15m"
        opp["boll15_signal_low"] = float(signal["boll15_signal_low"])
        opp["boll15_signal_high"] = float(signal["boll15_signal_high"])
    return opp


def _pin_15m_only():
    """Undo every historical 5m BOLL re-install, then pin the final source to 15m."""
    outer15._signal_adapter = _signal_adapter_15m_only
    outer15.boll_entry_signal_15m = _boll_entry_signal_15m_only
    outer15._install_15m_signal_source()
    signal_core.boll_entry_signal = _signal_adapter_15m_only
    v164.boll_entry_signal = _signal_adapter_15m_only
    model.boll_entry_signal = _signal_adapter_15m_only
    signal_core.new_opportunity = _new_opportunity_15m_only
    model._patch_v164_base = outer15._patch_v164_base_15m
    model.BOLL_TRIGGER_TIMEFRAME = "15m"


def _detail(opp):
    return (opp or {}).get("trigger_detail") if isinstance((opp or {}).get("trigger_detail"), dict) else {}


def _valid_15m_evidence(opp):
    if not isinstance(opp, dict) or str(opp.get("boll_timeframe") or "") != "15m":
        return False
    side = str(opp.get("side") or "")
    path = str(opp.get("signal_path") or opp.get("path") or "")
    detail = _detail(opp)
    try:
        high15 = float(opp.get("boll15_signal_high", detail.get("boll15_signal_high")))
        low15 = float(opp.get("boll15_signal_low", detail.get("boll15_signal_low")))
        upper = float(detail.get("boll15_upper"))
        lower = float(detail.get("boll15_lower"))
    except (TypeError, ValueError):
        return False
    if side == "做空":
        return path == "upper_band" and high15 >= upper
    if side == "做多":
        return path == "lower_band" and low15 <= lower
    return False


def _prepare_incoming(opportunity, quarter):
    """Keep an expired 15m opportunity until the next 15m close to prevent reuse."""
    if not isinstance(opportunity, dict):
        return opportunity
    # Old/malformed opportunities are discarded and may only be recreated by the strict 15m source.
    if not _valid_15m_evidence(opportunity):
        return None
    try:
        latest15_t = int(quarter[-1]["t"])
        signal_t = int(opportunity.get("signal_bar_t") or 0)
    except (TypeError, ValueError, IndexError):
        return None
    # Once a newer 15m candle has closed, the old trigger may be discarded.
    if latest15_t > signal_t:
        return None
    # Same 15m candle is deliberately retained even after its 5m execution window expires.
    # This prevents that same 15m candle from being recreated as a fresh opportunity.
    return opportunity


def _zero_expired_boll_score(result, opp, now_ms):
    if not isinstance(result, dict) or not isinstance(opp, dict):
        return result
    try:
        opened, _age, _remaining, end = model._signal_window(opp, int(now_ms or 0))
    except Exception:
        return result
    if opened or int(now_ms or 0) < int(end or 0):
        return result
    for row in (result.get("scores") or {}).values():
        if not isinstance(row, dict):
            continue
        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
        if "boll_entry" in layers:
            layers["boll_entry"] = 0.0
        row["layers"] = layers
        items = []
        for item in row.get("items") or []:
            if isinstance(item, (tuple, list)) and len(item) >= 3 and "BOLL" in str(item[0]):
                values = list(item)
                values[0] = "15m BOLL外轨触发（执行窗口已结束）"
                values[1] = 0.0
                item = tuple(values) if isinstance(item, tuple) else values
            items.append(item)
        row["items"] = items
        conf = row.setdefault("confirmations", {})
        required = conf.setdefault("required", {})
        required["boll_entry_signal"] = False
        required["signal_window_5m"] = False
        row["gate"] = False
        row["eligible"] = False
        row["position_multiplier"] = 0.0
        row["level"] = "15m外轨触发的5m执行窗口已结束"
        s167._reprice_without_kdj(row)
    result["side"] = "观望"
    result["why"] = "本根15m BOLL外轨触发的5m执行窗口已结束；等待下一根15m收盘重新判断"
    result["status"] = "等待下一根15m收盘"
    return result


def _evaluate_15m_only(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                       maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                       now_ms=None, allow_new=True):
    _pin_15m_only()
    incoming = _prepare_incoming(opportunity, quarter)
    result, opp, transition = _PREVIOUS_EVALUATE(
        hour, quarter, five, one, four,
        opportunity=incoming,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    if isinstance(opp, dict) and not _valid_15m_evidence(opp):
        opp = None
        transition = ("invalidated", "V1.6.7 Build1671：非15m BOLL外轨机会已丢弃")
    effective_now = int(now_ms if now_ms is not None else (int(one[-1]["t"]) + 60_000 if one else 0))
    result = _zero_expired_boll_score(result, opp, effective_now)
    if isinstance(result, dict):
        result["strategy_version"] = VERSION
        result["build"] = BUILD
        result["boll_trigger_timeframe"] = "15m"
        result["five_minute_boll_enabled"] = False
        result["signal_lifecycle"] = "one_closed_15m_outer_trigger_one_5m_execution_window"
    return result, opp, transition


def _execution_checks_15m_only(plan, opportunity, score):
    _pin_15m_only()
    ok, diag, blockers = _PREVIOUS_EXECUTION_CHECKS(plan, opportunity, score)
    blockers = list(blockers or [])
    if not _valid_15m_evidence(opportunity):
        blockers.append("V1.6.7 Build1671开仓禁止：缺少有效的15m BOLL外轨触发证据")
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "build": BUILD,
        "boll_trigger_timeframe": "15m",
        "five_minute_boll_enabled": False,
        "strict_15m_boll_evidence": _valid_15m_evidence(opportunity),
    })
    blockers = list(dict.fromkeys(str(x) for x in blockers if str(x)))
    return not blockers, diag, blockers


def _pre_submit_guard_15m_only(owner, market, score):
    blockers = list(_PREVIOUS_PRE_SUBMIT_GUARD(owner, market, score) or [])
    opp = runtime._opportunity_from(market, score)
    blockers = [
        _rewrite_runtime_text_15m_only(x)
        for x in blockers
        if "5m BOLL" not in str(x) or "15m BOLL" in str(x)
    ]
    if not _valid_15m_evidence(opp):
        blockers.append("V1.6.7 Build1671 Final Entry Guard：15m BOLL外轨触发证据无效，禁止开仓")
    return list(dict.fromkeys(str(x) for x in blockers if str(x)))


def _clear_stale_15m_only(owner, *, emit_log=False):
    store = getattr(owner, "store", None)
    if store is None:
        return False
    opp = store.data.get("v152_opportunity")
    if not isinstance(opp, dict):
        return False
    # Never clear a valid 15m trigger merely because its 5m execution window expired.
    # Keeping it until the next 15m close is the dedupe lock against same-candle recreation.
    if _valid_15m_evidence(opp):
        return False
    # Remove legacy Build1670/5m opportunities immediately.
    old_id = str(opp.get("id") or "")
    store.data["v152_opportunity"] = None
    store.data["v152_rearm_zone"] = None
    store.save()
    try:
        store.record("V1.6.7清理非15m BOLL机会", {"opportunity_id": old_id, "required_boll_timeframe": "15m"})
    except Exception:
        pass
    if emit_log:
        owner.emit("log", "V1.6.7 Build1671：已清理旧版/非15m BOLL机会；仅等待新的15m BOLL外轨触发")
    return True


def _rewrite_runtime_text_15m_only(data):
    if not isinstance(data, str):
        return data
    text = _PREVIOUS_RUNTIME_TEXT(data)
    if not isinstance(text, str):
        return text
    for old in ("Build1670", "Build 1670"):
        text = text.replace(old, "Build1671" if old == "Build1670" else "Build 1671")
    replacements = (
        ("上一5m BOLL信号周期已经结束", "本根15m BOLL外轨触发的5m执行窗口已结束"),
        ("当前5m信号周期已经结束", "本根15m BOLL外轨触发的5m执行窗口已结束"),
        ("允许等待新的5m BOLL信号", "等待下一根15m收盘重新判断BOLL外轨"),
        ("等待新的5m BOLL信号", "等待下一根15m收盘重新判断BOLL外轨"),
        ("最新已收盘5m BOLL外轨信号", "最新已收盘15m BOLL外轨触发"),
        ("5m BOLL信号周期", "15m BOLL触发的5m执行窗口"),
        ("5m外轨信号", "15m BOLL外轨触发"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    text = re.sub(r"(?<!1)5m BOLL", "15m BOLL", text)
    if text.startswith("自动交易启动；"):
        return (
            "自动交易启动；V1.6.7 Build1671：BOLL仅使用最新已收盘15m上下外轨；"
            "5m不再参与任何BOLL触发或BOLL评分，只用于RSI30-70、MACD、Volume及15m触发后的5分钟执行窗口；"
            "做空必须15m High触及/突破15m上轨，做多必须15m Low触及/跌破15m下轨；"
            "同一根15m触发只允许一次5分钟执行窗口，窗口结束后等待下一根15m收盘；"
            "MACD仅明确逆向时禁止开仓，改善保留+1分；KDJ不计分；4H同向、Volume<1.20×、评分≥6及其余风控保持不变"
        )
    return text


def _app_init_1671(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    try:
        self.root.title("KAYTRADE 1.6.7 · BTC 策略控制台 · Build 1671")
    except Exception:
        pass
    try:
        label = getattr(self, "_v167_version_label", None)
        if label is not None:
            label.configure(text="V1.6.7 · Build 1671")
    except Exception:
        pass
    try:
        self.signal.set(
            "V1.6.7 Build1671 · 15m BOLL外轨ONLY / 5m RSI30–70 / MACD非逆向Hard Gate（改善+1） / "
            "5m Volume<1.20× / KDJ不计分 / 4H同向"
        )
    except Exception:
        pass


def apply():
    if getattr(model, "_kaytrade_v167_15m_boll_only_applied", False):
        return
    _pin_15m_only()
    model.evaluate = _evaluate_15m_only
    model.execution_checks = _execution_checks_15m_only
    runtime._pre_submit_guard = _pre_submit_guard_15m_only
    runtime._clear_stale_opportunity = _clear_stale_15m_only
    runtime._rewrite_runtime_text = _rewrite_runtime_text_15m_only
    app.App.__init__ = _app_init_1671
    model.VERSION = VERSION
    model.BUILD = BUILD
    model.BOLL_TRIGGER_TIMEFRAME = "15m"
    model.FIVE_MINUTE_BOLL_ENABLED = False
    s167.BUILD = BUILD
    a167.BUILD = BUILD
    ui166.VERSION = VERSION
    ui166.BUILD = BUILD
    model._kaytrade_v167_15m_boll_only_applied = True
    app.App._kaytrade_v167_15m_boll_only_applied = True


apply()

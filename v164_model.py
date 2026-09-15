"""KAYTRADE V1.6.4 strategy overlay — Build 1641 execution hotfix.

Build 1641 keeps the confirmed V1.6.4 entry model and fixes the production
signal lifecycle:
- 5m BOLL outer signal is valid for exactly four minutes after the signal bar closes;
- expired/stale opportunities cannot be recreated from the same old closed 5m bar;
- remove 5m signal-volume +0.5 from scoring;
- transfer that +0.5 to the 5m BOLL outer-band signal (2.5 total);
- Volume >=1.20x prior-20 5m average is a hard opening gate;
- outer-only RSI 30-70, formal 5m MACD improvement and 4H alignment remain.
"""
from __future__ import annotations

import math
import re

import v153_model as signal_core
import v154_model as window_base
import v163_model as base

VERSION = "1.6.4"
THRESHOLD = base.THRESHOLD
ENTRY_WINDOW_MS = 4 * 60 * 1000
TIME_WINDOW_ENABLED = True
SIGNAL_DRIFT_ATR = base.SIGNAL_DRIFT_ATR
FRONT_MIN_R = base.FRONT_MIN_R
COST_MAX_R = base.COST_MAX_R
STOP_BUFFER_ATR = base.STOP_BUFFER_ATR
OVERLAP_ATR_TOL = base.OVERLAP_ATR_TOL
OUTER_PATHS = base.OUTER_PATHS
MIDDLE_PATH = base.MIDDLE_PATH
SCORE_MAX = base.SCORE_MAX
RSI_MIN = base.RSI_MIN
RSI_MAX = base.RSI_MAX

VOLUME_HARD_GATE = 1.20
BOLL_OUTER_BONUS = 0.50
BOLL_OUTER_SCORE = 2.50

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
boll_entry_signal = base.boll_entry_signal
_entry_ok = base._entry_ok
_macd_improving = base._macd_improving
_path_from = base._path_from


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _effective_now_ms(one, now_ms=None):
    if now_ms is not None:
        return int(now_ms)
    if one:
        return int(one[-1]["t"]) + 60_000
    return 0


def _signal_close_ms(opportunity):
    if not isinstance(opportunity, dict):
        return 0
    try:
        return int(opportunity.get("signal_close_ms") or opportunity.get("created_ms") or 0)
    except (TypeError, ValueError):
        return 0


def _signal_window(opportunity, now_ms):
    start = _signal_close_ms(opportunity)
    now = int(now_ms or 0)
    end = start + ENTRY_WINDOW_MS if start > 0 else 0
    opened = bool(start > 0 and start <= now < end)
    remaining_ms = max(0, end - now) if end else 0
    age_ms = max(0, now - start) if start else 0
    return opened, age_ms, remaining_ms, end


def _window_state_4m(now_ms, opportunity):
    opened, age_ms, remaining_ms, _end = _signal_window(opportunity, now_ms)
    minute = 0
    if opened:
        minute = min(4, max(1, int(age_ms // 60_000) + 1))
    return opened, minute, remaining_ms


def _normalize_opportunity_4m(opportunity):
    if not isinstance(opportunity, dict):
        return opportunity
    opp = dict(opportunity)
    start = _signal_close_ms(opp)
    if start > 0:
        opp["expires_ms"] = start + ENTRY_WINDOW_MS
        opp["time_window_enabled"] = True
    return opp


def new_opportunity(hour, quarter, five, one, side, signal):
    # v153 is the actual opportunity constructor below the historical v154
    # no-expiry layer. Force the final production four-minute semantics here.
    signal_core.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    signal_core._window_state = _window_state_4m
    opp = signal_core.new_opportunity(hour, quarter, five, one, side, signal)
    return _normalize_opportunity_4m(opp)


def _evaluate_windowed_core(hour, quarter, five, one, four, opportunity=None,
                            stop_atr=1.0, maker_bps=2.0, taker_bps=5.0,
                            slippage_bps=5.0, now_ms=None, allow_new=True):
    """Replacement for the inherited v154 no-expiry evaluator.

    v154 historically rewrote every opportunity to expires_ms=0. Build 1641
    bypasses only that obsolete time semantic and otherwise uses the audited
    v153 BOLL/structure/cost engine unchanged.
    """
    signal_core.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    signal_core._window_state = _window_state_4m
    opportunity = _normalize_opportunity_4m(opportunity)
    result, opp, transition = signal_core.evaluate(
        hour, quarter, five, one, four,
        opportunity=opportunity,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    opp = _normalize_opportunity_4m(opp)
    if isinstance(result, dict):
        result["time_window_enabled"] = True
        result["strategy_version"] = VERSION
        if isinstance(opp, dict):
            result["opportunity"] = dict(opp)
    return result, opp, transition


def _execution_checks_windowed_core(plan, opportunity, score):
    ok, diag, blockers = signal_core.execution_checks(plan, opportunity, score)
    diag = dict(diag or {})
    diag["time_window_enabled"] = True
    diag["entry_window_ms"] = ENTRY_WINDOW_MS
    return ok, diag, list(blockers or [])


def _install_window_patch():
    """Patch only the historical no-expiry layer used by the live V1.6.x chain."""
    signal_core.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    signal_core._window_state = _window_state_4m
    # V1.6.3 already installs outer-only BOLL generation on signal_core. Keep it
    # pinned here too so a direct V1.6.4 model call cannot fall back to middle.
    signal_core.boll_entry_signal = base.boll_entry_signal

    window_base.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    window_base.TIME_WINDOW_ENABLED = True
    window_base._window_state = _window_state_4m
    window_base.new_opportunity = new_opportunity
    window_base.evaluate = _evaluate_windowed_core
    window_base.execution_checks = _execution_checks_windowed_core


# Install at import time for all production facades, and again before evaluate
# as defense against another historical overlay mutating shared module globals.
_install_window_patch()


def _opportunity(row=None, result=None, opportunity=None):
    if isinstance(opportunity, dict) and opportunity:
        return opportunity
    if isinstance(row, dict):
        own = row.get("opportunity")
        if isinstance(own, dict) and own:
            return own
    if isinstance(result, dict):
        own = result.get("opportunity")
        if isinstance(own, dict) and own:
            return own
    return None


def _volume_ratio(row=None, result=None, opportunity=None):
    opp = _opportunity(row=row, result=result, opportunity=opportunity)
    if not isinstance(opp, dict):
        return None
    return _finite(opp.get("five_signal_volume_ratio"))


def _rewrite_items(row, path):
    if not isinstance(row, dict):
        return row
    layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
    rewritten = []
    for item in row.get("items") or []:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            rewritten.append(item)
            continue
        label = str(item[0])
        if "成交量" in label or "volume" in label.lower():
            continue
        value, maximum = item[1], item[2]
        if "BOLL" in label:
            if path in OUTER_PATHS and "boll_entry" in layers:
                value = layers.get("boll_entry", value)
            label, maximum = "5m BOLL外轨信号（4分钟有效）", BOLL_OUTER_SCORE
        elif "MACD" in label:
            label = "5m 外轨MACD连续改善（必须）"
        elif label.startswith("4H"):
            label = "4H同向（外轨 Hard Gate）"
        elif "15m EMA50" in label:
            label = "15m EMA50顺交易方向推进"
        elif "KDJ" in label:
            label = "5m KDJ动能交叉"
        elif "15m正确方向回调区共振" in label:
            label = "15m顺势回调区共振"
        elif "计划入场价接近15m回调区" in label:
            label = "入场位置接近15m回调区"
        elif "确认水平支撑/阻力与15m回调区重合" in label:
            label = "15m回调区 × 水平结构共振"
        values = [label, value, maximum]
        rewritten.append(tuple(values) if isinstance(item, tuple) else values)
    row["items"] = rewritten
    return row


def _reprice_score(row, path):
    layers = row.get("layers")
    if not isinstance(layers, dict):
        return
    layers.pop("volume5", None)
    if path in OUTER_PATHS:
        boll = _finite(layers.get("boll_entry"))
        if boll is not None and boll > 0:
            # Idempotent: repeated UI/runtime rewriting must never add +0.5 twice.
            layers["boll_entry"] = BOLL_OUTER_SCORE
    numeric = []
    for value in layers.values():
        number = _finite(value)
        if number is not None:
            numeric.append(number)
    raw = sum(numeric)
    total = max(0.0, min(SCORE_MAX, round(raw * 2.0) / 2.0))
    row["raw"] = raw
    row["total"] = total


def _apply_v164_row(row, path, result=None, opportunity=None):
    if not isinstance(row, dict):
        return row

    if path not in OUTER_PATHS:
        _rewrite_items(row, path)
        return row

    confirmations = row.setdefault("confirmations", {})
    required = confirmations.setdefault("required", {})
    blockers = list(confirmations.get("blockers") or [])

    ratio = _volume_ratio(row=row, result=result, opportunity=opportunity)
    volume_known = ratio is not None
    volume_ok = bool(volume_known and ratio < VOLUME_HARD_GATE)
    required["volume_below_1_2x"] = volume_ok
    confirmations["volume_ratio_5m"] = ratio
    confirmations["volume_hard_gate_ok"] = volume_ok
    confirmations["volume_hard_gate_rule"] = "5m signal volume must be < 1.20x prior-20 5m average"

    remaining = int(max(0, (result or {}).get("opportunity_remaining_seconds") or 0))
    window_ok = bool(required.get("entry_window_4x1m", remaining > 0))
    required["signal_window_4m"] = window_ok
    confirmations["signal_window_ok"] = window_ok
    confirmations["signal_remaining_seconds"] = remaining

    _reprice_score(row, path)
    _rewrite_items(row, path)

    if not volume_known:
        blockers.append("V1.6.4 Volume Hard Gate：5m信号K量能数据不可用，禁止开仓")
    elif not volume_ok:
        blockers.append(
            f"V1.6.4 Volume Hard Gate：5m信号K量能 {ratio:.2f}× ≥ {VOLUME_HARD_GATE:.2f}×，禁止开仓"
        )
    if not window_ok:
        blockers.append("V1.6.4外轨信号已超过4分钟执行窗口，禁止开仓")

    base_gate = bool(row.get("gate"))
    row["gate"] = bool(base_gate and volume_ok and window_ok)
    total = float(row.get("total") or 0.0)
    row["eligible"] = bool(row["gate"] and total >= THRESHOLD)
    row["position_multiplier"] = 1.0 if row["eligible"] else 0.0

    old_reason = str(row.get("reason") or "")
    old_reason = re.sub(r"评分\s+[0-9.]+/10", f"评分 {total:g}/10", old_reason)
    ratio_text = "未知" if ratio is None else f"{ratio:.2f}×"
    remaining_text = f"{remaining // 60}:{remaining % 60:02d}"
    if not window_ok:
        row["level"] = "外轨信号已失效"
        row["reason"] = "V1.6.4外轨信号超过4分钟执行窗口，等待新的已收盘5m外轨信号"
    elif not volume_ok:
        row["level"] = "Volume Hard Gate · 禁止开仓"
        row["reason"] = blockers[-1] if blockers else "Volume Hard Gate 禁止开仓"
    elif row["eligible"]:
        row["level"] = "V1.6.4外轨信号 · 1×"
        row["reason"] = (
            f"允许开仓｜外轨信号2.5分｜剩余 {remaining_text}｜"
            f"Volume {ratio_text} < {VOLUME_HARD_GATE:.2f}×｜评分 {total:g}/{SCORE_MAX:g}"
        )
    elif not base_gate:
        row["reason"] = (
            (old_reason + "｜") if old_reason else ""
        ) + f"V1.6.4外轨信号剩余 {remaining_text}｜Volume {ratio_text} < {VOLUME_HARD_GATE:.2f}×"
    else:
        row["level"] = "未达开仓线"
        row["reason"] = (
            f"外轨信号2.5分｜剩余 {remaining_text}｜Volume {ratio_text} < {VOLUME_HARD_GATE:.2f}×｜"
            f"评分 {total:g}/{SCORE_MAX:g}，未达开仓阈值 {THRESHOLD:g}"
        )

    confirmations["blockers"] = _dedupe(blockers)
    return row


def _reselect(scores):
    qualified = [
        side for side, row in (scores or {}).items()
        if isinstance(row, dict) and row.get("eligible")
    ]
    if len(qualified) == 1:
        return qualified[0]
    if len(qualified) == 2:
        a, b = qualified
        sa = float(scores[a].get("total") or 0.0)
        sb = float(scores[b].get("total") or 0.0)
        if sa != sb:
            return a if sa > sb else b
    return "观望"


def _rewrite_result(result, opportunity=None):
    if not isinstance(result, dict):
        return result
    result["strategy_version"] = VERSION
    result["score_max"] = SCORE_MAX
    result["time_window_enabled"] = True
    scores = result.get("scores") or {}
    for row in scores.values():
        if not isinstance(row, dict):
            continue
        path = _path_from(row, result=result, opportunity=opportunity)
        _apply_v164_row(row, path, result=result, opportunity=opportunity)

    selected = _reselect(scores)
    result["side"] = selected
    if selected != "观望":
        result["why"] = str(scores[selected].get("reason") or "")
    else:
        direction = str(result.get("direction") or "")
        if direction in scores and isinstance(scores.get(direction), dict):
            result["why"] = str(scores[direction].get("reason") or result.get("why") or "")
    return result


def _mark_expired_result(result, transition):
    if not isinstance(result, dict):
        return result
    result["side"] = "观望"
    result["status"] = "外轨信号已失效"
    result["why"] = "V1.6.4外轨信号4分钟执行窗口已结束；等待新的已收盘5m外轨信号"
    result["transition"] = transition
    result["opportunity"] = None
    result["opportunity_id"] = ""
    result["opportunity_remaining_seconds"] = 0
    result["entry_window_minute"] = 0
    result["time_window_enabled"] = True
    return result


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    _install_window_patch()
    current_ms = _effective_now_ms(one, now_ms)
    incoming = _normalize_opportunity_4m(opportunity)
    expired_incoming = bool(incoming and not _signal_window(incoming, current_ms)[0]
                            and current_ms >= _signal_close_ms(incoming) + ENTRY_WINDOW_MS)

    effective_opp = None if expired_incoming else incoming
    effective_allow_new = bool(allow_new and not expired_incoming)
    result, opp, transition = base.evaluate(
        hour, quarter, five, one, four,
        opportunity=effective_opp,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=current_ms,
        allow_new=effective_allow_new,
    )
    opp = _normalize_opportunity_4m(opp)

    # A restart can have no stored opportunity while the latest closed 5m bar is
    # already older than four minutes. Never recreate that stale signal.
    expired_returned = bool(opp and current_ms >= _signal_close_ms(opp) + ENTRY_WINDOW_MS)
    if expired_incoming or expired_returned:
        expired_id = str((incoming or opp or {}).get("id") or "")
        result, _discard, _ = base.evaluate(
            hour, quarter, five, one, four,
            opportunity=None,
            stop_atr=stop_atr,
            maker_bps=maker_bps,
            taker_bps=taker_bps,
            slippage_bps=slippage_bps,
            now_ms=current_ms,
            allow_new=False,
        )
        opp = None
        transition = (
            "invalidated",
            "V1.6.4外轨信号4分钟执行窗口已结束" + (f"：{expired_id}" if expired_id else ""),
        )
        _mark_expired_result(result, transition)

    if isinstance(opp, dict):
        opened, age_ms, remaining_ms, end_ms = _signal_window(opp, current_ms)
        opp["expires_ms"] = end_ms
        opp["time_window_enabled"] = True
        if isinstance(result, dict):
            result["opportunity"] = dict(opp)
            result["opportunity_remaining_seconds"] = int(math.ceil(remaining_ms / 1000.0))
            result["signal_age_seconds"] = int(age_ms // 1000)
            result["time_window_enabled"] = True
            # Keep the core required flag synchronized with the final window.
            side = str(opp.get("side") or "")
            row = (result.get("scores") or {}).get(side)
            if isinstance(row, dict):
                required = row.setdefault("confirmations", {}).setdefault("required", {})
                required["entry_window_4x1m"] = bool(opened)
                row["opportunity"] = dict(opp)

    return _rewrite_result(result, opp), opp, transition


def execution_checks(plan, opportunity, score):
    _install_window_patch()
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    blockers = list(blockers or [])
    path = _path_from(score if isinstance(score, dict) else {}, opportunity=opportunity)
    ratio = _volume_ratio(row=score if isinstance(score, dict) else {}, opportunity=opportunity)
    volume_ok = bool(ratio is not None and ratio < VOLUME_HARD_GATE)
    required = ((score or {}).get("confirmations") or {}).get("required") or {}
    window_ok = bool(required.get("entry_window_4x1m", required.get("signal_window_4m", False)))

    if path in OUTER_PATHS:
        if ratio is None:
            blockers.append("V1.6.4开仓禁止：5m信号K量能数据不可用")
        elif not volume_ok:
            blockers.append(
                f"V1.6.4开仓禁止：5m信号K量能 {ratio:.2f}× ≥ {VOLUME_HARD_GATE:.2f}×"
            )
        if not window_ok:
            blockers.append("V1.6.4开仓禁止：5m BOLL外轨信号已超过4分钟执行窗口")

    blockers = _dedupe(blockers)
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "volume_ratio_5m": ratio,
        "volume_hard_gate": VOLUME_HARD_GATE,
        "volume_hard_gate_ok": volume_ok if path in OUTER_PATHS else None,
        "boll_outer_score": BOLL_OUTER_SCORE,
        "volume_score": 0.0,
        "time_window_enabled": True,
        "entry_window_ms": ENTRY_WINDOW_MS,
        "signal_window_ok": window_ok if path in OUTER_PATHS else None,
    })
    return not blockers, diag, blockers

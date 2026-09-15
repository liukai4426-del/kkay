"""Research-only overlay: V1.6.2 outer RSI/MACD model with Volume as a hard gate.

Only change versus research_v162_outer_rsi_macd_model:
- 5m signal-candle Volume >= 1.2x previous-20 average NEVER earns +0.5;
- the same Volume condition is a hard entry blocker.
Everything else is inherited unchanged.
"""
from __future__ import annotations

import research_v162_outer_rsi_macd_model as outer

VERSION = "1.6.2-research-outer-rsi30-70-macd-volume-hard-gate"
THRESHOLD = outer.THRESHOLD
ENTRY_WINDOW_MS = outer.ENTRY_WINDOW_MS
TIME_WINDOW_ENABLED = outer.TIME_WINDOW_ENABLED
SIGNAL_DRIFT_ATR = outer.SIGNAL_DRIFT_ATR
FRONT_MIN_R = outer.FRONT_MIN_R
COST_MAX_R = outer.COST_MAX_R
STOP_BUFFER_ATR = outer.STOP_BUFFER_ATR
OVERLAP_ATR_TOL = outer.OVERLAP_ATR_TOL
OUTER_PATHS = outer.OUTER_PATHS
MIDDLE_PATH = outer.MIDDLE_PATH
SCORE_MAX = outer.SCORE_MAX
RSI_MIN = outer.RSI_MIN
RSI_MAX = outer.RSI_MAX
VOLUME_RATIO_THRESHOLD = 1.2

trend_direction = outer.trend_direction
four_hour_state = outer.four_hour_state
_entry_ok = outer._entry_ok
_window_state = outer._window_state
new_opportunity = outer.new_opportunity
_macd_improving = outer._macd_improving
_path_from = outer._path_from
boll_entry_signal = outer.boll_entry_signal
_install_signal_patch = outer._install_signal_patch


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _volume_on(row, opportunity=None):
    opp = opportunity if isinstance(opportunity, dict) else None
    if opp is None and isinstance(row, dict):
        candidate = row.get("opportunity")
        if isinstance(candidate, dict):
            opp = candidate
    if isinstance(opp, dict) and bool(opp.get("five_signal_volume_ok")):
        return True
    layers = (row or {}).get("layers") or {}
    return float(layers.get("volume5") or 0.0) > 0.0


def _remove_volume_bonus_and_gate(row, opportunity=None):
    if not isinstance(row, dict):
        return row

    confirmations = row.setdefault("confirmations", {})
    required = confirmations.setdefault("required", {})
    blockers = list(confirmations.get("blockers") or [])
    layers = row.setdefault("layers", {})

    volume_on = _volume_on(row, opportunity)
    legacy_points = float(layers.get("volume5") or 0.0)
    if legacy_points:
        raw = float(row.get("raw") or row.get("total") or 0.0) - legacy_points
        row["raw"] = raw
        row["total"] = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
    layers["volume5"] = 0.0

    items = []
    for item in row.get("items") or []:
        if isinstance(item, (list, tuple)) and item and "成交量" in str(item[0]):
            items.append(("5m信号K线成交量≥20均量1.2×（Hard Gate）", 0.0, 0.0))
        else:
            items.append(item)
    row["items"] = items

    required["volume_below_1_2x_20"] = not volume_on
    confirmations["volume_hard_gate_enabled"] = True
    confirmations["volume_ratio_threshold"] = VOLUME_RATIO_THRESHOLD
    confirmations["volume_signal_on"] = bool(volume_on)

    if volume_on:
        blockers.append("研究规则：5m信号K线成交量≥前20根均量1.2×，Hard Gate禁止开仓")
        row["gate"] = False
        row["eligible"] = False
        row["position_multiplier"] = 0.0
        row["signal_tier"] = 0
        row["level"] = "Volume Hard Gate"
        row["reason"] = str(row.get("reason") or "") + "｜禁止：Volume≥1.2×20均量"
    else:
        # Score is unchanged for Volume-OFF signals because the legacy component was already zero.
        row["eligible"] = bool(row.get("gate")) and float(row.get("total") or 0.0) >= THRESHOLD
        row["position_multiplier"] = 1.0 if row["eligible"] else 0.0
        row["signal_tier"] = 1 if row["eligible"] else 0

    confirmations["blockers"] = _dedupe(blockers)
    return row


def _rewrite_result(result, opportunity=None):
    if not isinstance(result, dict):
        return result
    scores = result.get("scores") or {}
    original = result.get("side")
    for row in scores.values():
        _remove_volume_bonus_and_gate(row, opportunity)

    selected = original
    if selected not in scores or not isinstance(scores.get(selected), dict) or not scores[selected].get("eligible"):
        selected = outer._reselect(scores)
    result["side"] = selected
    result["strategy_version"] = VERSION
    if selected != "观望":
        result["why"] = str(scores[selected].get("reason") or "")
    elif original != "观望":
        result["why"] = "研究规则过滤：Volume≥1.2×20均量禁止开仓，且Volume不再加0.5分"
    return result


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    result, opp, transition = outer.evaluate(
        hour, quarter, five, one, four,
        opportunity=opportunity,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    return _rewrite_result(result, opp), opp, transition


def execution_checks(plan, opportunity, score):
    ok, diag, blockers = outer.execution_checks(plan, opportunity, score)
    blockers = list(blockers or [])
    volume_on = bool((opportunity or {}).get("five_signal_volume_ok")) if isinstance(opportunity, dict) else False
    if volume_on:
        blockers.append("研究规则：5m Volume≥1.2×20均量，Hard Gate禁止开仓")
    blockers = _dedupe(blockers)
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "volume_hard_gate_enabled": True,
        "volume_ratio_threshold": VOLUME_RATIO_THRESHOLD,
        "volume_signal_on": volume_on,
        "volume_score_points": 0.0,
    })
    return not blockers, diag, blockers

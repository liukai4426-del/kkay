"""Research overlay: V1.6.0 baseline + 4H opposite hard lock only.

Changes from V1.6.0:
- 4H aligned keeps +1.
- 4H neutral keeps 0.
- 4H opposite no longer contributes -1; instead it is a hard entry block.
Everything else, including the original 3-bar middle MACD rule and V1.6.0
1x/2x path sizing semantics, remains unchanged.
"""
from __future__ import annotations

import v160_model as base

VERSION = "1.6.0-research-4h-opposite-lock"
THRESHOLD = base.THRESHOLD
ENTRY_WINDOW_MS = base.ENTRY_WINDOW_MS
TIME_WINDOW_ENABLED = base.TIME_WINDOW_ENABLED
MIDDLE_TOUCH_ATR = base.MIDDLE_TOUCH_ATR
SIGNAL_DRIFT_ATR = base.SIGNAL_DRIFT_ATR
LONG_MIDDLE_RSI_MIN = base.LONG_MIDDLE_RSI_MIN
SHORT_MIDDLE_RSI_MAX = base.SHORT_MIDDLE_RSI_MAX
FRONT_MIN_R = base.FRONT_MIN_R
COST_MAX_R = base.COST_MAX_R
STOP_BUFFER_ATR = base.STOP_BUFFER_ATR
OVERLAP_ATR_TOL = base.OVERLAP_ATR_TOL
OUTER_PATHS = base.OUTER_PATHS
MIDDLE_PATH = base.MIDDLE_PATH
SCORE_MAX = 10.0

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
boll_entry_signal = base.boll_entry_signal
_entry_ok = base._entry_ok
_window_state = base._window_state
new_opportunity = base.new_opportunity
_macd_improving = base._macd_improving


def _dedupe(values):
    out = []
    for value in values:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _rewrite_4h_item(items, score):
    out = []
    for item in items or []:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            out.append(item)
            continue
        label, value, maximum = item[0], item[1], item[2]
        if "4H同向" in str(label):
            value = score
        out.append((label, value, maximum))
    return out


def _apply_opposite_lock(result, opportunity=None):
    if not isinstance(result, dict):
        return result
    result["strategy_version"] = VERSION
    result["score_max"] = SCORE_MAX

    scores = result.get("scores") or {}
    blocked_selected = False
    for side, row in scores.items():
        if not isinstance(row, dict):
            continue
        confirmations = row.setdefault("confirmations", {})
        required = confirmations.setdefault("required", {})
        blockers = list(confirmations.get("blockers") or [])
        state = str(confirmations.get("4H_trend_state") or "")

        layers = dict(row.get("layers") or {})
        if state == "opposite":
            # Remove the legacy -1 deduction. Opposite is handled only as a hard lock.
            layers["trend4h"] = 0.0
            row["layers"] = layers
            raw = sum(float(v or 0.0) for v in layers.values())
            total = max(0.0, min(SCORE_MAX, round(raw * 2.0) / 2.0))
            row["raw"] = raw
            row["total"] = total
            row["signal_tier"] = 1 if total >= THRESHOLD else 0
            row["items"] = _rewrite_4h_item(row.get("items"), 0.0)
            blockers.append("4H趋势明确逆向：禁止开仓")
            required["4h_not_opposite"] = False
            row["gate"] = False
            row["eligible"] = False
            row["position_multiplier"] = 0.0
            row["level"] = "禁止开仓"
            reason = str(row.get("reason") or "")
            msg = "4H趋势明确逆向，Hard Lock禁止开仓"
            row["reason"] = f"{reason}｜{msg}" if reason else msg
            if result.get("side") == side:
                blocked_selected = True
        else:
            required["4h_not_opposite"] = True

        confirmations["required"] = required
        confirmations["blockers"] = _dedupe(blockers)

    if blocked_selected:
        result["side"] = "观望"
        result["why"] = "4H趋势明确逆向，Hard Lock禁止开仓"
    return result


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    result, opp, transition = base.evaluate(
        hour, quarter, five, one, four,
        opportunity=opportunity,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    return _apply_opposite_lock(result, opportunity=opp), opp, transition


def execution_checks(plan, opportunity, score):
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    blockers = list(blockers or [])
    state = str((((score or {}).get("confirmations") or {}).get("4H_trend_state")) or "")
    if state == "opposite":
        blockers.append("4H趋势明确逆向：禁止开仓")
    blockers = _dedupe(blockers)
    diag = dict(diag or {})
    diag["strategy_version"] = VERSION
    diag["4H_trend_state"] = state
    diag["4h_opposite_hard_lock"] = True
    return not blockers, diag, blockers

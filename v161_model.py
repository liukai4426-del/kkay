"""KAYTRADE V1.6.1 strategy overlay.

V1.6.1 keeps the V1.6.0 model and adds one entry hard-gate only:
- 5m BOLL lower-band long requires 4H aligned with the long direction;
- 5m BOLL upper-band short mirrors the same rule and requires 4H aligned.
4H neutral/opposite therefore cannot open an outer-band position. middle_rsi keeps
V1.6.0 semantics, including the formal 5m macd_improving requirement.
"""
from __future__ import annotations

import v160_model as base

VERSION = "1.6.1"
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
SCORE_MAX = getattr(base, "SCORE_MAX", 10.0)

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
boll_entry_signal = base.boll_entry_signal
_entry_ok = base._entry_ok
_window_state = base._window_state
new_opportunity = base.new_opportunity
_macd_improving = base._macd_improving
_path_from = base._path_from


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _normalize_version_text(value):
    return str(value or "").replace("V1.5.4未开仓", "V1.6.1未开仓")


def _apply_outer_4h_gate(row, path):
    if not isinstance(row, dict) or path not in OUTER_PATHS:
        return row
    confirmations = row.setdefault("confirmations", {})
    required = confirmations.setdefault("required", {})
    state = str(confirmations.get("4H_trend_state") or "")
    aligned = state == "aligned"
    required["outer_4h_aligned"] = aligned
    confirmations["outer_4h_rule"] = "5m BOLL outer-band requires aligned 4H trend"
    blockers = list(confirmations.get("blockers") or [])
    if not aligned:
        blockers.append("5m BOLL外轨要求4H同向：4H中性/逆向禁止开仓")
        row["gate"] = False
        row["eligible"] = False
        row["position_multiplier"] = 0.0
        row["level"] = "等待4H同向"
        reason = _normalize_version_text(row.get("reason"))
        msg = "V1.6.1外轨Hard Gate：4H必须与交易方向同向"
        row["reason"] = f"{reason}｜{msg}" if reason else msg
    else:
        row["reason"] = _normalize_version_text(row.get("reason"))
    confirmations["blockers"] = _dedupe(blockers)
    return row


def _apply_row_rules(row, path):
    base._apply_row_rules(row, path)
    return _apply_outer_4h_gate(row, path)


def _reselect(scores):
    qualified = [side for side, row in scores.items() if isinstance(row, dict) and row.get("eligible")]
    if len(qualified) == 1:
        return qualified[0]
    if len(qualified) == 2:
        first, second = qualified
        a = float(scores[first].get("total") or 0.0)
        b = float(scores[second].get("total") or 0.0)
        if a != b:
            return first if a > b else second
    return "观望"


def _rewrite_result(result, opportunity=None):
    if not isinstance(result, dict):
        return result
    result["strategy_version"] = VERSION
    result["score_max"] = SCORE_MAX
    result["why"] = _normalize_version_text(result.get("why"))
    scores = result.get("scores") or {}
    original_side = result.get("side")
    for side, row in scores.items():
        if not isinstance(row, dict):
            continue
        row["reason"] = _normalize_version_text(row.get("reason"))
        path = _path_from(row, result=result, opportunity=opportunity)
        _apply_outer_4h_gate(row, path)

    selected = original_side
    if selected not in scores or not isinstance(scores.get(selected), dict) or not scores[selected].get("eligible"):
        selected = _reselect(scores)
    result["side"] = selected
    if selected != "观望":
        result["why"] = str(scores[selected].get("reason") or "")
    elif original_side != "观望":
        result["why"] = "V1.6.1：5m BOLL外轨信号要求4H同向；被Hard Gate过滤后无可执行方向"
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
    return _rewrite_result(result, opportunity=opp), opp, transition


def execution_checks(plan, opportunity, score):
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    blockers = list(blockers or [])
    path = _path_from(score if isinstance(score, dict) else {}, opportunity=opportunity)
    state = str((((score or {}).get("confirmations") or {}).get("4H_trend_state")) or "")
    if path in OUTER_PATHS and state != "aligned":
        blockers.append("V1.6.1外轨开仓禁止：5m BOLL外轨必须4H同向")
    blockers = _dedupe(blockers)
    diag = dict(diag or {})
    diag["strategy_version"] = VERSION
    diag["boll_signal_path"] = path
    diag["outer_4h_aligned_required"] = path in OUTER_PATHS
    diag["4H_trend_state"] = state
    return not blockers, diag, blockers

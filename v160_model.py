"""Shared KAYTRADE V1.6.0 strategy overlay.

V1.6.0 changes only two entry semantics on top of the verified V1.5.6 core:
1) middle_rsi requires the formal existing 5m macd_improving state;
2) lower_band / upper_band are marked as 2x base-position entries.

The MACD definition itself is NOT changed.  It is still produced by the formal
V1.5.x model through v152_model._macd_state(): long c>b>a, short c<b<a on the
last three closed 5m MACD histogram values.  No cross/zero-axis/sign rule is
added here.
"""
from __future__ import annotations

import v154_model as base

VERSION = "1.6.0"
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

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
boll_entry_signal = base.boll_entry_signal
_entry_ok = base._entry_ok
_window_state = base._window_state
new_opportunity = base.new_opportunity

OUTER_PATHS = {"lower_band", "upper_band"}
MIDDLE_PATH = "middle_rsi"


def _path_from(row, result=None, opportunity=None):
    for source in (
        row.get("opportunity") if isinstance(row, dict) else None,
        (result or {}).get("opportunity") if isinstance(result, dict) else None,
        opportunity,
    ):
        if isinstance(source, dict):
            path = str(source.get("signal_path") or source.get("path") or "")
            if path:
                return path
    return ""


def _macd_improving(row):
    try:
        return float((row.get("layers") or {}).get("macd_improving") or 0.0) > 0.0
    except (TypeError, ValueError):
        return False


def _ensure_required(row):
    confirmations = row.setdefault("confirmations", {})
    required = confirmations.setdefault("required", {})
    return confirmations, required


def _apply_row_rules(row, path):
    """Apply only the V1.6.0 hard gate / sizing metadata to one score row."""
    if not isinstance(row, dict):
        return row

    confirmations, required = _ensure_required(row)
    total = float(row.get("total") or 0.0)

    if path == MIDDLE_PATH:
        macd_ok = _macd_improving(row)
        required["middle_macd_improving"] = macd_ok
        confirmations["middle_macd_rule"] = "formal_existing_macd_improving"
        confirmations["middle_macd_required"] = True
        row["position_multiplier"] = 1.0 if row.get("eligible") and macd_ok else 0.0
        row["boll_position_multiplier"] = 1.0
        if not macd_ok:
            row["gate"] = False
            row["eligible"] = False
            row["position_multiplier"] = 0.0
            row["level"] = "等待5m MACD改善"
            reason = str(row.get("reason") or "")
            msg = "5m BOLL中轨要求现有macd_improving通过；MACD连续两根未向交易方向改善，禁止开仓"
            row["reason"] = f"{reason}｜{msg}" if reason else msg
        elif row.get("eligible") and total >= THRESHOLD:
            row["level"] = "开仓信号 · 中轨1×基础仓位"

    elif path in OUTER_PATHS:
        # Outer-band trigger logic is unchanged.  Only the requested base-size
        # multiplier changes; live order construction enforces the same 2x rule.
        row["boll_position_multiplier"] = 2.0
        confirmations["outer_band_position_multiplier"] = 2.0
        if row.get("eligible") and total >= THRESHOLD:
            row["position_multiplier"] = 2.0
            row["level"] = "开仓信号 · 外轨2×基础仓位"

    return row


def _rewrite_result(result, opportunity=None):
    if not isinstance(result, dict):
        return result
    result["strategy_version"] = VERSION
    scores = result.get("scores") or {}
    blocked_selected = False
    for side, row in scores.items():
        if not isinstance(row, dict):
            continue
        path = _path_from(row, result=result, opportunity=opportunity)
        was_selected = result.get("side") == side
        _apply_row_rules(row, path)
        if was_selected and not row.get("eligible"):
            blocked_selected = True
    if blocked_selected:
        result["side"] = "观望"
        selected_rows = [r for r in scores.values() if isinstance(r, dict) and r.get("eligible")]
        if not selected_rows:
            result["why"] = "5m BOLL中轨已触发，但现有macd_improving未通过；保留机会并等待后续闭合5m重新评估"
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
    # Never mutate/destroy/reclassify the opportunity merely because the middle
    # MACD gate is temporarily false.  Only eligibility is blocked.
    return _rewrite_result(result, opportunity=opp), opp, transition


def execution_checks(plan, opportunity, score):
    """Re-check the V1.6.0 middle-MACD hard gate immediately pre-submit."""
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    blockers = list(blockers or [])
    path = _path_from(score if isinstance(score, dict) else {}, opportunity=opportunity)
    if path == MIDDLE_PATH and not _macd_improving(score or {}):
        blockers.append("V1.6.0中轨开仓禁止：5m现有macd_improving未通过")
    diag = dict(diag or {})
    diag["strategy_version"] = VERSION
    diag["boll_signal_path"] = path
    diag["middle_macd_required"] = path == MIDDLE_PATH
    diag["middle_macd_improving"] = _macd_improving(score or {}) if path == MIDDLE_PATH else None
    diag["position_multiplier"] = 2.0 if path in OUTER_PATHS else 1.0
    return not blockers, diag, blockers

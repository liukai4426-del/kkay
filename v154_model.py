"""KAYTRADE V1.5.4 execution overlay.

V1.5.4 keeps the V1.5.3 BOLL signal/scoring/risk model, but changes execution:
- a closed 5m BOLL signal is valid only during the immediately following 1 minute;
- market-entry economics use taker fee plus the configured slippage budget;
- no 1m technical confirmation is reintroduced.
"""
from __future__ import annotations

import v153_model as base

VERSION = "1.5.4"
THRESHOLD = base.THRESHOLD
ENTRY_WINDOW_MS = 60 * 1000
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

# The inherited evaluate()/new_opportunity() functions resolve this global in
# v153_model at call time. Lock it to one minute for all V1.5.4 live calls.
base.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS


def _window_state(now_ms, opportunity):
    return base._window_state(now_ms, opportunity)


def new_opportunity(hour, quarter, five, one, side, signal):
    base.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    opp = base.new_opportunity(hour, quarter, five, one, side, signal)
    opp["expires_ms"] = int(opp["signal_close_ms"]) + ENTRY_WINDOW_MS
    return opp


def _normalize_opportunity(opportunity):
    """Never inherit V1.5.3's old 4-minute expiry across an app upgrade/restart."""
    if not isinstance(opportunity, dict):
        return opportunity
    opp = dict(opportunity)
    start = int(opp.get("signal_close_ms") or opp.get("created_ms") or 0)
    if start > 0:
        opp["expires_ms"] = start + ENTRY_WINDOW_MS
    return opp


def _rewrite_text(value):
    if not isinstance(value, str):
        return value
    return (value
            .replace("V1.5.3", "V1.5.4")
            .replace("BOLL信号4分钟窗口已过期", "BOLL信号首分钟窗口已过期")
            .replace("BOLL信号/4分钟窗口", "BOLL信号/首分钟窗口")
            .replace("4分钟窗口", "首分钟窗口")
            .replace("4根1m", "仅第1根1m"))


def _rewrite_required(row):
    if not isinstance(row, dict):
        return
    confirmations = row.get("confirmations")
    if not isinstance(confirmations, dict):
        return
    required = confirmations.get("required")
    if isinstance(required, dict) and "entry_window_4x1m" in required:
        required["entry_window_1x1m"] = bool(required.pop("entry_window_4x1m"))
    confirmations["status"] = _rewrite_text(confirmations.get("status"))
    blockers = confirmations.get("blockers")
    if isinstance(blockers, list):
        confirmations["blockers"] = [_rewrite_text(x) for x in blockers]


def _rewrite_result(result):
    if not isinstance(result, dict):
        return result
    result["strategy_version"] = VERSION
    result["status"] = _rewrite_text(result.get("status"))
    result["why"] = _rewrite_text(result.get("why"))
    result["entry_window_minute"] = 1 if result.get("entry_window_minute") else 0
    for row in (result.get("scores") or {}).values():
        if isinstance(row, dict):
            row["reason"] = _rewrite_text(row.get("reason"))
            _rewrite_required(row)
    opp = result.get("opportunity")
    if isinstance(opp, dict):
        opp["expires_ms"] = int(opp.get("signal_close_ms") or 0) + ENTRY_WINDOW_MS
    return result


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    """Evaluate V1.5.3 signals using V1.5.4 market-entry execution economics."""
    base.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    opportunity = _normalize_opportunity(opportunity)
    # Market entry consumes taker fee and can slip. The inherited cost formula
    # treats the first argument as entry cost bps, so feed taker+slippage there.
    entry_cost_bps = float(taker_bps) + float(slippage_bps)
    result, opp, transition = base.evaluate(
        hour, quarter, five, one, four,
        opportunity=opportunity,
        stop_atr=stop_atr,
        maker_bps=entry_cost_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    if isinstance(opp, dict):
        opp["expires_ms"] = int(opp.get("signal_close_ms") or 0) + ENTRY_WINDOW_MS
    return _rewrite_result(result), opp, transition


def execution_checks(plan, opportunity, score):
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    blockers = [_rewrite_text(x) for x in blockers]
    return ok, diag, blockers

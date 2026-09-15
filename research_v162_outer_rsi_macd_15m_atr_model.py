"""Research-only 15m ATR exit overlay for the outer-only V1.6.2 variant.

This keeps the outer-only + RSI 30..70 + 5m MACD improvement entry model,
but changes the effective risk distance used by scoring/gates/orders from
1x 1H ATR to 1x 15m ATR. TP remains 2R, so TP distance = 2x 15m ATR.
Production V1.6.2 files are untouched.
"""
from __future__ import annotations

import v153_model as signal_core
import research_v162_outer_rsi_macd_model as base

VERSION = "1.6.2-research-outer-rsi30-70-macd-15m-atr"
THRESHOLD = base.THRESHOLD
ENTRY_WINDOW_MS = base.ENTRY_WINDOW_MS
TIME_WINDOW_ENABLED = base.TIME_WINDOW_ENABLED
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

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
_entry_ok = base._entry_ok
_window_state = base._window_state
_macd_improving = base._macd_improving
_path_from = base._path_from
boll_entry_signal = base.boll_entry_signal

_ORIGINAL_NEW_OPPORTUNITY = signal_core.new_opportunity


def _new_opportunity_15m(hour, quarter, five, one, side, signal):
    opp = _ORIGINAL_NEW_OPPORTUNITY(hour, quarter, five, one, side, signal)
    original_1h = float(opp.get("atr1h") or 0.0)
    atr15 = float(opp.get("atr15") or 0.0)
    opp["atr1h_original"] = original_1h
    opp["effective_stop_atr"] = atr15
    opp["effective_stop_atr_timeframe"] = "15m"
    # v153 evaluation and the audited submit path both consume opp['atr1h']
    # as the stop-distance basis. Replace that effective field only inside this
    # isolated research process so every R-based gate uses the same 15m ATR.
    opp["atr1h"] = atr15
    detail = dict(opp.get("trigger_detail") or {})
    detail["effective_stop_atr_timeframe"] = "15m"
    detail["effective_stop_atr"] = atr15
    detail["original_1h_atr"] = original_1h
    opp["trigger_detail"] = detail
    return opp


def _install_signal_patch():
    base._install_signal_patch()
    signal_core.new_opportunity = _new_opportunity_15m


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    _install_signal_patch()
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
    if isinstance(result, dict):
        result["strategy_version"] = VERSION
        result["effective_stop_atr_timeframe"] = "15m"
    return result, opp, transition


def execution_checks(plan, opportunity, score):
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    diag = dict(diag or {})
    diag["strategy_version"] = VERSION
    diag["effective_stop_atr_timeframe"] = "15m"
    diag["effective_stop_atr"] = float((opportunity or {}).get("atr15") or 0.0)
    return ok, diag, blockers

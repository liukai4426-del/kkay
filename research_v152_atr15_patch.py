"""Research-only V1.5.2 stop model override.

This does NOT change production V1.5.2. It changes only the ATR source used by
V1.5.2's R-based checks and execution plan for this backtest:
- stop distance = 1.5 x 15m ATR
- target distance = 3.0 x 15m ATR = 2R

The shared model stores the execution ATR on the opportunity as ``atr1h`` in
production. For this isolated research branch we replace that execution field
with the already-frozen 15m ATR when each opportunity is created. This means
front-space R, cost R, stop-buffer checks and actual order stop/target all use
the same 15m ATR definition.
"""
from __future__ import annotations

import v152_model as model

_original_new_opportunity = model.new_opportunity
_applied = False


def _new_opportunity_atr15(hour, quarter, one, side):
    opp = _original_new_opportunity(hour, quarter, one, side)
    opp["atr1h_original"] = float(opp["atr1h"])
    opp["atr1h"] = float(opp["atr15"])
    opp["execution_atr_timeframe"] = "15m"
    return opp


def apply():
    global _applied
    if _applied:
        return
    model.new_opportunity = _new_opportunity_atr15
    _applied = True


apply()

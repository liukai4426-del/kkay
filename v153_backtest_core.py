"""V1.5.3 backtest execution core.

Execution mechanics are inherited from the audited V1.5.2 core, while all
strategy decisions delegate to the exact V1.5.3 BOLL model used by live runtime.
"""
from __future__ import annotations

import v152_backtest_core as _base
import v153_model as model

# Keep inherited helpers that internally reference `_base.model` aligned with V1.5.3.
_base.model = model

ONE_MINUTE_MS = _base.ONE_MINUTE_MS
LOSS_PAUSE_MS = _base.LOSS_PAUSE_MS
COOLDOWN_MS = _base.COOLDOWN_MS
validate_candle_continuity = _base.validate_candle_continuity
validate_funding_coverage = _base.validate_funding_coverage
RiskState = _base.RiskState
PendingEntry = _base.PendingEntry
Position = _base.Position
limit_touched = _base.limit_touched
fill_entry = _base.fill_entry
exit_on_bar = _base.exit_on_bar
apply_funding = _base.apply_funding


def evaluate_signal(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                    maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                    allow_new=True, now_ms=None):
    """Backtest decision uses the same V1.5.3 model as live execution."""
    return model.evaluate(
        hour, quarter, five, one, four,
        opportunity=opportunity,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        allow_new=allow_new,
        now_ms=now_ms,
    )

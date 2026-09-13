"""KAYTRADE V1.5.4 repaired LIMIT-entry backtest core.

Execution mechanics stay on the audited LIMIT-entry V1.5.2 core, while signal,
score, hard-gate and one-minute opportunity timing delegate to the exact
production V1.5.4 model. This mirrors the repaired live build where V1.5.4
keeps the 60-second BOLL window but restores passive LIMIT entry.
"""
from __future__ import annotations

import v152_backtest_core as _base
import v154_model as model

# Helpers in v152_backtest_core reference the module-global model for execution
# checks. Keep them aligned with the exact V1.5.4 production decision model.
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

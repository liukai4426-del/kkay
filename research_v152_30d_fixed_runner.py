"""Run V1.5.2 on the exact V1.5.1 30-day comparison window.

Research variant only:
- stop = 1.5 x 15m ATR
- full take-profit = 3.0 x 15m ATR = 2R
- estimated fee+slippage hard gate = 0.40R
All entry logic, score threshold, front-space gate and risk controls otherwise
remain V1.5.2-equivalent. The ATR research patch also makes all R-based checks
use the same 15m ATR stop distance.
"""
from datetime import datetime, timezone

import research_v152_cache_patch as cache_patch
import research_v152_30d_backtest as r
import research_v152_data_cache as data_cache
import research_v152_atr15_patch  # noqa: F401

END = datetime(2026, 9, 13, 3, 16, tzinfo=timezone.utc)
START = datetime(2026, 8, 14, 3, 16, tzinfo=timezone.utc)

r.END = END
r.START = START
r.END_MS = int(END.timestamp() * 1000)
r.START_MS = int(START.timestamp() * 1000)
r.STOP_ATR = 1.5
r.REWARD_R = 2.0
r.model.COST_MAX_R = 0.40

for mod in (r.base,):
    mod.END = r.END
    mod.START = r.START
    mod.END_MS = r.END_MS
    mod.START_MS = r.START_MS
    mod.STOP_ATR = r.STOP_ATR
    mod.REWARD_R = r.REWARD_R

# Correct report metadata without changing simulation behavior.
_original_metrics = r.metrics

def _metrics_atr15(*args, **kwargs):
    m = _original_metrics(*args, **kwargs)
    m['stop_atr_timeframe'] = '15m'
    m['stop_atr_multiplier'] = 1.5
    m['target_atr_multiplier'] = 3.0
    m['reward_r'] = 2.0
    m['cost_max_r'] = 0.40
    m['research_variant'] = 'V1.5.2 / 15m ATR 1.5x SL / 3.0x ATR TP / cost gate 0.40R'
    return m

r.metrics = _metrics_atr15

# Install after fixed timestamps are applied so cache keys are deterministic.
data_cache.install(r.base, cache_patch=cache_patch)

if __name__ == '__main__':
    r.main()
    print('COMPUTE_CACHE_STATS', cache_patch.stats(), flush=True)
    print('MARKET_CACHE_STATS', data_cache.stats(), flush=True)

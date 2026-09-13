"""Run V1.5.2 on the exact V1.5.1 30-day comparison window.

The strategy/execution model is unchanged. Acceleration layers only reuse raw
public market data and deterministic calculations.
"""
from datetime import datetime, timezone

import research_v152_cache_patch as cache_patch
import research_v152_30d_backtest as r
import research_v152_data_cache as data_cache

END = datetime(2026, 9, 13, 3, 16, tzinfo=timezone.utc)
START = datetime(2026, 8, 14, 3, 16, tzinfo=timezone.utc)

r.END = END
r.START = START
r.END_MS = int(END.timestamp() * 1000)
r.START_MS = int(START.timestamp() * 1000)

for mod in (r.base,):
    mod.END = r.END
    mod.START = r.START
    mod.END_MS = r.END_MS
    mod.START_MS = r.START_MS

# Install after fixed timestamps are applied so cache keys are deterministic.
data_cache.install(r.base, cache_patch=cache_patch)

if __name__ == '__main__':
    r.main()
    print('COMPUTE_CACHE_STATS', cache_patch.stats(), flush=True)
    print('MARKET_CACHE_STATS', data_cache.stats(), flush=True)

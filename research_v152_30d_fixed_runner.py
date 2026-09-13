"""Run the V1.5.2 30-day backtest on the exact V1.5.1 comparison window.

Window: 2026-08-14 03:16 UTC -> 2026-09-13 03:16 UTC.
The strategy and execution model remain unchanged; research_v152_cache_patch only
memoizes pure deterministic calculations for identical candle inputs.
"""
from datetime import datetime, timezone

import research_v152_cache_patch as cache_patch  # noqa: F401
import research_v152_30d_backtest as r

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

if __name__ == '__main__':
    r.main()
    print('CACHE_STATS', cache_patch.stats(), flush=True)

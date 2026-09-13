"""Run V1.5.2 research ATR15 profile on a fixed 90-day window.

Research-only profile:
- score threshold = 6.0
- stop = 1.5 x 15m ATR
- full take-profit = 3.0 x 15m ATR = 2R
- cost gate <= 0.30R
All other V1.5.2 entry, hard-gate, risk and execution rules remain unchanged.
"""
from datetime import datetime, timezone
from pathlib import Path
import json

import research_v152_cache_patch as cache_patch
import research_v152_30d_backtest as r
import research_v152_data_cache as data_cache
import research_v152_atr15_patch  # noqa: F401

END = datetime(2026, 9, 13, 3, 16, tzinfo=timezone.utc)
START = datetime(2026, 6, 15, 3, 16, tzinfo=timezone.utc)

r.END = END
r.START = START
r.END_MS = int(END.timestamp() * 1000)
r.START_MS = int(START.timestamp() * 1000)
r.STOP_ATR = 1.5
r.REWARD_R = 2.0
r.model.THRESHOLD = 6.0
r.OUTDIR = Path('backtest_output_v152_btc_90d')
r.OUTDIR.mkdir(parents=True, exist_ok=True)

for mod in (r.base,):
    mod.END = r.END
    mod.START = r.START
    mod.END_MS = r.END_MS
    mod.START_MS = r.START_MS
    mod.STOP_ATR = r.STOP_ATR
    mod.REWARD_R = r.REWARD_R

_original_metrics = r.metrics

def _metrics_atr15_90d(*args, **kwargs):
    m = _original_metrics(*args, **kwargs)
    m['days'] = 90
    m['entry_threshold'] = 6.0
    m['stop_atr_timeframe'] = '15m'
    m['stop_atr_multiplier'] = 1.5
    m['target_atr_multiplier'] = 3.0
    m['reward_r'] = 2.0
    m['research_variant'] = 'V1.5.2 / score>=6.0 / 15m ATR 1.5x SL / 3.0x ATR TP / cost<=0.30R / 90D'
    return m

r.metrics = _metrics_atr15_90d

data_cache.install(r.base, cache_patch=cache_patch)

if __name__ == '__main__':
    r.main()
    out = r.OUTDIR
    m = json.loads((out/'metrics.json').read_text(encoding='utf-8'))
    summary = [
        '# KAYTRADE V1.5.2 ATR15 — 90D Research Summary',
        '',
        f"Window: {m['start_utc']} -> {m['end_utc']}",
        'Profile: score>=6.0; 15m ATR x1.5 SL; 15m ATR x3.0 full TP (2R); cost<=0.30R; front space>=1.5R; 3 losses => 1h pause.',
        '',
        f"Trades: {m['trades']}",
        f"Net PnL: {m['net_pnl']:+.6f} USDT",
        f"Return: {m['net_return_pct']:+.6f}%",
        f"Win rate: {m['win_rate_pct']:.6f}%",
        f"Profit factor: {m['profit_factor']}",
        f"Expectancy: {m['expectancy']:+.6f} USDT/trade",
        f"Max drawdown: {m['max_drawdown_usdt']:.6f} USDT / {m['max_drawdown_pct']:.6f}%",
        f"Fees: {m['fees']:.6f} USDT",
        f"Funding: {m['funding_pnl']:+.6f} USDT",
        f"Stress return: {m['stress_return_pct']:+.6f}%",
        f"Stress net PnL: {m['stress_net_pnl']:+.6f} USDT",
        '',
        'By side:',
        json.dumps(m['by_side'], ensure_ascii=False, indent=2),
        '',
        'By score:',
        json.dumps(m['by_score'], ensure_ascii=False, indent=2),
        '',
        'Funnel:',
        json.dumps(m['funnel'], ensure_ascii=False, indent=2),
        '',
        'Execution blockers:',
        json.dumps(m['execution_blockers'], ensure_ascii=False, indent=2),
    ]
    (out/'V152_ATR15_90D_Summary.md').write_text('\n'.join(summary), encoding='utf-8')
    print('COMPUTE_CACHE_STATS', cache_patch.stats(), flush=True)
    print('MARKET_CACHE_STATS', data_cache.stats(), flush=True)

#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import research_v152_30d_backtest as r
import research_v152_atr15_patch  # noqa: F401
import research_v152_cache_patch as cache_patch
import research_v152_data_cache as data_cache
import v152_model as model
import v152_backtest_core as bt

START = datetime(2026, 8, 14, 3, 16, tzinfo=timezone.utc)
END = datetime(2026, 9, 13, 3, 16, tzinfo=timezone.utc)
OUT = Path('backtest_output_v152_costgate_selective')
OUT.mkdir(parents=True, exist_ok=True)

r.START = START
r.END = END
r.START_MS = int(START.timestamp() * 1000)
r.END_MS = int(END.timestamp() * 1000)
r.STOP_ATR = 1.5
r.REWARD_R = 2.0
for mod in (r.base,):
    mod.START = r.START
    mod.END = r.END
    mod.START_MS = r.START_MS
    mod.END_MS = r.END_MS
    mod.STOP_ATR = r.STOP_ATR
    mod.REWARD_R = r.REWARD_R

data_cache.install(r.base, cache_patch=cache_patch)

SCENARIOS = {
    'baseline_030': {'kind': 'fixed', 'gate': 0.30},
    'global_035': {'kind': 'fixed', 'gate': 0.35},
    'long040_short030': {'kind': 'side', 'long': 0.40, 'short': 0.30},
    'long030_short040': {'kind': 'side', 'long': 0.30, 'short': 0.40},
    'score7_040_else030': {'kind': 'score7', 'gate7': 0.40, 'other': 0.30},
    'long_score7_040_else030': {'kind': 'long_score7', 'special': 0.40, 'other': 0.30},
}

CURRENT = {'name': 'baseline_030'}
ORIG_EXEC = model.execution_checks
CANDIDATES = {}


def gate_for(side: str, score: float) -> float:
    cfg = SCENARIOS[CURRENT['name']]
    if cfg['kind'] == 'fixed':
        return float(cfg['gate'])
    if cfg['kind'] == 'side':
        return float(cfg['long'] if side == '做多' else cfg['short'])
    if cfg['kind'] == 'score7':
        return float(cfg['gate7'] if abs(score - 7.0) < 1e-9 else cfg['other'])
    if cfg['kind'] == 'long_score7':
        return float(cfg['special'] if side == '做多' and abs(score - 7.0) < 1e-9 else cfg['other'])
    raise KeyError(cfg['kind'])


def selective_execution_checks(plan, opportunity, score):
    # Use 0.40R only as the diagnostic ceiling. Scenario-specific gating is
    # reapplied below, so all other production execution checks stay intact.
    old = model.COST_MAX_R
    model.COST_MAX_R = 0.40
    try:
        _ok, diag, blockers = ORIG_EXEC(plan, opportunity, score)
    finally:
        model.COST_MAX_R = old

    non_cost = [b for b in blockers if not str(b).startswith('实际预计成本')]
    side = str((opportunity or {}).get('side') or '')
    total = float((score or {}).get('total') or 0.0)
    cost_r = float(diag.get('cost_r', math.inf))
    threshold = gate_for(side, total)
    out = list(non_cost)
    if cost_r > threshold:
        out.append(f'实际预计成本 {cost_r:.2f}R > {threshold:.2f}R')

    # Capture unique 0.30R-0.40R opportunities during baseline path. These are
    # diagnostic records only and do not alter portfolio state.
    if CURRENT['name'] == 'baseline_030' and not non_cost and 0.30 < cost_r <= 0.40:
        oid = str((opportunity or {}).get('id') or '')
        key = f'{oid}|{total:.1f}'
        rec = CANDIDATES.setdefault(key, {
            'opportunity_id': oid,
            'side': side,
            'score': total,
            'created_ms': int((opportunity or {}).get('created_ms') or 0),
            'trigger': dict(((score or {}).get('confirmations') or {}).get('trigger') or {}),
            'layers': dict((score or {}).get('layers') or {}),
            'attempts': 0,
            'min_cost_r': cost_r,
            'max_cost_r': cost_r,
        })
        rec['attempts'] += 1
        rec['min_cost_r'] = min(float(rec['min_cost_r']), cost_r)
        rec['max_cost_r'] = max(float(rec['max_cost_r']), cost_r)

    diag = dict(diag)
    diag['scenario_cost_gate_r'] = threshold
    return not out, diag, out


model.execution_checks = selective_execution_checks
# Let evaluate expose otherwise-valid candidates up to 0.40R; the exact
# scenario threshold is enforced at execution_checks above.
model.COST_MAX_R = 0.40


def slim(m):
    return {
        'net_pnl': m['net_pnl'],
        'net_return_pct': m['net_return_pct'],
        'trades': m['trades'],
        'win_rate_pct': m['win_rate_pct'],
        'profit_factor': m['profit_factor'],
        'expectancy': m['expectancy'],
        'max_drawdown_usdt': m['max_drawdown_usdt'],
        'max_drawdown_pct': m['max_drawdown_pct'],
        'fees': m['fees'],
        'by_side': m['by_side'],
        'by_score': m['by_score'],
        'funnel': m['funnel'],
        'execution_blockers': m['execution_blockers'],
    }


def candidate_summary():
    rows = list(CANDIDATES.values())
    by_side = defaultdict(lambda: {'signals': 0, 'attempts': 0})
    by_score = defaultdict(lambda: {'signals': 0, 'attempts': 0})
    by_band = defaultdict(lambda: {'signals': 0, 'attempts': 0})
    for x in rows:
        by_side[x['side']]['signals'] += 1
        by_side[x['side']]['attempts'] += x['attempts']
        sk = f"{x['score']:.1f}"
        by_score[sk]['signals'] += 1
        by_score[sk]['attempts'] += x['attempts']
        c = x['min_cost_r']
        if c <= 0.32: band = '0.30-0.32'
        elif c <= 0.34: band = '0.32-0.34'
        elif c <= 0.36: band = '0.34-0.36'
        elif c <= 0.38: band = '0.36-0.38'
        else: band = '0.38-0.40'
        by_band[band]['signals'] += 1
        by_band[band]['attempts'] += x['attempts']
    return {
        'unique_signal_score_pairs': len(rows),
        'total_block_attempts_in_030_040_band': sum(x['attempts'] for x in rows),
        'by_side': dict(by_side),
        'by_score': dict(sorted(by_score.items())),
        'by_min_cost_band': dict(by_band),
        'records': rows,
    }


def main():
    print('Loading fixed-window market data once...')
    meta = r.base.fetch_instrument()
    data = {tf: r.base.fetch_candles(tf) for tf in ('1m', '5m', '15m', '1H', '4H')}
    funding = r.base.fetch_funding()
    for tf, rows in data.items():
        bt.validate_candle_continuity(rows, r.base.BAR_MS[tf], tf)
    bt.validate_funding_coverage([{'t': t} for t, _ in funding], r.START_MS, r.END_MS)
    ts = {tf: [x['t'] for x in rows] for tf, rows in data.items()}

    results = {}
    for name in SCENARIOS:
        CURRENT['name'] = name
        sim, m = r.run(data, ts, meta, funding, 0.0)
        results[name] = slim(m)
        print('SCENARIO', name, json.dumps({
            'trades': m['trades'], 'net_pnl': m['net_pnl'], 'wr': m['win_rate_pct'],
            'pf': m['profit_factor'], 'dd': m['max_drawdown_pct']}, ensure_ascii=False), flush=True)

    b = results['baseline_030']
    assert b['trades'] == 15, b
    assert abs(b['net_pnl'] - 23.371736868934022) < 1e-6, b
    assert abs(b['profit_factor'] - 1.3700249170316623) < 1e-9, b

    csum = candidate_summary()
    payload = {
        'window': {'start_utc': START.isoformat(), 'end_utc': END.isoformat()},
        'stop': '1.5 x 15m ATR', 'target': '3.0 x 15m ATR = 2R',
        'scenarios': results, 'blocked_030_040_diagnostics': csum,
        'compute_cache_stats': cache_patch.stats(), 'market_cache_stats': data_cache.stats(),
    }
    (OUT / 'costgate_selective_results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    lines = [
        '# V1.5.2 selective cost-gate diagnostic — 30D', '',
        'Common setup: 15m ATR ×1.5 stop, 15m ATR ×3 TP (=2R), threshold 6.0, front space ≥1.5R.', '',
        '| Scenario | Trades | Net PnL | Win rate | PF | Expectancy | Max DD | Fees |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for name, m in results.items():
        lines.append(f"| {name} | {m['trades']} | {m['net_pnl']:+.4f} | {m['win_rate_pct']:.2f}% | {m['profit_factor']:.3f} | {m['expectancy']:+.4f} | {m['max_drawdown_pct']:.3f}% | {m['fees']:.4f} |")
    lines += ['', '## Blocked 0.30R–0.40R diagnostics', '',
              f"Unique opportunity/score pairs: {csum['unique_signal_score_pairs']}",
              f"Repeated execution-block attempts represented: {csum['total_block_attempts_in_030_040_band']}", '',
              '### By side', '```json', json.dumps(csum['by_side'], ensure_ascii=False, indent=2), '```', '',
              '### By score', '```json', json.dumps(csum['by_score'], ensure_ascii=False, indent=2), '```', '',
              '### By minimum cost-R band', '```json', json.dumps(csum['by_min_cost_band'], ensure_ascii=False, indent=2), '```', '',
              'Path-dependent scenario results are the primary decision evidence; candidate counts alone are diagnostic only.']
    (OUT / 'CostGate_Selective_Report.md').write_text('\n'.join(lines), encoding='utf-8')
    print('FINAL_RESULTS')
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

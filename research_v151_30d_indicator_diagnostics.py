#!/usr/bin/env python3
from __future__ import annotations

import bisect
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import research_v146_backtest as base
import research_v138_final_3m_backtest as oldfinal
import research_v151_30d_backtest as v151

START = datetime(2026, 8, 14, 3, 16, tzinfo=timezone.utc)
END = datetime(2026, 9, 13, 3, 16, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path('backtest_output_v151_30d_indicator_diagnostics')
OUTDIR.mkdir(parents=True, exist_ok=True)

for mod in (base, oldfinal, v151):
    mod.START = START
    mod.END = END
    mod.START_MS = START_MS
    mod.END_MS = END_MS
base.OUTDIR = OUTDIR
oldfinal.OUTDIR = OUTDIR
v151.OUTDIR = OUTDIR
oldfinal.LOSS_PAUSE_MS = 60 * 60 * 1000


def _pf(values):
    gp = sum(x for x in values if x > 0)
    gl = -sum(x for x in values if x < 0)
    return gp / gl if gl > 0 else math.inf


def _stats(values):
    n = len(values)
    wins = sum(1 for x in values if x > 0)
    return {
        'n': n,
        'wins': wins,
        'win_rate_pct': wins / n * 100 if n else 0.0,
        'net_pnl': sum(values),
        'expectancy': sum(values) / n if n else 0.0,
        'profit_factor': _pf(values),
    }


class DiagnosticSimulator(v151.V151Simulator):
    def __init__(self, market, meta, funding):
        super().__init__(market, meta, funding)
        self.entry_features = {}

    def _setup_detail(self, sig, side):
        buy = side == '做多'
        i15 = sig['idx']['15m']
        data = self.m.data['15m']
        inds = self.m.inds['15m']
        c = data[i15]
        cur = inds[i15]
        boll_touch = c['l'] <= cur['lower'] if buy else c['h'] >= cur['upper']
        vols = [float(data[j]['v']) for j in range(max(0, i15 - 20), i15)]
        avg = sum(vols) / len(vols) if vols else 0.0
        ratio = float(c['v']) / avg if avg > 0 else 0.0
        returned = (c['l'] <= cur['lower'] and c['c'] > cur['lower']) if buy else (c['h'] >= cur['upper'] and c['c'] < cur['upper'])
        volume_boll = bool(returned and ratio >= 1.3)
        kdj = bool((cur['j'] <= 30 and cur['cross_up']) if buy else (cur['j'] >= 70 and cur['cross_down']))
        reversal = bool(base.reversal(data, i15, buy))
        return {
            'setup_boll_touch': bool(boll_touch),
            'setup_volume_boll': volume_boll,
            'setup_kdj': kdj,
            'setup_reversal': reversal,
        }

    def submit(self, sig, i1, price):
        before = len(self.legs)
        super().submit(sig, i1, price)
        if len(self.legs) <= before:
            return
        leg = self.legs[-1]
        side = sig['side']
        row = sig['scores'][side]
        comp = row['components']
        feat = {
            'side': side,
            'score': float(row['total']),
            'trigger_score': float(comp['trigger']),
            'trigger_ema_reclaim': bool(row['trigger_detail'].get('ema_reclaim')),
            'trigger_kdj': bool(row['trigger_detail'].get('kdj')),
            'trigger_reversal': bool(row['trigger_detail'].get('reversal')),
            'fav15': bool(comp['fav15'] > 0),
            'setup_score': float(comp['setup']),
            'setup_any': bool(comp['setup'] > 0),
            'macd5': bool(comp['macd5'] > 0),
            'boll5': bool(comp['boll5'] > 0),
            'macd15': bool(comp['macd15'] > 0),
            'boll15': bool(comp['boll15'] > 0),
            'trend1h_aligned': bool(comp['trend1h'] > 0),
            'trend1h_neutral': row['hstate'] == 'neutral',
            'trend4h_aligned': bool(comp['trend4h'] > 0),
            'trend4h_opposite': bool(comp['trend4h'] < 0),
            'rsi_penalty': bool(comp['rsi'] < 0),
            'rsi_penalty_value': float(comp['rsi']),
            'struct1h_adverse': bool(comp['struct1h'] < 0),
            'front_r': float(row['front_r']),
            'atr1h': float(row['atr1h']),
            'atr1h_pct': float(row['atr1h']) / float(sig['price']) * 100.0,
        }
        feat.update(self._setup_detail(sig, side))
        self.entry_features[leg.leg_id] = feat


def _front_bucket(x):
    if math.isinf(x):
        return 'no_front_structure'
    if x < 2.0:
        return '1.3-2R'
    if x < 3.0:
        return '2-3R'
    return '>=3R'


def main():
    meta = base.fetch_instrument()
    print('Instrument:', meta)
    data = {tf: base.fetch_candles(tf) for tf in ('1m', '5m', '15m', '1H', '4H')}
    funding = base.fetch_funding()
    inds = {tf: base.compute_indicators(rows) for tf, rows in data.items()}
    ts = {tf: [r['t'] for r in rows] for tf, rows in data.items()}
    zones = {
        '15m': base.ZoneCache(data['15m'], inds['15m'], 160),
        '1H': base.ZoneCache(data['1H'], inds['1H'], 120),
        '4H': base.ZoneCache(data['4H'], inds['4H'], 180),
    }
    market = v151.V151Market(data, inds, ts, zones)
    sim = DiagnosticSimulator(market, meta, funding)
    d1 = data['1m']
    start_i = bisect.bisect_left(ts['1m'], START_MS)
    for i in range(start_i, len(d1)):
        bar = d1[i]
        if bar['t'] >= END_MS:
            break
        bar_close = bar['t'] + 60_000
        sim.apply_funding_until(bar['t'], bar['o'])
        sim.fill_pending(bar)
        sim.process_exits(bar)
        sim.apply_funding_until(bar_close, bar['c'])
        sim.record_equity(bar_close, bar['c'])
        sig = market.score(i)
        sim.submit(sig, i, bar['c'])
    last = [r for r in d1 if r['t'] < END_MS][-1]
    sim.finish(last)

    metrics, leg_net, cycles, closed = base.analyze(sim, d1, funding, meta)
    rows = []
    by_feature = defaultdict(lambda: {'on': [], 'off': []})
    bool_features = [
        'trigger_ema_reclaim', 'trigger_kdj', 'trigger_reversal', 'fav15', 'setup_any',
        'setup_boll_touch', 'setup_volume_boll', 'setup_kdj', 'setup_reversal',
        'macd5', 'boll5', 'macd15', 'boll15', 'trend1h_aligned', 'trend1h_neutral',
        'trend4h_aligned', 'trend4h_opposite', 'rsi_penalty', 'struct1h_adverse',
    ]
    score_groups = defaultdict(list)
    trigger_score_groups = defaultdict(list)
    setup_score_groups = defaultdict(list)
    front_groups = defaultdict(list)
    vol_records = []

    for leg in closed:
        feat = sim.entry_features.get(leg.leg_id)
        if not feat:
            continue
        pnl = leg_net(leg)
        rec = {'leg_id': leg.leg_id, 'net_pnl': pnl, **feat}
        rows.append(rec)
        for f in bool_features:
            by_feature[f]['on' if feat[f] else 'off'].append(pnl)
        score_groups[f"{feat['score']:.1f}"].append(pnl)
        trigger_score_groups[f"{feat['trigger_score']:.1f}"].append(pnl)
        setup_score_groups[f"{feat['setup_score']:.1f}"].append(pnl)
        front_groups[_front_bucket(feat['front_r'])].append(pnl)
        vol_records.append((feat['atr1h_pct'], pnl))

    vol_records.sort(key=lambda x: x[0])
    thirds = []
    if vol_records:
        n = len(vol_records)
        cut1 = vol_records[max(0, n // 3 - 1)][0]
        cut2 = vol_records[max(0, 2 * n // 3 - 1)][0]
        buckets = defaultdict(list)
        for atrpct, pnl in vol_records:
            label = 'low' if atrpct <= cut1 else ('mid' if atrpct <= cut2 else 'high')
            buckets[label].append(pnl)
        thirds = {'cut1_pct': cut1, 'cut2_pct': cut2, 'groups': {k: _stats(v) for k, v in buckets.items()}}

    feature_summary = {
        f: {'on': _stats(v['on']), 'off': _stats(v['off'])}
        for f, v in by_feature.items()
    }
    result = {
        'period_start': START.isoformat(),
        'period_end': END.isoformat(),
        'baseline': {
            'trades': len(closed), 'net_pnl': metrics['net_pnl'],
            'win_rate_pct': metrics['cycle_win_rate_pct'],
            'profit_factor': metrics['profit_factor'],
            'max_drawdown_pct': metrics['max_drawdown_pct'],
        },
        'feature_summary': feature_summary,
        'score_groups': {k: _stats(v) for k, v in sorted(score_groups.items(), key=lambda kv: float(kv[0]))},
        'trigger_score_groups': {k: _stats(v) for k, v in sorted(trigger_score_groups.items(), key=lambda kv: float(kv[0]))},
        'setup_score_groups': {k: _stats(v) for k, v in sorted(setup_score_groups.items(), key=lambda kv: float(kv[0]))},
        'front_space_groups': {k: _stats(v) for k, v in front_groups.items()},
        'atr1h_volatility_terciles': thirds,
        'hard_gate_primary_rejections': market.gate_stats,
        'risk_filters': {
            'loss_pause_triggers': sim.stats['loss_pause_triggers'],
            'loss_pause_blocked_signals': sim.stats['loss_pause_blocked_signals'],
            'cooldown_blocks': sim.stats['cooldown_blocks'],
            'active_position_blocks': sim.stats['active_position_blocks'],
        },
    }
    (OUTDIR / 'indicator_diagnostics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    fieldnames = ['leg_id', 'side', 'score', 'net_pnl', 'trigger_score', 'trigger_ema_reclaim', 'trigger_kdj', 'trigger_reversal',
                  'fav15', 'setup_score', 'setup_any', 'setup_boll_touch', 'setup_volume_boll', 'setup_kdj', 'setup_reversal',
                  'macd5', 'boll5', 'macd15', 'boll15', 'trend1h_aligned', 'trend1h_neutral', 'trend4h_aligned', 'trend4h_opposite',
                  'rsi_penalty', 'rsi_penalty_value', 'struct1h_adverse', 'front_r', 'atr1h', 'atr1h_pct']
    with (OUTDIR / 'V151_BTC_30D_Indicator_Trades.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k) for k in fieldnames})

    lines = ['# V1.5.1 30天指标诊断', '',
             f"基线：{len(closed)}笔，净PnL {metrics['net_pnl']:+.2f}U，胜率 {metrics['cycle_win_rate_pct']:.2f}%，PF {metrics['profit_factor']:.3f}。", '',
             '## 指标出现/未出现对照', '',
             '| 指标 | 出现笔数 | 出现胜率 | 出现PnL | 出现PF | 未出现笔数 | 未出现胜率 | 未出现PnL | 未出现PF |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for f in bool_features:
        a, b = feature_summary[f]['on'], feature_summary[f]['off']
        lines.append(f"| {f} | {a['n']} | {a['win_rate_pct']:.2f}% | {a['net_pnl']:+.2f} | {a['profit_factor']:.3f} | {b['n']} | {b['win_rate_pct']:.2f}% | {b['net_pnl']:+.2f} | {b['profit_factor']:.3f} |")
    lines += ['', '## Hard Gate主拒绝原因', '']
    for k, val in market.gate_stats.items():
        lines.append(f'- {k}: {val}')
    (OUTDIR / 'V151_BTC_30D_Indicator_Diagnostics.md').write_text('\n'.join(lines), encoding='utf-8')

    print('INDICATOR_DIAGNOSTICS')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('OUTPUT_DIR', OUTDIR.resolve())


if __name__ == '__main__':
    main()

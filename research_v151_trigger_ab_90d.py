#!/usr/bin/env python3
from __future__ import annotations

import bisect
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v146_backtest as base
import research_v138_final_3m_backtest as oldfinal
import research_v151_30d_backtest as v151

END = datetime(2026, 9, 13, 3, 16, tzinfo=timezone.utc)
START = END - timedelta(days=90)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path('backtest_output_v151_trigger_ab_90d')
OUTDIR.mkdir(parents=True, exist_ok=True)

for mod in (base, oldfinal, v151):
    mod.START = START
    mod.END = END
    mod.START_MS = START_MS
    mod.END_MS = END_MS
    mod.CAPITAL = 10_000.0
    mod.LEVERAGE = 5
    mod.RISK_USDT = 100.0
    mod.RISK_PCT = 1.0
    mod.DAILY_LOSS = 300.0
    mod.CONSECUTIVE_LOSSES = 3
    mod.COOLDOWN_MINUTES = 30
    mod.STOP_ATR = 1.0
    mod.REWARD_R = 2.0
    mod.TIER_CAP = {1: 1000.0, 2: 1000.0, 3: 1000.0}
    mod.OUTDIR = OUTDIR
v151.POSITION_CAP = 1000.0
v151.THRESHOLD = 6.0
oldfinal.LOSS_PAUSE_MS = 60 * 60 * 1000


class DualTriggerMarket(v151.V151Market):
    """A/B variant: require trigger score == 1.0 (at least two trigger sources)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dual_trigger_rejections = 0

    def score(self, i1):
        sig = super().score(i1)
        scores = sig.get('scores') or {}
        for side in ('做多', '做空'):
            row = scores.get(side)
            if not row:
                continue
            trig = float((row.get('components') or {}).get('trigger', 0.0) or 0.0)
            if row.get('eligible') and trig < 1.0:
                self.dual_trigger_rejections += 1
                row['eligible'] = False
                row['dual_trigger_gate'] = False
            else:
                row['dual_trigger_gate'] = trig >= 1.0

        qualified = [s for s in ('做多', '做空') if scores.get(s, {}).get('eligible')]
        if len(qualified) == 1:
            sig['side'] = qualified[0]
        elif len(qualified) == 2:
            a, b = scores[qualified[0]]['total'], scores[qualified[1]]['total']
            sig['side'] = '观望' if abs(a - b) <= 1e-9 else max(qualified, key=lambda s: scores[s]['total'])
        else:
            sig['side'] = '观望'
        return sig


def make_market(cls, data, inds, ts):
    zones = {
        '15m': base.ZoneCache(data['15m'], inds['15m'], 160),
        '1H': base.ZoneCache(data['1H'], inds['1H'], 120),
        '4H': base.ZoneCache(data['4H'], inds['4H'], 180),
    }
    return cls(data, inds, ts, zones)


def run_variant(name, market_cls, data, inds, ts, funding, meta):
    market = make_market(market_cls, data, inds, ts)
    sim = v151.V151Simulator(market, meta, funding)
    d1 = data['1m']
    start_i = bisect.bisect_left(ts['1m'], START_MS)
    if start_i < 1:
        raise RuntimeError('not enough 1m warmup')

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
        if (i - start_i) % 30000 == 0:
            print(f'{name} progress {i-start_i:,}, equity={sim.mark_equity(bar["c"]):.2f}, cycles={len(sim.cycles)}', flush=True)

    last = [r for r in d1 if r['t'] < END_MS][-1]
    sim.finish(last)
    metrics, leg_net, cycles, closed = base.analyze(sim, d1, funding, meta)
    summary = {
        'ending_equity': metrics['ending_equity'],
        'net_pnl': metrics['net_pnl'],
        'net_return_pct': metrics['net_return_pct'],
        'trades': metrics['cycles'],
        'win_rate_pct': metrics['cycle_win_rate_pct'],
        'profit_factor': metrics['profit_factor'],
        'expectancy_per_trade': metrics['expectancy_cycle'],
        'avg_win': metrics['avg_win_cycle'],
        'avg_loss': metrics['avg_loss_cycle'],
        'payoff_ratio': metrics['payoff_ratio'],
        'max_drawdown_usdt': metrics['max_drawdown_usdt'],
        'max_drawdown_pct': metrics['max_drawdown_pct'],
        'fees': metrics['fees'],
        'funding_pnl': metrics['funding_pnl'],
        'avg_hold_min': metrics['avg_hold_min'],
        'median_hold_min': metrics['median_hold_min'],
        'by_side': metrics.get('by_side', {}),
        'by_reason': metrics.get('by_reason', {}),
        'loss_pause_triggers': sim.stats.get('loss_pause_triggers', 0),
        'loss_pause_blocked_signals': sim.stats.get('loss_pause_blocked_signals', 0),
        'cooldown_blocks': sim.stats.get('cooldown_blocks', 0),
        'active_position_blocks': sim.stats.get('active_position_blocks', 0),
        'gate_diagnostics': market.gate_stats,
        'dual_trigger_rejections': getattr(market, 'dual_trigger_rejections', 0),
    }
    return summary


def fmt(x):
    if isinstance(x, float) and math.isinf(x):
        return '∞'
    return f'{x:.3f}' if isinstance(x, float) else str(x)


def main():
    meta = base.fetch_instrument()
    print('Instrument:', meta)
    data = {tf: base.fetch_candles(tf) for tf in ('1m', '5m', '15m', '1H', '4H')}
    funding = base.fetch_funding()
    inds = {tf: base.compute_indicators(rows) for tf, rows in data.items()}
    ts = {tf: [r['t'] for r in rows] for tf, rows in data.items()}

    a = run_variant('A_BASELINE', v151.V151Market, data, inds, ts, funding, meta)
    b = run_variant('B_DUAL_TRIGGER', DualTriggerMarket, data, inds, ts, funding, meta)

    result = {
        'period_start': START.isoformat(),
        'period_end': END.isoformat(),
        'days': 90,
        'strategy': 'KAYTRADE V1.5.1',
        'common_rules': {
            'entry_score_threshold': 6.0,
            'position_multiplier': '1x',
            'position_cap_usdt': 1000.0,
            'leverage': 5,
            'stop': '1H ATR x 1.0',
            'take_profit': '2R full position',
            'adverse_4h_structure_hard_gate': True,
            'three_losses_pause_hours': 1,
        },
        'A_current_v151': a,
        'B_dual_trigger': b,
        'delta_B_minus_A': {
            'net_pnl': b['net_pnl'] - a['net_pnl'],
            'net_return_pct': b['net_return_pct'] - a['net_return_pct'],
            'trades': b['trades'] - a['trades'],
            'win_rate_pct_points': b['win_rate_pct'] - a['win_rate_pct'],
            'profit_factor': b['profit_factor'] - a['profit_factor'],
            'expectancy_per_trade': b['expectancy_per_trade'] - a['expectancy_per_trade'],
            'max_drawdown_pct_points': b['max_drawdown_pct'] - a['max_drawdown_pct'],
            'fees': b['fees'] - a['fees'],
        },
    }
    (OUTDIR / 'trigger_ab_90d.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    lines = [
        '# KAYTRADE V1.5.1 — 90天 Trigger A/B 测试', '',
        f'区间：{START:%Y-%m-%d %H:%M} UTC → {END:%Y-%m-%d %H:%M} UTC', '',
        'A：当前V1.5.1，任意1种Trigger即可通过。  ',
        'B：至少2种Trigger共振（Trigger=1.0）才允许开仓。', '',
        '| 指标 | A 当前V1.5.1 | B 双Trigger | B-A |',
        '|---|---:|---:|---:|',
        f"| 交易数 | {a['trades']} | {b['trades']} | {b['trades']-a['trades']:+d} |",
        f"| 净PnL | {a['net_pnl']:+.2f}U | {b['net_pnl']:+.2f}U | {b['net_pnl']-a['net_pnl']:+.2f}U |",
        f"| 收益率 | {a['net_return_pct']:+.3f}% | {b['net_return_pct']:+.3f}% | {b['net_return_pct']-a['net_return_pct']:+.3f}pp |",
        f"| 胜率 | {a['win_rate_pct']:.2f}% | {b['win_rate_pct']:.2f}% | {b['win_rate_pct']-a['win_rate_pct']:+.2f}pp |",
        f"| PF | {fmt(a['profit_factor'])} | {fmt(b['profit_factor'])} | {b['profit_factor']-a['profit_factor']:+.3f} |",
        f"| 每笔期望 | {a['expectancy_per_trade']:+.3f}U | {b['expectancy_per_trade']:+.3f}U | {b['expectancy_per_trade']-a['expectancy_per_trade']:+.3f}U |",
        f"| 最大回撤 | {a['max_drawdown_pct']:.3f}% | {b['max_drawdown_pct']:.3f}% | {b['max_drawdown_pct']-a['max_drawdown_pct']:+.3f}pp |",
        f"| 手续费 | {a['fees']:.2f}U | {b['fees']:.2f}U | {b['fees']-a['fees']:+.2f}U |",
        '',
        f"B组额外拦截的单Trigger候选：{b['dual_trigger_rejections']}次。",
    ]
    (OUTDIR / 'V151_Trigger_AB_90D_Report.md').write_text('\n'.join(lines), encoding='utf-8')

    print('V151_TRIGGER_AB_90D')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('OUTPUT_DIR', OUTDIR.resolve())


if __name__ == '__main__':
    main()

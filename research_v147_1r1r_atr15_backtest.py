#!/usr/bin/env python3
"""V1.4.7 BTC 30-day comparison scenario: true 1:1 TP/SL, 15m ATR x1.5.

Keeps the prior 2,000 USDT profile, fee model, signal rules and exact 30-day
window so results are directly comparable with the preceding V1.4.7 run.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v147_1m_2000u_backtest as v

# Pin the exact same sample used by the prior run for apples-to-apples comparison.
END = datetime(2026, 9, 12, 7, 40, tzinfo=timezone.utc)
START = END - timedelta(days=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)

# Requested exit geometry: both stop and target are 1.5 x 15m ATR.
STOP_ATR = 1.5
REWARD_R = 1.0

OUTDIR = Path('backtest_output_v147_1r1r_atr15')
OUTDIR.mkdir(parents=True, exist_ok=True)

# Override both the V1.4.7 research module and inherited execution simulator.
for module in (v, v.base):
    module.START = START
    module.END = END
    module.START_MS = START_MS
    module.END_MS = END_MS
    module.STOP_ATR = STOP_ATR
    module.REWARD_R = REWARD_R
    module.OUTDIR = OUTDIR


def plan_1r1r(self, side, ti, score, signal_price, atr15, equity, daily_remaining):
    """Inherited sizing logic with a true 1R target instead of the old hard-coded 2R."""
    b = v.base
    buy = side == '做多'
    tick = self.meta['tickSz']
    entry = b.floor_tick(signal_price, tick) if buy else b.ceil_tick(signal_price, tick)
    dist = atr15 * STOP_ATR
    sl = b.floor_tick(entry - dist, tick) if buy else b.ceil_tick(entry + dist, tick)
    tp = b.ceil_tick(entry + dist, tick) if buy else b.floor_tick(entry - dist, tick)
    if not ((sl < entry < tp) if buy else (tp < entry < sl)):
        return None

    maker = b.MAKER_BPS / 10000
    taker = b.TAKER_BPS / 10000
    slip = b.SLIPPAGE_BPS / 10000
    per_btc = abs(entry - sl) + entry * max(maker, taker) + sl * (taker + slip)
    risk_capital = min(b.CAPITAL, equity)
    base_risk = min(b.RISK_USDT, risk_capital * b.RISK_PCT / 100)
    mult = {1: 1.0, 2: 1.5, 3: 2.0}[ti]
    risk = min(base_risk * mult, daily_remaining)
    if self.active:
        current_risk = sum(l.est_loss for l in self.active.legs if l.status == 'open')
        risk = min(risk, max(0.0, daily_remaining - current_risk))

    avail = self.available(signal_price)
    notional_cap = min(b.TIER_CAP[ti], risk_capital * b.LEVERAGE, avail * .9 * b.LEVERAGE)
    unit = self.meta['ctVal'] * self.meta['ctMult']
    contracts = b.floor_lot(min(risk / per_btc, notional_cap / entry) / unit, self.meta['lotSz'])
    if contracts + 1e-12 < self.meta['minSz'] or contracts <= 0:
        return None
    qty = contracts * unit
    notional = qty * entry
    est = qty * per_btc

    expected_cost_per_btc = entry * maker + tp * taker
    multiple = dist / expected_cost_per_btc if expected_cost_per_btc > 0 else float('inf')
    if multiple < b.EXPECTED_COST_MIN:
        return None
    return entry, qty, notional, sl, tp, est, multiple


def handle_open_leg_exit_1r(self, leg, bar, just_filled=False):
    """Same trigger-limit model, but label the target correctly as TP_1R."""
    if leg.status != 'open' and not leg.stop_child_pending:
        return
    d = leg.direction()
    if leg.stop_child_pending:
        can_fill = bar['h'] >= leg.sl if d > 0 else bar['l'] <= leg.sl
        if can_fill:
            self.close_leg(leg, leg.sl, bar['t'], 'SL_limit_recovered')
        return
    hit_sl = bar['l'] <= leg.sl if d > 0 else bar['h'] >= leg.sl
    hit_tp = bar['h'] >= leg.tp if d > 0 else bar['l'] <= leg.tp
    if hit_sl:
        gap_miss = (bar['o'] < leg.sl and bar['h'] < leg.sl) if d > 0 else (bar['o'] > leg.sl and bar['l'] > leg.sl)
        if gap_miss:
            leg.stop_child_pending = True
            self.stats['stop_limit_gap_misses'] += 1
            return
        self.close_leg(leg, leg.sl, bar['t'], 'SL')
        return
    if hit_tp:
        self.close_leg(leg, leg.tp, bar['t'], 'TP_1R')


# Replace the inherited simulator's hard-coded 2R target only for this research branch.
v.base.Simulator.plan = plan_1r1r
v.base.Simulator.handle_open_leg_exit = handle_open_leg_exit_1r

_original_write_report = v.write_report


def write_report(sim, metrics, leg_net, cycles, closed):
    _original_write_report(sim, metrics, leg_net, cycles, closed)

    renames = {
        'V147_BTC_SWAP_30D_2000U_Trades.csv': 'V147_BTC_SWAP_30D_2000U_1R1R_ATR15_Trades.csv',
        'V147_BTC_SWAP_30D_2000U_Cycles.csv': 'V147_BTC_SWAP_30D_2000U_1R1R_ATR15_Cycles.csv',
        'V147_BTC_SWAP_30D_2000U_DailyEquity.csv': 'V147_BTC_SWAP_30D_2000U_1R1R_ATR15_DailyEquity.csv',
        'V147_BTC_SWAP_30D_2000U_Backtest_Report.md': 'V147_BTC_SWAP_30D_2000U_1R1R_ATR15_Backtest_Report.md',
    }
    for old, new in renames.items():
        src = OUTDIR / old
        if src.exists():
            src.replace(OUTDIR / new)

    report = OUTDIR / 'V147_BTC_SWAP_30D_2000U_1R1R_ATR15_Backtest_Report.md'
    text = report.read_text(encoding='utf-8')
    text = text.replace(
        '**策略：** V1.4.7，1:2整仓止盈止损，15m ATR×1止损。',
        '**策略：** V1.4.7，1:1整仓止盈止损；止损距离=15m ATR×1.5，止盈距离=15m ATR×1.5。'
    )
    text = text.replace(
        '# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天回测报告',
        '# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天 1:1 / ATR1.5 回测报告'
    )
    report.write_text(text, encoding='utf-8')


v.write_report = write_report

if __name__ == '__main__':
    v.main()

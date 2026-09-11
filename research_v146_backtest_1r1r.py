#!/usr/bin/env python3
"""Run the V1.4.6 three-month backtest with TP=1R and SL=1R only.

All signal, sizing, cooldown, tier, fee, funding and risk-control behavior comes
from research_v146_backtest.py. This wrapper changes only reward distance from
2R to 1R and updates report labels accordingly.
"""
import math
import os
from decimal import Decimal, ROUND_DOWN

import research_v146_backtest as base

base.REWARD_R = 1.0
base.OUTDIR = base.Path(os.environ.get("BACKTEST_OUT", "backtest_output_1r1r"))
base.OUTDIR.mkdir(parents=True, exist_ok=True)


def plan_1r1r(self, side, ti, score, signal_price, atr15, equity, daily_remaining):
    buy = side == "做多"
    tick = self.meta["tickSz"]
    entry = base.floor_tick(signal_price, tick) if buy else base.ceil_tick(signal_price, tick)
    dist = atr15 * base.STOP_ATR
    sl = base.floor_tick(entry-dist, tick) if buy else base.ceil_tick(entry+dist, tick)
    tp = base.ceil_tick(entry+dist, tick) if buy else base.floor_tick(entry-dist, tick)
    if not ((sl < entry < tp) if buy else (tp < entry < sl)):
        return None

    maker = base.MAKER_BPS / 10000
    taker = base.TAKER_BPS / 10000
    slip = base.SLIPPAGE_BPS / 10000
    per_btc = abs(entry-sl) + entry*max(maker,taker) + sl*(taker+slip)
    risk_capital = min(base.CAPITAL, equity)
    base_risk = min(base.RISK_USDT, risk_capital*base.RISK_PCT/100)
    mult = {1:1.0, 2:1.5, 3:2.0}[ti]
    risk = min(base_risk*mult, daily_remaining)
    if self.active:
        current_risk = sum(l.est_loss for l in self.active.legs if l.status == "open")
        risk = min(risk, max(0.0, daily_remaining-current_risk))

    avail = self.available(signal_price)
    notional_cap = min(base.TIER_CAP[ti], risk_capital*base.LEVERAGE, avail*.9*base.LEVERAGE)
    unit = self.meta["ctVal"] * self.meta["ctMult"]
    contracts = base.floor_lot(min(risk/per_btc, notional_cap/entry)/unit, self.meta["lotSz"])
    if contracts + 1e-12 < self.meta["minSz"] or contracts <= 0:
        return None

    qty = contracts * unit
    notional = qty * entry
    est = qty * per_btc
    expected_cost_per_btc = entry*maker + tp*taker
    multiple = dist / expected_cost_per_btc if expected_cost_per_btc > 0 else math.inf
    if multiple < base.EXPECTED_COST_MIN:
        return None
    return entry, qty, notional, sl, tp, est, multiple


def handle_open_leg_exit_1r1r(self, l, bar, just_filled=False):
    if l.status != "open" and not l.stop_child_pending:
        return
    d = l.direction()
    if l.stop_child_pending:
        can_fill = bar["h"] >= l.sl if d > 0 else bar["l"] <= l.sl
        if can_fill:
            self.close_leg(l, l.sl, bar["t"], "SL_limit_recovered")
        return
    hit_sl = bar["l"] <= l.sl if d > 0 else bar["h"] >= l.sl
    hit_tp = bar["h"] >= l.tp if d > 0 else bar["l"] <= l.tp
    if hit_sl:
        gap_miss = (bar["o"] < l.sl and bar["h"] < l.sl) if d > 0 else (bar["o"] > l.sl and bar["l"] > l.sl)
        if gap_miss:
            l.stop_child_pending = True
            self.stats["stop_limit_gap_misses"] += 1
            return
        self.close_leg(l, l.sl, bar["t"], "SL")
        return
    if hit_tp:
        self.close_leg(l, l.tp, bar["t"], "TP_1R")


base.Simulator.plan = plan_1r1r
base.Simulator.handle_open_leg_exit = handle_open_leg_exit_1r1r

_original_write_outputs = base.write_outputs

def write_outputs_1r1r(sim, metrics, leg_net, cycles, closed):
    _original_write_outputs(sim, metrics, leg_net, cycles, closed)
    report = base.OUTDIR / "V146_BTC_SWAP_3M_Backtest_Report.md"
    text = report.read_text(encoding="utf-8")
    replacements = {
        "2R整仓止盈":"1R整仓止盈",
        "整仓2R":"整仓1R",
        "TP 2R":"TP 1R",
        "TP_2R":"TP_1R",
        "2R TP":"1R TP",
        "2R结构":"1R结构",
        "2R止盈":"1R止盈",
        "15m ATR×1 SL；整仓TP=2R":"15m ATR×1 SL；整仓TP=1R",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace("# KAYTRADE V1.4.6 — BTC-USDT-SWAP 三个月历史回测报告",
                        "# KAYTRADE V1.4.6 — BTC-USDT-SWAP 三个月 1:1 止盈止损回测报告")
    text += "\n\n> 本情景仅将TP从2R改为1R；SL仍为1R。其余V1.4.6信号、三档仓位、手续费、Funding、冷却、连亏停止和日内风控保持不变。\n"
    report.write_text(text, encoding="utf-8")
    target = base.OUTDIR / "V146_BTC_SWAP_3M_1R1R_Backtest_Report.md"
    target.write_text(text, encoding="utf-8")

base.write_outputs = write_outputs_1r1r

if __name__ == "__main__":
    base.main()

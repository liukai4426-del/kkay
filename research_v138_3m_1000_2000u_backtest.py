#!/usr/bin/env python3
"""KAYTRADE V1.3.8 confirmed two-signal BTC-USDT-SWAP 3-month backtest.

Research only. Reuses the proven OKX historical-data and execution simulator from
research_v146_backtest, while restoring the exact V1.3.8 signal formula and the
user-confirmed V1.3.8 entry bands / position relationship:

- 6.0-7.5: opening signal, first signal notional cap 1,000 USDT
- 8.0-10.0: strong signal, second signal notional cap 2,000 USDT
- only these two entry levels
- 3 consecutive losing completed cycles -> no new entries for 6 hours
- latest CLOSED 1m candle drives signals; 1m Trigger > 0 is a hard gate
- LIMIT entry valid for one minute; full-position TP=2R, SL=1R using 15m ATR x1

Because the user specified signal position sizes but not account equity, this run
uses a 10,000 USDT strategy-equity baseline only for return/drawdown and the
preserved 3% daily-drawdown guard. Absolute USDT PnL is the primary result.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

import research_v146_backtest as base

# Calendar three-month window requested in this conversation.
START = datetime(2026, 6, 12, 0, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)

# Monetary assumptions. Position inputs are the user's explicit settings.
CAPITAL = 10_000.0
LEVERAGE = 5
RISK_USDT = 100.0          # 1% scaled default; normally does not bind 1k/2k caps.
RISK_PCT = 1.0
DAILY_LOSS = 300.0         # Preserved V1.3.8 3% daily-equity drawdown guard.
CONSECUTIVE_LOSSES = 3
LOSS_PAUSE_MS = 6 * 60 * 60 * 1000
COOLDOWN_MINUTES = 30
STOP_ATR = 1.0
REWARD_R = 2.0
FIRST_SIGNAL_NOTIONAL = 1_000.0
SECOND_SIGNAL_NOTIONAL = 2_000.0
TIER_CAP = {1: FIRST_SIGNAL_NOTIONAL, 2: SECOND_SIGNAL_NOTIONAL, 3: 0.0}
TIER_LABEL = {1: "Opening 6.0-7.5 / 1000U", 2: "Strong 8.0-10 / 2000U", 3: "unused"}

OUTDIR = Path("backtest_output_v138_3m_1000_2000u_6h")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Override the proven base simulator globals. The base Market scorer is the V1.3.8
# formula: daily EMA + 15m setup/structure + 1m trigger + 5m/15m trend, RSI and
# 1H/4H adverse penalties, plus the <1R forward-structure block / 1.0-1.3R penalty.
base.START = START
base.END = END
base.START_MS = START_MS
base.END_MS = END_MS
base.CAPITAL = CAPITAL
base.LEVERAGE = LEVERAGE
base.RISK_USDT = RISK_USDT
base.RISK_PCT = RISK_PCT
base.DAILY_LOSS = DAILY_LOSS
base.CONSECUTIVE_LOSSES = CONSECUTIVE_LOSSES
base.COOLDOWN_MINUTES = COOLDOWN_MINUTES
base.STOP_ATR = STOP_ATR
base.REWARD_R = REWARD_R
base.TIER_CAP = TIER_CAP
base.TIER_LABEL = TIER_LABEL
base.OUTDIR = OUTDIR


def tier(total):
    """User-confirmed V1.3.8 has exactly two entry levels."""
    value = float(total or 0.0)
    if value >= 8.0:
        return 2
    if value >= 6.0:
        return 1
    return 0


# base.Market.score resolves tier() through its module globals at runtime.
base.tier = tier


class V138Simulator(base.Simulator):
    def __init__(self, market, meta, funding):
        super().__init__(market, meta, funding)
        self.loss_pause_until_ms = -10**18
        self.loss_pause_count = 0
        self.max_effective_loss_streak = 0

    def update_day(self, ms, equity):
        """Keep daily drawdown reset, but do NOT reset consecutive losses by date."""
        day = datetime.fromtimestamp(ms/1000, tz=timezone.utc).astimezone(base.SH_TZ).strftime("%Y-%m-%d")
        if self.daily_day != day:
            self.daily_day = day
            self.daily_peak = equity
            self.daily_block_day = None
        else:
            self.daily_peak = max(self.daily_peak, equity)
        if self.daily_peak - equity >= DAILY_LOSS - 1e-12:
            self.daily_block_day = day
        return day

    def available(self, price):
        eq = self.mark_equity(price)
        margin = sum(l.notional / LEVERAGE for l in self.open_legs())
        return max(0.0, eq - margin)

    def daily_remaining(self, equity):
        return max(0.0, DAILY_LOSS - max(0.0, self.daily_peak - equity))

    def plan(self, side, ti, score, signal_price, atr15, equity, daily_remaining):
        """V1.3.8 sizing: first risk x1, strong risk x2; notional caps 1000/2000U."""
        if ti not in (1, 2):
            return None
        buy = side == "做多"
        tick = self.meta["tickSz"]
        entry = base.floor_tick(signal_price, tick) if buy else base.ceil_tick(signal_price, tick)
        dist = float(atr15) * STOP_ATR
        sl = base.floor_tick(entry-dist, tick) if buy else base.ceil_tick(entry+dist, tick)
        tp = base.ceil_tick(entry+2*dist, tick) if buy else base.floor_tick(entry-2*dist, tick)
        if not ((sl < entry < tp) if buy else (tp < entry < sl)):
            return None

        maker = base.MAKER_BPS / 10000
        taker = base.TAKER_BPS / 10000
        slip = base.SLIPPAGE_BPS / 10000
        per_btc = abs(entry-sl) + entry*max(maker, taker) + sl*(taker+slip)
        risk_capital = min(CAPITAL, equity)
        base_risk = min(RISK_USDT, risk_capital*RISK_PCT/100)
        multiplier = 1.0 if ti == 1 else 2.0
        risk = min(base_risk*multiplier, daily_remaining)
        if self.active:
            current_risk = sum(l.est_loss for l in self.active.legs if l.status == "open")
            risk = min(risk, max(0.0, daily_remaining-current_risk))
        if risk <= 0:
            return None

        avail = self.available(signal_price)
        notional_cap = min(TIER_CAP[ti], risk_capital*LEVERAGE, avail*.9*LEVERAGE)
        unit = self.meta["ctVal"] * self.meta["ctMult"]
        contracts = base.floor_lot(min(risk/per_btc, notional_cap/entry)/unit, self.meta["lotSz"])
        if contracts + 1e-12 < self.meta["minSz"] or contracts <= 0:
            return None
        qty = contracts * unit
        notional = qty * entry
        est = qty * per_btc
        expected_cost_per_btc = entry*maker + tp*taker
        multiple = 2*dist/expected_cost_per_btc if expected_cost_per_btc > 0 else math.inf
        if multiple < base.EXPECTED_COST_MIN:
            return None
        return entry, qty, notional, sl, tp, est, multiple

    def can_signal(self, side, ti, signal_ms):
        if ti not in (1, 2):
            return False
        day = datetime.fromtimestamp(signal_ms/1000, tz=timezone.utc).astimezone(base.SH_TZ).strftime("%Y-%m-%d")
        if self.daily_block_day == day:
            self.stats["daily_risk_blocks"] += 1
            return False

        if signal_ms < self.loss_pause_until_ms:
            self.stats["streak_blocks"] += 1
            return False
        if self.loss_pause_until_ms > -10**17 and signal_ms >= self.loss_pause_until_ms:
            self.loss_streak = 0
            self.loss_pause_until_ms = -10**18

        if self.pending_legs():
            return False
        if self.active is None:
            if signal_ms - self.last_close_ms < COOLDOWN_MINUTES*60_000:
                self.stats["cooldown_blocks"] += 1
                return False
            return True

        if side != self.active.side:
            return False
        if ti < self.active.highest_tier:
            return False
        if self.active.tier_counts[ti] >= 1:
            return False
        if ti == 1 and self.active.legs:
            return False
        return True

    def cleanup_cycle(self, ms):
        c = self.active
        if c is None:
            return
        alive = any(l.status in ("pending", "open") or l.stop_child_pending for l in c.legs)
        if alive:
            return
        filled = [l for l in c.legs if l.fill_ms is not None]
        if not filled:
            self.cycles.remove(c)
            self.active = None
            return

        c.end_ms = ms
        c.pnl = self.cash - c.start_equity
        if c.pnl < 0:
            self.loss_streak += 1
            self.max_effective_loss_streak = max(self.max_effective_loss_streak, self.loss_streak)
            if self.loss_streak >= CONSECUTIVE_LOSSES:
                self.loss_pause_until_ms = ms + LOSS_PAUSE_MS
                self.loss_pause_count += 1
        else:
            self.loss_streak = 0
        self.last_close_ms = ms
        self.active = None

    def handle_open_leg_exit(self, l, bar, just_filled=False):
        """V1.3.8 exchange-attached TP/SL are market-on-trigger; SL-first is conservative when both hit in one minute."""
        if l.status != "open":
            return
        d = l.direction()
        hit_sl = bar["l"] <= l.sl if d > 0 else bar["h"] >= l.sl
        hit_tp = bar["h"] >= l.tp if d > 0 else bar["l"] <= l.tp
        if hit_sl:
            self.close_leg(l, l.sl, bar["t"], "SL_market")
            return
        if hit_tp:
            self.close_leg(l, l.tp, bar["t"], "TP_2R_market")


def write_report(sim, metrics, leg_net, cycles, closed):
    base.write_outputs(sim, metrics, leg_net, cycles, closed)
    renames = {
        "V146_BTC_SWAP_3M_Trades.csv": "V138_BTC_SWAP_3M_Trades.csv",
        "V146_BTC_SWAP_3M_Cycles.csv": "V138_BTC_SWAP_3M_Cycles.csv",
        "V146_BTC_SWAP_3M_DailyEquity.csv": "V138_BTC_SWAP_3M_DailyEquity.csv",
    }
    for old, new in renames.items():
        src = OUTDIR/old
        if src.exists():
            src.replace(OUTDIR/new)
    old_report = OUTDIR/"V146_BTC_SWAP_3M_Backtest_Report.md"
    if old_report.exists():
        old_report.unlink()

    metrics["loss_pause_hours"] = 6
    metrics["loss_pause_count"] = sim.loss_pause_count
    metrics["max_effective_loss_streak"] = sim.max_effective_loss_streak
    metrics["first_signal_notional"] = FIRST_SIGNAL_NOTIONAL
    metrics["second_signal_notional"] = SECOND_SIGNAL_NOTIONAL
    metrics["equity_baseline_assumption"] = CAPITAL
    metrics["daily_loss_guard"] = DAILY_LOSS

    by_tier_notional = {}
    for t in (1, 2):
        vals = [l.notional for l in closed if l.tier == t]
        by_tier_notional[str(t)] = {
            "count": len(vals),
            "avg": statistics.mean(vals) if vals else 0.0,
            "median": statistics.median(vals) if vals else 0.0,
            "max": max(vals) if vals else 0.0,
        }
    metrics["by_tier_notional"] = by_tier_notional
    (OUTDIR/"metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    def num(x):
        return "∞" if math.isinf(x) else f"{x:.3f}"

    lines = [
        "# KAYTRADE V1.3.8 — BTC-USDT-SWAP 三个月回测",
        "",
        f"**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（{metrics['days']}天）  ",
        "**数据：** OKX BTC-USDT-SWAP 历史K线 + Funding。  ",
        "**信号：** V1.3.8确认版；6.0–7.5开仓信号，8.0–10强信号；低于6分不开仓。  ",
        "**仓位：** 第一信号1000U；第二信号2000U；同一轮可由第一信号升级追加第二信号。  ",
        "**风控：** 连续亏损3个完整交易轮次后禁止新开仓6小时；普通平仓后冷却30分钟；15m ATR×1止损，2R整仓止盈。",
        "",
        "## 结果",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 账户权益计算基准（假设） | {CAPITAL:.0f} USDT |",
        f"| 期末权益 | {metrics['ending_equity']:.2f} USDT |",
        f"| 净收益 | {metrics['net_pnl']:+.2f} USDT |",
        f"| 净收益率（按1万U基准） | {metrics['net_return_pct']:+.2f}% |",
        f"| 压力情景收益率（每次退出额外5bps滑点） | {metrics['stress_return_pct']:+.2f}% |",
        f"| 完整交易轮次 | {metrics['cycles']} |",
        f"| 成交腿数 | {metrics['filled_legs']} |",
        f"| 轮次胜率 | {metrics['cycle_win_rate_pct']:.2f}% |",
        f"| 单腿胜率 | {metrics['leg_win_rate_pct']:.2f}% |",
        f"| Profit Factor | {num(metrics['profit_factor'])} |",
        f"| 每轮期望 | {metrics['expectancy_cycle']:+.4f} USDT |",
        f"| 平均盈利轮次 | {metrics['avg_win_cycle']:+.4f} USDT |",
        f"| 平均亏损轮次 | {metrics['avg_loss_cycle']:+.4f} USDT |",
        f"| 盈亏金额比 | {num(metrics['payoff_ratio'])} |",
        f"| 最大回撤 | {metrics['max_drawdown_usdt']:.2f} USDT / {metrics['max_drawdown_pct']:.2f}% |",
        f"| 风控观察到的最大连续亏损 | {sim.max_effective_loss_streak} |",
        f"| 触发“连亏3次→暂停6小时”次数 | {sim.loss_pause_count} |",
        f"| 暂停期间拦截的合格开仓信号 | {metrics['streak_blocks']} |",
        f"| 日回撤保护拦截信号 | {metrics['daily_risk_blocks']} |",
        f"| 30分钟冷却拦截信号 | {metrics['cooldown_blocks']} |",
        f"| 手续费 | {metrics['fees']:.2f} USDT |",
        f"| Funding净影响 | {metrics['funding_pnl']:+.4f} USDT |",
        f"| 平均/中位持仓 | {metrics['avg_hold_min']:.1f} / {metrics['median_hold_min']:.1f} 分钟 |",
        f"| BTC同期买入持有 | {metrics['btc_buy_hold_pct']:+.2f}% |",
        "",
        "## 分方向",
        "",
    ]
    for side in ("做多", "做空"):
        n, pnl, wr = metrics["by_side"].get(side, (0, 0, 0))
        lines.append(f"- {side}：{n}腿，净收益 {pnl:+.2f}U，胜率 {wr:.2f}%")
    lines += ["", "## 分信号档位", ""]
    for t, label in ((1, "开仓信号 6.0–7.5"), (2, "强信号 8.0–10")):
        n, pnl, wr = metrics["by_tier"].get(t, (0, 0, 0))
        pos = by_tier_notional[str(t)]
        lines.append(f"- {label}：{n}腿，净收益 {pnl:+.2f}U，胜率 {wr:.2f}%，平均实际名义仓位 {pos['avg']:.2f}U")
    lines += ["", "## 月度净收益", ""]
    for month, row in sorted(metrics["by_month"].items()):
        lines.append(f"- {month}：{row[0]}腿，{row[1]:+.2f}U")
    lines += [
        "",
        "## 回测执行假设",
        "",
        "- 信号只使用当时已经收盘的数据，避免未来函数。",
        "- 开仓按信号价附近的限价单模拟，有效期1分钟；下一根1m K线触价才算成交，否则撤单。",
        "- 入场按Maker 0.02%，离场按Taker 0.05%；Funding按OKX历史结算率计入。",
        "- TP和SL同一分钟都被触及时，按SL先发生处理，这是偏保守的分钟级假设。",
        "- 主结果未额外扣离场滑点；另给出每次离场再扣5bps的压力情景。",
        "- 你未指定账户总权益，因此1万U只用于收益率、可用保证金和3%日回撤保护计算；1000/2000U信号仓位是本次核心固定参数。",
        "- 历史回测不代表未来收益。",
    ]
    (OUTDIR/"V138_BTC_SWAP_3M_Backtest_Report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def main():
    meta = base.fetch_instrument()
    print("Instrument:", meta)
    data = {}
    for tf in ("1m", "5m", "15m", "1H", "4H", "1Dutc"):
        data[tf] = base.fetch_candles(tf)
    funding = base.fetch_funding()
    inds = {tf: base.compute_indicators(rows) for tf, rows in data.items()}
    ts = {tf: [r["t"] for r in rows] for tf, rows in data.items()}
    zones = {
        "15m": base.ZoneCache(data["15m"], inds["15m"], 160),
        "1H": base.ZoneCache(data["1H"], inds["1H"], 120),
        "4H": base.ZoneCache(data["4H"], inds["4H"], 180),
    }
    market = base.Market(data, inds, ts, zones)
    sim = V138Simulator(market, meta, funding)
    d1 = data["1m"]
    start_i = base.bisect.bisect_left(ts["1m"], START_MS)
    if start_i < 1:
        raise RuntimeError("not enough 1m warmup")

    for i in range(start_i, len(d1)):
        bar = d1[i]
        bar_close = bar["t"] + 60_000
        if bar["t"] >= END_MS:
            break
        sim.apply_funding_until(bar["t"], bar["o"])
        sim.fill_pending(bar)
        sim.process_exits(bar)
        sim.apply_funding_until(bar_close, bar["c"])
        sim.record_equity(bar_close, bar["c"])
        sig = market.score(i)
        sim.submit(sig, i, bar["c"])
        if (i-start_i) % 10_000 == 0:
            print(f"progress {i-start_i:,}/{len(d1)-start_i:,}, equity={sim.mark_equity(bar['c']):.3f}, cycles={len(sim.cycles)}, pauses={sim.loss_pause_count}", flush=True)

    last = [r for r in d1 if r["t"] < END_MS][-1]
    sim.finish(last)
    metrics, leg_net, cycles, closed = base.analyze(sim, d1, funding, meta)
    write_report(sim, metrics, leg_net, cycles, closed)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print("OUTPUT_DIR", OUTDIR.resolve())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""KAYTRADE V1.3.8 FINAL BTC-USDT-SWAP rolling 92-day historical backtest.

Research-only backtest for the final V1.3.8 rules actually loaded by
v138_user_update + v138_hard_gate_final.

Key rules reproduced here:
- no 1D indicators
- 1m Trigger is a hard gate only (KDJ cross OR EMA20 reclaim OR reversal); it does NOT add score
- 5m MACD must agree with direction and adds +1
- 1H aligned +2; 1H opposite is a hard block
- 4H aligned +1; 4H opposite -1
- forward strong structure must leave >=1.3R
- 6.0-7.5 opening signal / 8.0-10 strong signal, no third tier
- first/second signal notional caps 1,000 / 2,000 USDT
- if Tier1 later upgrades to Tier2, total cycle cap can reach 3,000 USDT
- latest closed 1m dedupe; LIMIT entry valid for one minute
- TP=2R / SL=1R using 15m ATR; exchange-side exits are market-on-trigger
- three consecutive losing completed cycles pause new entries for exactly six hours
"""
from __future__ import annotations

import bisect
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v146_backtest as base

_now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
END = _now
START = END - timedelta(days=92)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)

# Account baseline is only needed for equity/risk/drawdown simulation. The requested
# signal position caps are fixed below at 1,000 / 2,000 USDT.
CAPITAL = 10_000.0
LEVERAGE = 5
RISK_USDT = 100.0
RISK_PCT = 1.0
DAILY_LOSS = 300.0
CONSECUTIVE_LOSSES = 3
LOSS_PAUSE_MS = 6 * 60 * 60 * 1000
COOLDOWN_MINUTES = 30
STOP_ATR = 1.0
REWARD_R = 2.0
FIRST_POSITION = 1_000.0
SECOND_POSITION = 2_000.0
TIER_CAP = {1: FIRST_POSITION, 2: SECOND_POSITION, 3: SECOND_POSITION}
TIER_LABEL = {1: "开仓信号 6.0-7.5 / 1000U", 2: "强信号 8.0-10 / 2000U", 3: "未使用"}

OUTDIR = Path("backtest_output_v138_final_3m_1000_2000u")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Reuse the validated OKX data loader, fee/funding accounting and CSV analyzer.
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


def signal_tier(total: float) -> int:
    value = float(total or 0.0)
    if value >= 8.0:
        return 2
    if value >= 6.0:
        return 1
    return 0


def trend_parts(data, inds, i, buy):
    if i < 1:
        return False, False
    cur, prev = inds[i], inds[i-1]
    if buy:
        macd = cur["dif"] > cur["dea"] and cur["hist"] > 0 and cur["hist"] >= cur["prev_hist"]
        boll = data[i]["c"] > cur["middle"] and cur["middle"] > prev["middle"]
    else:
        macd = cur["dif"] < cur["dea"] and cur["hist"] < 0 and cur["hist"] <= cur["prev_hist"]
        boll = data[i]["c"] < cur["middle"] and cur["middle"] < prev["middle"]
    return bool(macd), bool(boll)


def valid_trigger(data, inds, i, buy):
    if i < 1:
        return False, {}
    ema_reclaim = base.ema_reclaim(data, inds, i, buy)
    kdj = inds[i]["cross_up"] if buy else inds[i]["cross_down"]
    reversal = base.reversal(data, i, buy)
    return bool(ema_reclaim or kdj or reversal), {
        "ema_reclaim": bool(ema_reclaim), "kdj": bool(kdj), "reversal": bool(reversal)
    }


def trend_state(data, inds, i, buy):
    c, cur = data[i], inds[i]
    close = float(c["c"])
    if buy:
        aligned = close > cur["ema200"] and cur["ema20"] > cur["ema50"] and cur["up"]
        opposite = close < cur["ema200"] and cur["ema20"] < cur["ema50"] and cur["down"]
    else:
        aligned = close < cur["ema200"] and cur["ema20"] < cur["ema50"] and cur["down"]
        opposite = close > cur["ema200"] and cur["ema20"] > cur["ema50"] and cur["up"]
    if aligned:
        return "aligned"
    if opposite:
        return "opposite"
    return "neutral"


class V138FinalMarket(base.Market):
    def score(self, i1):
        signal_close = self.data["1m"][i1]["t"] + base.BAR_MS["1m"]
        idx = {tf: base.mapped_index(self.ts[tf], signal_close, base.BAR_MS[tf])
               for tf in ("5m", "15m", "1H", "4H")}
        if min(idx.values()) < 1:
            return {"side": "观望", "scores": {}}
        i5, i15, i1h, i4h = idx["5m"], idx["15m"], idx["1H"], idx["4H"]
        price = float(self.data["1m"][i1]["c"])
        hz = self.zones["1H"].zones(i1h)
        mz = self.zones["15m"].zones(i15)
        qz = self.zones["4H"].zones(i4h)
        results = {}

        for side in ("做多", "做空"):
            buy = side == "做多"
            trigger_ok, trigger_detail = valid_trigger(self.data["1m"], self.inds["1m"], i1, buy)
            macd5, boll5 = trend_parts(self.data["5m"], self.inds["5m"], i5, buy)
            macd15, boll15 = trend_parts(self.data["15m"], self.inds["15m"], i15, buy)
            hstate = trend_state(self.data["1H"], self.inds["1H"], i1h, buy)
            qstate = trend_state(self.data["4H"], self.inds["4H"], i4h, buy)

            setup = base.setup_score(self.data["15m"], self.inds["15m"], i15, buy)
            favorable_kind = "support" if buy else "resistance"
            adverse_kind = "resistance" if buy else "support"
            ahead = "above" if buy else "below"

            fav15_zone = base.nearest(mz, price, favorable_kind)
            fav15 = .5 if fav15_zone and abs(fav15_zone["price"]-price)/self.inds["15m"][i15]["atr"] <= .25 else 0.0

            bad1 = base.nearest(hz, price, adverse_kind, ahead)
            bad4 = base.nearest(qz, price, adverse_kind, ahead)
            struct1 = -1.0 if bad1 and abs(bad1["price"]-price)/self.inds["1H"][i1h]["atr"] <= .25 else 0.0
            struct4 = -1.5 if bad4 and abs(bad4["price"]-price)/self.inds["4H"][i4h]["atr"] <= .25 else 0.0

            forward = []
            for tf, zones in (("1H", hz), ("15m", mz)):
                z = base.nearest(zones, price, adverse_kind, ahead, strong_only=True)
                if z:
                    forward.append((abs(z["price"]-price), tf, z))
            front = min(forward, key=lambda x: x[0]) if forward else None
            risk_dist = float(self.inds["15m"][i15]["atr"]) * STOP_ATR
            front_r = front[0] / risk_dist if front and risk_dist > 0 else math.inf
            space_ok = front_r >= 1.3

            rsi = base.rsi_penalty(self.inds["15m"][i15], self.inds["5m"][i5], buy)
            hscore = 2.0 if hstate == "aligned" else 0.0
            qscore = 1.0 if qstate == "aligned" else (-1.0 if qstate == "opposite" else 0.0)

            # IMPORTANT: 1m Trigger is NOT part of raw score in final V1.3.8.
            raw = (
                setup + fav15 +
                (1.0 if macd5 else 0.0) + (1.0 if boll5 else 0.0) +
                (1.0 if macd15 else 0.0) + (1.0 if boll15 else 0.0) +
                hscore + qscore + rsi + struct1 + struct4
            )
            total = max(0.0, min(10.0, round(raw * 2) / 2))
            ti = signal_tier(total)
            gate = bool(trigger_ok and macd5 and hstate != "opposite" and space_ok)
            eligible = bool(gate and ti > 0)

            results[side] = {
                "total": total, "raw": raw, "gate": gate, "eligible": eligible, "tier": ti,
                "atr15": float(self.inds["15m"][i15]["atr"]),
                "atr1h": float(self.inds["1H"][i1h]["atr"]),
                "front_r": front_r,
                "hstate": hstate, "qstate": qstate,
                "components": {
                    "setup": setup, "fav15": fav15,
                    "macd5": 1.0 if macd5 else 0.0, "boll5": 1.0 if boll5 else 0.0,
                    "macd15": 1.0 if macd15 else 0.0, "boll15": 1.0 if boll15 else 0.0,
                    "trend1h": hscore, "trend4h": qscore, "rsi": rsi,
                    "struct1h": struct1, "struct4h": struct4,
                    "trigger_ok": 1.0 if trigger_ok else 0.0,
                    "trigger_ema_reclaim": 1.0 if trigger_detail.get("ema_reclaim") else 0.0,
                    "trigger_kdj": 1.0 if trigger_detail.get("kdj") else 0.0,
                    "trigger_reversal": 1.0 if trigger_detail.get("reversal") else 0.0,
                    "front_block": 0.0 if space_ok else 1.0,
                },
            }

        qualified = [s for s in ("做多", "做空") if results[s]["eligible"]]
        if len(qualified) == 1:
            selected = qualified[0]
        elif len(qualified) == 2:
            a, b = results[qualified[0]]["total"], results[qualified[1]]["total"]
            selected = "观望" if abs(a-b) <= 1e-9 else max(qualified, key=lambda s: results[s]["total"])
        else:
            selected = "观望"
        return {"side": selected, "scores": results, "idx": idx,
                "price": price, "signal_close": signal_close}


class V138Simulator(base.Simulator):
    def __init__(self, market, meta, funding):
        super().__init__(market, meta, funding)
        self.loss_pause_until_ms = 0

    def update_day(self, ms, equity):
        # Daily drawdown still resets by China date; consecutive-loss streak does not.
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

    def plan(self, side, ti, score, signal_price, atr15, equity, daily_remaining):
        buy = side == "做多"
        tick = self.meta["tickSz"]
        entry = base.floor_tick(signal_price, tick) if buy else base.ceil_tick(signal_price, tick)
        dist = atr15 * STOP_ATR
        sl = base.floor_tick(entry-dist, tick) if buy else base.ceil_tick(entry+dist, tick)
        tp = base.ceil_tick(entry+2*dist, tick) if buy else base.floor_tick(entry-2*dist, tick)
        if not ((sl < entry < tp) if buy else (tp < entry < sl)):
            return None
        maker = base.MAKER_BPS/10000
        taker = base.TAKER_BPS/10000
        slip = base.SLIPPAGE_BPS/10000
        per_btc = abs(entry-sl) + entry*max(maker, taker) + sl*(taker+slip)
        risk_capital = min(CAPITAL, equity)
        base_risk = min(RISK_USDT, risk_capital*RISK_PCT/100)
        mult = {1: 1.0, 2: 2.0}[ti]
        risk = min(base_risk*mult, daily_remaining)
        if self.active:
            current_risk = sum(l.est_loss for l in self.active.legs if l.status == "open")
            risk = min(risk, max(0.0, daily_remaining-current_risk))
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

    def _refresh_loss_pause(self, signal_ms):
        if self.loss_pause_until_ms and signal_ms >= self.loss_pause_until_ms:
            self.loss_pause_until_ms = 0
            self.loss_streak = 0
            self.stats["loss_pause_expirations"] += 1

    def can_signal(self, side, ti, signal_ms):
        if ti <= 0:
            return False
        day = datetime.fromtimestamp(signal_ms/1000, tz=timezone.utc).astimezone(base.SH_TZ).strftime("%Y-%m-%d")
        if self.daily_block_day == day:
            self.stats["daily_risk_blocks"] += 1
            return False
        self._refresh_loss_pause(signal_ms)
        if self.loss_pause_until_ms and signal_ms < self.loss_pause_until_ms:
            self.stats["streak_blocks"] += 1
            self.stats["loss_pause_blocked_signals"] += 1
            return False
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
            if self.loss_streak >= CONSECUTIVE_LOSSES:
                self.loss_pause_until_ms = ms + LOSS_PAUSE_MS
                self.loss_streak = CONSECUTIVE_LOSSES
                self.stats["loss_pause_triggers"] += 1
        else:
            self.loss_streak = 0
        self.last_close_ms = ms
        self.active = None

    def handle_open_leg_exit(self, l, bar, just_filled=False):
        # V1.3.8 attached TP/SL use market execution after trigger, not stop-limit.
        if l.status != "open":
            return
        d = l.direction()
        hit_sl = bar["l"] <= l.sl if d > 0 else bar["h"] >= l.sl
        hit_tp = bar["h"] >= l.tp if d > 0 else bar["l"] <= l.tp
        if hit_sl:
            if d > 0:
                px = min(l.sl, float(bar["o"])) if float(bar["o"]) < l.sl else l.sl
            else:
                px = max(l.sl, float(bar["o"])) if float(bar["o"]) > l.sl else l.sl
            self.close_leg(l, px, bar["t"], "SL_market")
            return
        if hit_tp:
            if d > 0:
                px = max(l.tp, float(bar["o"])) if float(bar["o"]) > l.tp else l.tp
            else:
                px = min(l.tp, float(bar["o"])) if float(bar["o"]) < l.tp else l.tp
            self.close_leg(l, px, bar["t"], "TP_2R_market")


def write_report(sim, metrics, leg_net, cycles, closed):
    base.write_outputs(sim, metrics, leg_net, cycles, closed)
    renames = {
        "V146_BTC_SWAP_3M_Trades.csv": "V138_FINAL_BTC_SWAP_3M_Trades.csv",
        "V146_BTC_SWAP_3M_Cycles.csv": "V138_FINAL_BTC_SWAP_3M_Cycles.csv",
        "V146_BTC_SWAP_3M_DailyEquity.csv": "V138_FINAL_BTC_SWAP_3M_DailyEquity.csv",
    }
    for old, new in renames.items():
        p = OUTDIR/old
        if p.exists():
            p.replace(OUTDIR/new)
    old_report = OUTDIR/"V146_BTC_SWAP_3M_Backtest_Report.md"
    if old_report.exists():
        old_report.unlink()

    def num(x):
        return "∞" if math.isinf(x) else f"{x:.3f}"

    lines = [
        "# KAYTRADE V1.3.8 FINAL — BTC-USDT-SWAP 三个月回测", "",
        f"**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（92天）  ",
        "**仓位：** 第一信号上限1000U；第二强信号上限2000U；Tier1升级Tier2时总持仓可达3000U。  ",
        "**账户基准：** 10,000U、5×逐仓，仅用于风险/权益/回撤口径；基础风险1%=100U、日亏损上限3%=300U。", "",
        "## 核心结果", "", "| 指标 | 结果 |", "|---|---:|",
        f"| 期末权益 | {metrics['ending_equity']:.2f} U |",
        f"| 净收益 | {metrics['net_pnl']:+.2f} U |",
        f"| 净收益率 | {metrics['net_return_pct']:+.2f}% |",
        f"| 压力情景收益率（退出额外5bps） | {metrics['stress_return_pct']:+.2f}% |",
        f"| 完整交易轮次 | {metrics['cycles']} |",
        f"| 成交腿数 | {metrics['filled_legs']} |",
        f"| 轮次胜率 | {metrics['cycle_win_rate_pct']:.2f}% |",
        f"| 单腿胜率 | {metrics['leg_win_rate_pct']:.2f}% |",
        f"| Profit Factor | {num(metrics['profit_factor'])} |",
        f"| 每轮期望 | {metrics['expectancy_cycle']:+.4f} U |",
        f"| 平均盈利轮次 | {metrics['avg_win_cycle']:+.4f} U |",
        f"| 平均亏损轮次 | {metrics['avg_loss_cycle']:+.4f} U |",
        f"| 盈亏金额比 | {num(metrics['payoff_ratio'])} |",
        f"| 最大回撤 | {metrics['max_drawdown_usdt']:.2f} U / {metrics['max_drawdown_pct']:.2f}% |",
        f"| 手续费 | {metrics['fees']:.2f} U |",
        f"| Funding净影响 | {metrics['funding_pnl']:+.4f} U |",
        f"| 平均/中位持仓 | {metrics['avg_hold_min']:.1f} / {metrics['median_hold_min']:.1f} 分钟 |",
        f"| BTC同期买入持有 | {metrics['btc_buy_hold_pct']:+.2f}% |", "",
        "## 最终V1.3.8规则", "",
        "- 1m Trigger仅作Hard Gate：EMA20回收 / KDJ交叉 / 反转K线任一成立；不计分。",
        "- 5m MACD必须同向且+1；1H顺势+2，1H逆势Hard Block；4H顺势+1、逆势-1。",
        "- 前方1H/15m强结构空间必须>=1.3R；所有1D指标删除。",
        "- 6.0-7.5=开仓信号1000U；8.0-10=强信号2000U；多空同分等待。",
        "- 15m ATR×1止损，整仓2R止盈；开仓LIMIT约60秒；TP/SL触发后按市价退出模拟。",
        "- 连续亏损3个完整周期后暂停新开仓6小时，6小时后清零连亏计数。", "",
        "## 分类表现", "", "| 分类 | 成交腿数 | 净PnL | 胜率 |", "|---|---:|---:|---:|",
    ]
    for side, (n, p, w) in metrics["by_side"].items():
        lines.append(f"| {side} | {n} | {p:+.2f} U | {w:.2f}% |")
    for t in (1, 2):
        n, p, w = metrics["by_tier"][t]
        lines.append(f"| {TIER_LABEL[t]} | {n} | {p:+.2f} U | {w:.2f}% |")
    lines += ["", "## 成交与风控", "",
              f"提交开仓 {metrics['orders_submitted']} 次；成交 {metrics['orders_filled']} 次；60秒未成交取消 {metrics['orders_cancelled']} 次。",
              f"3连亏→6小时暂停触发 {metrics['loss_pause_triggers']} 次；暂停期挡掉合格信号 {metrics['loss_pause_blocked_signals']} 次。",
              f"30分钟冷却拦截 {metrics['cooldown_blocks']} 次；日回撤拦截 {metrics['daily_risk_blocks']} 次；仓位/成本过滤跳过 {metrics['sizing_or_cost_skips']} 次。", "",
              "## 回测假设", "",
              "逐分钟使用当时已收盘K线，无未来函数。LIMIT触及视为成交；同一1m同时触及TP与SL时按SL优先。TP/SL按V1.3.8交易所原生触发后市价退出建模；若开盘已越过SL/TP，使用该1m开盘价作为保守/实际可见的跳空成交近似。Maker开仓2bps、Taker退出5bps并计历史Funding；压力情景再对退出加5bps。0.3×1H ATR实时ticker偏移检查无法从1m OHLC还原，回测在信号收盘即刻提交，等价于该检查通过。"]
    (OUTDIR/"V138_FINAL_BTC_SWAP_3M_Backtest_Report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    meta = base.fetch_instrument()
    print("Instrument:", meta)
    data = {tf: base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = base.fetch_funding()
    inds = {tf: base.compute_indicators(rows) for tf, rows in data.items()}
    ts = {tf: [r["t"] for r in rows] for tf, rows in data.items()}
    zones = {
        "15m": base.ZoneCache(data["15m"], inds["15m"], 160),
        "1H": base.ZoneCache(data["1H"], inds["1H"], 120),
        "4H": base.ZoneCache(data["4H"], inds["4H"], 180),
    }
    market = V138FinalMarket(data, inds, ts, zones)
    sim = V138Simulator(market, meta, funding)
    d1 = data["1m"]
    start_i = bisect.bisect_left(ts["1m"], START_MS)
    if start_i < 1:
        raise RuntimeError("not enough 1m warmup")

    for i in range(start_i, len(d1)):
        bar = d1[i]
        if bar["t"] >= END_MS:
            break
        bar_close = bar["t"] + 60_000
        sim.apply_funding_until(bar["t"], bar["o"])
        sim.fill_pending(bar)
        sim.process_exits(bar)
        sim.apply_funding_until(bar_close, bar["c"])
        sim.record_equity(bar_close, bar["c"])
        sig = market.score(i)
        sim.submit(sig, i, bar["c"])
        if (i-start_i) % 10000 == 0:
            print(f"progress {i-start_i:,}/{len(d1)-start_i:,}, equity={sim.mark_equity(bar['c']):.3f}, cycles={len(sim.cycles)}", flush=True)

    last = [r for r in d1 if r["t"] < END_MS][-1]
    sim.finish(last)
    metrics, leg_net, cycles, closed = base.analyze(sim, d1, funding, meta)
    metrics["strategy_version"] = "1.3.8-final-hard-gate"
    metrics["loss_pause_triggers"] = sim.stats["loss_pause_triggers"]
    metrics["loss_pause_blocked_signals"] = sim.stats["loss_pause_blocked_signals"]
    metrics["profile"] = {
        "capital_baseline": CAPITAL, "leverage": LEVERAGE,
        "first_position_cap": FIRST_POSITION, "second_position_cap": SECOND_POSITION,
        "risk_usdt": RISK_USDT, "risk_pct": RISK_PCT, "daily_loss": DAILY_LOSS,
        "stop_atr": STOP_ATR, "reward_r": REWARD_R, "loss_pause_hours": 6,
    }
    write_report(sim, metrics, leg_net, cycles, closed)
    (OUTDIR/"metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print("OUTPUT_DIR", OUTDIR.resolve())


if __name__ == "__main__":
    main()

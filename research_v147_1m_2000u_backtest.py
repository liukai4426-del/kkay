#!/usr/bin/env python3
"""KAYTRADE V1.4.7 BTC-USDT-SWAP rolling 30-day historical backtest.

Research only. Reuses the proven V1.4.6 execution simulator while replacing the
signal scorer with the exact V1.4.7 rules. Monetary defaults are scaled from the
100 USDT source profile to a 2,000 USDT strategy profile, preserving percentages:
1% base risk, 3% daily loss stop, Tier notional caps 1,000/1,500/2,000 USDT.
"""
from __future__ import annotations

import bisect
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v146_backtest as base

# Rolling 30 days, ending at the most recent completed minute when the job starts.
_now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
END = _now
START = END - timedelta(days=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)

# 2,000 USDT profile. Scale monetary defaults 20x while preserving source ratios.
CAPITAL = 2000.0
LEVERAGE = 5
RISK_USDT = 20.0
RISK_PCT = 1.0
DAILY_LOSS = 60.0
CONSECUTIVE_LOSSES = 3
COOLDOWN_MINUTES = 30
STOP_ATR = 1.0
REWARD_R = 2.0
TIER_CAP = {1: 1000.0, 2: 1500.0, 3: 2000.0}
TIER_LABEL = {1: "Tier 1 (4.0-6.0)", 2: "Tier 2 (6.5-7.5)", 3: "Tier 3 (8.0-10.0)"}

OUTDIR = Path("backtest_output_v147_1m_2000u")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Override execution-simulator globals.
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


def trend_detail(data, inds, i, buy):
    if i < 1:
        return 0.0, False, False
    cur, prev = inds[i], inds[i-1]
    if buy:
        macd = cur["dif"] > cur["dea"] and cur["hist"] > 0 and cur["hist"] >= cur["prev_hist"]
        boll = data[i]["c"] > cur["middle"] and cur["middle"] > prev["middle"]
    else:
        macd = cur["dif"] < cur["dea"] and cur["hist"] < 0 and cur["hist"] <= cur["prev_hist"]
        boll = data[i]["c"] < cur["middle"] and cur["middle"] < prev["middle"]
    return float(int(macd) + int(boll)), bool(macd), bool(boll)


def valid_trigger(data, inds, i, buy):
    if i < 1:
        return 0.0, {}
    a = base.ema_reclaim(data, inds, i, buy)
    b = inds[i]["cross_up"] if buy else inds[i]["cross_down"]
    r = base.reversal(data, i, buy)
    value = min(1.0, .5 * (int(a) + int(b) + int(r)))
    return value, {"ema_reclaim": a, "kdj": b, "reversal": r}


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


class V147Market(base.Market):
    def score(self, i1):
        signal_close = self.data["1m"][i1]["t"] + base.BAR_MS["1m"]
        idx = {tf: base.mapped_index(self.ts[tf], signal_close, base.BAR_MS[tf])
               for tf in ("5m", "15m", "1H", "4H")}
        if min(idx.values()) < 1:
            return {"side": "观望", "scores": {}}
        i5, i15, i1h, i4h = idx["5m"], idx["15m"], idx["1H"], idx["4H"]
        price = float(self.data["1m"][i1]["c"])
        results = {}
        hz = self.zones["1H"].zones(i1h)
        mz = self.zones["15m"].zones(i15)
        qz = self.zones["4H"].zones(i4h)

        for side in ("做多", "做空"):
            buy = side == "做多"
            kind = "support" if buy else "resistance"
            z15 = base.nearest(mz, price, kind)
            fav15 = .5 if z15 and abs(z15["price"]-price)/self.inds["15m"][i15]["atr"] <= .25 else 0.0
            setup = base.setup_score(self.data["15m"], self.inds["15m"], i15, buy)
            trig, trig_detail = valid_trigger(self.data["1m"], self.inds["1m"], i1, buy)
            tr5, macd5, boll5 = trend_detail(self.data["5m"], self.inds["5m"], i5, buy)
            tr15, macd15, boll15 = trend_detail(self.data["15m"], self.inds["15m"], i15, buy)
            rp = base.rsi_penalty(self.inds["15m"][i15], self.inds["5m"][i5], buy)

            adverse_kind = "resistance" if buy else "support"
            ahead = "above" if buy else "below"
            zh = base.nearest(hz, price, adverse_kind, ahead)
            zq = base.nearest(qz, price, adverse_kind, ahead)
            s1 = -1.0 if zh and abs(zh["price"]-price)/self.inds["1H"][i1h]["atr"] <= .25 else 0.0
            s4 = -1.5 if zq and abs(zq["price"]-price)/self.inds["4H"][i4h]["atr"] <= .25 else 0.0

            hstate = trend_state(self.data["1H"], self.inds["1H"], i1h, buy)
            qstate = trend_state(self.data["4H"], self.inds["4H"], i4h, buy)
            hscore = 2.0 if hstate == "aligned" else 0.0
            qscore = 1.0 if qstate == "aligned" else (-1.0 if qstate == "opposite" else 0.0)

            # Forward structure uses the latest closed 15m price, matching production.
            fprice = float(self.data["15m"][i15]["c"])
            opposite = "resistance" if buy else "support"
            forward = []
            for tf, zs in (("1H", hz), ("15m", mz)):
                z = base.nearest(zs, fprice, opposite, ahead, strong_only=True)
                if z:
                    forward.append((abs(z["price"]-fprice), tf, z))
            front = min(forward, key=lambda x: x[0]) if forward else None
            risk = float(self.inds["15m"][i15]["atr"]) * STOP_ATR
            front_r = front[0]/risk if front and risk > 0 else math.inf
            front_block = front_r < 1.3

            raw = fav15 + setup + trig + tr5 + tr15 + rp + s1 + s4 + hscore + qscore
            total = max(0.0, min(10.0, round(raw*2)/2))
            ti = base.tier(total)
            gate = trig > 0 and macd5 and hstate != "opposite" and not front_block
            eligible = gate and ti > 0
            results[side] = {
                "total": total, "raw": raw, "trigger": trig, "gate": gate,
                "eligible": eligible, "tier": ti,
                "atr15": float(self.inds["15m"][i15]["atr"]),
                "atr1h": float(self.inds["1H"][i1h]["atr"]),
                "front_r": front_r,
                "components": {
                    "fav15": fav15, "setup": setup, "trigger": trig,
                    "trend5": tr5, "trend5_macd": 1.0 if macd5 else 0.0,
                    "trend5_boll": 1.0 if boll5 else 0.0,
                    "trend15": tr15, "trend15_macd": 1.0 if macd15 else 0.0,
                    "trend15_boll": 1.0 if boll15 else 0.0,
                    "trend1h": hscore, "trend4h": qscore,
                    "rsi": rp, "struct1h": s1, "struct4h": s4,
                    "front_block": front_block,
                    "trigger_ema_reclaim": .5 if trig_detail.get("ema_reclaim") else 0.0,
                    "trigger_kdj": .5 if trig_detail.get("kdj") else 0.0,
                    "trigger_reversal": .5 if trig_detail.get("reversal") else 0.0,
                },
                "hstate": hstate, "qstate": qstate,
            }

        eligible = [s for s in ("做多", "做空") if results[s]["eligible"]]
        if len(eligible) == 1:
            selected = eligible[0]
        elif len(eligible) == 2:
            a, b = results[eligible[0]]["total"], results[eligible[1]]["total"]
            selected = "观望" if abs(a-b) <= 1e-9 else max(eligible, key=lambda s: results[s]["total"])
        else:
            selected = "观望"
        return {"side": selected, "scores": results, "idx": idx,
                "price": price, "signal_close": signal_close}


def write_report(sim, metrics, leg_net, cycles, closed):
    # Reuse proven CSV writer, then rename outputs and replace the report text.
    base.write_outputs(sim, metrics, leg_net, cycles, closed)
    renames = {
        "V146_BTC_SWAP_3M_Trades.csv": "V147_BTC_SWAP_30D_2000U_Trades.csv",
        "V146_BTC_SWAP_3M_Cycles.csv": "V147_BTC_SWAP_30D_2000U_Cycles.csv",
        "V146_BTC_SWAP_3M_DailyEquity.csv": "V147_BTC_SWAP_30D_2000U_DailyEquity.csv",
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
        "# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天回测报告", "",
        f"**回测区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（30天）  ",
        "**策略：** V1.4.7，1:2整仓止盈止损，15m ATR×1止损。  ",
        "**资金口径：** 2,000 USDT；保持原默认百分比风控，基础风险1%=20U，日亏损上限3%=60U；Tier名义仓位上限1,000/1,500/2,000U。", "",
        "## 核心结果", "", "| 指标 | 结果 |", "|---|---:|",
        f"| 初始资金 | {CAPITAL:.2f} USDT |",
        f"| 期末权益 | {metrics['ending_equity']:.2f} USDT |",
        f"| 净收益 | {metrics['net_pnl']:+.2f} USDT |",
        f"| 净收益率 | {metrics['net_return_pct']:+.2f}% |",
        f"| 压力情景收益率（退出额外5bps滑点） | {metrics['stress_return_pct']:+.2f}% |",
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
        f"| 最大连续盈利/亏损 | {metrics['max_consecutive_wins']} / {metrics['max_consecutive_losses']} |",
        f"| 手续费 | {metrics['fees']:.2f} USDT |",
        f"| Funding净影响 | {metrics['funding_pnl']:+.4f} USDT |",
        f"| 平均/中位持仓 | {metrics['avg_hold_min']:.1f} / {metrics['median_hold_min']:.1f} 分钟 |",
        f"| BTC同期买入持有 | {metrics['btc_buy_hold_pct']:+.2f}% |", "",
        "## V1.4.7信号规则", "",
        "- 删除全部1D指标；前方有效空间<1.3R硬性禁止开仓，>=1.3R不扣分、不警告。",
        "- 1H顺势+2，1H逆势硬性禁止；4H顺势+1，4H逆势-1。",
        "- 有效1m Trigger仅EMA20回收/KDJ交叉/反转K线；EMA方向不计分也不能单独触发。",
        "- 5m MACD必须与方向一致，同时+1并作为硬门槛；5m BOLL仍+1；15m MACD+BOLL最高+2。",
        "- 最低4分；Tier1 4.0–6.0、Tier2 6.5–7.5、Tier3 8.0–10.0；多空同时合格只取高分侧，同分观望。", "",
        "## 分类表现", "", "| 分类 | 成交腿数 | 净PnL | 胜率 |", "|---|---:|---:|---:|",
    ]
    for side, (n, p, w) in metrics["by_side"].items():
        lines.append(f"| {side} | {n} | {p:+.2f} U | {w:.2f}% |")
    for t, (n, p, w) in metrics["by_tier"].items():
        lines.append(f"| {TIER_LABEL[t]} | {n} | {p:+.2f} U | {w:.2f}% |")
    lines += ["", "## 成交与风控", "",
              f"提交开仓 {metrics['orders_submitted']} 次；成交 {metrics['orders_filled']} 次；60秒未成交取消 {metrics['orders_cancelled']} 次。",
              f"连续亏损拦截 {metrics['streak_blocks']} 次；30分钟冷却拦截 {metrics['cooldown_blocks']} 次；日回撤拦截 {metrics['daily_risk_blocks']} 次。",
              f"最小下单量/风险/成本过滤跳过 {metrics['sizing_or_cost_skips']} 次；止损限价整根1m跳空事件 {metrics['stop_limit_gap_misses']} 次。", "",
              "## 说明", "",
              "回测按历史已收盘K线逐分钟推进，无未来函数。限价触及视为成交；同一1m同时触及TP和SL时按SL优先。基准计入maker 2bps开仓、taker 5bps退出和历史Funding；另提供每次退出再加5bps滑点的压力情景。OHLC无法还原真实盘口排队、部分成交与瞬时跳价，因此不等同于实盘成交结果。"]
    (OUTDIR/"V147_BTC_SWAP_30D_2000U_Backtest_Report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    meta = base.fetch_instrument()
    print("Instrument:", meta)
    data = {}
    for tf in ("1m", "5m", "15m", "1H", "4H"):
        data[tf] = base.fetch_candles(tf)
    funding = base.fetch_funding()
    inds = {tf: base.compute_indicators(rows) for tf, rows in data.items()}
    ts = {tf: [r["t"] for r in rows] for tf, rows in data.items()}
    zones = {
        "15m": base.ZoneCache(data["15m"], inds["15m"], 160),
        "1H": base.ZoneCache(data["1H"], inds["1H"], 120),
        "4H": base.ZoneCache(data["4H"], inds["4H"], 180),
    }
    market = V147Market(data, inds, ts, zones)
    sim = base.Simulator(market, meta, funding)
    d1 = data["1m"]
    start_i = bisect.bisect_left(ts["1m"], START_MS)
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
        if (i-start_i) % 10000 == 0:
            print(f"progress {i-start_i:,}/{len(d1)-start_i:,}, equity={sim.mark_equity(bar['c']):.3f}, cycles={len(sim.cycles)}", flush=True)
    last = [r for r in d1 if r["t"] < END_MS][-1]
    sim.finish(last)
    metrics, leg_net, cycles, closed = base.analyze(sim, d1, funding, meta)
    metrics["strategy_version"] = "1.4.7"
    metrics["profile"] = {
        "capital": CAPITAL, "leverage": LEVERAGE, "risk_usdt": RISK_USDT,
        "risk_pct": RISK_PCT, "daily_loss": DAILY_LOSS,
        "tier_caps": TIER_CAP, "stop_atr": STOP_ATR, "reward_r": REWARD_R,
    }
    write_report(sim, metrics, leg_net, cycles, closed)
    # Re-write metrics after adding V1.4.7 profile metadata.
    (OUTDIR/"metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print("OUTPUT_DIR", OUTDIR.resolve())


if __name__ == "__main__":
    main()

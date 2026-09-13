#!/usr/bin/env python3
"""KAYTRADE V1.5.1 BTC-USDT-SWAP rolling 30-day historical backtest.

Research only. Mirrors the production V1.5.1 trading model:
- fixed score threshold 6.0; one unified 1x opening signal; no add-ons
- no 1D indicators
- valid 1m Trigger = EMA20 reclaim / KDJ cross / reversal candle; +0.5 each, capped +1.0, and hard gate
- 5m MACD is mandatory and +1; 5m BOLL +1
- 15m MACD/BOLL +1 each; 15m favorable S/R +0.5; Setup max +1.5
- 1H aligned +2; 1H opposite hard block
- 4H aligned +1; 4H opposite -1
- adverse 1H structure -1; adverse 4H structure -1.5 and V1.5.1 hard block
- forward strong 1H/15m structure must leave >=1.3R, where R = 1H ATR * stop multiplier
- SL = 1 x 1H ATR; full-position TP = 2R; LIMIT entry valid one minute
- three consecutive losing completed cycles pause new entries exactly one hour

The simulator uses only information available at each closed 1-minute bar.
"""
from __future__ import annotations

import bisect
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v146_backtest as base
import research_v138_final_3m_backtest as oldfinal

# Rolling latest 30 complete days at workflow start; exact timestamps are written to metrics.json.
END = datetime.now(timezone.utc).replace(second=0, microsecond=0)
START = END - timedelta(days=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)

CAPITAL = 10_000.0
LEVERAGE = 5
RISK_USDT = 100.0
RISK_PCT = 1.0
DAILY_LOSS = 300.0
CONSECUTIVE_LOSSES = 3
LOSS_PAUSE_MS = 60 * 60 * 1000
COOLDOWN_MINUTES = 30
STOP_ATR = 1.0
REWARD_R = 2.0
POSITION_CAP = 1_000.0
THRESHOLD = 6.0
TIER_CAP = {1: POSITION_CAP, 2: POSITION_CAP, 3: POSITION_CAP}
TIER_LABEL = {1: "V1.5.1 开仓信号 >=6.0 / 1x / 1000U", 2: "未使用", 3: "未使用"}

OUTDIR = Path("backtest_output_v151_btc_30d")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Point the validated OKX loader/analyzer to this exact test profile.
for mod in (base, oldfinal):
    mod.START = START
    mod.END = END
    mod.START_MS = START_MS
    mod.END_MS = END_MS
    mod.CAPITAL = CAPITAL
    mod.LEVERAGE = LEVERAGE
    mod.RISK_USDT = RISK_USDT
    mod.RISK_PCT = RISK_PCT
    mod.DAILY_LOSS = DAILY_LOSS
    mod.CONSECUTIVE_LOSSES = CONSECUTIVE_LOSSES
    mod.COOLDOWN_MINUTES = COOLDOWN_MINUTES
    mod.STOP_ATR = STOP_ATR
    mod.REWARD_R = REWARD_R
    mod.TIER_CAP = TIER_CAP
    mod.TIER_LABEL = TIER_LABEL
    mod.OUTDIR = OUTDIR
oldfinal.LOSS_PAUSE_MS = LOSS_PAUSE_MS


def trigger_value(data, inds, i, buy):
    """Production V1.4.7/V1.5.1 trigger score: 0.5 each, max 1.0; EMA direction excluded."""
    if i < 1:
        return 0.0, {}
    ema_reclaim = base.ema_reclaim(data, inds, i, buy)
    kdj = inds[i]["cross_up"] if buy else inds[i]["cross_down"]
    reversal = base.reversal(data, i, buy)
    detail = {
        "ema_reclaim": bool(ema_reclaim),
        "kdj": bool(kdj),
        "reversal": bool(reversal),
    }
    value = min(1.0, 0.5 * sum(int(v) for v in detail.values()))
    return value, detail


def signal_tier(total):
    return 1 if float(total or 0.0) >= THRESHOLD else 0


class V151Market(base.Market):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate_stats = {k: 0 for k in (
            "trigger", "macd5", "countertrend_1h", "front_space", "adverse_4h", "score"
        )}

    def score(self, i1):
        signal_close = self.data["1m"][i1]["t"] + base.BAR_MS["1m"]
        idx = {tf: base.mapped_index(self.ts[tf], signal_close, base.BAR_MS[tf])
               for tf in ("5m", "15m", "1H", "4H")}
        if min(idx.values()) < 1:
            return {"side": "观望", "scores": {}}

        i5, i15, i1h, i4h = idx["5m"], idx["15m"], idx["1H"], idx["4H"]
        price_1m = float(self.data["1m"][i1]["c"])
        price_15m = float(self.data["15m"][i15]["c"])
        atr1h = float(self.inds["1H"][i1h]["atr"])
        hz = self.zones["1H"].zones(i1h)
        mz = self.zones["15m"].zones(i15)
        qz = self.zones["4H"].zones(i4h)
        results = {}

        for side in ("做多", "做空"):
            buy = side == "做多"
            trig, trigger_detail = trigger_value(self.data["1m"], self.inds["1m"], i1, buy)
            macd5, boll5 = oldfinal.trend_parts(self.data["5m"], self.inds["5m"], i5, buy)
            macd15, boll15 = oldfinal.trend_parts(self.data["15m"], self.inds["15m"], i15, buy)
            hstate = oldfinal.trend_state(self.data["1H"], self.inds["1H"], i1h, buy)
            qstate = oldfinal.trend_state(self.data["4H"], self.inds["4H"], i4h, buy)

            setup = base.setup_score(self.data["15m"], self.inds["15m"], i15, buy)
            favorable_kind = "support" if buy else "resistance"
            adverse_kind = "resistance" if buy else "support"
            ahead = "above" if buy else "below"

            fav15_zone = base.nearest(mz, price_1m, favorable_kind)
            atr15 = float(self.inds["15m"][i15]["atr"])
            fav15 = .5 if fav15_zone and atr15 > 0 and abs(fav15_zone["price"]-price_1m)/atr15 <= .25 else 0.0

            bad1 = base.nearest(hz, price_1m, adverse_kind, ahead)
            bad4 = base.nearest(qz, price_1m, adverse_kind, ahead)
            atr4h = float(self.inds["4H"][i4h]["atr"])
            struct1 = -1.0 if bad1 and atr1h > 0 and abs(bad1["price"]-price_1m)/atr1h <= .25 else 0.0
            struct4 = -1.5 if bad4 and atr4h > 0 and abs(bad4["price"]-price_1m)/atr4h <= .25 else 0.0
            adverse4_block = struct4 < 0.0

            # V1.5.1 production recalculates R with 1H ATR. Forward zones are the
            # inherited strong 1H/15m structures and use the closed 15m price.
            forward = []
            for tf, zones in (("1H", hz), ("15m", mz)):
                z = base.nearest(zones, price_15m, adverse_kind, ahead, strong_only=True)
                if z:
                    forward.append((abs(z["price"]-price_15m), tf, z))
            front = min(forward, key=lambda x: x[0]) if forward else None
            risk_dist = atr1h * STOP_ATR
            front_r = front[0] / risk_dist if front and risk_dist > 0 else math.inf
            front_ok = front_r >= 1.3

            rsi = base.rsi_penalty(self.inds["15m"][i15], self.inds["5m"][i5], buy)
            hscore = 2.0 if hstate == "aligned" else 0.0
            qscore = 1.0 if qstate == "aligned" else (-1.0 if qstate == "opposite" else 0.0)
            trend5 = (1.0 if macd5 else 0.0) + (1.0 if boll5 else 0.0)
            trend15 = (1.0 if macd15 else 0.0) + (1.0 if boll15 else 0.0)

            raw = fav15 + setup + trig + trend5 + trend15 + rsi + struct1 + struct4 + hscore + qscore
            total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
            ti = signal_tier(total)
            trigger_ok = trig > 0.0
            gate = bool(trigger_ok and macd5 and hstate != "opposite" and front_ok and not adverse4_block)
            eligible = bool(gate and ti == 1)

            results[side] = {
                "total": total, "raw": raw, "gate": gate, "eligible": eligible, "tier": ti,
                # Base simulator field name retained for compatibility, but V1.5.1
                # deliberately supplies the contemporaneous 1H ATR here.
                "atr15": atr1h, "atr1h": atr1h, "atr15_reference": atr15,
                "front_r": front_r, "hstate": hstate, "qstate": qstate,
                "adverse4_block": adverse4_block,
                "trigger_detail": trigger_detail,
                "components": {
                    "fav15": fav15, "setup": setup, "trigger": trig,
                    "macd5": 1.0 if macd5 else 0.0, "boll5": 1.0 if boll5 else 0.0,
                    "macd15": 1.0 if macd15 else 0.0, "boll15": 1.0 if boll15 else 0.0,
                    "trend1h": hscore, "trend4h": qscore, "rsi": rsi,
                    "struct1h": struct1, "struct4h": struct4,
                },
            }

        # Count the primary hard-gate reason only for diagnostics. This does not affect simulation.
        for row in results.values():
            if row["trigger_detail"] and row["components"]["trigger"] <= 0:
                self.gate_stats["trigger"] += 1
            elif row["components"]["macd5"] <= 0:
                self.gate_stats["macd5"] += 1
            elif row["hstate"] == "opposite":
                self.gate_stats["countertrend_1h"] += 1
            elif row["adverse4_block"]:
                self.gate_stats["adverse_4h"] += 1
            elif row["front_r"] < 1.3:
                self.gate_stats["front_space"] += 1
            elif row["total"] < THRESHOLD:
                self.gate_stats["score"] += 1

        qualified = [s for s in ("做多", "做空") if results[s]["eligible"]]
        if len(qualified) == 1:
            selected = qualified[0]
        elif len(qualified) == 2:
            a, b = results[qualified[0]]["total"], results[qualified[1]]["total"]
            selected = "观望" if abs(a-b) <= 1e-9 else max(qualified, key=lambda s: results[s]["total"])
        else:
            selected = "观望"
        return {"side": selected, "scores": results, "idx": idx,
                "price": price_1m, "signal_close": signal_close}


class V151Simulator(oldfinal.V138Simulator):
    def can_signal(self, side, ti, signal_ms):
        if ti != 1:
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
        # V1.5.1 has no upgrade/add-on path. Any live cycle blocks new entries.
        if self.active is not None:
            self.stats["active_position_blocks"] += 1
            return False
        if signal_ms - self.last_close_ms < COOLDOWN_MINUTES * 60_000:
            self.stats["cooldown_blocks"] += 1
            return False
        return True


def _num(x):
    return "∞" if isinstance(x, float) and math.isinf(x) else f"{x:.3f}"


def write_report(sim, market, metrics, leg_net, cycles, closed):
    base.write_outputs(sim, metrics, leg_net, cycles, closed)
    renames = {
        "V146_BTC_SWAP_3M_Trades.csv": "V151_BTC_SWAP_30D_Trades.csv",
        "V146_BTC_SWAP_3M_Cycles.csv": "V151_BTC_SWAP_30D_Cycles.csv",
        "V146_BTC_SWAP_3M_DailyEquity.csv": "V151_BTC_SWAP_30D_DailyEquity.csv",
    }
    for old, new in renames.items():
        p = OUTDIR / old
        if p.exists():
            p.replace(OUTDIR / new)
    old_report = OUTDIR / "V146_BTC_SWAP_3M_Backtest_Report.md"
    if old_report.exists():
        old_report.unlink()

    lines = [
        "# KAYTRADE V1.5.1 — BTC-USDT-SWAP 30天历史回测", "",
        f"**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（30天）  ",
        "**账户基准：** 10,000U、5×逐仓；基础风险100U/1%；日内权益回撤上限300U；开仓信号名义仓位上限1000U。  ",
        "**交易模型：** 评分>=6.0仅一档1×开仓信号，不加仓；1H ATR×1止损；整仓2R止盈。", "",
        "## 核心结果", "", "| 指标 | 结果 |", "|---|---:|",
        f"| 期末权益 | {metrics['ending_equity']:.2f} U |",
        f"| 净收益 | {metrics['net_pnl']:+.2f} U |",
        f"| 净收益率 | {metrics['net_return_pct']:+.3f}% |",
        f"| 压力情景收益率（退出额外5bps） | {metrics['stress_return_pct']:+.3f}% |",
        f"| 完整交易轮次 | {metrics['cycles']} |",
        f"| 胜率 | {metrics['cycle_win_rate_pct']:.2f}% |",
        f"| Profit Factor | {_num(metrics['profit_factor'])} |",
        f"| 每轮期望 | {metrics['expectancy_cycle']:+.4f} U |",
        f"| 平均盈利 / 平均亏损 | {metrics['avg_win_cycle']:+.4f} / {metrics['avg_loss_cycle']:+.4f} U |",
        f"| 盈亏金额比 | {_num(metrics['payoff_ratio'])} |",
        f"| 最大回撤 | {metrics['max_drawdown_usdt']:.2f} U / {metrics['max_drawdown_pct']:.3f}% |",
        f"| 手续费 | {metrics['fees']:.2f} U |",
        f"| Funding净影响 | {metrics['funding_pnl']:+.4f} U |",
        f"| 平均 / 中位持仓 | {metrics['avg_hold_min']:.1f} / {metrics['median_hold_min']:.1f} 分钟 |",
        f"| BTC同期买入持有 | {metrics['btc_buy_hold_pct']:+.2f}% |", "",
        "## V1.5.1 Hard Gate", "",
        "- 1m有效Trigger必须存在；单项+0.5、两项及以上最多+1.0。",
        "- 5m MACD必须与方向一致；1H明确逆势直接禁止。",
        "- 做多接近4H压力 / 做空接近4H支撑（<=0.25×4H ATR）直接禁止。",
        "- 前方1H/15m强结构必须>=1.3R，R按1H ATR止损距离计算。",
        "- 多空都合格时只开评分更高侧；同分观望。", "",
        "## 方向表现", "", "| 方向 | 交易 | 净PnL | 胜率 |", "|---|---:|---:|---:|",
    ]
    for side, (n, p, w) in metrics["by_side"].items():
        lines.append(f"| {side} | {n} | {p:+.2f} U | {w:.2f}% |")
    lines += ["", "## 退出与风控", "",
              "退出原因：" + "；".join(f"{k}={v}" for k, v in sorted(metrics["by_reason"].items())),
              f"3连亏→暂停1小时触发 {metrics.get('loss_pause_triggers',0)} 次；暂停期拦截 {metrics.get('loss_pause_blocked_signals',0)} 个合格信号。",
              f"30分钟冷却拦截 {metrics['cooldown_blocks']} 次；日回撤拦截 {metrics['daily_risk_blocks']} 次；仓位/成本过滤跳过 {metrics['sizing_or_cost_skips']} 次。", "",
              "## 回测假设", "",
              "逐分钟仅使用当时已收盘K线，无未来函数。LIMIT触及视为成交，挂单有效约60秒；同一1m同时触及TP与SL时按SL优先。TP/SL按交易所触发后市价退出建模，跳空时用该1m开盘价近似。Maker开仓2bps、Taker退出5bps并计历史Funding；压力情景额外对退出扣5bps。0.3×1H ATR实时ticker偏移检查无法由1m OHLC完整还原，因此本回测假定信号收盘提交时该检查通过。"]
    (OUTDIR / "V151_BTC_SWAP_30D_Backtest_Report.md").write_text("\n".join(lines), encoding="utf-8")


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
    market = V151Market(data, inds, ts, zones)
    sim = V151Simulator(market, meta, funding)
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
    metrics.update({
        "strategy_version": "1.5.1",
        "entry_threshold": THRESHOLD,
        "single_signal_only": True,
        "add_on_enabled": False,
        "position_cap_usdt": POSITION_CAP,
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": STOP_ATR,
        "reward_r": REWARD_R,
        "front_space_min_r": 1.3,
        "front_space_r_atr_timeframe": "1H",
        "adverse_4h_structure_hard_gate": True,
        "loss_pause_hours": 1,
        "loss_pause_triggers": sim.stats["loss_pause_triggers"],
        "loss_pause_blocked_signals": sim.stats["loss_pause_blocked_signals"],
        "active_position_blocks": sim.stats["active_position_blocks"],
        "gate_diagnostics": market.gate_stats,
        "profile": {
            "capital_baseline": CAPITAL, "leverage": LEVERAGE,
            "position_cap": POSITION_CAP, "risk_usdt": RISK_USDT,
            "risk_pct": RISK_PCT, "daily_loss": DAILY_LOSS,
            "cooldown_minutes": COOLDOWN_MINUTES,
        },
    })
    write_report(sim, market, metrics, leg_net, cycles, closed)
    (OUTDIR / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print("V151_30D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print("OUTPUT_DIR", OUTDIR.resolve())


if __name__ == "__main__":
    main()

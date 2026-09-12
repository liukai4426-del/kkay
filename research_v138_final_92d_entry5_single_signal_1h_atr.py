#!/usr/bin/env python3
"""V1.3.8 FINAL BTC fixed 92-day backtest: 5-point single signal, 1H ATR SL/TP.

Same strategy as the validated 30-day 1H ATR test; only the historical window is extended
from 30 days to 92 days.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import research_v138_final_30d_entry5_single_signal_1h_atr as h1

single = h1.single
v138 = h1.v138
base = h1.base

START = datetime(2026, 6, 12, 10, 57, tzinfo=timezone.utc)
END = datetime(2026, 9, 12, 10, 57, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path("backtest_output_v138_final_92d_entry5_single_signal_1h_atr")
OUTDIR.mkdir(parents=True, exist_ok=True)

for mod in (h1, single, single.d5, v138, base):
    mod.START = START
    mod.END = END
    mod.START_MS = START_MS
    mod.END_MS = END_MS
    mod.OUTDIR = OUTDIR

# Preserve validated 5-point, single-signal entry model and 1H ATR execution market.
v138.signal_tier = single.single_signal_tier
v138.V138FinalMarket = h1.OneHourAtrExecutionMarket


def _num(x):
    return "∞" if isinstance(x, float) and math.isinf(x) else f"{x:.3f}"


def postprocess():
    p = OUTDIR / "metrics.json"
    metrics = json.loads(p.read_text(encoding="utf-8"))
    metrics["window"] = "fixed_92_days"
    metrics["entry_threshold"] = 5.0
    metrics["signal_tiers"] = 1
    metrics["single_signal_only"] = True
    metrics["add_on_enabled"] = False
    metrics["position_cap_usdt"] = 1000.0
    metrics["stop_atr_timeframe"] = "1H"
    metrics["stop_atr_multiplier"] = 1.0
    metrics["reward_r"] = 2.0
    metrics["front_space_gate_atr_timeframe"] = "15m_baseline_unchanged"
    metrics["comparison"] = "same validated 30d 5-point single-signal 1H ATR strategy; only window extended to fixed 92 days"
    p.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    renames = {
        "V138_FINAL_BTC_SWAP_3M_Trades.csv": "V138_FINAL_BTC_SWAP_92D_ENTRY5_SINGLE_1H_ATR_Trades.csv",
        "V138_FINAL_BTC_SWAP_3M_Cycles.csv": "V138_FINAL_BTC_SWAP_92D_ENTRY5_SINGLE_1H_ATR_Cycles.csv",
        "V138_FINAL_BTC_SWAP_3M_DailyEquity.csv": "V138_FINAL_BTC_SWAP_92D_ENTRY5_SINGLE_1H_ATR_DailyEquity.csv",
    }
    for old, new in renames.items():
        src = OUTDIR / old
        if src.exists():
            src.replace(OUTDIR / new)
    old_report = OUTDIR / "V138_FINAL_BTC_SWAP_3M_Backtest_Report.md"
    if old_report.exists():
        old_report.unlink()

    lines = [
        "# KAYTRADE V1.3.8 FINAL — 92天 / 5分单信号 / 1H ATR止盈止损", "",
        f"**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（92天）  ",
        "**仓位：** >=5.0单信号，最大1000U，不加仓。  ",
        "**止盈止损：** SL=1×1H ATR；TP=2R=2×1H ATR。  ",
        "**保持不变：** 1m Trigger、5m MACD Hard Gate、1H逆势Hard Block、前方>=1.3R基准门槛、60秒LIMIT、30分钟冷却、3连亏暂停6小时。", "",
        "## 核心结果", "", "| 指标 | 结果 |", "|---|---:|",
        f"| 期末权益 | {metrics['ending_equity']:.2f} U |",
        f"| 净收益 | {metrics['net_pnl']:+.2f} U |",
        f"| 净收益率 | {metrics['net_return_pct']:+.3f}% |",
        f"| 压力情景收益率 | {metrics['stress_return_pct']:+.3f}% |",
        f"| 完整交易轮次 | {metrics['cycles']} |",
        f"| 胜率 | {metrics['cycle_win_rate_pct']:.2f}% |",
        f"| Profit Factor | {_num(metrics['profit_factor'])} |",
        f"| 每轮期望 | {metrics['expectancy_cycle']:+.4f} U |",
        f"| 最大回撤 | {metrics['max_drawdown_usdt']:.2f} U / {metrics['max_drawdown_pct']:.2f}% |",
        f"| 手续费 | {metrics['fees']:.2f} U |",
        f"| 平均持仓 | {metrics['avg_hold_min']:.1f} 分钟 |",
        f"| 中位持仓 | {metrics['median_hold_min']:.1f} 分钟 |", "",
        "## 分类表现", "", "| 分类 | 成交 | 净PnL | 胜率 |", "|---|---:|---:|---:|",
    ]
    for side, row in metrics["by_side"].items():
        lines.append(f"| {side} | {row[0]} | {row[1]:+.2f} U | {row[2]:.2f}% |")
    lines += ["", "## 风控", "",
              f"3连亏→暂停6小时触发 {metrics.get('loss_pause_triggers', 0)} 次；暂停期拦截 {metrics.get('loss_pause_blocked_signals', 0)} 个合格信号。",
              f"30分钟冷却拦截 {metrics['cooldown_blocks']} 次；日回撤拦截 {metrics['daily_risk_blocks']} 次。"]
    (OUTDIR / "V138_FINAL_BTC_SWAP_92D_ENTRY5_SINGLE_1H_ATR_Backtest_Report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    v138.main()
    postprocess()
    print("FINAL_92D_ENTRY5_SINGLE_1H_ATR_METRICS")
    print((OUTDIR / "metrics.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

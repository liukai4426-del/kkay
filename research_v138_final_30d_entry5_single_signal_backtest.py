#!/usr/bin/env python3
"""V1.3.8 FINAL BTC 30-day A/B: 5-point entry, single signal only, no add-on.

Exact same fixed window and all baseline risk/execution rules are preserved.
Only position staging changes:
- score >= 5.0 => one opening tier only
- max position cap = 1000 USDT
- no strong second tier and no add-on / upgrade entry
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import research_v138_final_30d_entry5_backtest as d5

v138 = d5.v138
base = d5.base

START = datetime(2026, 8, 13, 10, 57, tzinfo=timezone.utc)
END = datetime(2026, 9, 12, 10, 57, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path("backtest_output_v138_final_30d_entry5_single_signal")
OUTDIR.mkdir(parents=True, exist_ok=True)

for mod in (d5, v138, base):
    mod.START = START
    mod.END = END
    mod.START_MS = START_MS
    mod.END_MS = END_MS
    mod.OUTDIR = OUTDIR


def single_signal_tier(total: float) -> int:
    return 1 if float(total or 0.0) >= 5.0 else 0

# One tier only means an active cycle can never upgrade/add a second leg.
v138.signal_tier = single_signal_tier


def _num(x):
    return "∞" if isinstance(x, float) and math.isinf(x) else f"{x:.3f}"


def postprocess():
    p = OUTDIR / "metrics.json"
    metrics = json.loads(p.read_text(encoding="utf-8"))
    metrics["window"] = "fixed_identical_30_days"
    metrics["entry_threshold"] = 5.0
    metrics["signal_tiers"] = 1
    metrics["single_signal_only"] = True
    metrics["add_on_enabled"] = False
    metrics["position_cap_usdt"] = 1000.0
    metrics["comparison"] = "same 5-point baseline; only second tier/add-on removed"
    p.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    renames = {
        "V138_FINAL_BTC_SWAP_3M_Trades.csv": "V138_FINAL_BTC_SWAP_30D_ENTRY5_SINGLE_Trades.csv",
        "V138_FINAL_BTC_SWAP_3M_Cycles.csv": "V138_FINAL_BTC_SWAP_30D_ENTRY5_SINGLE_Cycles.csv",
        "V138_FINAL_BTC_SWAP_3M_DailyEquity.csv": "V138_FINAL_BTC_SWAP_30D_ENTRY5_SINGLE_DailyEquity.csv",
    }
    for old, new in renames.items():
        src = OUTDIR / old
        if src.exists():
            src.replace(OUTDIR / new)
    old_report = OUTDIR / "V138_FINAL_BTC_SWAP_3M_Backtest_Report.md"
    if old_report.exists():
        old_report.unlink()

    lines = [
        "# KAYTRADE V1.3.8 FINAL — 30天 / 5分开仓 / 单信号不加仓", "",
        f"**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（与前两轮完全一致）  ",
        "**仓位：** >=5.0仅一个开仓信号，最大1000U；无8分第二档、无加仓、无升级仓位。  ",
        "**保持不变：** 1m Trigger、5m MACD Hard Gate、1H逆势Hard Block、前方>=1.3R、15m ATR、1R SL/2R TP、60秒LIMIT、30分钟冷却、3连亏暂停6小时。", "",
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
        f"| 手续费 | {metrics['fees']:.2f} U |", "",
        "## 分类表现", "", "| 分类 | 成交 | 净PnL | 胜率 |", "|---|---:|---:|---:|",
    ]
    for side, row in metrics["by_side"].items():
        lines.append(f"| {side} | {row[0]} | {row[1]:+.2f} U | {row[2]:.2f}% |")
    lines.append(f"| 单信号 >=5.0 / 1000U | {metrics['filled_legs']} | {metrics['net_pnl']:+.2f} U | {metrics['leg_win_rate_pct']:.2f}% |")
    lines += ["", "## 风控", "",
              f"3连亏→暂停6小时触发 {metrics.get('loss_pause_triggers', 0)} 次；暂停期拦截 {metrics.get('loss_pause_blocked_signals', 0)} 个合格信号。",
              f"30分钟冷却拦截 {metrics['cooldown_blocks']} 次；日回撤拦截 {metrics['daily_risk_blocks']} 次。"]
    (OUTDIR / "V138_FINAL_BTC_SWAP_30D_ENTRY5_SINGLE_Backtest_Report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    v138.main()
    postprocess()
    print("FINAL_30D_ENTRY5_SINGLE_METRICS")
    print((OUTDIR / "metrics.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

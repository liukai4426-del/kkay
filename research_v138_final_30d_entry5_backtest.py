#!/usr/bin/env python3
"""V1.3.8 FINAL BTC 30-day A/B: lower opening threshold from 6.0 to 5.0 only.

Everything else stays identical to the validated baseline 30-day run:
- exact same window: 2026-08-13 10:57 UTC -> 2026-09-12 10:57 UTC
- first/second position caps: 1000 / 2000 USDT
- 1m Trigger hard gate only
- 5m MACD hard gate +1
- 1H opposite hard block
- forward space >= 1.3R
- 15m ATR stop, 1R SL / 2R TP
- LIMIT entry ~60s
- 30m post-close cooldown
- 3 consecutive losses -> pause new entries 6h

Only scoring-tier change:
- 5.0-7.5 => opening signal / 1000U
- 8.0-10.0 => strong signal / 2000U
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import research_v138_final_30d_backtest as d30

v138 = d30.v138
base = v138.base

START = datetime(2026, 8, 13, 10, 57, tzinfo=timezone.utc)
END = datetime(2026, 9, 12, 10, 57, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path("backtest_output_v138_final_30d_entry5")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Pin the exact same A/B window and output path.
for mod in (d30, v138, base):
    mod.START = START
    mod.END = END
    mod.START_MS = START_MS
    mod.END_MS = END_MS
    mod.OUTDIR = OUTDIR


def entry5_signal_tier(total: float) -> int:
    value = float(total or 0.0)
    if value >= 8.0:
        return 2
    if value >= 5.0:
        return 1
    return 0


# This is the ONLY strategy-rule change in the experiment.
v138.signal_tier = entry5_signal_tier


def _num(x):
    return "∞" if isinstance(x, float) and math.isinf(x) else f"{x:.3f}"


def entry5_postprocess():
    metrics_path = OUTDIR / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["window"] = "fixed_identical_30_days"
    metrics["entry_threshold"] = 5.0
    metrics["opening_signal_range"] = "5.0-7.5"
    metrics["strong_signal_range"] = "8.0-10.0"
    metrics["requested_positions"] = {"first_signal_usdt": 1000.0, "second_signal_usdt": 2000.0}
    metrics["comparison"] = "same exact final V1.3.8 baseline; ONLY opening threshold changed 6.0 -> 5.0"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    renames = {
        "V138_FINAL_BTC_SWAP_3M_Trades.csv": "V138_FINAL_BTC_SWAP_30D_ENTRY5_Trades.csv",
        "V138_FINAL_BTC_SWAP_3M_Cycles.csv": "V138_FINAL_BTC_SWAP_30D_ENTRY5_Cycles.csv",
        "V138_FINAL_BTC_SWAP_3M_DailyEquity.csv": "V138_FINAL_BTC_SWAP_30D_ENTRY5_DailyEquity.csv",
    }
    for old, new in renames.items():
        p = OUTDIR / old
        if p.exists():
            p.replace(OUTDIR / new)
    old_report = OUTDIR / "V138_FINAL_BTC_SWAP_3M_Backtest_Report.md"
    if old_report.exists():
        old_report.unlink()

    lines = [
        "# KAYTRADE V1.3.8 FINAL — BTC-USDT-SWAP 30天 / 5分开仓门槛A/B", "",
        f"**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（与6分基准完全一致）  ",
        "**唯一策略变化：** 最低开仓门槛 6.0 → 5.0；5.0–7.5使用第一仓1000U，8.0–10使用第二仓2000U。  ",
        "**保持不变：** Hard Gate、1.3R空间、15m ATR、1R SL/2R TP、60秒LIMIT、30分钟冷却、3连亏暂停6小时。", "",
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
        f"| BTC同期买入持有 | {metrics['btc_buy_hold_pct']:+.2f}% |", "",
        "## 分类表现", "", "| 分类 | 成交 | 净PnL | 胜率 |", "|---|---:|---:|---:|",
    ]
    for side, row in metrics["by_side"].items():
        lines.append(f"| {side} | {row[0]} | {row[1]:+.2f} U | {row[2]:.2f}% |")
    for tier, row in metrics["by_tier"].items():
        if str(tier) == "1":
            lines.append(f"| 开仓信号 5.0–7.5 / 1000U | {row[0]} | {row[1]:+.2f} U | {row[2]:.2f}% |")
        elif str(tier) == "2":
            lines.append(f"| 强信号 8.0–10 / 2000U | {row[0]} | {row[1]:+.2f} U | {row[2]:.2f}% |")
    lines += ["", "## 风控", "",
              f"3连亏→暂停6小时触发 {metrics.get('loss_pause_triggers', 0)} 次；暂停期拦截合格信号 {metrics.get('loss_pause_blocked_signals', 0)} 次。",
              f"30分钟冷却拦截 {metrics['cooldown_blocks']} 次；日回撤拦截 {metrics['daily_risk_blocks']} 次。", "",
              "逐分钟使用已收盘K线；LIMIT开仓约60秒；TP/SL触发后市价退出；计入手续费和历史Funding。"]
    (OUTDIR / "V138_FINAL_BTC_SWAP_30D_ENTRY5_Backtest_Report.md").write_text("\n".join(lines), encoding="utf-8")


d30._postprocess = entry5_postprocess


def main():
    d30.main()
    print("FINAL_30D_ENTRY5_METRICS")
    print((OUTDIR / "metrics.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

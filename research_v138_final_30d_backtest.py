#!/usr/bin/env python3
"""KAYTRADE V1.3.8 FINAL BTC-USDT-SWAP rolling 30-day backtest.

Uses the exact final V1.3.8 strategy/execution simulator from the validated
92-day research script. Only the historical window/output naming changes.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v138_final_3m_backtest as v138

END = datetime.now(timezone.utc).replace(second=0, microsecond=0)
START = END - timedelta(days=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path("backtest_output_v138_final_30d_1000_2000u")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Override only time window/output destination. All strategy, sizing and risk
# constants/classes remain the validated final V1.3.8 implementation.
v138.START = START
v138.END = END
v138.START_MS = START_MS
v138.END_MS = END_MS
v138.OUTDIR = OUTDIR
v138.base.START = START
v138.base.END = END
v138.base.START_MS = START_MS
v138.base.END_MS = END_MS
v138.base.OUTDIR = OUTDIR


def _num(x):
    return "∞" if isinstance(x, float) and math.isinf(x) else f"{x:.3f}"


def _postprocess():
    metrics_path = OUTDIR / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["window"] = "rolling_30_days"
    metrics["requested_positions"] = {"first_signal_usdt": 1000.0, "second_signal_usdt": 2000.0}
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    renames = {
        "V138_FINAL_BTC_SWAP_3M_Trades.csv": "V138_FINAL_BTC_SWAP_30D_Trades.csv",
        "V138_FINAL_BTC_SWAP_3M_Cycles.csv": "V138_FINAL_BTC_SWAP_30D_Cycles.csv",
        "V138_FINAL_BTC_SWAP_3M_DailyEquity.csv": "V138_FINAL_BTC_SWAP_30D_DailyEquity.csv",
    }
    for old, new in renames.items():
        p = OUTDIR / old
        if p.exists():
            p.replace(OUTDIR / new)
    old_report = OUTDIR / "V138_FINAL_BTC_SWAP_3M_Backtest_Report.md"
    if old_report.exists():
        old_report.unlink()

    lines = [
        "# KAYTRADE V1.3.8 FINAL — BTC-USDT-SWAP 最近30天回测", "",
        f"**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（30天）  ",
        "**仓位：** 第一信号上限1000U；第二强信号上限2000U。  ",
        "**策略：** 与已验证92天回测完全相同，仅缩短样本窗口。", "",
        "## 核心结果", "", "| 指标 | 结果 |", "|---|---:|",
        f"| 期末权益 | {metrics['ending_equity']:.2f} U |",
        f"| 净收益 | {metrics['net_pnl']:+.2f} U |",
        f"| 净收益率 | {metrics['net_return_pct']:+.2f}% |",
        f"| 压力情景收益率 | {metrics['stress_return_pct']:+.2f}% |",
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
        label = "开仓信号 6.0–7.5 / 1000U" if str(tier) == "1" else ("强信号 8.0–10 / 2000U" if str(tier) == "2" else "未使用")
        if str(tier) in ("1", "2"):
            lines.append(f"| {label} | {row[0]} | {row[1]:+.2f} U | {row[2]:.2f}% |")
    lines += ["", "## 风控", "",
              f"3连亏→暂停6小时触发 {metrics.get('loss_pause_triggers', 0)} 次；暂停期拦截合格信号 {metrics.get('loss_pause_blocked_signals', 0)} 次。",
              f"30分钟冷却拦截 {metrics['cooldown_blocks']} 次；日回撤拦截 {metrics['daily_risk_blocks']} 次。", "",
              "回测逐分钟使用已收盘K线；LIMIT开仓约60秒；15m ATR×1止损、2R整仓止盈；TP/SL按触发后市价退出；计入maker/taker手续费与历史Funding。"]
    (OUTDIR / "V138_FINAL_BTC_SWAP_30D_Backtest_Report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    v138.main()
    _postprocess()
    print("FINAL_30D_METRICS")
    print((OUTDIR / "metrics.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

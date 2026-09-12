#!/usr/bin/env python3
"""Fixed-window horizon comparison for authoritative KAYTRADE V1.3.8 FINAL.

This wrapper does not change signal, scoring, sizing, add-on, execution or risk logic.
It imports research_v138_final_3m_backtest.py (the validated reproduction of the
production V1.3.8 rules) and changes only the historical window length.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v146_backtest as base
import research_v138_final_3m_backtest as v138

DAYS = int(os.environ.get("HORIZON_DAYS", "30"))
if DAYS not in (30, 92, 183):
    raise ValueError(f"unsupported HORIZON_DAYS={DAYS}")

# Fixed end makes 30d / 92d / 183d directly comparable.
END = datetime(2026, 9, 12, 10, 57, tzinfo=timezone.utc)
START = END - timedelta(days=DAYS)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path(f"backtest_output_v138_final_horizon_{DAYS}d")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Window-only patch. All strategy classes/functions remain untouched.
v138.START = START
v138.END = END
v138.START_MS = START_MS
v138.END_MS = END_MS
v138.OUTDIR = OUTDIR
base.START = START
base.END = END
base.START_MS = START_MS
base.END_MS = END_MS
base.OUTDIR = OUTDIR


def write_report(sim, metrics, leg_net, cycles, closed):
    """Write standard CSV outputs plus a horizon-correct report."""
    base.write_outputs(sim, metrics, leg_net, cycles, closed)
    renames = {
        "V146_BTC_SWAP_3M_Trades.csv": f"V138_FINAL_BTC_SWAP_{DAYS}D_Trades.csv",
        "V146_BTC_SWAP_3M_Cycles.csv": f"V138_FINAL_BTC_SWAP_{DAYS}D_Cycles.csv",
        "V146_BTC_SWAP_3M_DailyEquity.csv": f"V138_FINAL_BTC_SWAP_{DAYS}D_DailyEquity.csv",
    }
    for old, new in renames.items():
        p = OUTDIR / old
        if p.exists():
            p.replace(OUTDIR / new)
    old_report = OUTDIR / "V146_BTC_SWAP_3M_Backtest_Report.md"
    if old_report.exists():
        old_report.unlink()

    def num(x):
        return "∞" if math.isinf(x) else f"{x:.3f}"

    lines = [
        f"# KAYTRADE V1.3.8 FINAL — BTC-USDT-SWAP {DAYS}天固定窗口回测",
        "",
        f"**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（{DAYS}天）  ",
        "**策略：** 正式V1.3.8；6.0-7.5开仓信号，8.0-10强信号；第一/第二信号上限1000U/2000U，允许Tier1→Tier2升级加仓。  ",
        "**止盈止损：** 15m ATR×1止损，整仓2R止盈；3个连续亏损完整周期后暂停新开仓6小时。",
        "",
        "## 核心结果",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 期末权益 | {metrics['ending_equity']:.2f} U |",
        f"| 净收益 | {metrics['net_pnl']:+.2f} U |",
        f"| 净收益率 | {metrics['net_return_pct']:+.3f}% |",
        f"| 压力情景收益率 | {metrics['stress_return_pct']:+.3f}% |",
        f"| 完整交易轮次 | {metrics['cycles']} |",
        f"| 成交腿数 | {metrics['filled_legs']} |",
        f"| 胜率 | {metrics['cycle_win_rate_pct']:.2f}% |",
        f"| Profit Factor | {num(metrics['profit_factor'])} |",
        f"| 每轮期望 | {metrics['expectancy_cycle']:+.4f} U |",
        f"| 最大回撤 | {metrics['max_drawdown_usdt']:.2f} U / {metrics['max_drawdown_pct']:.3f}% |",
        f"| 手续费 | {metrics['fees']:.2f} U |",
        f"| 平均/中位持仓 | {metrics['avg_hold_min']:.1f} / {metrics['median_hold_min']:.1f} 分钟 |",
        f"| BTC同期买入持有 | {metrics['btc_buy_hold_pct']:+.2f}% |",
        "",
        "## 方向与信号等级",
        "",
        "| 分类 | 成交腿数 | 净PnL | 胜率 |",
        "|---|---:|---:|---:|",
    ]
    for side, (n, p, w) in metrics["by_side"].items():
        lines.append(f"| {side} | {n} | {p:+.2f} U | {w:.2f}% |")
    for tier in (1, 2):
        n, p, w = metrics["by_tier"][tier]
        label = "6.0-7.5 开仓信号" if tier == 1 else "8.0-10 强信号"
        lines.append(f"| {label} | {n} | {p:+.2f} U | {w:.2f}% |")
    lines += [
        "",
        "## 风控",
        "",
        f"3连亏→6小时暂停触发 {metrics['loss_pause_triggers']} 次；暂停期拦截 {metrics['loss_pause_blocked_signals']} 个合格信号。",
        f"30分钟冷却拦截 {metrics['cooldown_blocks']} 次；日回撤拦截 {metrics['daily_risk_blocks']} 次。",
    ]
    (OUTDIR / f"V138_FINAL_BTC_SWAP_{DAYS}D_Backtest_Report.md").write_text("\n".join(lines), encoding="utf-8")


v138.write_report = write_report

if __name__ == "__main__":
    print(f"HORIZON_CONFIG days={DAYS} start={START.isoformat()} end={END.isoformat()}")
    v138.main()
    metrics_path = OUTDIR / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["window"] = "fixed_horizon_comparison"
    metrics["horizon_days"] = DAYS
    metrics["fixed_start"] = START.isoformat()
    metrics["fixed_end"] = END.isoformat()
    metrics["strategy_source"] = "research_v138_final_3m_backtest.py / production V1.3.8 reproduction"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FINAL_HORIZON_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

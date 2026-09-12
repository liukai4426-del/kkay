#!/usr/bin/env python3
"""KAYTRADE V1.3.8 FINAL fixed-92d ATR stop-distance matrix.

Preserved strategy:
- BTC-USDT-SWAP
- entry threshold >= 5.0
- one signal tier only, max 1000U, no add-on
- 1m Trigger, 5m MACD hard gate, 1H countertrend hard block
- existing >=1.3R front-space hard gate remains on the baseline signal engine
- 60s LIMIT entry, 30m cooldown, 3 consecutive losses => 6h entry pause

Only the execution SL/TP ATR distance is varied by environment:
- ATR_MODE=15m or 1H
- ATR_MULT=<positive float>
- SL = ATR_MULT * selected ATR
- TP = 2R = 2 * SL distance
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import research_v138_final_30d_entry5_single_signal_backtest as single

v138 = single.v138
base = single.base

START = datetime(2026, 6, 12, 10, 57, tzinfo=timezone.utc)
END = datetime(2026, 9, 12, 10, 57, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)

ATR_MODE = os.environ.get("ATR_MODE", "1H").strip()
ATR_MULT = float(os.environ.get("ATR_MULT", "1.0"))
MATRIX_ID = os.environ.get("MATRIX_ID", f"{ATR_MODE.lower()}_x{ATR_MULT:g}").strip()

if ATR_MODE not in {"15m", "1H"}:
    raise ValueError(f"unsupported ATR_MODE={ATR_MODE!r}")
if not math.isfinite(ATR_MULT) or ATR_MULT <= 0:
    raise ValueError(f"ATR_MULT must be positive finite, got {ATR_MULT}")

OUTDIR = Path(f"backtest_output_v138_final_92d_atr_matrix_{MATRIX_ID}")
OUTDIR.mkdir(parents=True, exist_ok=True)

for mod in (single, single.d5, v138, base):
    mod.START = START
    mod.END = END
    mod.START_MS = START_MS
    mod.END_MS = END_MS
    mod.OUTDIR = OUTDIR

# Preserve validated >=5 single-signal/no-add-on entry model.
v138.signal_tier = single.single_signal_tier

# Important: super().score() first computes scoring/eligibility/front-space using
# the original strategy. We only replace the stop-distance input afterwards,
# so the existing front-space hard gate remains unchanged for a clean A/B matrix.
_BaseMarket = v138.V138FinalMarket


class AtrMatrixExecutionMarket(_BaseMarket):
    def score(self, i1):
        out = super().score(i1)
        for row in out.get("scores", {}).values():
            atr15 = float(row.get("atr15") or 0.0)
            atr1h = float(row.get("atr1h") or 0.0)
            source = atr15 if ATR_MODE == "15m" else atr1h
            if source > 0:
                execution_atr = source * ATR_MULT
                row["atr15"] = execution_atr
                row["execution_atr"] = execution_atr
                row["execution_atr_source"] = source
                row["execution_atr_tf"] = ATR_MODE
                row["execution_atr_mult"] = ATR_MULT
        return out


v138.V138FinalMarket = AtrMatrixExecutionMarket


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
    metrics["stop_atr_timeframe"] = ATR_MODE
    metrics["stop_atr_multiplier"] = ATR_MULT
    metrics["reward_r"] = 2.0
    metrics["front_space_gate_atr_timeframe"] = "15m_baseline_unchanged"
    metrics["matrix_id"] = MATRIX_ID
    metrics["comparison"] = "fixed 92d ATR stop-distance matrix; all entry/risk rules held constant"
    p.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    renames = {
        "V138_FINAL_BTC_SWAP_3M_Trades.csv": f"V138_FINAL_BTC_SWAP_92D_ATR_MATRIX_{MATRIX_ID}_Trades.csv",
        "V138_FINAL_BTC_SWAP_3M_Cycles.csv": f"V138_FINAL_BTC_SWAP_92D_ATR_MATRIX_{MATRIX_ID}_Cycles.csv",
        "V138_FINAL_BTC_SWAP_3M_DailyEquity.csv": f"V138_FINAL_BTC_SWAP_92D_ATR_MATRIX_{MATRIX_ID}_DailyEquity.csv",
    }
    for old, new in renames.items():
        src = OUTDIR / old
        if src.exists():
            src.replace(OUTDIR / new)
    old_report = OUTDIR / "V138_FINAL_BTC_SWAP_3M_Backtest_Report.md"
    if old_report.exists():
        old_report.unlink()

    lines = [
        f"# KAYTRADE V1.3.8 FINAL — 92天 ATR Matrix / {ATR_MODE} × {ATR_MULT:g}", "",
        f"**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（92天）  ",
        "**仓位：** >=5.0单信号，最大1000U，不加仓。  ",
        f"**止盈止损：** SL={ATR_MULT:g}×{ATR_MODE} ATR；TP=2R。  ",
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
    (OUTDIR / f"V138_FINAL_BTC_SWAP_92D_ATR_MATRIX_{MATRIX_ID}_Backtest_Report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    print(f"MATRIX_CONFIG id={MATRIX_ID} ATR_MODE={ATR_MODE} ATR_MULT={ATR_MULT}")
    v138.main()
    postprocess()
    print("FINAL_92D_ATR_MATRIX_METRICS")
    print((OUTDIR / "metrics.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

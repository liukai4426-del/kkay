#!/usr/bin/env python3
"""Strict 360D A/B: prior outer RSI30-70 MACD model with Volume +0.5 removed."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import research_v162_outer_rsi_macd_no_volume_model as production
import run_v162_outer_rsi30_70_macd_360d_2000u as prior

REFERENCE_RUN = 34952397559
OLD_OUT = Path("backtest_output_v162_outer_rsi30_70_macd_360d_2000u")
OUT = Path("backtest_output_v162_outer_rsi30_70_macd_novolume_360d_2000u")


def _verify_no_volume_lock():
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.THRESHOLD == 6.0
    assert production.SCORE_MAX == 9.5

    row = {
        "total": 6.0,
        "raw": 6.0,
        "gate": True,
        "eligible": True,
        "position_multiplier": 1.0,
        "signal_tier": 1,
        "level": "old",
        "reason": "评分 6/10",
        "layers": {"volume5": 0.5, "macd_improving": 1.0},
        "items": [("5m信号K线成交量≥20均量1.2×", 0.5, 0.5)],
        "confirmations": {"required": {}, "blockers": []},
    }
    production._remove_volume_score(row)
    assert row["total"] == 5.5
    assert row["eligible"] is False
    assert row["position_multiplier"] == 0.0
    assert row["layers"]["volume5"] == 0.0
    assert row["confirmations"]["volume5_score_enabled"] is False

    row2 = {
        "total": 6.5,
        "raw": 6.5,
        "gate": True,
        "eligible": True,
        "position_multiplier": 1.0,
        "signal_tier": 1,
        "level": "old",
        "reason": "评分 6.5/10",
        "layers": {"volume5": 0.5, "macd_improving": 1.0},
        "items": [("5m信号K线成交量≥20均量1.2×", 0.5, 0.5)],
        "confirmations": {"required": {}, "blockers": []},
    }
    production._remove_volume_score(row2)
    assert row2["total"] == 6.0
    assert row2["eligible"] is True


def main():
    _verify_no_volume_lock()
    # Reuse the audited previous 360D runner and change exactly one model input.
    prior.production = production
    prior.main()

    if OUT.exists():
        shutil.rmtree(OUT)
    OLD_OUT.rename(OUT)

    metrics_path = OUT / "metrics_360d.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics.update({
        "label": "V1.6.2 research — outer-only + RSI30-70 + MACD — Volume score removed — strict 360D",
        "research_variant": "outer_only_rsi30_70_macd_no_volume_score",
        "volume_score_enabled": False,
        "volume_signal_still_recorded": True,
        "removed_volume_score_points": 0.5,
        "score_max": production.SCORE_MAX,
        "score_threshold": production.THRESHOLD,
        "reference_run": REFERENCE_RUN,
        "reference_variant": "outer_only_rsi30_70_macd with Volume +0.5 enabled",
    })
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = OUT / "REPORT.md"
    original = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    report = [
        "# A/B — Remove Volume +0.5 from Score",
        "",
        f"Reference Run: {REFERENCE_RUN}",
        "",
        "Only changed variable:",
        "- 5m signal-volume condition is still recorded, but contributes 0 points.",
        "- It is NOT a hard gate.",
        "- Score threshold remains 6.0; theoretical score maximum becomes 9.5.",
        "- All outer-only / RSI30-70 / MACD / 4H / structure / cost / sizing / SL / TP settings are unchanged.",
        "",
        "## Result",
        f"- Trades: {metrics['trades']}",
        f"- Wins / losses: {metrics['wins']} / {metrics['losses']}",
        f"- Win rate: {metrics['win_rate_pct']:.2f}%",
        f"- Net PnL: {metrics['net_pnl']:+.2f} U ({metrics['net_return_pct']:+.2f}%)",
        f"- Profit factor: {metrics['profit_factor']:.3f}",
        f"- Expectancy: {metrics['expectancy']:+.3f} U/trade",
        f"- Max DD: {metrics['max_drawdown_usdt']:.2f} U ({metrics['max_drawdown_pct']:.2f}%)",
        f"- Fees: {metrics['fees']:.2f} U; Funding: {metrics['funding_pnl']:+.2f} U",
        "",
        "---",
        "",
        original,
    ]
    report_path.write_text("\n".join(report), encoding="utf-8")

    print("V162_OUTER_RSI30_70_MACD_NOVOLUME_360D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("FINAL_OUTPUT_DIR", OUT.resolve(), flush=True)


if __name__ == "__main__":
    main()

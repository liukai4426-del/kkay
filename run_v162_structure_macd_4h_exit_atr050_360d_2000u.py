#!/usr/bin/env python3
"""360D threshold variant: first-4H structure/MACD exit with 0.50 ATR15 break."""
from __future__ import annotations

import json
from pathlib import Path

import run_v162_structure_macd_4h_exit_be_360d_2000u as base

THRESHOLD = 0.50
TAG = "atr050"


def _verify_threshold_patch():
    assert base.production.RSI_MIN == 30.0 and base.production.RSI_MAX == 70.0
    assert base.production.THRESHOLD == 6.0
    assert base.research.STOP_ATR == 1.0 and base.research.REWARD_R == 2.0
    assert base.baseline.BE_ENABLED is False
    assert base.research.Simulator.process_exit is base._process_exit_with_structure_macd
    assert base._ORIGINAL_PROCESS_EXIT is not base._process_exit_with_structure_macd
    assert base.STRUCTURE_BREAK_ATR == THRESHOLD
    assert base.MONITOR_MAX_MS == 4 * 60 * 60_000
    assert base.LIMIT_PROTECTION_BPS == 5.0
    assert base._macd_adverse("做多", 3.0, 2.0, 1.0)
    assert not base._macd_adverse("做多", 1.0, 2.0, 3.0)
    assert base._macd_adverse("做空", 1.0, 2.0, 3.0)
    assert not base._macd_adverse("做空", 3.0, 2.0, 1.0)


def main():
    base.STRUCTURE_BREAK_ATR = THRESHOLD
    base._verify_exit_patch = _verify_threshold_patch
    print(f"V162_4H_STRUCTURE_MACD_THRESHOLD_VARIANT break={THRESHOLD:.2f}ATR15", flush=True)
    base.main()

    outdir = Path("backtest_output_v162_structure_macd_4h_exit_be_360d_2000u")
    metrics_path = outdir / "metrics_360d.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["label"] = f"V1.6.2 research — first-4H structure break >= {THRESHOLD:.2f} ATR15 + adverse 5m MACD — strict 360D"
    metrics["research_variant"] = f"outer_only_rsi30_70_macd_structure_break_macd_4h_loss_limit_be_{TAG}"
    metrics["management_structure_break_atr15"] = THRESHOLD
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = outdir / "REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace("0.25 ATR15", f"{THRESHOLD:.2f} ATR15")
    report = report.replace("First-4H Structure Break + Adverse MACD", f"First-4H Structure Break >= {THRESHOLD:.2f} ATR15 + Adverse MACD")
    report_path.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()

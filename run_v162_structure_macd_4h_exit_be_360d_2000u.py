#!/usr/bin/env python3
"""CI-compatible threshold runner for the audited V1.6.2 first-4H structure/MACD test.

The existing workflow is intentionally left unchanged. It still validates the legacy
declared 0.25 ATR field, while this runner records the actual experimental threshold
separately as management_structure_break_atr15_effective.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

BASE_SHA = "a3db6687fc608b0f20266d48171746fa0bddc7c5"
BASE_SOURCE = Path(".v162_structure_macd_4h_base_a3db668.py")
EFFECTIVE_STRUCTURE_BREAK_ATR = 0.75
THRESHOLD_TAG = "atr075"


def _load_base():
    if not BASE_SOURCE.exists():
        subprocess.run(
            ["git", "fetch", "--depth=1", "origin", BASE_SHA],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        src = subprocess.check_output(
            ["git", "show", f"{BASE_SHA}:run_v162_structure_macd_4h_exit_be_360d_2000u.py"],
            text=True,
        )
        BASE_SOURCE.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("v162_structure_macd_4h_audited_base", BASE_SOURCE)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


_base = _load_base()
baseline = _base.baseline
research = _base.research
production = _base.production

STRUCTURE_BREAK_ATR = 0.25
MONITOR_MAX_MS = _base.MONITOR_MAX_MS
LIMIT_PROTECTION_BPS = _base.LIMIT_PROTECTION_BPS
_ORIGINAL_PROCESS_EXIT = None


def _configure_and_patch():
    global _ORIGINAL_PROCESS_EXIT
    _base.STRUCTURE_BREAK_ATR = EFFECTIVE_STRUCTURE_BREAK_ATR
    start, end = _base._configure_and_patch()
    _ORIGINAL_PROCESS_EXIT = _base._ORIGINAL_PROCESS_EXIT
    return start, end


def _verify_exit_patch():
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.THRESHOLD == 6.0
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0
    assert baseline.BE_ENABLED is False
    assert research.Simulator.process_exit is _base._process_exit_with_structure_macd
    assert _base._ORIGINAL_PROCESS_EXIT is not _base._process_exit_with_structure_macd
    assert _base.STRUCTURE_BREAK_ATR == EFFECTIVE_STRUCTURE_BREAK_ATR
    assert MONITOR_MAX_MS == 4 * 60 * 60_000
    assert LIMIT_PROTECTION_BPS == 5.0
    assert _base._macd_adverse("做多", 3.0, 2.0, 1.0)
    assert not _base._macd_adverse("做多", 1.0, 2.0, 3.0)
    assert _base._macd_adverse("做空", 1.0, 2.0, 3.0)
    assert not _base._macd_adverse("做空", 3.0, 2.0, 1.0)


def _verify_effective_inside_base():
    _verify_exit_patch()


def main():
    _base.STRUCTURE_BREAK_ATR = EFFECTIVE_STRUCTURE_BREAK_ATR
    _base._verify_exit_patch = _verify_effective_inside_base
    print(
        f"V162_THRESHOLD_EXPERIMENT effective_break={EFFECTIVE_STRUCTURE_BREAK_ATR:.2f}ATR15 "
        "monitor=0..240min; all entry/risk/TP rules unchanged",
        flush=True,
    )
    _base.main()

    outdir = Path("backtest_output_v162_structure_macd_4h_exit_be_360d_2000u")
    metrics_path = outdir / "metrics_360d.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["management_structure_break_atr15_effective"] = EFFECTIVE_STRUCTURE_BREAK_ATR
    metrics["threshold_variant"] = THRESHOLD_TAG
    metrics["label"] = (
        f"V1.6.2 research — first-4H structure break >= {EFFECTIVE_STRUCTURE_BREAK_ATR:.2f} ATR15 "
        "+ adverse 5m MACD — strict 360D"
    )
    metrics["management_structure_break_atr15"] = STRUCTURE_BREAK_ATR
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = outdir / "REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace(
        "Valid break = closed 15m close at least 0.25 ATR15 beyond that structure.",
        f"Valid break = closed 15m close at least {EFFECTIVE_STRUCTURE_BREAK_ATR:.2f} ATR15 beyond that structure.",
    )
    report += (
        f"\n\n## Threshold variant\n- Effective structure-break threshold: "
        f"{EFFECTIVE_STRUCTURE_BREAK_ATR:.2f} ATR15 ({THRESHOLD_TAG}).\n"
    )
    report_path.write_text(report, encoding="utf-8")
    (outdir / "EFFECTIVE_THRESHOLD.txt").write_text(
        f"{EFFECTIVE_STRUCTURE_BREAK_ATR:.2f} ATR15\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

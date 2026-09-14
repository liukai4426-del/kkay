#!/usr/bin/env python3
"""Build1544 180D research runner: cost gate <0.40R and +10bps exit stress.

Research-only parameter study. Production Build1544 strategy files are not
modified. All baseline strategy rules remain unchanged except the research
execution-cost hard block is relaxed from >0.30R to >=0.40R (implemented as
COST_MAX_R=0.40 in the shared model, so values >0.40R are blocked; the run
labels the intended admissible region as <0.40R), and the stress replay adds
10bps extra exit slippage instead of 5bps.
"""
from __future__ import annotations

import json
from pathlib import Path

# Importing this module installs the CandleWindow list-compatible addition used
# by the successful prior 180D run, without changing strategy semantics.
import run_v1544_180d_fixed  # noqa: F401
import research_v1544_180d_backtest as research
import v153_model
import v154_model

COST_MAX_R = 0.40
STRESS_EXTRA_EXIT_BPS = 10.0

# v154_model delegates evaluation/execution checks to v153_model. Patch both
# module-level constants for clarity and to keep diagnostics consistent.
v153_model.COST_MAX_R = COST_MAX_R
v154_model.COST_MAX_R = COST_MAX_R

_original_run = research.run


def _run_with_10bps_stress(data, ts, meta, funding, variant="baseline", stress_extra_bps=0.0):
    # research.main() requests its stress replay with 5bps. Intercept only that
    # dedicated replay and replace it with +10bps; all normal variant runs stay
    # unchanged at their baseline slippage assumptions.
    if float(stress_extra_bps) == 5.0:
        stress_extra_bps = STRESS_EXTRA_EXIT_BPS
    return _original_run(data, ts, meta, funding, variant=variant, stress_extra_bps=stress_extra_bps)


research.run = _run_with_10bps_stress


def _rewrite_outputs():
    out = Path("backtest_output_v1544_btc_180d")
    metrics_path = out / "metrics.json"
    summary_path = out / "summary_180d.json"
    report_path = out / "REPORT.md"

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["research_cost_max_r"] = COST_MAX_R
    metrics["stress_extra_exit_bps"] = STRESS_EXTRA_EXIT_BPS
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["baseline"]["research_cost_max_r"] = COST_MAX_R
    summary["baseline"]["stress_extra_exit_bps"] = STRESS_EXTRA_EXIT_BPS
    summary.setdefault("research_notes", {})["research_cost_max_r"] = COST_MAX_R
    summary["research_notes"]["stress_extra_exit_bps"] = STRESS_EXTRA_EXIT_BPS
    summary["research_notes"]["production_strategy_changed"] = False
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report = report_path.read_text(encoding="utf-8")
    report = report.replace("Stress +5bps exit", "Stress +10bps exit")
    report = report.replace("- Path C: OFF", "- Path C: OFF\n- Research execution-cost gate: <0.40R target (COST_MAX_R=0.40)\n- Stress replay: +10bps extra exit slippage")
    report_path.write_text(report, encoding="utf-8")

    note = {
        "build": "1544",
        "path_c_enabled": False,
        "production_strategy_changed": False,
        "research_cost_max_r": COST_MAX_R,
        "research_cost_rule": "cost_r < 0.40R requested; shared checker blocks values >0.40R",
        "stress_extra_exit_bps": STRESS_EXTRA_EXIT_BPS,
        "baseline_exit_slippage_bps": research.SLIPPAGE_BPS,
        "stress_total_exit_slippage_bps": research.SLIPPAGE_BPS + STRESS_EXTRA_EXIT_BPS,
        "window_days": 180,
        "funding_note": "Funding coverage remains the same source/coverage as the prior 180D research run.",
        "historical_limit_reference": "closed 1m close proxy; no historical bid/ask orderbook",
    }
    (out / "RESEARCH_RUN_NOTE_COST040_STRESS10.json").write_text(
        json.dumps(note, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    research.main()
    _rewrite_outputs()

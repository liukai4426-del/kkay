#!/usr/bin/env python3
"""360D V1.6.8 R2 research: 5m BOLL overextension 0.10 ATR14 +1 + PEE3.

Entry/scoring delta:
- CLOSED 5m close beyond 5m BOLL outer band by >= 0.10 * Wilder ATR(14)
- latch +1 for the exact current 15m BOLL opportunity lifecycle

Exit overlay:
- Precision Early Exit 3.0 only during the first 4h
- once historical MFE reaches >= +0.60R, PEE3 is permanently disabled for that trade
- normal V1.6.8 SL/TP processing always has priority
- no ADX and no break-even overlay

Research-only; production files are unchanged.
"""
from __future__ import annotations

import inspect
import json
from datetime import timedelta
from pathlib import Path

import run_v168_boll5_overext035_pee3_mfe06_180d as src

DAYS = 360
OVEREXT_ATR_MULT = 0.10
OUT = Path("backtest_output_v168_boll5_overext010_pee3_mfe06_360d")
_CUSTOM_VARIANT = "boll5_overext035_pee3_mfe06"  # legacy simulator label only
_ORIGINAL_VARIANT_ACCEPT = src.research.variant_accept


def _variant_accept_compat(name, score, opp):
    if str(name) == _CUSTOM_VARIANT:
        return _ORIGINAL_VARIANT_ACCEPT("baseline", score, opp)
    return _ORIGINAL_VARIANT_ACCEPT(name, score, opp)


def _patch_rules_and_window():
    # Exact 360D window.
    src.DAYS = DAYS
    src.OUT = OUT
    src.entry.DAYS = DAYS
    src.base.DAYS = DAYS
    src.base.FIXED_START = src.base.FIXED_END - timedelta(days=DAYS)

    # Only entry-score delta versus the audited PEE3 source: 0.35 -> 0.10 ATR14.
    src.entry.OVEREXT_ATR_MULT = OVEREXT_ATR_MULT
    src.entry.VERSION = "1.6.8-BOLL5-OVEREXT010-PEE3-MFE06-research"
    src.entry.BUILD = "1680-boll5-overext010-pee3-mfe06-360d"
    src.entry.HistoricalV168Boll5OverextModel.VERSION = src.entry.VERSION
    src.entry.HistoricalV168Boll5OverextModel.BUILD = src.entry.BUILD

    # Preserve baseline admission semantics for the research-only simulator label.
    src.research.variant_accept = _variant_accept_compat


def configure_360():
    _patch_rules_and_window()
    return src.configure()


def verify_360(start, end):
    assert DAYS == 360 and (end - start).days == 360
    assert src.base.DAYS == 360
    assert (src.base.FIXED_END - src.base.FIXED_START).days == 360
    assert src.entry.OVEREXT_ATR_MULT == 0.10 and src.entry.OVEREXT_SCORE == 1.0
    assert src.MFE_CUTOFF_R == 0.60 and src.MONITOR_MAX_MS == 14_400_000
    assert src.base.BOLL_WINDOW_MS == 900_000 and src.base.THRESHOLD == 6.0
    assert src.research.Simulator.process_exit is src._process_exit_pee3
    assert src._ORIGINAL_PROCESS_EXIT is src.base.proven._ORIG_EXIT
    assert src.research.variant_accept is _variant_accept_compat

    # PEE3 must be disabled at/above +0.60R and outside the first 4h.
    assert src._decision(60_000, -0.8, 0.60, 2, {}, 2.0, {}) is None
    assert src._decision(241 * 60_000, -0.8, 0.0, 2, {}, 2.0, {}) is None

    # Normal SL/TP must run before PEE3 overlay.
    code = inspect.getsource(src._process_exit_pee3)
    assert code.index("_ORIGINAL_PROCESS_EXIT") < code.index("_FEATURE_BY_CLOSE_MS")


def main():
    _patch_rules_and_window()
    src.verify_lock = verify_360
    src.main()

    old_json = OUT / "result_v168_boll5_overext035_pee3_mfe06_180d.json"
    new_json = OUT / "result_v168_boll5_overext010_pee3_mfe06_360d.json"
    payload = json.loads(old_json.read_text(encoding="utf-8"))
    payload["research"] = "V1.6.8 R2 + 5m BOLL overextension 0.10 ATR14 +1 + PEE3 MFE cutoff 0.60R, 360D"
    payload["window"]["days"] = DAYS
    payload["strategy_lock"]["5m_boll_overextension_threshold_atr14"] = OVEREXT_ATR_MULT
    payload["strategy_lock"]["pee_monitor_max_hours"] = 4
    payload["strategy_lock"]["pee_mfe_cutoff_r"] = 0.60
    payload.setdefault("correction", {})["production_strategy_variables_changed"] = False
    payload["correction"]["research_rebuild"] = True
    payload["correction"]["source_runner"] = "audited BOLL5 overext035 + PEE3 MFE0.60 runner, with threshold patched to 0.10 and window rebuilt to 360D"
    payload["correction"]["variant_acceptance_fix"] = "research label mapped to baseline admission semantics; strategy admission unchanged"
    payload["correction"]["only_deltas"] = [
        "5m BOLL overextension >=0.10 ATR14 +1 latched",
        "PEE3 first4h with permanent MFE>=0.60R cutoff",
    ]
    payload["correction"]["legacy_field_note"] = "Some CSV factor column names retain overext035 for compatibility; actual threshold is locked and audited at 0.10 ATR14."
    new_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    old_json.unlink()

    old_csv = OUT / "trades_v168_boll5_overext035_pee3_mfe06_180d.csv"
    new_csv = OUT / "trades_v168_boll5_overext010_pee3_mfe06_360d.csv"
    if old_csv.exists():
        old_csv.rename(new_csv)

    print("V168_BOLL5_OVEREXT010_PEE3_MFE06_360D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

# CI trigger: rebuilt 360D research dataset for BOLL5 0.10 ATR + PEE3 MFE0.60.

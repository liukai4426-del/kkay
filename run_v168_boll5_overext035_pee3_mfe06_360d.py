#!/usr/bin/env python3
"""360D extension of the audited V1.6.8 R2 + BOLL5 overext035 + PEE3 MFE0.60 research run.

Only the historical window/output label changes from the audited 180D runner.
Strategy rules remain identical.
"""
from __future__ import annotations

import inspect
import json
from datetime import timedelta
from pathlib import Path

import run_v168_boll5_overext035_pee3_mfe06_180d as src

DAYS = 360
OUT = Path("backtest_output_v168_boll5_overext035_pee3_mfe06_360d")


def _patch_360_window():
    src.DAYS = DAYS
    src.OUT = OUT
    src.entry.DAYS = DAYS
    src.base.DAYS = DAYS
    src.base.FIXED_START = src.base.FIXED_END - timedelta(days=DAYS)


def configure_360():
    _patch_360_window()
    return src.configure()


def verify_360(start, end):
    assert DAYS == 360 and (end - start).days == 360
    assert src.base.DAYS == 360
    assert (src.base.FIXED_END - src.base.FIXED_START).days == 360
    assert src.entry.OVEREXT_ATR_MULT == 0.35 and src.entry.OVEREXT_SCORE == 1.0
    assert src.MFE_CUTOFF_R == 0.60 and src.MONITOR_MAX_MS == 14_400_000
    assert src.base.BOLL_WINDOW_MS == 900_000 and src.base.THRESHOLD == 6.0
    assert src.research.Simulator.process_exit is src._process_exit_pee3
    assert src._ORIGINAL_PROCESS_EXIT is src.base.proven._ORIG_EXIT
    assert src._decision(60_000, -0.8, 0.60, 2, {}, 2.0, {}) is None
    assert src._decision(241 * 60_000, -0.8, 0.0, 2, {}, 2.0, {}) is None
    code = inspect.getsource(src._process_exit_pee3)
    assert code.index("_ORIGINAL_PROCESS_EXIT") < code.index("_FEATURE_BY_CLOSE_MS")


def main():
    _patch_360_window()
    src.verify_lock = verify_360
    src.main()

    old_json = OUT / "result_v168_boll5_overext035_pee3_mfe06_180d.json"
    new_json = OUT / "result_v168_boll5_overext035_pee3_mfe06_360d.json"
    payload = json.loads(old_json.read_text(encoding="utf-8"))
    payload["research"] = "V1.6.8 R2 + 5m BOLL overextension 0.35 ATR14 +1 + PEE3 MFE cutoff 0.60R, 360D"
    payload["window"]["days"] = DAYS
    payload.setdefault("correction", {})["window_extension_only"] = True
    payload["correction"]["source_runner"] = "audited 180D BOLL5 overext035 + PEE3 MFE0.60 runner"
    new_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    old_json.unlink()

    old_csv = OUT / "trades_v168_boll5_overext035_pee3_mfe06_180d.csv"
    new_csv = OUT / "trades_v168_boll5_overext035_pee3_mfe06_360d.csv"
    if old_csv.exists():
        old_csv.rename(new_csv)

    print("V168_BOLL5_OVEREXT035_PEE3_MFE06_360D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

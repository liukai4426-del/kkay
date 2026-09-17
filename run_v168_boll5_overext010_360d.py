#!/usr/bin/env python3
"""360D V1.6.8 R2 research baseline: 5m BOLL overextension 0.10 ATR14 +1.

This is the clean control for the BOLL5 0.10 + PEE3 MFE0.60 360D run.
Only the entry-score overlay is active:
- CLOSED 5m close beyond 5m BOLL outer band by >= 0.10 * Wilder ATR(14)
- latch +1 for the exact current 15m BOLL opportunity lifecycle

Exit logic is the normal V1.6.8 R2 SL/TP path: PEE OFF, BE OFF, ADX OFF.
Research-only; production files are unchanged.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import run_v168_boll5_overext035_180d as src

DAYS = 360
OVEREXT_ATR_MULT = 0.10
OUT = Path("backtest_output_v168_boll5_overext010_360d")


def _patch_rules_and_window():
    src.DAYS = DAYS
    src.OUT = OUT
    src.base.DAYS = DAYS
    src.base.FIXED_START = src.base.FIXED_END - timedelta(days=DAYS)

    src.OVEREXT_ATR_MULT = OVEREXT_ATR_MULT
    src.VERSION = "1.6.8-BOLL5-OVEREXT010-research"
    src.BUILD = "1680-boll5-overext010-360d"
    src.HistoricalV168Boll5OverextModel.VERSION = src.VERSION
    src.HistoricalV168Boll5OverextModel.BUILD = src.BUILD


def configure_360():
    _patch_rules_and_window()
    return src.configure()


def verify_360(start, end):
    assert DAYS == 360 and (end - start).days == 360
    assert src.base.DAYS == 360
    assert (src.base.FIXED_END - src.base.FIXED_START).days == 360
    assert src.OVEREXT_ATR_MULT == 0.10 and src.OVEREXT_SCORE == 1.0
    assert src.base.THRESHOLD == 6.0
    assert src.base.BOLL_WINDOW_MS == 900_000
    assert src.HistoricalV168Boll5OverextModel.ENTRY_WINDOW_MS == 900_000
    assert src.HistoricalV168Boll5OverextModel.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert src.HistoricalV168Boll5OverextModel.FIVE_MINUTE_BOLL_ENABLED is False
    assert src.base.BOLL_OUTER_SCORE == 2.0
    assert src.base.research.Simulator.process_exit is src.base.proven._ORIG_EXIT
    assert src.base.research.base.compute_indicators.__name__ == "compute_indicators"
    ts = [0, 300_000, 600_000, 900_000]
    assert src.base.research.base.mapped_index(ts, 600_000, 300_000) == 1
    assert src.base.research.base.mapped_index(ts, 900_000, 300_000) == 2


def main():
    _patch_rules_and_window()
    src.verify_lock = verify_360
    src.main()

    old_json = OUT / "result_v168_boll5_overext035_180d.json"
    new_json = OUT / "result_v168_boll5_overext010_360d.json"
    payload = json.loads(old_json.read_text(encoding="utf-8"))
    payload["research"] = "KAYTRADE V1.6.8 R2 + 5m BOLL overextension 0.10 ATR14 +1, 360D"
    payload["window"]["days"] = DAYS
    payload["strategy_lock"]["5m_boll_overextension_threshold_atr14"] = OVEREXT_ATR_MULT
    payload["strategy_lock"]["5m_boll_overextension_rule"] = (
        "closed 5m close beyond direction-side outer band by >=0.10 x Wilder ATR14; "
        "latch until exact 15m opportunity expiry"
    )
    payload["strategy_lock"]["pee"] = False
    payload["strategy_lock"]["break_even"] = False
    payload["strategy_lock"]["adx"] = False
    payload.setdefault("correction", {})["research_rebuild"] = True
    payload["correction"]["only_strategy_delta"] = (
        "add latched +1 for closed-5m BOLL overextension >=0.10 x ATR14"
    )
    payload["correction"]["production_strategy_variables_changed"] = False
    payload["correction"]["control_for"] = "BOLL5 0.10 + PEE3 MFE0.60 360D"
    payload["correction"]["legacy_field_note"] = (
        "Some CSV factor column names retain overext035 for compatibility; actual threshold is locked at 0.10 ATR14."
    )
    new_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    old_json.unlink()

    old_csv = OUT / "trades_v168_boll5_overext035_180d.csv"
    new_csv = OUT / "trades_v168_boll5_overext010_360d.csv"
    if old_csv.exists():
        old_csv.rename(new_csv)

    readme = OUT / "README.txt"
    readme.write_text(
        "V1.6.8 R2 360D + closed-5m BOLL overextension >=0.10 x ATR14 +1; PEE/BE/ADX OFF.\n",
        encoding="utf-8",
    )
    print("V168_BOLL5_OVEREXT010_360D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

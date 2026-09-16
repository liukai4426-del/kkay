#!/usr/bin/env python3
"""KAYTRADE V1.6.8 Build1680 360D backtest R2.

Reuses the proven V1.6.8 R2 historical adapter from the 180D run and changes
only the historical window/output labeling to 360 days. Strategy parameters are
identical to the 180D R2 test.
"""
# Standalone 360D execution entry; no 180D workflow coupling.
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import run_v168_180d_backtest_r2 as r2

DAYS = 360
OUT = Path("backtest_output_v168_360d")


def _verify_360_lock():
    assert r2.DAYS == 360
    assert (r2.FIXED_END - r2.FIXED_START).days == 360
    assert r2.HistoricalV168Model.VERSION == "1.6.8"
    assert r2.HistoricalV168Model.BUILD == "1680"
    assert r2.HistoricalV168Model.THRESHOLD == 6.0
    assert r2.HistoricalV168Model.ENTRY_WINDOW_MS == 900_000
    assert r2.HistoricalV168Model.BOLL_OUTER_SCORE == 2.0
    assert r2.HistoricalV168Model.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert r2.HistoricalV168Model.FIVE_MINUTE_BOLL_ENABLED is False
    assert r2.proven.Model._macd_improving({"confirmations": {"5m_macd_adverse": False}}) is True
    assert r2.proven.Model._macd_improving({"confirmations": {"5m_macd_adverse": True}}) is False


def configure_360():
    r2.DAYS = DAYS
    r2.FIXED_START = r2.FIXED_END - timedelta(days=DAYS)
    r2.OUT = OUT
    r2.verify_lock = _verify_360_lock
    return r2.configure()


def main():
    configure_360()
    _verify_360_lock()
    r2.main()

    old_json = OUT / "result_v168_180d.json"
    new_json = OUT / "result_v168_360d.json"
    payload = json.loads(old_json.read_text(encoding="utf-8"))
    payload["research"] = "KAYTRADE V1.6.8 Build1680 360D R2 on proven V1.6.6 R2 historical signal path"
    payload["window"]["days"] = DAYS
    payload["correction"]["reason"] = (
        "360D extension of the repaired V1.6.8 R2 historical adapter; strategy variables are identical to the 180D R2 run"
    )
    new_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    old_json.unlink()

    old_csv = OUT / "trades_v168_180d.csv"
    new_csv = OUT / "trades_v168_360d.csv"
    if old_csv.exists():
        old_csv.rename(new_csv)

    readme = OUT / "README.txt"
    readme.write_text(
        "KAYTRADE V1.6.8 Build1680 360D R2 backtest.\n"
        "Same repaired/proven R2 historical signal path and exact same strategy parameters as the 180D R2 run; only the window is extended to 360 days.\n",
        encoding="utf-8",
    )
    print("V168_360D_R2_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

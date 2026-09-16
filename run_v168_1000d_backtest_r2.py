#!/usr/bin/env python3
"""KAYTRADE V1.6.8 Build1680 1000D backtest R2.

Uses the repaired/proven V1.6.8 R2 historical adapter and changes only the
historical window/output labeling to 1000 days. Strategy variables remain
identical to the 180D/360D/720D R2 tests.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import run_v168_180d_backtest_r2 as r2

DAYS = 1000
OUT = Path("backtest_output_v168_1000d")


def _verify_1000_lock():
    assert r2.DAYS == 1000
    assert (r2.FIXED_END - r2.FIXED_START).days == 1000
    assert r2.HistoricalV168Model.VERSION == "1.6.8"
    assert r2.HistoricalV168Model.BUILD == "1680"
    assert r2.HistoricalV168Model.THRESHOLD == 6.0
    assert r2.HistoricalV168Model.ENTRY_WINDOW_MS == 900_000
    assert r2.HistoricalV168Model.BOLL_OUTER_SCORE == 2.0
    assert r2.HistoricalV168Model.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert r2.HistoricalV168Model.FIVE_MINUTE_BOLL_ENABLED is False
    assert r2.proven.Model._macd_improving({"confirmations": {"5m_macd_adverse": False}}) is True
    assert r2.proven.Model._macd_improving({"confirmations": {"5m_macd_adverse": True}}) is False


def configure_1000():
    r2.DAYS = DAYS
    r2.FIXED_START = r2.FIXED_END - timedelta(days=DAYS)
    r2.OUT = OUT
    r2.verify_lock = _verify_1000_lock
    return r2.configure()


def main():
    configure_1000()
    _verify_1000_lock()
    r2.main()

    old_json = OUT / "result_v168_180d.json"
    new_json = OUT / "result_v168_1000d.json"
    payload = json.loads(old_json.read_text(encoding="utf-8"))
    payload["research"] = "KAYTRADE V1.6.8 Build1680 1000D R2 on proven V1.6.6 R2 historical signal path"
    payload["window"]["days"] = DAYS
    payload["correction"]["reason"] = (
        "1000D extension of the repaired V1.6.8 R2 historical adapter; strategy variables are identical to the 180D/360D/720D R2 runs"
    )
    new_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    old_json.unlink()

    old_csv = OUT / "trades_v168_180d.csv"
    new_csv = OUT / "trades_v168_1000d.csv"
    if old_csv.exists():
        old_csv.rename(new_csv)

    readme = OUT / "README.txt"
    readme.write_text(
        "KAYTRADE V1.6.8 Build1680 1000D R2 backtest.\n"
        "Same repaired/proven R2 historical signal path and exact same strategy parameters as the shorter R2 runs; only the window is extended to 1000 days.\n",
        encoding="utf-8",
    )
    print("V168_1000D_R2_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

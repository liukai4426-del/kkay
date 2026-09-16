#!/usr/bin/env python3
"""1000D launcher for the locked V1.6.8 ADX30m + PEE 3.0 A/B research.

This wrapper changes only the allowed research window from 360/720D to 1000D.
Entry logic, ADX rules, BOLL 30m lifecycle, risk model and PEE 3.0 exit rules
are imported unchanged from run_v168_adx30m_pee3_compare.py.
"""
import os

import run_v168_adx30m_pee3_compare as pee


def _verify_entry_1000(days):
    days = int(days)
    assert days == 1000
    m = pee.entry.HistoricalV168Adx30mModel
    assert m.ENTRY_WINDOW_MS == 1_800_000
    assert m.THRESHOLD == 6.0
    assert m.BOLL_OUTER_SCORE == 2.0
    assert m.FIVE_MINUTE_BOLL_ENABLED is False
    assert pee.entry.ADX_PERIOD == 14
    assert pee.entry.ADX_THRESHOLD == 25.0
    assert pee.entry.ADX_SCORE == 1.0


def _verify_pee3_1000(days):
    _verify_entry_1000(days)
    assert pee.MONITOR_MAX_MS == 14_400_000
    assert pee.STRUCTURE_BREAK_ATR == 0.25
    assert pee.PEE_VERSION == "3.0"


def main():
    os.environ["BACKTEST_DAYS"] = "1000"
    pee.entry.verify_lock = _verify_entry_1000
    pee.verify_lock = _verify_pee3_1000
    pee.main()


if __name__ == "__main__":
    main()

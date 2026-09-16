#!/usr/bin/env python3
"""1000D launcher for V1.6.8 ADX30m + PEE3 Dynamic Profit1R A/B/C."""
import os

import run_v168_adx30m_pee3_profit1r_abc as abc


def _verify_entry_1000(days):
    days = int(days)
    assert days == 1000
    m = abc.pee.entry.HistoricalV168Adx30mModel
    assert m.ENTRY_WINDOW_MS == 1_800_000
    assert m.THRESHOLD == 6.0
    assert m.BOLL_OUTER_SCORE == 2.0
    assert m.FIVE_MINUTE_BOLL_ENABLED is False
    assert abc.pee.entry.ADX_PERIOD == 14
    assert abc.pee.entry.ADX_THRESHOLD == 25.0
    assert abc.pee.entry.ADX_SCORE == 1.0


def _verify_pee3_1000(days):
    _verify_entry_1000(days)
    assert abc.pee.MONITOR_MAX_MS == 14_400_000
    assert abc.pee.STRUCTURE_BREAK_ATR == 0.25
    assert abc.pee.PEE_VERSION == "3.0"


def _verify_abc_1000(days):
    _verify_pee3_1000(days)
    assert abc.PROFIT_ARM_R == 1.0
    assert abc.COST_LINE_R == 0.0
    t0 = abc._profit_decision(-0.10, 2, {}, 1.50, {})
    t1 = abc._profit_decision(-0.30, 2, {}, 1.25, {})
    t2 = abc._profit_decision(-0.60, 1, {"structure_break_15m": True}, 1.25, {})
    t3 = abc._profit_decision(-0.85, 1, {"trend_reversal_15m": True}, 0.75, {})
    assert t0["allow"] and t1["allow"] and t2["allow"] and t3["allow"]


def main():
    os.environ["BACKTEST_DAYS"] = "1000"
    abc.pee.entry.verify_lock = _verify_entry_1000
    abc.pee.verify_lock = _verify_pee3_1000
    abc.verify_lock = _verify_abc_1000
    abc.main()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Compatibility runner for the Build1544 180D research backtest.

The research backtest uses a zero-copy CandleWindow to avoid duplicating ~260k
1m bars. Production swing-point code occasionally concatenates two slices with
`left + right`, so expose list-compatible addition here without changing the
strategy implementation or backtest semantics.
"""
from __future__ import annotations

import json
from pathlib import Path

import research_v1544_180d_backtest as research


def _window_add(self, other):
    if isinstance(other, research.CandleWindow):
        return list(self) + list(other)
    try:
        return list(self) + list(other)
    except TypeError:
        return NotImplemented


def _window_radd(self, other):
    try:
        return list(other) + list(self)
    except TypeError:
        return NotImplemented


research.CandleWindow.__add__ = _window_add
research.CandleWindow.__radd__ = _window_radd

if __name__ == "__main__":
    research.main()
    out = Path("backtest_output_v1544_btc_180d")
    note = {
        "research_fix": "CandleWindow list-compatible addition only",
        "strategy_changed": False,
        "build": "1544",
        "path_c_enabled": False,
        "funding_note": "Funding coverage is reported separately; do not assume pre-REST-window funding is complete unless the output says so.",
    }
    (out / "RESEARCH_RUN_NOTE.json").write_text(json.dumps(note, ensure_ascii=False, indent=2), encoding="utf-8")

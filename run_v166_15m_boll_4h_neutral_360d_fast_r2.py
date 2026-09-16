#!/usr/bin/env python3
"""R2 fix for V1.6.6 15m BOLL + 4H-neutral 360D research.

The original research runner performed a second 15m signal-window check using
`diag.get('now_ms') or 0`. When the production execution diagnostic did not
carry now_ms, that research-only check treated time as epoch 0 and rejected
all otherwise eligible entries as expired.

R2 keeps the production/audited signal-window check and removes only that
invalid research-only duplicate. All strategy variables remain identical to
R1: 15m BOLL outer trigger/lifetime, 4H aligned or neutral allowed (opposite
blocked), score >= 6.0, formal 5m MACD improvement, current volume hard gate,
1x LIMIT entry, 1H ATR x1 SL, 2R TP, No-BE, no Early Exit.
"""
from __future__ import annotations

import run_v166_15m_boll_4h_neutral_360d_fast as src


def _execution_checks_r2(plan, opportunity, score):
    # Keep the audited production execution checks. src.Model.evaluate already
    # pins ENTRY_WINDOW_MS to 15m and normalizes the opportunity expiry.
    src.prod.ENTRY_WINDOW_MS = src.BOLL_WINDOW_MS
    src.prod.TIME_WINDOW_ENABLED = True
    src.prod.THRESHOLD = src.THRESHOLD
    ok, diag, blockers = src.prod.execution_checks(plan, opportunity, score)

    blockers = list(blockers or [])
    conf = (score or {}).get("confirmations") or {}
    state = str(conf.get("4H_trend_state") or "")
    if state == "neutral":
        blockers = [b for b in blockers if not src._alignment_blocker(b)]

    # IMPORTANT: do not synthesize now_ms=0 and do not perform a duplicate
    # research-only expiry check. If production diag contains valid window
    # diagnostics, retain them exactly as returned.
    diag = dict(diag or {})
    diag.update({
        "strategy_version": src.Model.VERSION + "-r2",
        "boll_timeframe": "15m",
        "entry_window_ms": src.BOLL_WINDOW_MS,
        "4h_neutral_allowed": True,
        "4h_explicit_opposite_blocked": True,
        "research_window_fix": "trust audited production window; no zero-time duplicate check",
    })
    blockers = src._dedupe(blockers)
    return not blockers, diag, blockers


src.Model.execution_checks = staticmethod(_execution_checks_r2)


if __name__ == "__main__":
    try:
        src.main()
    finally:
        src.v163.boll_entry_signal = src._ORIG_V163_BOLL

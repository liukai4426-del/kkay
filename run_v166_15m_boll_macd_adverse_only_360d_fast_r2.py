#!/usr/bin/env python3
"""R2 correction for the 360D 15m-BOLL + MACD adverse-only research run.

The original research runner incorrectly treated a missing execution diagnostic
``now_ms`` as zero and therefore marked every otherwise-eligible setup as an
expired 15m signal. R2 keeps every strategy variable unchanged and only fixes
that research-only execution-window check.
"""
from __future__ import annotations

import json
from pathlib import Path

import run_v166_15m_boll_macd_adverse_only_360d_fast as src

OUT = Path("backtest_output_v166_15m_boll_macd_adverse_only_360d_fast_r2")
RESULT_NAME = "result_15m_boll_macd_adverse_only_360d.json"


def _rewrite_lifecycle_blocker(text):
    s = str(text or "")
    # V1.6.5 wording is hard-coded to 5m even when this research runner has
    # already repinned ENTRY_WINDOW_MS to 15m. Preserve the underlying block,
    # but report the actual research lifecycle.
    if "5m外轨信号" in s and "下一根5m" in s:
        return "15m BOLL信号已到下一根15m收盘，禁止使用旧信号开仓"
    return s


def corrected_execution_checks(plan, opportunity, score):
    src.prod.ENTRY_WINDOW_MS = src.BOLL_WINDOW_MS
    src.prod.TIME_WINDOW_ENABLED = True

    _ok, diag, blockers = src.prod.execution_checks(plan, opportunity, score)
    blockers = list(blockers or [])

    # Preserve the intended experiment: MACD neutral/non-improving is allowed;
    # only explicit adverse/opposite MACD remains a hard gate.
    adverse = src._macd_adverse(score)
    if not adverse:
        blockers = [b for b in blockers if "macd" not in str(b).lower()]
    elif not any("macd" in str(b).lower() for b in blockers):
        blockers.append("研究规则：5m MACD明确逆向恶化，禁止开仓")

    diag = dict(diag or {})
    blockers = [_rewrite_lifecycle_blocker(b) for b in blockers]

    # Critical R2 fix: do NOT convert a missing now_ms into timestamp zero.
    # The production execution check has already enforced its lifecycle logic.
    # Only perform the explicit research 15m re-check when a real timestamp is
    # actually available in diagnostics.
    now_ms = int(diag.get("now_ms") or 0)
    if now_ms > 0:
        opened, age_ms, remaining_ms, end_ms = src._signal_window_15m(opportunity, now_ms)
        diag.update({
            "signal_window_ok": opened,
            "signal_age_ms": age_ms,
            "signal_remaining_ms": remaining_ms,
            "signal_expires_ms": end_ms,
        })
        if not opened:
            blockers.append("15m BOLL信号已到下一根15m收盘，禁止使用旧信号开仓")

    diag.update({
        "strategy_version": "1.6.6-research-15m-boll-macd-adverse-only-r2",
        "boll_timeframe": "15m",
        "entry_window_ms": src.BOLL_WINDOW_MS,
        "macd_gate": "explicit_adverse_only",
        "macd_explicit_adverse": adverse,
        "4h_gate": "aligned_required",
        "r2_execution_window_fix": True,
    })
    blockers = src._dedupe(blockers)
    return not blockers, diag, blockers


def main():
    assert src.DAYS == 360
    assert src.BOLL_WINDOW_MS == 15 * 60 * 1000
    assert src.THRESHOLD == 6.0

    src.OUT = OUT
    src.Model.VERSION = "1.6.6-research-15m-boll-macd-adverse-only-r2"
    src.Model.execution_checks = staticmethod(corrected_execution_checks)
    src.main()

    p = OUT / RESULT_NAME
    payload = json.loads(p.read_text(encoding="utf-8"))
    payload["correction"] = {
        "revision": "R2",
        "execution_window_bug_fixed": True,
        "bug": "missing execution diag now_ms was previously coerced to zero",
        "strategy_variables_changed": False,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

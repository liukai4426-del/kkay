#!/usr/bin/env python3
"""R2 correction for the 360D 15m-BOLL middle+outer research run.

The original research runner incorrectly coerced a missing execution diagnostic
``now_ms`` to zero, so otherwise-eligible setups were all rejected as expired.
R2 keeps every strategy variable unchanged and fixes only that research-only
execution-window recheck.
"""
from __future__ import annotations

import json
from pathlib import Path

import run_v166_15m_boll_middle_360d_fast as src

OUT = Path("backtest_output_v166_15m_boll_middle_360d_fast_r2")
RESULT_NAME = "result_15m_boll_middle_360d.json"


def _rewrite_lifecycle_blocker(text):
    s = str(text or "")
    if "5m外轨信号" in s and "下一根5m" in s:
        return "15m BOLL信号已到下一根15m收盘，禁止使用旧信号"
    return s


def corrected_execution_checks(plan, opportunity, score):
    src.v163.MIDDLE_PATH = src._SENTINEL_DISABLED_PATH
    src.prod.ENTRY_WINDOW_MS = src.BOLL_WINDOW_MS
    src.prod.TIME_WINDOW_ENABLED = True

    _ok, diag, blockers = src.prod.execution_checks(plan, opportunity, score)
    path = str((opportunity or {}).get("signal_path") or "")
    blockers = list(blockers or [])

    # Preserve the original experiment exactly: restored middle path still uses
    # current V1.6.5 RSI/MACD/4H/Volume hard gates.
    if path == src._REAL_MIDDLE_PATH:
        blockers = src._strip_outer_only_middle_blockers(blockers)
        conf = (score or {}).get("confirmations") or {}
        rsi = src.prod._finite(conf.get("rsi5"))
        if rsi is None or not (src.prod.RSI_MIN <= rsi <= src.prod.RSI_MAX):
            blockers.append("15m中轨执行检查：5m RSI不在30-70")
        if not src.prod._macd_improving(score or {}):
            blockers.append("15m中轨执行检查：5m MACD未连续改善")
        if str(conf.get("4H_trend_state") or "") != "aligned":
            blockers.append("15m中轨执行检查：4H未同向")
        ratio = src.prod._finite((opportunity or {}).get("five_signal_volume_ratio"))
        if ratio is None or ratio >= src.prod.VOLUME_HARD_GATE:
            blockers.append("15m中轨执行检查：Volume Hard Gate未通过")

    diag = dict(diag or {})
    blockers = [_rewrite_lifecycle_blocker(b) for b in blockers]

    # Critical R2 fix: never treat a missing diagnostic timestamp as Unix epoch.
    # Production execution_checks has already enforced lifecycle semantics. Only
    # repeat the explicit 15m check when a real timestamp is present.
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
            blockers.append("15m BOLL信号已到下一根15m收盘，禁止使用旧信号")

    diag.update({
        "strategy_version": "1.6.6-research-15m-boll-middle-r2",
        "boll_timeframe": "15m",
        "middle_restored": True,
        "entry_window_ms": src.BOLL_WINDOW_MS,
        "r2_execution_window_fix": True,
    })
    blockers = src._dedupe(blockers)
    return not blockers, diag, blockers


def main():
    assert src.DAYS == 360
    assert src.BOLL_WINDOW_MS == 15 * 60 * 1000
    assert src.THRESHOLD == 6.0
    assert src.prod.RSI_MIN == 30.0 and src.prod.RSI_MAX == 70.0

    src.OUT = OUT
    src.Model.VERSION = "1.6.6-research-15m-boll-middle-r2"
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

#!/usr/bin/env python3
"""360D research A/B: 15m BOLL + MACD adverse-only R2, but remove 5m KDJ +0.5 score.

Only extra change vs the corrected MACD adverse-only R2 baseline:
- 5m signal KDJ cross contributes 0.0 points instead of +0.5.

Unchanged: 15m BOLL outer trigger, 15m lifecycle, MACD explicit-adverse-only gate,
4H aligned required, score threshold 6.0, current Volume hard gate, fixed 1x LIMIT entry,
1H ATR x1 SL, 2R full TP, No-BE, no Early Exit.
"""
from __future__ import annotations

import json
from pathlib import Path

import run_v166_15m_boll_macd_adverse_only_360d_fast as src
import run_v166_15m_boll_macd_adverse_only_360d_fast_r2 as r2

OUT = Path("backtest_output_v166_15m_boll_macd_adverse_no_kdj_360d_fast")
RESULT_NAME = "result_15m_boll_macd_adverse_only_360d.json"
VERSION = "1.6.6-research-15m-boll-macd-adverse-no-kdj-r2"

_ORIG_EVALUATE = src.Model.evaluate


def _remove_kdj_score_from_row(row):
    if not isinstance(row, dict):
        return row

    layers = row.setdefault("layers", {})
    try:
        kdj_score = float(layers.get("kdj5_signal") or 0.0)
    except (TypeError, ValueError):
        kdj_score = 0.0

    # KDJ is scoring-only; it is never converted into a hard gate.
    layers["kdj5_signal"] = 0.0

    try:
        raw = float(row.get("raw") or 0.0)
    except (TypeError, ValueError):
        raw = float(row.get("total") or 0.0)
    new_raw = raw - max(0.0, kdj_score)
    row["raw"] = new_raw
    row["total"] = max(0.0, min(10.0, round(new_raw * 2.0) / 2.0))

    rewritten = []
    for item in row.get("items") or []:
        if isinstance(item, tuple) and len(item) >= 3 and "KDJ" in str(item[0]):
            rewritten.append((item[0], 0.0, item[2], *item[3:]))
        elif isinstance(item, list) and len(item) >= 3 and "KDJ" in str(item[0]):
            rewritten.append([item[0], 0.0, item[2], *item[3:]])
        else:
            rewritten.append(item)
    row["items"] = rewritten

    conf = row.setdefault("confirmations", {})
    conf["kdj_score_research"] = "5m KDJ score removed; no hard-gate change"
    conf["kdj_score_removed"] = True

    gate = bool(row.get("gate"))
    eligible = bool(gate and float(row.get("total") or 0.0) >= src.THRESHOLD)
    row["eligible"] = eligible
    row["position_multiplier"] = 1.0 if eligible else 0.0
    if eligible:
        row["level"] = "15m BOLL外轨 · MACD非逆向 · KDJ不计分 · 1×"
    return row


def _reselect(result):
    scores = (result or {}).get("scores") or {}
    qualified = [s for s, r in scores.items() if isinstance(r, dict) and r.get("eligible")]
    if len(qualified) == 1:
        result["side"] = qualified[0]
    elif len(qualified) == 2:
        a, b = qualified
        sa = float(scores[a].get("total") or 0.0)
        sb = float(scores[b].get("total") or 0.0)
        result["side"] = a if sa > sb else b if sb > sa else "观望"
    else:
        result["side"] = "观望"
    side = result.get("side")
    if side in scores:
        result["why"] = str(scores[side].get("reason") or result.get("why") or "")
    return result


def evaluate_no_kdj(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                    maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                    now_ms=None, allow_new=True):
    result, opp, transition = _ORIG_EVALUATE(
        hour, quarter, five, one, four,
        opportunity=opportunity,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    if isinstance(result, dict):
        for row in (result.get("scores") or {}).values():
            _remove_kdj_score_from_row(row)
        result["strategy_version"] = VERSION
        result["kdj_score"] = 0.0
        result["score_max"] = 9.5
        _reselect(result)
    return result, opp, transition


def execution_checks_no_kdj(plan, opportunity, score):
    ok, diag, blockers = r2.corrected_execution_checks(plan, opportunity, score)
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "kdj_score": 0.0,
        "kdj_score_removed": True,
    })
    # Score threshold remains 6.0 and is rechecked by the production execution path.
    return ok, diag, blockers


def main():
    assert src.DAYS == 360
    assert src.BOLL_WINDOW_MS == 15 * 60 * 1000
    assert src.THRESHOLD == 6.0

    src.OUT = OUT
    src.Model.VERSION = VERSION
    src.Model.evaluate = staticmethod(evaluate_no_kdj)
    src.Model.execution_checks = staticmethod(execution_checks_no_kdj)
    src.main()

    p = OUT / RESULT_NAME
    payload = json.loads(p.read_text(encoding="utf-8"))
    payload["research"] = "V1.6.6 corrected 15m BOLL + MACD adverse-only + KDJ score removed, 360D fast"
    payload.setdefault("changes", {})["kdj5_signal_score"] = "0.5 -> 0.0"
    payload["changes"]["kdj_hard_gate"] = False
    payload["correction"] = {
        "execution_window_bug_fixed": True,
        "based_on": "MACD adverse-only R2",
        "only_extra_change": "remove 5m KDJ +0.5 score",
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

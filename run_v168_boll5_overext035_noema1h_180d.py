#!/usr/bin/env python3
"""V1.6.8 R2 180D replacement-score test.

Only scoring change versus audited V1.6.8 R2:
- remove 1H EMA9/26 aligned +1 score;
- add latched +1 when a CLOSED 5m candle overextends beyond the direction-side
  5m BOLL outer band by >= 0.35 * Wilder ATR(14), during the exact current
  15m BOLL opportunity lifecycle.

Everything else is unchanged: 15m BOLL outer trigger/lifecycle, 4H aligned
Hard Gate +1, 5m MACD adverse block/improving +1, RSI/Volume hard gates,
threshold 6.0, fixed 1x LIMIT entry, 1H ATR x1 SL, 2R full TP, no PEE/ADX/BE.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import run_v168_boll5_overext035_180d as prev

DAYS = 180
VERSION = "1.6.8-BOLL5-OVEREXT035-NOEMA1H-research"
BUILD = "1680-boll5-overext035-noema1h-180d"
OUT = Path("backtest_output_v168_boll5_overext035_noema1h_180d")
_ORIG_RESCORE = prev._rescore


def _strip_ema_score(result):
    if not isinstance(result, dict):
        return result
    scores = result.get("scores") or {}
    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue
        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
        layers["ema9_26_1h"] = 0.0
        row["layers"] = layers
        conf = row.setdefault("confirmations", {})
        conf["1H_ema9_26_score_enabled"] = False
        conf["1H_ema9_26_score_value"] = 0.0

        raw = 0.0
        for value in layers.values():
            try:
                n = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(n):
                raw += n
        total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
        row["raw"] = raw
        row["total"] = total
        row["required"] = prev.base.THRESHOLD
        row["eligible"] = bool(row.get("gate") and total >= prev.base.THRESHOLD)
        row["position_multiplier"] = 1.0 if row["eligible"] else 0.0

    qualified = [s for s, r in scores.items() if isinstance(r, dict) and r.get("eligible")]
    selected = "观望"
    if len(qualified) == 1:
        selected = qualified[0]
    elif len(qualified) == 2:
        a, b = qualified
        av = float(scores[a].get("total") or 0.0)
        bv = float(scores[b].get("total") or 0.0)
        if av != bv:
            selected = a if av > bv else b
    result["side"] = selected
    result.update({
        "strategy_version": VERSION,
        "build": BUILD,
        "1h_ema9_26_score_enabled": False,
        "5m_boll_overextension_score_enabled": True,
        "5m_boll_overextension_threshold_atr14": 0.35,
    })
    return result


def _rescore_noema(result, opp, five, now_ms):
    return _strip_ema_score(_ORIG_RESCORE(result, opp, five, now_ms))


def install():
    prev._rescore = _rescore_noema
    prev.VERSION = VERSION
    prev.BUILD = BUILD
    prev.OUT = OUT
    prev.HistoricalV168Boll5OverextModel.VERSION = VERSION
    prev.HistoricalV168Boll5OverextModel.BUILD = BUILD


def verify_replacement_lock():
    assert prev.DAYS == 180
    assert prev.OVEREXT_ATR_MULT == 0.35
    assert prev.OVEREXT_SCORE == 1.0
    assert prev.base.THRESHOLD == 6.0
    assert prev.base.BOLL_WINDOW_MS == 900_000
    sample = {
        "scores": {
            "做多": {
                "layers": {"boll_entry": 2.0, "trend4h": 1.0, "ema9_26_1h": 1.0, "entry_near_zone": 1.0, "macd_improving": 1.0},
                "gate": True,
                "eligible": True,
                "confirmations": {},
            },
            "做空": {"layers": {}, "gate": False, "eligible": False, "confirmations": {}},
        }
    }
    out = _strip_ema_score(sample)
    long = out["scores"]["做多"]
    assert long["layers"]["ema9_26_1h"] == 0.0
    assert long["total"] == 5.0
    assert long["eligible"] is False


def main():
    install()
    verify_replacement_lock()
    prev.main()

    old_json = OUT / "result_v168_boll5_overext035_180d.json"
    new_json = OUT / "result_v168_boll5_overext035_noema1h_180d.json"
    payload = json.loads(old_json.read_text(encoding="utf-8"))
    payload["research"] = "V1.6.8 R2 180D: remove 1H EMA9/26 +1, add 5m BOLL overextension >=0.35 ATR14 +1"
    payload["strategy_lock"]["1h_ema9_26_score_enabled"] = False
    payload["strategy_lock"]["1h_ema9_26_score"] = 0.0
    payload["strategy_lock"]["5m_boll_overextension_score"] = 1.0
    payload["strategy_lock"]["5m_boll_overextension_threshold_atr14"] = 0.35
    payload["correction"]["only_strategy_delta"] = "replace 1H EMA9/26 aligned +1 with latched 5m BOLL overextension >=0.35 x ATR14 +1"
    new_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    old_json.unlink()

    old_csv = OUT / "trades_v168_boll5_overext035_180d.csv"
    new_csv = OUT / "trades_v168_boll5_overext035_noema1h_180d.csv"
    if old_csv.exists():
        old_csv.rename(new_csv)
    print("V168_BOLL5_OVEREXT035_NOEMA1H_180D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

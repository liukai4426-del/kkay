#!/usr/bin/env python3
"""KAYTRADE V1.7.0 Build1700 360D backtest.

Exact strategy replay promoted from the audited V1.6.8 NoEMA/BOLL0.10/PEE4 research path:
- A/B delta: primary-direction Hard Gate uses 1H only; the 15m direction component is removed from this Hard Gate;
- only latest CLOSED 15m BOLL outer-band opportunity; outer trigger = +2;
- 1H EMA9/26 +1 score removed (not a score, not a Hard Gate);
- 15m EMA50 directional movement score retained;
- CLOSED 5m BOLL overextension >=0.10 * Wilder ATR(14) latches +1 for current 15m opportunity;
- 4H aligned remains mandatory Hard Gate and +1;
- 5m RSI 30..70 and Volume <1.20x are Hard Gates;
- 5m MACD blocks only explicit adverse/opposite; improvement retains +1;
- threshold 6.0, fixed 1x LIMIT, SL=1x1H ATR, TP=2R full, No-BE;
- PEE4 first 4h, disabled once MFE>=+0.60R, tiered at -0.60/-0.70/-0.80R;
- after an actual PEE4 exit, block ALL new entries for exactly 60 minutes.

Single-variable A/B test only. Production files are unchanged. 15m EMA50 directional +1 remains.
"""
from __future__ import annotations

import json
import math
from datetime import timedelta
from pathlib import Path

import run_v168_boll5_overext010_pee4_365d as pee4

src = pee4.src
DAYS = 360
OUT = Path("backtest_output_v170_1h_only_direction_360d")
LOCK_MS = 60 * 60_000
VERSION = "1.7.0"
BUILD = "1700"

_ORIG_RESCORE = src.entry._rescore
_ORIG_CLOSE = src._close_pee3
_ORIG_SUBMIT = src.entry._submit
_LOCK_UNTIL_MS = 0
_LOCK_EVENTS = []

def _trend_direction_1h_only(hour, quarter=None):
    """V1.7 A/B: remove only the 15m component from the primary-direction Hard Gate."""
    signal_core = src.base.proven.prod.base.signal_core
    h = signal_core.indicators(hour)
    close = float(hour[-1]["c"])
    long_ok = close > float(h["ema200"]) and float(h["ema20"]) > float(h["ema50"]) and bool(h["up"])
    short_ok = close < float(h["ema200"]) and float(h["ema20"]) < float(h["ema50"]) and bool(h["down"])
    if long_ok and not short_ok:
        return "做多"
    if short_ok and not long_ok:
        return "做空"
    return "观望"


def _patch_primary_direction_1h_only():
    """Patch every historical facade that can resolve trend_direction to the same 1H-only function."""
    prod = src.base.proven.prod
    prod.trend_direction = _trend_direction_1h_only
    prod.base.trend_direction = _trend_direction_1h_only
    prod.base.signal_core.trend_direction = _trend_direction_1h_only
    try:
        src.base.proven.v163.trend_direction = _trend_direction_1h_only
    except Exception:
        pass


def _strip_ema(result):
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
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                raw += number
        total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
        row["raw"] = raw
        row["total"] = total
        row["required"] = src.base.THRESHOLD
        row["eligible"] = bool(row.get("gate") and total >= src.base.THRESHOLD)
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
    result["1h_ema9_26_score_enabled"] = False
    return result


def _rescore_v170(result, opp, five, now_ms):
    # Underlying scorer already applies BOLL5 overextension; V1.7 removes EMA9/26 afterward.
    return _strip_ema(_ORIG_RESCORE(result, opp, five, now_ms))


def _close_with_lock(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req):
    global _LOCK_UNTIL_MS
    before = len(self.trades)
    _ORIG_CLOSE(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req)
    if len(self.trades) > before:
        close_ms = int(bar["t"]) + 60_000
        _LOCK_UNTIL_MS = max(_LOCK_UNTIL_MS, close_ms + LOCK_MS)
        _LOCK_EVENTS.append({"pee4_exit_ms": close_ms, "lock_until_ms": _LOCK_UNTIL_MS})


def _submit_lock(self, result, now_ms, mark):
    if int(now_ms) < int(_LOCK_UNTIL_MS):
        self.stats["pee4_lock1h_blocked_submit"] += 1
        return
    return _ORIG_SUBMIT(self, result, now_ms, mark)


def _patch():
    global _LOCK_UNTIL_MS, _LOCK_EVENTS
    _LOCK_UNTIL_MS = 0
    _LOCK_EVENTS = []

    pee4.DAYS = DAYS
    pee4.OUT = OUT
    src.DAYS = DAYS
    src.OUT = OUT
    src.entry.DAYS = DAYS
    src.base.DAYS = DAYS
    src.base.FIXED_START = src.base.FIXED_END - timedelta(days=DAYS)

    src.entry.OVEREXT_ATR_MULT = 0.10
    src.entry.VERSION = VERSION
    src.entry.BUILD = BUILD
    src.entry.HistoricalV168Boll5OverextModel.VERSION = VERSION
    src.entry.HistoricalV168Boll5OverextModel.BUILD = BUILD
    src.entry._rescore = _rescore_v170
    _patch_primary_direction_1h_only()

    pee4._patch()
    # PEE4 patch sets the verified tiered Boolean exit path. Keep V1.7 rescore and 1H-only direction afterward.
    src.entry._rescore = _rescore_v170
    _patch_primary_direction_1h_only()
    src._close_pee3 = _close_with_lock
    src.entry._submit = _submit_lock


def verify(start, end):
    assert (end - start).days == DAYS
    assert src.base.DAYS == DAYS and src.entry.DAYS == DAYS
    assert abs(src.entry.OVEREXT_ATR_MULT - 0.10) < 1e-12
    assert src.base.THRESHOLD == 6.0
    assert src.base.BOLL_WINDOW_MS == 900_000
    assert LOCK_MS == 3_600_000
    signal_core = src.base.proven.prod.base.signal_core
    assert signal_core.trend_direction is _trend_direction_1h_only

    # Score semantic lock: EMA9/26 is gone; BOLL5 score remains.
    sample = {
        "scores": {
            "做多": {
                "layers": {
                    "boll_entry": 2.0,
                    "trend4h": 1.0,
                    "ema9_26_1h": 1.0,
                    "ema50_15m_move": 1.0,
                    "entry_near_zone": 1.0,
                    "macd_improving": 1.0,
                    "boll5_overextension": 0.0,
                },
                "gate": True,
                "eligible": True,
                "confirmations": {},
            },
            "做空": {"layers": {}, "gate": False, "eligible": False, "confirmations": {}},
        }
    }
    out = _strip_ema(sample)
    assert out["scores"]["做多"]["layers"]["ema9_26_1h"] == 0.0
    assert out["scores"]["做多"]["total"] == 6.0
    assert out["scores"]["做多"]["eligible"] is True

    # PEE4 exact Boolean tier checks.
    strict = pee4._pee4_decision(
        60_000, -0.65, 0.0, 2,
        {"opposite_closed_1h": True, "trend_reversal_15m": True},
        0.0, {},
    )
    assert strict and strict["allow"]
    assert pee4._pee4_decision(60_000, -0.65, 0.60, 2, {}, 0.0, {}) is None


def main():
    _patch()
    start, end = src.configure()
    verify(start, end)

    # Disable the inherited 180D assertion only; all V1.7 semantic checks above remain.
    src.verify_lock = lambda a, b: None
    src.main()

    old = OUT / "result_v168_boll5_overext035_pee3_mfe06_180d.json"
    if not old.exists():
        old = OUT / "result_v168_boll5_overext010_pee4_365d.json"
    payload = json.loads(old.read_text(encoding="utf-8"))

    payload["research"] = "KAYTRADE V1.7.0 Build1700 A/B: primary-direction Hard Gate = 1H only (15m removed), 360D, 2000U"
    payload["window"]["days"] = DAYS
    s = payload["strategy_lock"]
    s.update({
        "version": VERSION,
        "build": BUILD,
        "1h_15m_primary_direction_hard_gate": False,
        "1h_primary_direction_hard_gate": True,
        "15m_primary_direction_hard_gate": False,
        "1h_ema9_26_score_enabled": False,
        "1h_ema9_26_score": 0.0,
        "15m_ema50_directional_score_retained": True,
        "boll_trigger_timeframe": "15m",
        "boll_outer_score": 2.0,
        "5m_boll_overextension_score": 1.0,
        "5m_boll_overextension_threshold_atr14": 0.10,
        "4h_aligned_hard_gate": True,
        "5m_rsi_hard_gate": "30<=RSI<=70",
        "5m_volume_hard_gate": "ratio<1.20x prior20",
        "5m_macd_gate": "explicit_adverse_only",
        "score_threshold": 6.0,
        "position_multiplier": 1.0,
        "stop": "1.0x 1H ATR",
        "target": "2R full position",
        "break_even": False,
        "pee_version": "4.0-tiered-boolean",
        "pee4_monitor_max_hours": 4,
        "pee4_mfe_cutoff_r": 0.60,
        "pee4_post_exit_entry_lock_minutes": 60,
        "pee4_post_exit_entry_lock_scope": "GLOBAL: block both long and short new entries",
        "pee4_post_exit_entry_lock_trigger": "only actual PEE4 early exit",
    })
    payload["pee4_lock1h"] = {
        "lock_events": len(_LOCK_EVENTS),
        "blocked_submit_attempts": int(payload.get("simulator_stats", {}).get("pee4_lock1h_blocked_submit", 0)),
        "events": _LOCK_EVENTS,
    }
    payload.setdefault("correction", {})["only_deltas"] = [
        "V1.7.0 Build1700 label",
        "360D exact historical window",
        "ONLY A/B DELTA: remove 15m from primary-direction Hard Gate; keep 1H primary direction Hard Gate",
        "remove 1H EMA9/26 +1",
        "BOLL5 overextension 0.10 ATR14 +1 latched",
        "PEE4 tiered Boolean",
        "after actual PEE4 exit block ALL new entries for exactly 60 minutes",
    ]

    new = OUT / "result_v170_1h_only_direction_360d.json"
    new.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if old != new and old.exists():
        old.unlink()

    for oldcsv in (
        OUT / "trades_v168_boll5_overext035_pee3_mfe06_180d.csv",
        OUT / "trades_v168_boll5_overext010_pee4_365d.csv",
    ):
        if oldcsv.exists():
            oldcsv.rename(OUT / "trades_v170_1h_only_direction_360d.csv")
            break

    readme = OUT / "README.txt"
    readme.write_text(
        "KAYTRADE V1.7.0 Build1700 360D A/B backtest.\n"
        "ONLY DELTA: primary-direction Hard Gate uses 1H only; 15m component removed. "
        "NoEMA1H score, 15m BOLL outer, BOLL5 0.10 ATR14 +1 latch, 15m EMA50 +1 retained, 4H aligned Hard Gate, "
        "PEE4 first-4H/MFE0.60, global Lock1H, fixed 1x, 1H ATR SL, 2R TP, No-BE.\n",
        encoding="utf-8",
    )
    print("V170_1H_ONLY_DIRECTION_360D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

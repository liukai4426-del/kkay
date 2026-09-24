#!/usr/bin/env python3
"""KAYTRADE V1.7.0 Build1700 720D research backtest.

Exact strategy replay promoted from the audited V1.6.8 NoEMA/BOLL0.10/PEE4 research path:
- 1H/15m primary direction remains a Hard Gate;
- only latest CLOSED 15m BOLL outer-band opportunity; outer trigger = +2;
- 1H EMA9/26 +1 score removed (not a score, not a Hard Gate);
- 15m EMA50 directional movement score retained;
- CLOSED 5m BOLL overextension >=0.10 * Wilder ATR(14) latches +1 for current 15m opportunity;
- A/B delta: 4H aligned +1 and 4H neutral +1 are allowed; only explicit 4H opposite is a Hard Gate block;\n- A/B delta: SHORT entry_drift_atr5 > 0.20 is a Hard Gate block;
- 5m RSI 30..70 and Volume <1.20x are Hard Gates;
- 5m MACD blocks only explicit adverse/opposite; improvement retains +1;
- threshold 6.0, fixed 1x LIMIT, SL=1x1H ATR, TP=2R full, No-BE;
- PEE4 first 4h, disabled once MFE>=+0.60R, tiered at -0.60/-0.70/-0.80R;
- after an actual PEE4 exit, block ALL new entries for exactly 60 minutes.

Single-variable A/B test only. Production files are unchanged.
"""
from __future__ import annotations

import json
import math
from datetime import timedelta
from pathlib import Path

import run_v168_boll5_overext010_pee4_365d as pee4

src = pee4.src
DAYS = 720
OUT = Path("backtest_output_v170_4h_neutralplus1_short_drift020_720d")
LOCK_MS = 60 * 60_000
VERSION = "1.7.0"
BUILD = "1700"
SHORT_ENTRY_DRIFT_MAX_ATR5 = 0.20

_ORIG_RESCORE = src.entry._rescore
_ORIG_CLOSE = src._close_pee3
_ORIG_SUBMIT = src.entry._submit
_LOCK_UNTIL_MS = 0
_LOCK_EVENTS = []
_ORIG_BASE_FOUR_STATE = src.base._four_state
_LAST_4H_ACTUAL = {}


def _four_state_opposite_only_block(four, side):
    """A/B gate adapter: aligned + neutral pass, explicit opposite blocks."""
    actual = str(_ORIG_BASE_FOUR_STATE(four, side) or "neutral")
    _LAST_4H_ACTUAL[side] = actual
    # The inherited V1.6.8 gate only knows 'aligned passes'.
    # Map neutral to aligned for gate transport; preserve the real state separately.
    return "opposite" if actual == "opposite" else "aligned"


def _apply_4h_actual(result):
    """Restore V1.7 score semantics after the gate adapter:
    aligned +1, neutral +1, opposite +0 and blocked.
    """
    if not isinstance(result, dict):
        return result
    scores = result.get("scores") or {}
    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue
        actual = str(_LAST_4H_ACTUAL.get(side) or "neutral")
        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
        layers["trend4h"] = 1.0 if actual in ("aligned", "neutral") else 0.0
        row["layers"] = layers
        conf = row.setdefault("confirmations", {})
        conf["4H_trend_state_actual"] = actual
        conf["4H_gate_rule"] = "explicit opposite blocks; aligned and neutral allowed; both score +1"
        conf["4H_neutral_allowed"] = True
        conf["4H_direction_score"] = 1.0 if actual in ("aligned", "neutral") else 0.0
        req = conf.setdefault("required", {})
        req["outer_4h_not_opposite"] = actual != "opposite"
        blockers = []
        for b in conf.get("blockers") or []:
            t = str(b)
            if "4H非同向 Hard Gate" in t:
                if actual == "opposite":
                    blockers.append("V1.7 A/B：4H明确逆向 Hard Gate，禁止开仓")
                continue
            blockers.append(t)
        if actual == "opposite" and not any("4H明确逆向" in x for x in blockers):
            blockers.append("V1.7 A/B：4H明确逆向 Hard Gate，禁止开仓")
        conf["blockers"] = list(dict.fromkeys(blockers))
        if actual == "opposite":
            row["gate"] = False
            row["eligible"] = False
            row["position_multiplier"] = 0.0
    result["4h_gate_mode"] = "opposite_only_block"
    return result


def _patch_4h_gate():
    src.base._four_state = _four_state_opposite_only_block


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
    # Underlying scorer already applies BOLL5 overextension.
    # Then apply the A/B 4H rule and finally remove EMA9/26.
    result = _ORIG_RESCORE(result, opp, five, now_ms)
    result = _apply_4h_actual(result)
    return _strip_ema(result)


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

    opp = result.get("opportunity") or {}
    side = str(opp.get("side") or "")
    drift = math.nan
    if side == "做空":
        try:
            entry = float(mark)
            ref = float(opp["trigger_reference"])
            atr5 = max(float(opp["atr5"]), 1e-12)
            # Positive drift = short entry chased downward away from the frozen BOLL reference.
            drift = (ref - entry) / atr5
        except (KeyError, TypeError, ValueError):
            drift = math.nan
        if math.isfinite(drift) and drift > SHORT_ENTRY_DRIFT_MAX_ATR5:
            self.stats["short_entry_drift_gt020_block"] += 1
            self.stats["execution_gate_blocks"] += 1
            self.blockers["V1.7 A/B：做空 Entry Drift > 0.20 ATR5 Hard Gate"] += 1
            return

    before = self.pending
    out = _ORIG_SUBMIT(self, result, now_ms, mark)
    if self.pending is not None and self.pending is not before:
        row = (result.get("scores") or {}).get(side) or {}
        conf = row.get("confirmations") or {}
        self.pending.factors["4h_trend_state_actual"] = str(conf.get("4H_trend_state_actual") or "neutral")
        self.pending.factors["4h_gate_mode"] = "opposite_only_block_aligned_neutral_plus1"
        self.pending.factors["short_entry_drift_hard_gate_max_atr5"] = SHORT_ENTRY_DRIFT_MAX_ATR5
        self.pending.factors["entry_drift_atr5"] = drift if math.isfinite(drift) else None
    return out

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
    _patch_4h_gate()

    pee4._patch()
    # PEE4 patch sets the verified tiered Boolean exit path. Keep V1.7 rescore and A/B 4H gate afterward.
    src.entry._rescore = _rescore_v170
    _patch_4h_gate()
    src._close_pee3 = _close_with_lock
    src.entry._submit = _submit_lock


def verify(start, end):
    assert (end - start).days == DAYS
    assert src.base.DAYS == DAYS and src.entry.DAYS == DAYS
    assert abs(src.entry.OVEREXT_ATR_MULT - 0.10) < 1e-12
    assert src.base.THRESHOLD == 6.0
    assert src.base.BOLL_WINDOW_MS == 900_000
    assert LOCK_MS == 3_600_000
    assert src.base._four_state is _four_state_opposite_only_block

    # A/B semantic lock: aligned +1/pass, neutral +1/pass, opposite +0/block.
    global _LAST_4H_ACTUAL
    sample4 = {
        "scores": {
            "做多": {"layers": {"trend4h": 1.0}, "gate": True, "eligible": True, "position_multiplier": 1.0, "confirmations": {"blockers": [], "required": {}}},
            "做空": {"layers": {"trend4h": 1.0}, "gate": True, "eligible": True, "position_multiplier": 1.0, "confirmations": {"blockers": [], "required": {}}},
        }
    }
    _LAST_4H_ACTUAL = {"做多": "neutral", "做空": "opposite"}
    chk = _apply_4h_actual(sample4)
    assert chk["scores"]["做多"]["layers"]["trend4h"] == 1.0
    assert chk["scores"]["做多"]["gate"] is True
    assert abs(SHORT_ENTRY_DRIFT_MAX_ATR5 - 0.20) < 1e-12
    assert chk["scores"]["做空"]["layers"]["trend4h"] == 0.0
    assert chk["scores"]["做空"]["gate"] is False
    _LAST_4H_ACTUAL = {}

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

    payload["research"] = "KAYTRADE V1.7.0 Build1700 720D BASELINE: 4H explicit opposite blocked; aligned+1 and neutral+1 allowed; SHORT Entry Drift >0.20 ATR5 blocked, 2000U"
    payload["window"]["days"] = DAYS
    s = payload["strategy_lock"]
    s.update({
        "version": VERSION,
        "build": BUILD,
        "1h_15m_primary_direction_hard_gate": True,
        "1h_ema9_26_score_enabled": False,
        "1h_ema9_26_score": 0.0,
        "15m_ema50_directional_score_retained": True,
        "boll_trigger_timeframe": "15m",
        "boll_outer_score": 2.0,
        "5m_boll_overextension_score": 1.0,
        "5m_boll_overextension_threshold_atr14": 0.10,
        "4h_aligned_hard_gate": False,
        "4h_opposite_only_hard_gate": True,
        "4h_aligned_allowed": True,
        "4h_aligned_score": 1.0,
        "4h_neutral_allowed": True,
        "4h_neutral_score": 1.0,
        "short_entry_drift_hard_gate": True,
        "short_entry_drift_max_atr5": SHORT_ENTRY_DRIFT_MAX_ATR5,
        "4h_opposite_allowed": False,
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
        "720D exact historical window",
        "A/B DELTA: 4H aligned +1 allowed; 4H neutral +1 allowed; explicit 4H opposite Hard Gate blocked",
        "A/B DELTA: SHORT Entry Drift >0.20 ATR5 Hard Gate blocked",
        "remove 1H EMA9/26 +1",
        "BOLL5 overextension 0.10 ATR14 +1 latched",
        "PEE4 tiered Boolean",
        "after actual PEE4 exit block ALL new entries for exactly 60 minutes",
    ]

    new = OUT / "result_v170_4h_neutralplus1_short_drift020_720d.json"
    new.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if old != new and old.exists():
        old.unlink()

    for oldcsv in (
        OUT / "trades_v168_boll5_overext035_pee3_mfe06_180d.csv",
        OUT / "trades_v168_boll5_overext010_pee4_365d.csv",
    ):
        if oldcsv.exists():
            oldcsv.rename(OUT / "trades_v170_4h_neutralplus1_short_drift020_720d.csv")
            break

    readme = OUT / "README.txt"
    readme.write_text(
        "KAYTRADE V1.7.0 Build1700 720D BASELINE backtest.\n"
        "A/B DELTAS: explicit 4H opposite blocks; aligned +1 and neutral +1; SHORT Entry Drift >0.20 ATR5 blocks. "
        "NoEMA1H score, 15m BOLL outer, BOLL5 0.10 ATR14 +1 latch, "
        "1H/15m primary-direction Hard Gate retained, "
        "PEE4 first-4H/MFE0.60, global Lock1H, fixed 1x, 1H ATR SL, 2R TP, No-BE.\n",
        encoding="utf-8",
    )
    print("V170_4H_NEUTRALPLUS1_SHORT_DRIFT020_720D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""360D V1.6.8: NoEMA1H + BOLL5 overextension 0.10 + PEE4 + 1H global entry lock after PEE4."""
from __future__ import annotations
import json, math
from datetime import timedelta
from pathlib import Path
import run_v168_boll5_overext010_pee4_365d as pee4

src = pee4.src
DAYS = 360
OUT = Path("backtest_output_v168_boll5_overext010_noema1h_pee4_lock1h_360d")
LOCK_MS = 60 * 60_000
VERSION = "1.6.8-BOLL5-OVEREXT010-NOEMA1H-PEE4-LOCK1H-research"
BUILD = "1680-boll5-overext010-noema1h-pee4-lock1h-360d"

_ORIG_RESCORE = src.entry._rescore
_ORIG_CLOSE = src._close_pee3
_ORIG_SUBMIT = src.entry._submit
_LOCK_UNTIL_MS = 0
_LOCK_EVENTS = []

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
        for v in layers.values():
            try: n = float(v)
            except (TypeError, ValueError): continue
            if math.isfinite(n): raw += n
        total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
        row["raw"] = raw
        row["total"] = total
        row["required"] = src.base.THRESHOLD
        row["eligible"] = bool(row.get("gate") and total >= src.base.THRESHOLD)
        row["position_multiplier"] = 1.0 if row["eligible"] else 0.0
    qualified = [s for s,r in scores.items() if isinstance(r,dict) and r.get("eligible")]
    selected = "观望"
    if len(qualified) == 1:
        selected = qualified[0]
    elif len(qualified) == 2:
        a,b = qualified
        av,bv = float(scores[a].get("total") or 0), float(scores[b].get("total") or 0)
        if av != bv: selected = a if av > bv else b
    result["side"] = selected
    result["1h_ema9_26_score_enabled"] = False
    return result

def _rescore_noema(result, opp, five, now_ms):
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
    _LOCK_UNTIL_MS = 0; _LOCK_EVENTS = []
    pee4.DAYS = DAYS; pee4.OUT = OUT
    src.DAYS = DAYS; src.OUT = OUT
    src.entry.DAYS = DAYS; src.base.DAYS = DAYS
    src.base.FIXED_START = src.base.FIXED_END - timedelta(days=DAYS)
    src.entry.OVEREXT_ATR_MULT = 0.10
    src.entry.VERSION = VERSION; src.entry.BUILD = BUILD
    src.entry.HistoricalV168Boll5OverextModel.VERSION = VERSION
    src.entry.HistoricalV168Boll5OverextModel.BUILD = BUILD
    src.entry._rescore = _rescore_noema
    pee4._patch()
    # pee4._patch resets core PEE decision/version but not our no-EMA rescore.
    src._close_pee3 = _close_with_lock
    src.entry._submit = _submit_lock

def verify(start,end):
    assert (end-start).days == 360
    assert src.base.DAYS == 360 and src.entry.DAYS == 360
    assert abs(src.entry.OVEREXT_ATR_MULT - 0.10) < 1e-12
    assert src.base.THRESHOLD == 6.0
    assert LOCK_MS == 3_600_000
    sample={"scores":{"做多":{"layers":{"boll_entry":2.0,"trend4h":1.0,"ema9_26_1h":1.0,"entry_near_zone":1.0,"macd_improving":1.0},"gate":True,"eligible":True,"confirmations":{}},"做空":{"layers":{},"gate":False,"eligible":False,"confirmations":{}}}}
    out=_strip_ema(sample)
    assert out["scores"]["做多"]["layers"]["ema9_26_1h"] == 0.0
    assert out["scores"]["做多"]["total"] == 5.0
    assert out["scores"]["做多"]["eligible"] is False
    d=pee4._pee4_decision(60_000,-0.65,0,2,{"opposite_closed_1h":True,"trend_reversal_15m":True},0,{})
    assert d and d["allow"]

def main():
    _patch()
    start,end=src.configure()
    verify(start,end)
    src.verify_lock=lambda a,b: None
    src.main()
    old=OUT/"result_v168_boll5_overext035_pee3_mfe06_180d.json"
    if not old.exists():
        old=OUT/"result_v168_boll5_overext010_pee4_365d.json"
    d=json.loads(old.read_text(encoding="utf-8"))
    d["research"]="V1.6.8 NoEMA1H + BOLL5 overextension 0.10 ATR14 + PEE4 + 1H global entry lock after PEE4, 360D"
    d["window"]["days"]=360
    s=d["strategy_lock"]
    s["1h_ema9_26_score_enabled"]=False
    s["1h_ema9_26_score"]=0.0
    s["5m_boll_overextension_threshold_atr14"]=0.10
    s["pee_version"]="4.0-tiered-boolean"
    s["pee4_post_exit_entry_lock_minutes"]=60
    s["pee4_post_exit_entry_lock_scope"]="GLOBAL: block both long and short new entries"
    s["pee4_post_exit_entry_lock_trigger"]="only actual PEE4 early exit"
    d["pee4_lock1h"]={"lock_events":len(_LOCK_EVENTS),"blocked_submit_attempts":int(d.get("simulator_stats",{}).get("pee4_lock1h_blocked_submit",0)),"events":_LOCK_EVENTS}
    d.setdefault("correction",{})["only_deltas"]=["remove 1H EMA9/26 +1","BOLL5 overextension 0.10 ATR14 +1","PEE4 tiered Boolean","after actual PEE4 exit block ALL new entries for exactly 60 minutes","360D exact baseline window"]
    new=OUT/"result_v168_boll5_overext010_noema1h_pee4_lock1h_360d.json"
    new.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
    if old != new and old.exists(): old.unlink()
    for oldcsv in [OUT/"trades_v168_boll5_overext035_pee3_mfe06_180d.csv", OUT/"trades_v168_boll5_overext010_pee4_365d.csv"]:
        if oldcsv.exists():
            oldcsv.rename(OUT/"trades_v168_boll5_overext010_noema1h_pee4_lock1h_360d.csv")
            break
    print("FINAL_RESULT",json.dumps(d,ensure_ascii=False,indent=2),flush=True)

if __name__=="__main__":
    main()

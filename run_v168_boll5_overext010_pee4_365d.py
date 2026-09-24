#!/usr/bin/env python3
"""365D V1.6.8 R2 research: BOLL5 overextension 0.10 ATR14 + PEE4 tiered Boolean exit.

PEE4:
- active only during first 4h; elapsed time does NOT change indicator thresholds
- permanently disabled once historical MFE >= +0.60R
- starts only at current R <= -0.60R
- -0.60R..-0.70R: strict
- -0.70R..-0.80R: strong
- -0.80R..-1.00R: ordinary
- no PEE score; Boolean reverse-logic only
- 1H + 15m direction replace 4H in PEE
- normal SL/TP always has priority
"""
from __future__ import annotations
import inspect, json
from datetime import timedelta
from pathlib import Path
import run_v168_boll5_overext035_pee3_mfe06_180d as src

DAYS=365
OVEREXT_ATR_MULT=0.10
OUT=Path("backtest_output_v168_boll5_overext010_pee4_365d")
CUSTOM_VARIANT="boll5_overext035_pee3_mfe06"
ORIGINAL_ACCEPT=src.research.variant_accept

def _accept(name, score, opp):
    if str(name)==CUSTOM_VARIANT:
        return ORIGINAL_ACCEPT("baseline", score, opp)
    return ORIGINAL_ACCEPT(name, score, opp)

def _pee4_decision(hold_ms,current_r,mfe_r,hard_count,hard,soft_score,components):
    # Time is only a global PEE validity window, never a tier/indicator factor.
    if hold_ms < 0 or hold_ms > 4*60*60_000 or mfe_r >= 0.60 or current_r > -0.60:
        return None
    h1=bool(hard.get("opposite_closed_1h"))
    d15=bool(hard.get("trend_reversal_15m"))
    struct=bool(hard.get("structure_break_15m"))
    boll=bool(hard.get("boll5_persistent_failure"))
    macd5=float(components.get("macd5") or 0.0)>0
    advvol=float(components.get("adverse_volume") or 0.0)>0

    # -0.60 .. -0.70: STRICT. Require dual direction reversal or structure failure + confirmation.
    if current_r > -0.70:
        allow=(h1 and d15) or (struct and (h1 or d15 or boll or advvol))
        path="strict_060_070"
    # -0.70 .. -0.80: STRONG. Core failure alone, or two persistent reverse confirmations.
    elif current_r > -0.80:
        allow=(h1 and d15) or struct or (d15 and boll) or (d15 and advvol)
        path="strong_070_080"
    # -0.80 .. -1.00: ORDINARY. Preserve remaining risk when a strong failure is present.
    else:
        allow=h1 or struct or boll or (d15 and macd5) or (d15 and advvol)
        path="ordinary_080_100"
    return {"stage":"PEE4","path":path,"allow":bool(allow),"score_free":True}

def _patch():
    src.DAYS=DAYS; src.OUT=OUT
    src.entry.DAYS=DAYS; src.base.DAYS=DAYS
    src.base.FIXED_START=src.base.FIXED_END-timedelta(days=DAYS)
    src.entry.OVEREXT_ATR_MULT=OVEREXT_ATR_MULT
    src.entry.VERSION="1.6.8-BOLL5-OVEREXT010-PEE4-research"
    src.entry.BUILD="1680-boll5-overext010-pee4-365d"
    src.entry.HistoricalV168Boll5OverextModel.VERSION=src.entry.VERSION
    src.entry.HistoricalV168Boll5OverextModel.BUILD=src.entry.BUILD
    src.research.variant_accept=_accept
    src._decision=_pee4_decision
    src.PEE_VERSION="4.0-tiered-boolean"
    src.EXIT_REASON="PEE4_EARLY_EXIT_TIERED"

def verify(start,end):
    assert (end-start).days==365 and src.base.DAYS==365
    assert src.entry.OVEREXT_ATR_MULT==0.10
    assert src.MFE_CUTOFF_R==0.60 and src.MONITOR_MAX_MS==14_400_000
    assert src.base.THRESHOLD==6.0 and src.base.BOLL_WINDOW_MS==900_000
    # PEE4 itself has no score threshold; entry score remains baseline V1.6.8.
    assert _pee4_decision(60_000,-0.59,0,2,{},3,{}) is None
    assert _pee4_decision(60_000,-0.65,0.60,2,{},3,{}) is None
    assert _pee4_decision(241*60_000,-0.85,0,2,{},3,{}) is None
    # Strict: trend reversal alone must NOT exit.
    d=_pee4_decision(60_000,-0.65,0,1,{"trend_reversal_15m":True},3,{})
    assert d and not d["allow"]
    # Strict: 1H+15m opposite direction exits.
    d=_pee4_decision(60_000,-0.65,0,2,{"opposite_closed_1h":True,"trend_reversal_15m":True},0,{})
    assert d["allow"]
    # Strong: structure break alone exits.
    d=_pee4_decision(60_000,-0.75,0,1,{"structure_break_15m":True},0,{})
    assert d["allow"]
    # Ordinary: BOLL persistent failure alone exits.
    d=_pee4_decision(60_000,-0.85,0,1,{"boll5_persistent_failure":True},0,{})
    assert d["allow"]
    code=inspect.getsource(src._process_exit_pee3)
    assert code.index("_ORIGINAL_PROCESS_EXIT") < code.index("_FEATURE_BY_CLOSE_MS")

def main():
    _patch()
    start,end=src.configure()
    verify(start,end)
    src.verify_lock=verify
    src.main()
    old=OUT/"result_v168_boll5_overext035_pee3_mfe06_180d.json"
    new=OUT/"result_v168_boll5_overext010_pee4_365d.json"
    d=json.loads(old.read_text(encoding="utf-8"))
    d["research"]="V1.6.8 R2 + BOLL5 overextension 0.10 ATR14 + PEE4 tiered Boolean, 365D"
    d["window"]["days"]=365
    s=d["strategy_lock"]
    s["5m_boll_overextension_threshold_atr14"]=0.10
    s["pee_version"]="4.0-tiered-boolean"
    s["pee_monitor_max_hours"]=4
    s["pee_mfe_cutoff_r"]=0.60
    s["pee_scoring"]=False
    s["pee_time_factor"]=False
    s["pee_tiers"]={"strict":[-0.60,-0.70],"strong":[-0.70,-0.80],"ordinary":[-0.80,-1.00]}
    s["pee_direction_timeframes"]=["1H","15m"]
    d["pee4"]={
      "logic":"reverse entry/Hard-Gate logic; Boolean only",
      "strict":"1H opposite AND 15m opposite OR 15m structure break + one confirmation",
      "strong":"1H+15m opposite OR structure break OR 15m opposite+BOLL5 failure OR 15m opposite+adverse volume",
      "ordinary":"1H opposite OR structure break OR BOLL5 failure OR 15m opposite+MACD5 adverse OR 15m opposite+adverse volume",
    }
    d.setdefault("correction",{})["production_strategy_variables_changed"]=False
    d["correction"]["only_deltas"]=["PEE3 -> PEE4 tiered Boolean exit","PEE starts <=-0.60R","MFE>=+0.60R disables PEE","1H+15m direction used by PEE","no hourly-stage factor","365D window"]
    new.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
    old.unlink()
    oldcsv=OUT/"trades_v168_boll5_overext035_pee3_mfe06_180d.csv"
    if oldcsv.exists(): oldcsv.rename(OUT/"trades_v168_boll5_overext010_pee4_365d.csv")
    print("PEE4_365D_RESULT",json.dumps(d,ensure_ascii=False,indent=2),flush=True)

if __name__=="__main__":
    main()

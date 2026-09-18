#!/usr/bin/env python3
"""V1.6.8 R2 360D: replace 1H EMA9/26 +1 with BOLL5 overextension >=0.10 ATR14 +1."""
from __future__ import annotations
import json
from datetime import timedelta
from pathlib import Path
import run_v168_boll5_overext035_noema1h_180d as x

DAYS=360
THRESHOLD=0.10
OUT=Path("backtest_output_v168_boll5_overext010_noema1h_360d")
VERSION="1.6.8-BOLL5-OVEREXT010-NOEMA1H-research"
BUILD="1680-boll5-overext010-noema1h-360d"

def configure():
    x.prev.DAYS=DAYS
    x.prev.base.DAYS=DAYS
    x.prev.base.FIXED_START=x.prev.base.FIXED_END-timedelta(days=DAYS)
    x.prev.OVEREXT_ATR_MULT=THRESHOLD
    x.VERSION=VERSION; x.BUILD=BUILD; x.OUT=OUT
    x.prev.VERSION=VERSION; x.prev.BUILD=BUILD; x.prev.OUT=OUT
    x.prev.HistoricalV168Boll5OverextModel.VERSION=VERSION
    x.prev.HistoricalV168Boll5OverextModel.BUILD=BUILD
    def verify_prev(start,end):
        assert (end-start).days==360
        assert x.prev.DAYS==360 and x.prev.base.DAYS==360
        assert abs(x.prev.OVEREXT_ATR_MULT-0.10)<1e-12
        assert x.prev.OVEREXT_SCORE==1.0
        assert x.prev.base.THRESHOLD==6.0
        assert x.prev.base.BOLL_WINDOW_MS==900000
        assert x.prev.base.research.Simulator.process_exit is x.prev.base.proven._ORIG_EXIT
    def verify_replacement():
        assert x.prev.DAYS==360
        assert abs(x.prev.OVEREXT_ATR_MULT-0.10)<1e-12
        assert x.prev.OVEREXT_SCORE==1.0
        assert x.prev.base.THRESHOLD==6.0
    x.prev.verify_lock=verify_prev
    x.verify_replacement_lock=verify_replacement

def main():
    configure()
    x.main()
    old=OUT/"result_v168_boll5_overext035_noema1h_180d.json"
    payload=json.loads(old.read_text())
    payload["research"]="V1.6.8 R2 360D: remove 1H EMA9/26 +1, add 5m BOLL overextension >=0.10 ATR14 +1"
    payload["window"]["days"]=360
    payload["strategy_lock"]["1h_ema9_26_score_enabled"]=False
    payload["strategy_lock"]["1h_ema9_26_score"]=0.0
    payload["strategy_lock"]["5m_boll_overextension_score"]=1.0
    payload["strategy_lock"]["5m_boll_overextension_threshold_atr14"]=0.10
    payload["metrics"]["strategy_version"]=VERSION
    payload["metrics"]["build"]=BUILD
    payload["correction"]["only_strategy_delta"]="replace 1H EMA9/26 aligned +1 with latched 5m BOLL overextension >=0.10 x ATR14 +1"
    new=OUT/"result_v168_boll5_overext010_noema1h_360d.json"
    new.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    old.unlink()
    oldcsv=OUT/"trades_v168_boll5_overext035_noema1h_180d.csv"
    newcsv=OUT/"trades_v168_boll5_overext010_noema1h_360d.csv"
    if oldcsv.exists(): oldcsv.rename(newcsv)
    print("RESULT",json.dumps(payload,ensure_ascii=False,indent=2),flush=True)

if __name__=="__main__":
    main()

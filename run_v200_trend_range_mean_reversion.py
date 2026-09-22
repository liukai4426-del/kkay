#!/usr/bin/env python3
"""V2.0 research baseline: 4H impulse -> range -> trend-side mean reversion.

Idea:
- Detect the latest meaningful 4H impulse from volume expansion OR BOLL-width expansion.
- The impulse direction becomes the persistent macro direction.
- Build a dynamic 4H range from bars after that impulse.
- LONG only in bullish regime, after price returns into the lower half of the range.
- SHORT only in bearish regime, after price returns into the upper half.
- Require a simple 5m reversal confirmation (candle direction + EMA20 reclaim/reject).
- No score / RSI / KDJ / MACD entry gate.
Research only; production strategy untouched.
"""
from __future__ import annotations
import json, math, os
from datetime import timedelta
from pathlib import Path
from core import indicators, ema
import run_v170_trend_pullback_180d as src

DAYS=int(os.environ.get("BACKTEST_DAYS","360"))
if DAYS not in (180,360,720): raise ValueError("BACKTEST_DAYS must be 180/360/720")
OUT=Path(f"backtest_output_v200_trend_range_mr_{DAYS}d")
RESULT_NAME=f"result_v200_trend_range_mr_{DAYS}d.json"
TRADES_NAME=f"trades_v200_trend_range_mr_{DAYS}d.csv"

VOL_RATIO=1.50
WIDTH_RATIO=1.20
IMPULSE_LOOKBACK=48
MIN_RANGE_BARS=6
MAX_RANGE_BARS=30

def _bw(rows):
    q=indicators(rows); mid=float(q["middle"])
    return (float(q["upper"])-float(q["lower"]))/abs(mid) if abs(mid)>1e-12 else math.inf

def _latest_impulse(four):
    if len(four)<80: return None
    start=max(25,len(four)-IMPULSE_LOOKBACK)
    best=None
    for i in range(start,len(four)-MIN_RANGE_BARS):
        hist=four[:i+1]; bar=four[i]
        prev=four[max(0,i-20):i]
        if len(prev)<20: continue
        avg_v=sum(float(x.get("v",0)) for x in prev)/len(prev)
        vr=float(bar.get("v",0))/avg_v if avg_v>0 else 0.0
        try:
            wr=_bw(hist)/_bw(four[:max(21,i-2)])
        except Exception:
            continue
        q=indicators(hist)
        mid=float(q["middle"])
        c,o=float(bar["c"]),float(bar["o"])
        side="做多" if c>o and c>mid else "做空" if c<o and c<mid else None
        if side and (vr>=VOL_RATIO or wr>=WIDTH_RATIO):
            best={"idx":i,"t":int(bar["t"]),"side":side,"volume_ratio":vr,"width_ratio":wr,
                  "impulse_close":c}
    return best

def _range_state(four):
    imp=_latest_impulse(four)
    if not imp: return None
    bars=four[imp["idx"]+1:]
    if len(bars)<MIN_RANGE_BARS: return None
    bars=bars[-MAX_RANGE_BARS:]
    hi=max(float(x["h"]) for x in bars); lo=min(float(x["l"]) for x in bars)
    if hi<=lo: return None
    return {**imp,"high":hi,"low":lo,"mean":(hi+lo)/2.0,"bars":len(bars)}

def _ema20_5m(five):
    vals=[float(x["c"]) for x in five]
    return float(ema(vals,20)[-1])

def _trigger(quarter,five,rs):
    q=quarter[-1]; f=five[-1]; mean=rs["mean"]; hi=rs["high"]; lo=rs["low"]; e=_ema20_5m(five)
    if rs["side"]=="做多":
        zone=(float(q["c"])<=mean and float(q["c"])>=lo)
        confirm=float(f["c"])>float(f["o"]) and float(f["c"])>e
        ref=mean; path="4h_bull_range_lower_half_5m_reclaim"
    else:
        zone=(float(q["c"])>=mean and float(q["c"])<=hi)
        confirm=float(f["c"])<float(f["o"]) and float(f["c"])<e
        ref=mean; path="4h_bear_range_upper_half_5m_reject"
    if not (zone and confirm): return None
    return {"bar_t":int(q["t"]),"signal_close_ms":int(q["t"])+15*60_000,
            "lower":lo,"middle":mean,"upper":hi,"reference":ref,"path":path,
            "bar_o":float(q["o"]),"bar_h":float(q["h"]),"bar_l":float(q["l"]),"bar_c":float(q["c"])}

class TrendRangeMeanReversionModel:
    VERSION="V2.0-TrendRangeMeanReversion-Research"
    BUILD=f"V200-{DAYS}D"
    THRESHOLD=0.0; ENTRY_WINDOW_MS=15*60_000
    BOLL_TRIGGER_TIMEFRAME="15m"; FIVE_MINUTE_BOLL_ENABLED=False

    @staticmethod
    def evaluate(hour,quarter,five,one,four,opportunity=None,stop_atr=1.0,
                 maker_bps=2.0,taker_bps=5.0,slippage_bps=5.0,now_ms=None,allow_new=True):
        now_ms=int(now_ms if now_ms is not None else int(one[-1]["t"])+60_000)
        rs=_range_state(four)
        direction=rs["side"] if rs else "观望"
        opp=dict(opportunity) if isinstance(opportunity,dict) else None
        transition=None
        if opp:
            if now_ms>=int(opp.get("expires_ms") or 0):
                transition=("invalidated","15m mean-reversion setup expired"); opp=None
            elif direction!=str(opp.get("side") or ""):
                transition=("invalidated","new 4H impulse changed regime"); opp=None
        trig=_trigger(quarter,five,rs) if rs else None
        if allow_new and trig:
            old=int((opp or {}).get("signal_bar_t") or -1)
            if opp is None or int(trig["bar_t"])>old:
                opp=src._make_opportunity(hour,quarter,five,one,four,direction,trig)
                opp.update({"regime_impulse_t":rs["t"],"impulse_volume_ratio":rs["volume_ratio"],
                            "impulse_width_ratio":rs["width_ratio"],"range_high":rs["high"],
                            "range_low":rs["low"],"range_mean":rs["mean"],"range_bars":rs["bars"]})
                transition=("created",str(opp["id"]))
        scores={s:{"total":0.0,"gate":False,"eligible":False,"confirmations":{},"layers":{}} for s in ("做多","做空")}
        if opp:
            side=opp["side"]; scores[side]={"total":0.0,"raw":0.0,"gate":True,"eligible":True,
                "required":0.0,"position_multiplier":1.0,"layers":{},"confirmations":{"required":{
                "4h_impulse_regime":True,"trend_side_range_half":True,"5m_reversal_confirmation":True},
                "range":{"high":opp["range_high"],"mean":opp["range_mean"],"low":opp["range_low"]}}}
        return {"side":str((opp or {}).get("side") or "观望"),"direction":direction,"scores":scores,
                "opportunity":dict(opp) if opp else None,"opportunity_id":str((opp or {}).get("id") or ""),
                "strategy_version":TrendRangeMeanReversionModel.VERSION,
                "trend_1h":src._trend_state(hour),"trend_4h":direction},opp,transition
    @staticmethod
    def execution_checks(plan,opportunity,score): return True,{},[]

def configure():
    src.DAYS=DAYS; src.FIXED_START=src.FIXED_END-timedelta(days=DAYS)
    src.OUT=OUT; src.RESULT_NAME=RESULT_NAME; src.TRADES_NAME=TRADES_NAME
    src.TrendPullbackModel=TrendRangeMeanReversionModel
    # Keep execution/risk comparable; remove the old Front-R entry restriction.
    src.FRONT_MIN_R=1.50; src.COST_MAX_R=0.30; src.STOP_ATR=1.0; src.REWARD_R=2.0

def main():
    configure()
    print("V200_CONFIG",f"days={DAYS}","4H impulse: volume>=1.5x OR BOLL width>=1.2x",
          "range=post-impulse 4H","entry=trend-side half + 5m EMA20 reversal",
          "SL=1H ATR","TP=2R","no score/RSI/MACD/KDJ",flush=True)
    src.main()
    p=OUT/RESULT_NAME
    d=json.loads(p.read_text(encoding="utf-8"))
    d["research"]="V2.0 4H impulse -> dynamic range -> trend-side mean reversion baseline"
    d["strategy_lock"]={"impulse":"4H volume ratio >=1.50 OR BOLL width ratio >=1.20; candle/mid direction",
      "range":"post-impulse 4H high/low, max 30 bars; midpoint is mean",
      "long":"bull regime + 15m close in lower half + bullish 5m close above EMA20",
      "short":"bear regime + 15m close in upper half + bearish 5m close below EMA20",
      "score":False,"rsi":False,"macd":False,"kdj":False,"front_r_gate":"inherited >1.50R known-only",
      "stop":"1.0x 1H ATR","tp":"2R full"}
    p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__": main()

# CI trigger: V2.0 baseline 360D

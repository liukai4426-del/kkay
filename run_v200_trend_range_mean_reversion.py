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
import json, math, os, time, sys, inspect, re
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

PURE_FACTOR_KEYS = {
    "boll_path","signal_path","boll_timeframe","trend_1h","trend_4h",
    "4h_impulse_regime","impulse_volume_ratio","impulse_width_ratio",
    "range_high","range_mean","range_low","range_bars",
    "front_gate","cost_gate","cost_r_info","entry_rule","position_multiplier",
}

_RANGE_CACHE_KEY = None
_RANGE_CACHE_VALUE = None
_EMA20_CACHE_KEY = None
_EMA20_CACHE_VALUE = None

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
    global _RANGE_CACHE_KEY, _RANGE_CACHE_VALUE
    if not four:
        return None
    key = (len(four), int(four[-1]["t"]))
    if key == _RANGE_CACHE_KEY:
        return _RANGE_CACHE_VALUE
    imp=_latest_impulse(four)
    if not imp:
        value=None
    else:
        bars=four[imp["idx"]+1:]
        if len(bars)<MIN_RANGE_BARS:
            value=None
        else:
            bars=bars[-MAX_RANGE_BARS:]
            hi=max(float(x["h"]) for x in bars); lo=min(float(x["l"]) for x in bars)
            value=None if hi<=lo else {**imp,"high":hi,"low":lo,"mean":(hi+lo)/2.0,"bars":len(bars)}
    _RANGE_CACHE_KEY=key
    _RANGE_CACHE_VALUE=value
    return value

def _ema20_5m(five):
    global _EMA20_CACHE_KEY, _EMA20_CACHE_VALUE
    key = (len(five), int(five[-1]["t"]))
    if key == _EMA20_CACHE_KEY:
        return _EMA20_CACHE_VALUE
    vals=[float(x["c"]) for x in five]
    value=float(ema(vals,20)[-1])
    _EMA20_CACHE_KEY=key
    _EMA20_CACHE_VALUE=value
    return value

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

def _make_v2_opportunity(hour, side, trig, rs):
    h1=indicators(hour)
    return {
        "id": f"v200-{side}-{int(trig['bar_t'])}",
        "side": side,
        "signal_path": trig["path"],
        "signal_bar_t": int(trig["bar_t"]),
        "signal_close_ms": int(trig["signal_close_ms"]),
        "created_ms": int(trig["signal_close_ms"]),
        "expires_ms": int(trig["signal_close_ms"]) + 15*60_000,
        "trigger_reference": float(trig["reference"]),
        "trigger_detail": {
            "boll_lower": float(trig["lower"]),
            "boll_middle": float(trig["middle"]),
            "boll_upper": float(trig["upper"]),
            "bar_o": trig["bar_o"], "bar_h": trig["bar_h"],
            "bar_l": trig["bar_l"], "bar_c": trig["bar_c"],
        },
        "atr1h": float(h1["atr"]),
        "front_structure": None,
        "trend_1h": "not_used",
        "trend_4h": side,
        "regime_impulse_t": rs["t"],
        "impulse_volume_ratio": rs["volume_ratio"],
        "impulse_width_ratio": rs["width_ratio"],
        "range_high": rs["high"],
        "range_low": rs["low"],
        "range_mean": rs["mean"],
        "range_bars": rs["bars"],
        "consumed": False,
    }

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
                opp=_make_v2_opportunity(hour,direction,trig,rs)
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
                "trend_1h":"not_used","trend_4h":direction},opp,transition
    @staticmethod
    def execution_checks(plan,opportunity,score): return True,{},[]

def _submit_pure(self, result, now_ms, mark):
    opp=result.get("opportunity")
    if not isinstance(opp,dict):
        return
    side=str(opp.get("side") or "")
    if side not in ("做多","做空"):
        return
    signal_bar_t=int(opp.get("signal_bar_t") or -1)
    if signal_bar_t <= int(getattr(self,"_trend_last_submitted_signal_bar_t",-1)):
        self.stats["same_15m_signal_repeat_block"] += 1
        return
    if not self.can_submit(now_ms):
        return

    entry=float(mark)
    stop_distance=float(opp.get("atr1h") or 0.0) * src.STOP_ATR
    if stop_distance <= 0:
        self.stats["invalid_atr1h"] += 1
        return

    direction=src.research.side_dir(side)
    stop=entry-direction*stop_distance
    target=entry+direction*stop_distance*src.REWARD_R

    eq=self.equity(mark)
    risk_capital=min(src.CAPITAL,eq)
    base_risk=min(src.RISK_USDT,risk_capital*src.RISK_PCT/100.0)
    risk_budget=min(base_risk,self.daily_remaining(now_ms,mark))
    if risk_budget <= 0:
        self.stats["sizing_skips"] += 1
        return

    maker=src.research.MAKER_BPS/10000.0
    taker=src.research.TAKER_BPS/10000.0
    slip=src.research.SLIPPAGE_BPS/10000.0
    per_btc=stop_distance + entry*max(maker,taker) + stop*(taker+slip)
    notional_cap=min(
        src.BASE_POSITION_NOTIONAL,
        risk_capital*src.LEVERAGE,
        max(eq,0.0)*0.9*src.LEVERAGE,
    )
    btc=src.research.round_contract_btc(min(risk_budget/per_btc,notional_cap/entry),self.meta)
    if btc <= 0:
        self.stats["sizing_skips"] += 1
        return

    cost_r=src.structure_model.estimated_cost_r(
        entry,stop_distance,side,
        maker_bps=src.research.MAKER_BPS,
        taker_bps=src.research.TAKER_BPS,
        slippage_bps=src.research.SLIPPAGE_BPS,
    )
    factors={
        "boll_path":str(opp.get("signal_path") or ""),
        "signal_path":str(opp.get("signal_path") or ""),
        "boll_timeframe":"15m",
        "trend_1h":"not_used",
        "trend_4h":side,
        "4h_impulse_regime":side,
        "impulse_volume_ratio":opp.get("impulse_volume_ratio"),
        "impulse_width_ratio":opp.get("impulse_width_ratio"),
        "range_high":opp.get("range_high"),
        "range_mean":opp.get("range_mean"),
        "range_low":opp.get("range_low"),
        "range_bars":opp.get("range_bars"),
        "front_gate":"OFF",
        "cost_gate":"OFF",
        "cost_r_info":float(cost_r),
        "entry_rule":"4H impulse/range + trend-side half + 5m EMA20 reversal",
        "position_multiplier":1.0,
    }
    self.pending=src.research.PendingEntry(
        side=side,
        limit=entry,
        stop=stop,
        target=target,
        quantity_btc=btc,
        score=0.0,
        opportunity_id=str(opp["id"]),
        signal_bar_t=signal_bar_t,
        signal_close_ms=int(opp.get("signal_close_ms") or now_ms),
        submitted_ms=int(now_ms),
        expires_ms=int(now_ms)+src.ORDER_TTL_MS,
        factors=factors,
        score_components={},
        trigger_combination=dict(opp.get("trigger_detail") or {}),
        front_r=math.inf,
        cost_r=float(cost_r),
    )
    self._trend_last_submitted_signal_bar_t=signal_bar_t
    self.stats["v200_pure_limit_submitted"] += 1
    self.stats["limit_submitted"] += 1


_SRC_CONFIGURE = src.configure

def configure():
    src.DAYS=DAYS
    src.FIXED_START=src.FIXED_END-timedelta(days=DAYS)
    src.OUT=OUT
    src.RESULT_NAME=RESULT_NAME
    src.TRADES_NAME=TRADES_NAME
    src.TrendPullbackModel=TrendRangeMeanReversionModel
    start,end=_SRC_CONFIGURE()

    import run_v166_15m_boll_macd_adverse_only_360d_fast as proven
    src.research.model=TrendRangeMeanReversionModel
    src.research.Simulator.submit=_submit_pure
    src.research.Simulator.process_pending=proven._ORIG_PENDING
    src.research.Simulator.process_exit=proven._ORIG_EXIT
    src.research.Simulator.finish=proven._ORIG_FINISH
    return start,end


def preflight():
    # Must run before any market-data fetch / full backtest.
    assert DAYS in (180,360,720)
    assert src.STOP_ATR == 1.0 and src.REWARD_R == 2.0
    start,end=configure()
    assert (end-start).days == DAYS

    import run_v166_15m_boll_macd_adverse_only_360d_fast as proven
    assert src.research.Simulator.submit is _submit_pure
    assert src.research.Simulator.process_pending is proven._ORIG_PENDING
    assert src.research.Simulator.process_exit is proven._ORIG_EXIT
    assert src.research.Simulator.finish is proven._ORIG_FINISH

    exit_source=inspect.getsource(proven._ORIG_EXIT)
    required=set(re.findall(r'p\\.factors\\[["\\\']([^"\\\']+)["\\\']\\]', exit_source))
    missing=required-PURE_FACTOR_KEYS
    assert not missing, f"PURE factor contract missing required keys: {sorted(missing)}"

    submit_source=inspect.getsource(_submit_pure)
    forbidden=("front_r_le_150_block","cost_r_ge_030_block","_trend_lock_until_ms","pee4_lock1h")
    leaked=[x for x in forbidden if x in submit_source]
    assert not leaked, f"legacy gate leaked into V2.0 pure submit: {leaked}"

    assert TrendRangeMeanReversionModel.THRESHOLD == 0.0
    assert TrendRangeMeanReversionModel.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert TrendRangeMeanReversionModel.FIVE_MINUTE_BOLL_ENABLED is False
    print(
        "V200_PREFLIGHT_PASS",
        f"days={DAYS}",
        f"required_factor_keys={sorted(required)}",
        "front_gate=OFF","cost_gate=OFF","pee4=OFF","lock1h=OFF",
        "ordinary_pending_exit_finish=PASS",
        flush=True,
    )
    return True


def main():
    started=time.perf_counter()
    start,end=configure()
    OUT.mkdir(parents=True,exist_ok=True)

    print(
        "V200_PURE_CONFIG",
        f"days={DAYS}",
        "4H impulse=volume>=1.5x OR BOLL-width>=1.2x",
        "range=post-impulse 4H max30",
        "entry=trend-side half + 5m EMA20 reversal",
        "front_gate=OFF","cost_gate=OFF","pee4=OFF","lock1h=OFF",
        "sl=1H_ATR_x1","tp=2R_full","be=OFF",
        flush=True,
    )

    meta,data,funding,cache_manifest=src.load_market(src.research.base,src.TIMEFRAMES)
    ts={tf:[int(r["t"]) for r in rows] for tf,rows in data.items()}
    src.research.cache_patch.prime_one_minute(data["1m"],src.research.MODEL_WINDOW)
    for tf in src.TIMEFRAMES:
        rows=data[tf]
        step=src.research.base.BAR_MS[tf]
        assert rows and all(int(b["t"])-int(a["t"])==step for a,b in zip(rows,rows[1:])), f"AUDIT FAIL {tf}"

    print("AUDIT PASS: V2.0 pure entry + ordinary SL/TP; Front/Cost/PEE4/Lock1H disabled",flush=True)
    sim,raw_metrics=src.research.run(data,ts,meta,funding,variant=f"v200_pure_{DAYS}d")
    rows=list(sim.trades)
    metrics=dict(raw_metrics)

    payload={
        "research":"V2.0 PURE: 4H impulse -> dynamic range -> trend-side mean reversion",
        "window":{"start":start.isoformat(),"end":end.isoformat(),"days":DAYS},
        "capital":src.CAPITAL,
        "strategy_lock":{
            "impulse":"4H volume ratio >=1.50 OR BOLL width ratio >=1.20; impulse candle/mid defines direction",
            "range":"post-impulse 4H high/low, max 30 bars; midpoint is mean",
            "long":"bull regime + latest closed 15m close in lower half + bullish 5m close above EMA20",
            "short":"bear regime + latest closed 15m close in upper half + bearish 5m close below EMA20",
            "score_system":False,
            "rsi_entry_gate":False,
            "macd_entry_gate":False,
            "kdj_entry_gate":False,
            "front_r_gate":False,
            "cost_r_gate":False,
            "pee4":False,
            "post_exit_lock":False,
            "break_even":False,
            "entry":"LIMIT at first closed-1m mark after signal; 60s historical touch proxy",
            "stop":"1.0 x closed 1H ATR",
            "tp":"2R full position",
            "fees_slippage":"retained in simulator and sizing",
        },
        "metrics":metrics,
        "analysis":src._analysis(rows),
        "simulator_stats":dict(sim.stats),
        "top_blockers":sim.blockers.most_common(30),
        "transitions":dict(sim.transitions),
        "cache":cache_manifest,
        "timing_sec":{"total":time.perf_counter()-started},
        "production_strategy_variables_changed":False,
    }
    (OUT/RESULT_NAME).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    src._write_csv(rows,OUT/TRADES_NAME)
    (OUT/"README.txt").write_text(
        "KAYTRADE V2.0 PURE research.\n"
        "4H impulse (volume/BOLL expansion) -> dynamic 4H range -> trend-side mean reversion -> "
        "5m EMA20 reversal confirmation. Front/Cost entry gates OFF; PEE4/Lock1H OFF. "
        "Exit only ordinary 1H ATR SL or full 2R TP.\n",
        encoding="utf-8",
    )
    print("V200_PURE_RESULT",json.dumps(payload,ensure_ascii=False,indent=2),flush=True)


if __name__=="__main__":
    if "--preflight" in sys.argv:
        preflight()
    else:
        main()

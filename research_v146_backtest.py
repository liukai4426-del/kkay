#!/usr/bin/env python3
"""KAYTRADE V1.4.6 BTC-USDT-SWAP 3-month historical backtest.

Research only. The trading rules mirror the strategy/runtime code on
codex/v1-4-6-ui-fix. Market data is pulled from OKX public REST endpoints.
The simulator uses only information available at each closed 1-minute bar.
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import os
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from zoneinfo import ZoneInfo

HOST = "https://www.okx.com"
INST = "BTC-USDT-SWAP"
START = datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
SH_TZ = ZoneInfo("Asia/Shanghai")

# V1.4.6 source-default settings. V1.4.6 itself is UI-only; trading behavior
# is inherited from V1.4.3/V1.4.1.
CAPITAL = 100.0
LEVERAGE = 5
RISK_USDT = 1.0
RISK_PCT = 1.0
DAILY_LOSS = 3.0
CONSECUTIVE_LOSSES = 3
COOLDOWN_MINUTES = 30
STOP_ATR = 1.0
REWARD_R = 2.0
MAKER_BPS = 2.0
TAKER_BPS = 5.0
SLIPPAGE_BPS = 5.0
EXPECTED_COST_MIN = 1.20
TIER_CAP = {1: 50.0, 2: 75.0, 3: 100.0}
TIER_LABEL = {1: "Tier 1 (4.0-6.0)", 2: "Tier 2 (6.5-7.5)", 3: "Tier 3 (8.0-10.0)"}
WARMUP_BARS = 1600
BAR_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000, "1Dutc": 86_400_000}

OUTDIR = Path(os.environ.get("BACKTEST_OUT", "backtest_output"))
OUTDIR.mkdir(parents=True, exist_ok=True)


def fmt_dt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def okx_get(path: str, params: dict, attempts: int = 8):
    url = HOST + path + "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json", "User-Agent": "KAYTRADE-V146-Research/1.0"}
    last = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("code") != "0":
                raise RuntimeError(f"OKX {payload.get('code')}: {payload.get('msg')}")
            return payload.get("data") or []
        except Exception as exc:
            last = exc
            if attempt == attempts:
                break
            time.sleep(min(6.0, 0.75 * attempt))
    raise RuntimeError(f"GET {path} failed after {attempts} attempts: {last}")


def fetch_candles(bar: str):
    step = BAR_MS[bar]
    want_from = START_MS - WARMUP_BARS * step
    cursor = END_MS
    rows = {}
    page = 0
    while True:
        page += 1
        batch = okx_get("/api/v5/market/history-candles", {
            "instId": INST, "bar": bar, "limit": "100", "after": str(cursor)
        })
        if not batch:
            break
        old = cursor
        oldest = old
        for r in batch:
            t = int(r[0]); oldest = min(oldest, t)
            if str(r[8]) != "1" or not (want_from <= t < END_MS):
                continue
            o, h, l, c, v = map(float, (r[1], r[2], r[3], r[4], r[5]))
            if all(math.isfinite(x) for x in (o, h, l, c, v)) and min(o, h, l, c) > 0 and v >= 0:
                rows[t] = {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}
        if oldest >= old:
            raise RuntimeError(f"Pagination did not move for {bar}: {oldest} >= {old}")
        cursor = oldest
        if oldest <= want_from:
            break
        if page % 100 == 0:
            print(f"{bar}: {len(rows):,} bars, oldest={fmt_dt(oldest)}", flush=True)
        time.sleep(0.105)
    data = [rows[t] for t in sorted(rows)]
    if not data:
        raise RuntimeError(f"No {bar} data")
    print(f"Fetched {bar}: {len(data):,} closed bars {fmt_dt(data[0]['t'])} .. {fmt_dt(data[-1]['t'])}")
    return data


def fetch_instrument():
    rows = okx_get("/api/v5/public/instruments", {"instType": "SWAP", "instId": INST})
    if not rows:
        raise RuntimeError("instrument metadata missing")
    r = rows[0]
    return {
        "tickSz": float(r["tickSz"]), "ctVal": float(r["ctVal"]),
        "ctMult": float(r.get("ctMult") or 1), "minSz": float(r["minSz"]),
        "lotSz": float(r["lotSz"]),
    }


def fetch_funding():
    cursor = END_MS
    out = {}
    while True:
        batch = okx_get("/api/v5/public/funding-rate-history", {
            "instId": INST, "limit": "100", "after": str(cursor)
        })
        if not batch:
            break
        oldest = cursor
        for r in batch:
            t = int(r["fundingTime"]); oldest = min(oldest, t)
            if START_MS <= t < END_MS:
                try: out[t] = float(r["fundingRate"])
                except Exception: pass
        if oldest >= cursor:
            break
        cursor = oldest
        if oldest < START_MS:
            break
        time.sleep(0.12)
    rows = sorted(out.items())
    print(f"Fetched funding settlements: {len(rows)}")
    return rows


def ema(values, period):
    a = 2.0 / (period + 1)
    out = [values[0]]
    v = values[0]
    for x in values[1:]:
        v += a * (x - v)
        out.append(v)
    return out


def rolling_mean_std(values, n=20):
    means = [math.nan] * len(values); sds = [math.nan] * len(values)
    s = ss = 0.0
    for i, x in enumerate(values):
        s += x; ss += x * x
        if i >= n:
            y = values[i-n]; s -= y; ss -= y*y
        if i >= n-1:
            mean = s/n
            var = max(0.0, ss/n - mean*mean)
            means[i] = mean; sds[i] = math.sqrt(var)
    return means, sds


def wilder_series(raw, length, n=14):
    out = [math.nan] * length
    if len(raw) < n:
        return out
    v = sum(raw[:n]) / n
    out[n] = v
    for j in range(n, len(raw)):
        v = (v * (n-1) + raw[j]) / n
        out[j+1] = v
    return out


def compute_indicators(rows):
    n = len(rows); close = [r["c"] for r in rows]
    e5, e10, e20, e50, e200 = (ema(close,p) for p in (5,10,20,50,200))
    gains = [max(close[i]-close[i-1],0.0) for i in range(1,n)]
    losses = [max(close[i-1]-close[i],0.0) for i in range(1,n)]
    avg_g = wilder_series(gains,n); avg_l = wilder_series(losses,n)
    tr = [max(rows[i]["h"]-rows[i]["l"], abs(rows[i]["h"]-close[i-1]), abs(rows[i]["l"]-close[i-1])) for i in range(1,n)]
    atr = wilder_series(tr,n)
    mean20, sd20 = rolling_mean_std(close,20)
    karr = [math.nan]*n; darr=[math.nan]*n; jarr=[math.nan]*n; cup=[False]*n; cdown=[False]*n
    k=d=50.0
    for i in range(8,n):
        low=min(rows[j]["l"] for j in range(i-8,i+1)); high=max(rows[j]["h"] for j in range(i-8,i+1))
        rsv=50.0 if high==low else 100.0*(close[i]-low)/(high-low)
        pk,pd=k,d
        k=(2*k+rsv)/3; d=(2*d+k)/3
        karr[i]=k; darr[i]=d; jarr[i]=3*k-2*d
        cup[i]=pk<=pd and k>d; cdown[i]=pk>=pd and k<d
    e12=ema(close,12); e26=ema(close,26); dif=[a-b for a,b in zip(e12,e26)]; dea=ema(dif,9); hist=[2*(a-b) for a,b in zip(dif,dea)]
    out=[]
    for i in range(n):
        g=avg_g[i]; l=avg_l[i]
        if math.isnan(g) or math.isnan(l): rsi=math.nan
        elif g==l==0: rsi=50.0
        elif l==0: rsi=100.0
        else: rsi=100-100/(1+g/l)
        out.append({
            "ema5":e5[i],"ema10":e10[i],"ema20":e20[i],"ema50":e50[i],"ema200":e200[i],
            "up": i>0 and e20[i]>e20[i-1] and e50[i]>e50[i-1],
            "down": i>0 and e20[i]<e20[i-1] and e50[i]<e50[i-1],
            "rsi":rsi,"atr":atr[i],"middle":mean20[i],
            "upper":mean20[i]+2*sd20[i] if not math.isnan(mean20[i]) else math.nan,
            "lower":mean20[i]-2*sd20[i] if not math.isnan(mean20[i]) else math.nan,
            "k":karr[i],"d":darr[i],"j":jarr[i],"cross_up":cup[i],"cross_down":cdown[i],
            "dif":dif[i],"dea":dea[i],"hist":hist[i],"prev_hist":hist[i-1] if i else hist[i],
        })
    return out


def floor_tick(value, tick):
    return float((Decimal(str(value))/Decimal(str(tick))).to_integral_value(rounding=ROUND_DOWN)*Decimal(str(tick)))


def ceil_tick(value, tick):
    return float((Decimal(str(value))/Decimal(str(tick))).to_integral_value(rounding=ROUND_UP)*Decimal(str(tick)))


def floor_lot(value, lot):
    return float((Decimal(str(value))/Decimal(str(lot))).to_integral_value(rounding=ROUND_DOWN)*Decimal(str(lot)))


def mapped_index(ts_list, signal_close_ms, step):
    return bisect.bisect_right(ts_list, signal_close_ms-step) - 1


class ZoneCache:
    def __init__(self, data, inds, lookback):
        self.data=data; self.inds=inds; self.lookback=lookback; self.cache={}
    def zones(self, end):
        if end in self.cache: return self.cache[end]
        atr=float(self.inds[end]["atr"])
        if not math.isfinite(atr) or atr<=0:
            self.cache[end]=[]; return []
        n=end+1; start=max(0,n-self.lookback); radius=3; pts=[]
        for i in range(start+radius,n-radius):
            left=self.data[i-radius:i]; right=self.data[i+1:i+radius+1]; row=self.data[i]
            low=row["l"]; high=row["h"]
            if low <= min(r["l"] for r in left+right) and (low < min(r["l"] for r in left) or low < min(r["l"] for r in right)):
                pts.append({"kind":"support","price":float(low),"i":i})
            if high >= max(r["h"] for r in left+right) and (high > max(r["h"] for r in left) or high > max(r["h"] for r in right)):
                pts.append({"kind":"resistance","price":float(high),"i":i})
        tolerance=.25*atr; clusters=[]
        for p in pts:
            same=[z for z in clusters if z["kind"]==p["kind"] and abs(z["center"]-p["price"])<=tolerance]
            if same:
                z=min(same,key=lambda x:abs(x["center"]-p["price"])); z["points"].append(p)
                z["center"]=sum(x["price"] for x in z["points"])/len(z["points"]); z["last_i"]=max(z["last_i"],p["i"])
            else:
                clusters.append({"kind":p["kind"],"center":p["price"],"points":[p],"last_i":p["i"]})
        out=[]
        for z in clusters:
            tests=len(z["points"])
            if tests<2: continue
            center=z["center"]
            after=self.data[z["last_i"]+1:end+1]
            broken=any(r["c"]<center-.3*atr for r in after) if z["kind"]=="support" else any(r["c"]>center+.3*atr for r in after)
            if broken: continue
            age=max(0,end-z["last_i"]); recency=max(0.0,1.0-age/max(1,self.lookback))
            out.append({"kind":z["kind"],"price":center,"tests":tests,"age":age,"recency":recency,"strong":tests>=3 and recency>=.25})
        self.cache[end]=out
        return out


def nearest(zones, price, kind, ahead=None, strong_only=False):
    c=[]
    for z in zones:
        if z["kind"]!=kind or (strong_only and not z.get("strong")): continue
        if ahead=="above" and z["price"]<=price: continue
        if ahead=="below" and z["price"]>=price: continue
        c.append(z)
    return min(c,key=lambda z:abs(z["price"]-price)) if c else None


def reversal(data,i,buy):
    if i<1:return False
    c,p=data[i],data[i-1]
    return (c["c"]>c["o"] and c["c"]>p["h"]) if buy else (c["c"]<c["o"] and c["c"]<p["l"])


def ema_reclaim(data,inds,i,buy):
    if i<1:return False
    c,p=data[i],data[i-1]; cur,prev=inds[i],inds[i-1]
    return (p["c"]<=prev["ema20"] and c["c"]>cur["ema20"]) if buy else (p["c"]>=prev["ema20"] and c["c"]<cur["ema20"])


def trigger_score(data,inds,i,buy):
    if i<1:return 0.0
    c=data[i]; cur=inds[i]; prev=inds[i-1]
    a=ema_reclaim(data,inds,i,buy)
    b=cur["cross_up"] if buy else cur["cross_down"]
    r=reversal(data,i,buy)
    e=(c["c"]>cur["ema20"] and cur["ema20"]>prev["ema20"]) if buy else (c["c"]<cur["ema20"] and cur["ema20"]<prev["ema20"])
    return min(1.0,.5*(int(a)+int(b)+int(r)+int(e)))


def setup_score(data,inds,i,buy):
    if i<20:return 0.0
    c=data[i]; cur=inds[i]
    boll=c["l"]<=cur["lower"] if buy else c["h"]>=cur["upper"]
    hist=[float(data[j]["v"]) for j in range(i-20,i)]; avg=sum(hist)/20
    ratio=float(c["v"])/avg if avg>0 else 0.0
    returned=(c["l"]<=cur["lower"] and c["c"]>cur["lower"]) if buy else (c["h"]>=cur["upper"] and c["c"]<cur["upper"])
    volume_boll=returned and ratio>=1.3
    kdj=(cur["j"]<=30 and cur["cross_up"]) if buy else (cur["j"]>=70 and cur["cross_down"])
    rev=reversal(data,i,buy)
    return min(1.5,.5*int(boll)+1.0*int(volume_boll)+.5*int(kdj)+.5*int(rev))


def trend_score(data,inds,i,buy):
    if i<1:return 0.0
    cur=inds[i]; prev=inds[i-1]
    if buy:
        macd=cur["dif"]>cur["dea"] and cur["hist"]>0 and cur["hist"]>=cur["prev_hist"]
        boll=data[i]["c"]>cur["middle"] and cur["middle"]>prev["middle"]
    else:
        macd=cur["dif"]<cur["dea"] and cur["hist"]<0 and cur["hist"]<=cur["prev_hist"]
        boll=data[i]["c"]<cur["middle"] and cur["middle"]<prev["middle"]
    return float(int(macd)+int(boll))


def trend_penalty(data,inds,i,buy):
    c=data[i]; cur=inds[i]
    opp=(c["c"]<cur["ema200"] and cur["ema20"]<cur["ema50"] and cur["down"]) if buy else (c["c"]>cur["ema200"] and cur["ema20"]>cur["ema50"] and cur["up"])
    return -1.0 if opp else 0.0


def daily_ema_score(data,inds,i,price,buy):
    cur=inds[i]; atr=float(cur["atr"])
    if not math.isfinite(atr) or atr<=0 or i<1:return 0.0
    tolerance=.20*atr
    for period,weight in ((20,3.0),(10,2.0),(5,1.0)):
        key=f"ema{period}"; value=cur[key]; previous=inds[i-1][key]
        slope_ok=value>=previous if buy else value<=previous
        side_ok=price>=value-.05*atr if buy else price<=value+.05*atr
        if slope_ok and side_ok and abs(price-value)<=tolerance:return weight
    return 0.0


def rsi_penalty(i15,i5,buy):
    a=float(i15["rsi"]); b=float(i5["rsi"])
    if buy:
        if a>80 and b>80:return -2.0
        if a>75 and b>75:return -1.0
    else:
        if a<20 and b<20:return -2.0
        if a<25 and b<25:return -1.0
    return 0.0


def tier(total):
    if total>=8.0:return 3
    if total>=6.5:return 2
    if total>=4.0:return 1
    return 0


@dataclass
class Market:
    data: dict
    inds: dict
    ts: dict
    zones: dict

    def score(self, i1):
        signal_close=self.data["1m"][i1]["t"]+BAR_MS["1m"]
        idx={tf:mapped_index(self.ts[tf],signal_close,BAR_MS[tf]) for tf in ("5m","15m","1H","4H","1Dutc")}
        if min(idx.values())<1: return {"side":"观望","scores":{}}
        i5,i15,i1h,i4h,id1=(idx["5m"],idx["15m"],idx["1H"],idx["4H"],idx["1Dutc"])
        price=float(self.data["1m"][i1]["c"]); results={}
        hz=self.zones["1H"].zones(i1h); mz=self.zones["15m"].zones(i15); qz=self.zones["4H"].zones(i4h)
        for side in ("做多","做空"):
            buy=side=="做多"
            de=daily_ema_score(self.data["1Dutc"],self.inds["1Dutc"],id1,price,buy)
            kind="support" if buy else "resistance"
            z15=nearest(mz,price,kind)
            fav15=.5 if z15 and abs(z15["price"]-price)/self.inds["15m"][i15]["atr"]<=.25 else 0.0
            setup=setup_score(self.data["15m"],self.inds["15m"],i15,buy)
            trig=trigger_score(self.data["1m"],self.inds["1m"],i1,buy)
            tr5=trend_score(self.data["5m"],self.inds["5m"],i5,buy)
            tr15=trend_score(self.data["15m"],self.inds["15m"],i15,buy)
            rp=rsi_penalty(self.inds["15m"][i15],self.inds["5m"][i5],buy)
            adverse_kind="resistance" if buy else "support"; ahead="above" if buy else "below"
            zh=nearest(hz,price,adverse_kind,ahead); zq=nearest(qz,price,adverse_kind,ahead)
            s1=-1.0 if zh and abs(zh["price"]-price)/self.inds["1H"][i1h]["atr"]<=.25 else 0.0
            s4=-1.5 if zq and abs(zq["price"]-price)/self.inds["4H"][i4h]["atr"]<=.25 else 0.0
            p1=trend_penalty(self.data["1H"],self.inds["1H"],i1h,buy)
            p4=trend_penalty(self.data["4H"],self.inds["4H"],i4h,buy)
            fprice=float(self.data["15m"][i15]["c"]); opposite="resistance" if buy else "support"
            forward=[]
            for tf,zs,ii in (("1H",hz,i1h),("15m",mz,i15)):
                z=nearest(zs,fprice,opposite,ahead,strong_only=True)
                if z: forward.append((abs(z["price"]-fprice),tf,z))
            front=min(forward,key=lambda x:x[0]) if forward else None
            risk=float(self.inds["15m"][i15]["atr"])*STOP_ATR
            front_r=front[0]/risk if front and risk>0 else math.inf
            blocked=front_r<1.0
            front_penalty=-1.0 if 1.0<=front_r<1.3 else 0.0
            raw=de+fav15+setup+trig+tr5+tr15+rp+s1+s4+p1+p4+front_penalty
            total=max(0.0,min(10.0,round(raw*2)/2))
            ti=tier(total); gate=trig>0 and not blocked; eligible=gate and ti>0
            results[side]={"total":total,"raw":raw,"trigger":trig,"gate":gate,"eligible":eligible,"tier":ti,
                           "atr15":float(self.inds["15m"][i15]["atr"]),"atr1h":float(self.inds["1H"][i1h]["atr"]),
                           "front_r":front_r,"components":{"daily":de,"fav15":fav15,"setup":setup,"trigger":trig,"trend5":tr5,"trend15":tr15,
                           "rsi":rp,"struct1h":s1,"struct4h":s4,"trend1h":p1,"trend4h":p4,"front":front_penalty}}
        eligible=[s for s in ("做多","做空") if results[s]["eligible"]]
        if len(eligible)==1: selected=eligible[0]
        elif len(eligible)==2:
            a,b=results[eligible[0]]["total"],results[eligible[1]]["total"]
            selected="观望" if abs(a-b)<=1e-9 else max(eligible,key=lambda s:results[s]["total"])
        else:selected="观望"
        return {"side":selected,"scores":results,"idx":idx,"price":price,"signal_close":signal_close}


@dataclass
class Leg:
    leg_id:int; cycle_id:int; side:str; tier:int; score:float; signal_ms:int; submitted_ms:int
    entry:float; qty:float; notional:float; sl:float; tp:float; est_loss:float; atr15:float
    status:str="pending"; fill_ms:int|None=None; exit_ms:int|None=None; exit_price:float|None=None; reason:str=""
    entry_fee:float=0.0; exit_fee:float=0.0; funding:float=0.0; stop_child_pending:bool=False
    def direction(self): return 1.0 if self.side=="做多" else -1.0


@dataclass
class Cycle:
    cycle_id:int; side:str; start_equity:float; start_ms:int; highest_tier:int=0
    tier_counts:dict=field(default_factory=lambda:{1:0,2:0,3:0}); legs:list=field(default_factory=list)
    end_ms:int|None=None; pnl:float|None=None


class Simulator:
    def __init__(self, market:Market, meta, funding):
        self.m=market; self.meta=meta; self.funding=funding; self.funding_i=0
        self.cash=CAPITAL; self.peak_equity=CAPITAL; self.max_dd=0.0; self.max_dd_pct=0.0
        self.daily_day=None; self.daily_peak=CAPITAL; self.daily_block_day=None
        self.streak_day=None; self.loss_streak=0; self.last_close_ms=-10**18
        self.active:Cycle|None=None; self.cycles=[]; self.legs=[]; self.next_leg=1; self.next_cycle=1
        self.equity_curve=[]; self.daily_equity={}; self.stats=defaultdict(int)
        self.stress_extra=0.0
    def open_legs(self): return [l for l in self.legs if l.status=="open" or l.stop_child_pending]
    def pending_legs(self): return [l for l in self.legs if l.status=="pending"]
    def mark_equity(self, price):
        unreal=sum((price-l.entry)*l.qty*l.direction() for l in self.open_legs())
        return self.cash+unreal
    def update_day(self, ms, equity):
        day=datetime.fromtimestamp(ms/1000,tz=timezone.utc).astimezone(SH_TZ).strftime("%Y-%m-%d")
        if self.daily_day!=day:
            self.daily_day=day; self.daily_peak=equity; self.daily_block_day=None
        else:self.daily_peak=max(self.daily_peak,equity)
        if self.streak_day!=day:
            self.streak_day=day; self.loss_streak=0
        if self.daily_peak-equity>=DAILY_LOSS-1e-12:
            self.daily_block_day=day
        return day
    def available(self, price):
        eq=self.mark_equity(price); margin=sum(l.notional/LEVERAGE for l in self.open_legs())
        return max(0.0,eq-margin)
    def daily_remaining(self, equity): return max(0.0,DAILY_LOSS-max(0.0,self.daily_peak-equity))
    def plan(self, side, ti, score, signal_price, atr15, equity, daily_remaining):
        buy=side=="做多"; tick=self.meta["tickSz"]
        entry=floor_tick(signal_price,tick) if buy else ceil_tick(signal_price,tick)
        dist=atr15*STOP_ATR; sl=(floor_tick(entry-dist,tick) if buy else ceil_tick(entry+dist,tick)); tp=(ceil_tick(entry+2*dist,tick) if buy else floor_tick(entry-2*dist,tick))
        if not ((sl<entry<tp) if buy else (tp<entry<sl)): return None
        maker=MAKER_BPS/10000; taker=TAKER_BPS/10000; slip=SLIPPAGE_BPS/10000
        per_btc=abs(entry-sl)+entry*max(maker,taker)+sl*(taker+slip)
        risk_capital=min(CAPITAL,equity); base=min(RISK_USDT,risk_capital*RISK_PCT/100)
        mult={1:1.0,2:1.5,3:2.0}[ti]; risk=min(base*mult,daily_remaining)
        if self.active:
            current_risk=sum(l.est_loss for l in self.active.legs if l.status=="open")
            risk=min(risk,max(0.0,daily_remaining-current_risk))
        avail=self.available(signal_price)
        notional_cap=min(TIER_CAP[ti],risk_capital*LEVERAGE,avail*.9*LEVERAGE)
        unit=self.meta["ctVal"]*self.meta["ctMult"]
        contracts=floor_lot(min(risk/per_btc,notional_cap/entry)/unit,self.meta["lotSz"])
        if contracts+1e-12<self.meta["minSz"] or contracts<=0:return None
        qty=contracts*unit; notional=qty*entry; est=qty*per_btc
        expected_cost_per_btc=entry*maker+tp*taker; multiple=2*dist/expected_cost_per_btc if expected_cost_per_btc>0 else math.inf
        if multiple<EXPECTED_COST_MIN:return None
        return entry,qty,notional,sl,tp,est,multiple
    def can_signal(self, side, ti, signal_ms):
        if ti<=0:return False
        day=datetime.fromtimestamp(signal_ms/1000,tz=timezone.utc).astimezone(SH_TZ).strftime("%Y-%m-%d")
        if self.daily_block_day==day:self.stats["daily_risk_blocks"]+=1;return False
        if self.loss_streak>=CONSECUTIVE_LOSSES:self.stats["streak_blocks"]+=1;return False
        if self.pending_legs():return False
        if self.active is None:
            if signal_ms-self.last_close_ms<COOLDOWN_MINUTES*60_000:self.stats["cooldown_blocks"]+=1;return False
            return True
        if side!=self.active.side:return False
        if ti<self.active.highest_tier:return False
        if self.active.tier_counts[ti]>=1:return False
        if ti==1 and self.active.legs:return False
        return True
    def submit(self, sig, i1, price):
        side=sig["side"]
        if side=="观望":return
        row=sig["scores"][side]; ti=row["tier"]; signal_ms=sig["signal_close"]
        if not self.can_signal(side,ti,signal_ms):return
        eq=self.mark_equity(price); remain=self.daily_remaining(eq)
        p=self.plan(side,ti,row["total"],sig["price"],row["atr15"],eq,remain)
        if p is None:self.stats["sizing_or_cost_skips"]+=1;return
        entry,qty,notional,sl,tp,est,_=p
        if self.active is None:
            self.active=Cycle(self.next_cycle,side,eq,signal_ms);self.next_cycle+=1;self.cycles.append(self.active)
        c=self.active; c.highest_tier=max(c.highest_tier,ti); c.tier_counts[ti]+=1
        leg=Leg(self.next_leg,c.cycle_id,side,ti,row["total"],signal_ms,signal_ms,entry,qty,notional,sl,tp,est,row["atr15"])
        self.next_leg+=1;c.legs.append(leg);self.legs.append(leg);self.stats["orders_submitted"]+=1
    def fill_pending(self, bar):
        for l in list(self.pending_legs()):
            touched=bar["l"]<=l.entry if l.side=="做多" else bar["h"]>=l.entry
            if touched:
                l.status="open";l.fill_ms=bar["t"];l.entry_fee=l.qty*l.entry*MAKER_BPS/10000;self.cash-=l.entry_fee;self.stats["orders_filled"]+=1
                self.handle_open_leg_exit(l,bar,just_filled=True)
            elif bar["t"]+60_000>=l.submitted_ms+60_000:
                l.status="cancelled";l.reason="limit_unfilled_60s";l.exit_ms=bar["t"]+60_000;self.stats["orders_cancelled"]+=1
        self.cleanup_cycle(bar["t"]+60_000)
    def handle_open_leg_exit(self,l,bar,just_filled=False):
        if l.status!="open" and not l.stop_child_pending:return
        d=l.direction()
        if l.stop_child_pending:
            can_fill=bar["h"]>=l.sl if d>0 else bar["l"]<=l.sl
            if can_fill:self.close_leg(l,l.sl,bar["t"],"SL_limit_recovered")
            return
        hit_sl=bar["l"]<=l.sl if d>0 else bar["h"]>=l.sl
        hit_tp=bar["h"]>=l.tp if d>0 else bar["l"]<=l.tp
        if hit_sl:
            gap_miss=(bar["o"]<l.sl and bar["h"]<l.sl) if d>0 else (bar["o"]>l.sl and bar["l"]>l.sl)
            if gap_miss:
                l.stop_child_pending=True; self.stats["stop_limit_gap_misses"]+=1;return
            self.close_leg(l,l.sl,bar["t"],"SL");return
        if hit_tp:self.close_leg(l,l.tp,bar["t"],"TP_2R")
    def close_leg(self,l,px,ms,reason):
        if l.status=="closed":return
        l.status="closed";l.stop_child_pending=False;l.exit_ms=ms;l.exit_price=float(px);l.reason=reason
        gross=(l.exit_price-l.entry)*l.qty*l.direction(); l.exit_fee=l.qty*l.exit_price*TAKER_BPS/10000
        self.cash+=gross-l.exit_fee
        self.stress_extra+=l.qty*l.exit_price*SLIPPAGE_BPS/10000
        self.stats["legs_closed"]+=1
    def process_exits(self,bar):
        for l in list(self.open_legs()):self.handle_open_leg_exit(l,bar)
        self.cleanup_cycle(bar["t"]+60_000)
    def cleanup_cycle(self,ms):
        c=self.active
        if c is None:return
        alive=any(l.status in ("pending","open") or l.stop_child_pending for l in c.legs)
        if alive:return
        filled=[l for l in c.legs if l.fill_ms is not None]
        if not filled:
            self.cycles.remove(c);self.active=None;return
        c.end_ms=ms;c.pnl=self.cash-c.start_equity
        self.loss_streak=self.loss_streak+1 if c.pnl<0 else 0
        self.last_close_ms=ms;self.active=None
    def apply_funding_until(self,bar_close,price):
        while self.funding_i<len(self.funding) and self.funding[self.funding_i][0]<=bar_close:
            t,rate=self.funding[self.funding_i]; self.funding_i+=1
            if t<START_MS:continue
            for l in self.open_legs():
                if l.fill_ms is not None and l.fill_ms<t and (l.exit_ms is None or t<l.exit_ms):
                    amount=-l.direction()*l.qty*price*rate;l.funding+=amount;self.cash+=amount
    def record_equity(self,ms,price):
        eq=self.mark_equity(price);day=self.update_day(ms,eq)
        self.peak_equity=max(self.peak_equity,eq);dd=self.peak_equity-eq
        self.max_dd=max(self.max_dd,dd);self.max_dd_pct=max(self.max_dd_pct,dd/self.peak_equity*100 if self.peak_equity else 0)
        self.equity_curve.append((ms,eq));self.daily_equity[day]=eq
    def finish(self,last_bar):
        end_ms=END_MS
        for l in self.pending_legs():l.status="cancelled";l.reason="period_end_unfilled";l.exit_ms=end_ms
        px=last_bar["c"]
        for l in list(self.open_legs()):self.close_leg(l,px,end_ms,"period_end_mark")
        self.cleanup_cycle(end_ms);self.record_equity(end_ms,px)


def analyze(sim:Simulator, data1, funding_rows, meta):
    filled=[l for l in sim.legs if l.fill_ms is not None]
    closed=[l for l in filled if l.exit_ms is not None]
    cycles=[c for c in sim.cycles if c.pnl is not None]
    def leg_net(l):
        gross=(l.exit_price-l.entry)*l.qty*l.direction() if l.exit_price is not None else 0
        return gross-l.entry_fee-l.exit_fee+l.funding
    leg_pnls=[leg_net(l) for l in closed]
    wins=[x for x in leg_pnls if x>0];losses=[x for x in leg_pnls if x<0]
    cycle_pnls=[c.pnl for c in cycles]
    cw=[x for x in cycle_pnls if x>0];cl=[x for x in cycle_pnls if x<0]
    gross_profit=sum(wins);gross_loss=-sum(losses);pf=gross_profit/gross_loss if gross_loss>0 else math.inf
    avg_win=statistics.mean(cw) if cw else 0;avg_loss=statistics.mean(cl) if cl else 0
    payoff=abs(avg_win/avg_loss) if avg_loss else math.inf
    start_px=next(r["o"] for r in data1 if r["t"]>=START_MS);end_px=[r for r in data1 if r["t"]<END_MS][-1]["c"]
    benchmark=(end_px/start_px-1)*100
    by_side={};by_tier={};by_month={};by_reason={}
    for label,selector in (("做多",lambda l:l.side=="做多"),("做空",lambda l:l.side=="做空")):
        xs=[leg_net(l) for l in closed if selector(l)];by_side[label]=(len(xs),sum(xs),sum(1 for x in xs if x>0)/len(xs)*100 if xs else 0)
    for t in (1,2,3):
        xs=[leg_net(l) for l in closed if l.tier==t];by_tier[t]=(len(xs),sum(xs),sum(1 for x in xs if x>0)/len(xs)*100 if xs else 0)
    for l in closed:
        month=datetime.fromtimestamp(l.exit_ms/1000,tz=timezone.utc).strftime("%Y-%m");by_month.setdefault(month,[0,0.0]);by_month[month][0]+=1;by_month[month][1]+=leg_net(l)
        by_reason[l.reason]=by_reason.get(l.reason,0)+1
    durations=[(l.exit_ms-l.fill_ms)/60000 for l in closed if l.fill_ms is not None and l.exit_ms is not None]
    trade_fees=sum(l.entry_fee+l.exit_fee for l in closed);funding=sum(l.funding for l in closed)
    net=sim.cash-CAPITAL;stress=net-sim.stress_extra
    maxwin=maxloss=runw=runl=0
    for x in cycle_pnls:
        if x>0:runw+=1;runl=0;maxwin=max(maxwin,runw)
        elif x<0:runl+=1;runw=0;maxloss=max(maxloss,runl)
        else:runw=runl=0
    metrics={
        "period_start":START.isoformat(),"period_end":END.isoformat(),"days":(END-START).days,"instrument":INST,
        "start_price":start_px,"end_price":end_px,"btc_buy_hold_pct":benchmark,
        "starting_capital":CAPITAL,"ending_equity":sim.cash,"net_pnl":net,"net_return_pct":net/CAPITAL*100,
        "stress_net_pnl":stress,"stress_return_pct":stress/CAPITAL*100,
        "cycles":len(cycles),"filled_legs":len(closed),"orders_submitted":sim.stats["orders_submitted"],"orders_filled":sim.stats["orders_filled"],"orders_cancelled":sim.stats["orders_cancelled"],
        "cycle_win_rate_pct":len(cw)/len(cycles)*100 if cycles else 0,"leg_win_rate_pct":len(wins)/len(closed)*100 if closed else 0,
        "profit_factor":pf,"expectancy_cycle":statistics.mean(cycle_pnls) if cycle_pnls else 0,"avg_win_cycle":avg_win,"avg_loss_cycle":avg_loss,"payoff_ratio":payoff,
        "max_drawdown_usdt":sim.max_dd,"max_drawdown_pct":sim.max_dd_pct,"max_consecutive_wins":maxwin,"max_consecutive_losses":maxloss,
        "fees":trade_fees,"funding_pnl":funding,"stress_extra_slippage":sim.stress_extra,
        "avg_hold_min":statistics.mean(durations) if durations else 0,"median_hold_min":statistics.median(durations) if durations else 0,
        "stop_limit_gap_misses":sim.stats["stop_limit_gap_misses"],"daily_risk_blocks":sim.stats["daily_risk_blocks"],"streak_blocks":sim.stats["streak_blocks"],"cooldown_blocks":sim.stats["cooldown_blocks"],"sizing_or_cost_skips":sim.stats["sizing_or_cost_skips"],
        "meta":meta,"funding_settlements":len(funding_rows),"by_side":by_side,"by_tier":by_tier,"by_month":by_month,"by_reason":by_reason,
    }
    return metrics,leg_net,cycles,closed


def write_outputs(sim,metrics,leg_net,cycles,closed):
    with (OUTDIR/"V146_BTC_SWAP_3M_Trades.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.writer(f);w.writerow(["leg_id","cycle_id","side","tier","score","signal_utc","fill_utc","entry","qty_btc","notional_usdt","sl","tp","exit_utc","exit_price","exit_reason","gross_pnl","entry_fee","exit_fee","funding_pnl","net_pnl","hold_min"])
        for l in closed:
            gross=(l.exit_price-l.entry)*l.qty*l.direction();net=leg_net(l);hold=(l.exit_ms-l.fill_ms)/60000
            w.writerow([l.leg_id,l.cycle_id,l.side,l.tier,l.score,fmt_dt(l.signal_ms),fmt_dt(l.fill_ms),l.entry,l.qty,l.notional,l.sl,l.tp,fmt_dt(l.exit_ms),l.exit_price,l.reason,gross,l.entry_fee,l.exit_fee,l.funding,net,hold])
    with (OUTDIR/"V146_BTC_SWAP_3M_Cycles.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.writer(f);w.writerow(["cycle_id","side","start_utc","end_utc","legs","tier1","tier2","tier3","net_pnl"])
        for c in cycles:w.writerow([c.cycle_id,c.side,fmt_dt(c.start_ms),fmt_dt(c.end_ms),sum(1 for l in c.legs if l.fill_ms is not None),c.tier_counts[1],c.tier_counts[2],c.tier_counts[3],c.pnl])
    with (OUTDIR/"V146_BTC_SWAP_3M_DailyEquity.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.writer(f);w.writerow(["china_date","ending_equity_usdt","cumulative_return_pct"])
        for d,e in sorted(sim.daily_equity.items()):w.writerow([d,e,(e/CAPITAL-1)*100])
    with (OUTDIR/"metrics.json").open("w",encoding="utf-8") as f:json.dump(metrics,f,ensure_ascii=False,indent=2)

    def num(x):return "∞" if math.isinf(x) else f"{x:.3f}"
    lines=[]
    lines += ["# KAYTRADE V1.4.6 — BTC-USDT-SWAP 三个月历史回测报告","",
              f"**回测区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（{metrics['days']}天）  ",
              f"**数据源：** OKX 公共历史 K 线 / funding-rate-history，标的 `{INST}`。  ",
              "**策略版本：** V1.4.6（界面更新），交易逻辑继承 V1.4.3 / V1.4.1。","",
              "## 1. 核心结果","",
              "| 指标 | 结果 |","|---|---:|",
              f"| 初始策略资金 | {CAPITAL:.2f} USDT |",f"| 期末权益 | {metrics['ending_equity']:.2f} USDT |",f"| 净收益 | {metrics['net_pnl']:+.2f} USDT |",f"| 净收益率 | {metrics['net_return_pct']:+.2f}% |",
              f"| 压力情景收益率（每次退出再扣5bps滑点） | {metrics['stress_return_pct']:+.2f}% |",f"| 完整交易轮次 | {metrics['cycles']} |",f"| 实际成交腿数 | {metrics['filled_legs']} |",
              f"| 轮次胜率 | {metrics['cycle_win_rate_pct']:.2f}% |",f"| 单腿胜率 | {metrics['leg_win_rate_pct']:.2f}% |",f"| Profit Factor | {num(metrics['profit_factor'])} |",
              f"| 每轮期望 | {metrics['expectancy_cycle']:+.4f} USDT |",f"| 平均盈利轮次 | {metrics['avg_win_cycle']:+.4f} USDT |",f"| 平均亏损轮次 | {metrics['avg_loss_cycle']:+.4f} USDT |",f"| 盈亏金额比 | {num(metrics['payoff_ratio'])} |",
              f"| 最大回撤 | {metrics['max_drawdown_usdt']:.2f} USDT / {metrics['max_drawdown_pct']:.2f}% |",f"| 最大连续盈利/亏损 | {metrics['max_consecutive_wins']} / {metrics['max_consecutive_losses']} |",
              f"| 交易手续费 | {metrics['fees']:.2f} USDT |",f"| Funding净影响 | {metrics['funding_pnl']:+.4f} USDT |",f"| 平均/中位持仓 | {metrics['avg_hold_min']:.1f} / {metrics['median_hold_min']:.1f} 分钟 |",
              f"| BTC同期买入持有 | {metrics['btc_buy_hold_pct']:+.2f}% |","",
              "## 2. 使用的 V1.4.6 参数","",
              "本回测没有读取你 Mac 本机的私有设置文件，而是使用代码默认值：策略资金100 USDT、5×逐仓、基础风险1 USDT/1%、日内权益回撤上限3 USDT、连续亏损3次停开、平仓后30分钟冷却、15m ATR×1止损、2R整仓止盈；一级/二级/三级信号名义仓位上限分别为50/75/100 USDT。","",
              "评分完整复现：1D EMA5/10/20、有利15m结构、15m Setup、1m Trigger硬门槛、5m/15m MACD+BOLL、5m+15m RSI惩罚、1H/4H不利结构及逆势惩罚、前方结构空间过滤；多空同时合格时只取高分侧，同分观望。","",
              "## 3. 方向与信号等级表现","",
              "| 分类 | 成交腿数 | 净PnL USDT | 胜率 |","|---|---:|---:|---:|"]
    for side,(n,p,w) in metrics["by_side"].items():lines.append(f"| {side} | {n} | {p:+.3f} | {w:.2f}% |")
    for t,(n,p,w) in metrics["by_tier"].items():lines.append(f"| {TIER_LABEL[t]} | {n} | {p:+.3f} | {w:.2f}% |")
    lines += ["","## 4. 月度结果","","| 月份（UTC） | 平仓腿数 | 净PnL USDT |","|---|---:|---:|"]
    for m,(n,p) in sorted(metrics["by_month"].items()):lines.append(f"| {m} | {n} | {p:+.3f} |")
    lines += ["","## 5. 成交与风险控制统计","",
              f"- 提交限价开仓 {metrics['orders_submitted']} 次；成交 {metrics['orders_filled']} 次；60秒未成交取消 {metrics['orders_cancelled']} 次。",
              f"- 止损 trigger-limit 发生整根1m跳空越过限价的模拟事件：{metrics['stop_limit_gap_misses']} 次。",
              f"- 日回撤限制拦截 {metrics['daily_risk_blocks']} 次；连续亏损限制拦截 {metrics['streak_blocks']} 次；30分钟冷却拦截 {metrics['cooldown_blocks']} 次。",
              f"- 因最小下单量 / 风险额度 / 成本过滤跳过 {metrics['sizing_or_cost_skips']} 次。","",
              "退出原因：" + "；".join(f"{k}={v}" for k,v in sorted(metrics["by_reason"].items())),"",
              "## 6. 回测执行假设与局限","",
              "1. 信号只使用当时已经收盘的K线，避免未来函数。限价开仓按信号收盘价（买单向下、卖单向上按tick取整）挂出，并只允许下一根1m K线内成交，模拟源码60秒有效期。",
              "2. OHLC历史数据没有真实逐笔盘口，因此无法复原实际 bid/ask、排队位置和部分成交。本报告把触及限价视为成交；若同一1m同时触及SL与TP，按更保守的SL优先。",
              "3. V1.4.1 的TP/SL是 trigger-limit，触发价=限价。基准结果假设价格触及时能够成交；若1m开盘直接跳过止损价且整根K线未回到止损限价，则模拟为挂单未成交并等待回价，同时停止新增风险。",
              "4. 基准净收益包含2bps maker开仓费、5bps退出费和历史funding；不把5bps“滑点预算”直接当成必然实际成本。另给出每次退出额外扣5bps的压力情景。",
              "5. 日回撤以1m收盘的盯市权益估算；实际OKX权益、手续费等级、成交价格和funding结算细节会造成偏差。回测结果不代表未来收益。","",
              "## 7. 结论判定",""]
    if metrics["net_pnl"]>0 and metrics["profit_factor"]>1:
        conclusion="这3个月样本在上述基准假设下为正期望。"
    else:
        conclusion="这3个月样本在上述基准假设下未显示稳定正期望。"
    lines.append(conclusion+f" 净收益率 {metrics['net_return_pct']:+.2f}%，最大回撤 {metrics['max_drawdown_pct']:.2f}%，Profit Factor {num(metrics['profit_factor'])}。是否适合实盘仍应结合更长时间的滚动样本、真实模拟盘成交记录和参数敏感性测试判断。")
    (OUTDIR/"V146_BTC_SWAP_3M_Backtest_Report.md").write_text("\n".join(lines),encoding="utf-8")


def main():
    meta=fetch_instrument();print("Instrument:",meta)
    data={}
    for tf in ("1m","5m","15m","1H","4H","1Dutc"):data[tf]=fetch_candles(tf)
    funding=fetch_funding()
    inds={tf:compute_indicators(rows) for tf,rows in data.items()}
    ts={tf:[r["t"] for r in rows] for tf,rows in data.items()}
    zones={"15m":ZoneCache(data["15m"],inds["15m"],160),"1H":ZoneCache(data["1H"],inds["1H"],120),"4H":ZoneCache(data["4H"],inds["4H"],180)}
    market=Market(data,inds,ts,zones);sim=Simulator(market,meta,funding)
    d1=data["1m"];start_i=bisect.bisect_left(ts["1m"],START_MS)
    if start_i<1:raise RuntimeError("not enough 1m warmup")
    for i in range(start_i,len(d1)):
        bar=d1[i];bar_close=bar["t"]+60_000
        if bar["t"]>=END_MS:break
        sim.apply_funding_until(bar["t"],bar["o"])
        sim.fill_pending(bar)
        sim.process_exits(bar)
        sim.apply_funding_until(bar_close,bar["c"])
        sim.record_equity(bar_close,bar["c"])
        sig=market.score(i)
        sim.submit(sig,i,bar["c"])
        if (i-start_i)%10000==0:print(f"progress {i-start_i:,}/{len(d1)-start_i:,}, equity={sim.mark_equity(bar['c']):.3f}, cycles={len(sim.cycles)}",flush=True)
    last=[r for r in d1 if r["t"]<END_MS][-1];sim.finish(last)
    metrics,leg_net,cycles,closed=analyze(sim,d1,funding,meta);write_outputs(sim,metrics,leg_net,cycles,closed)
    print(json.dumps(metrics,ensure_ascii=False,indent=2))
    print("OUTPUT_DIR",OUTDIR.resolve())

if __name__=="__main__":main()

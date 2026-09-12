#!/usr/bin/env python3
"""Standalone KAYTRADE V1.5.0 BTC-USDT-SWAP six-month backtest.

This file intentionally does NOT import any V1.4/V1.3 research backtest module.
It implements V1.5.0 strategy semantics directly:
- fixed score threshold >= 6.0
- one unified 1x opening signal only; no add-ons/upgrades
- V1.4.7 hard gates and score components inherited by V1.5.0
- no 1D factor
- valid 1m Trigger = EMA20 reclaim / KDJ cross / reversal, capped +1
- 5m MACD required (+1), 5m BOLL +1
- 1H aligned +2, opposite hard block
- 4H aligned +1, opposite -1
- forward strong structure < 1.3R hard block
- 15m ATR x1 stop and whole-position 2R take profit
- source-default V1.5 settings: 100U capital, 50U opening cap, 5x,
  1U/1% base risk, 3U daily drawdown, 3 consecutive losses block new
  entries for the rest of the China-time day, 30-minute post-close cooldown.

Historical data uses only completed candles from OKX public REST endpoints.
Research only; not a guarantee of live execution or future results.
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = "1.5.0"
HOST = "https://www.okx.com"
INST = "BTC-USDT-SWAP"
END = datetime(2026, 9, 12, 10, 57, tzinfo=timezone.utc)
START = END - timedelta(days=183)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
SH_TZ = ZoneInfo("Asia/Shanghai")

# Exact V1.5.0 source defaults after its single-signal UI/settings rewrite.
CAPITAL = 100.0
OPENING_NOTIONAL_CAP = 50.0
LEVERAGE = 5
RISK_USDT = 1.0
RISK_PCT = 1.0
DAILY_LOSS = 3.0
CONSECUTIVE_LOSSES = 3
COOLDOWN_MINUTES = 30
STOP_ATR = 1.0
REWARD_R = 2.0
SCORE_THRESHOLD = 6.0
MAKER_BPS = 2.0
TAKER_BPS = 5.0
SLIPPAGE_BPS = 5.0
EXPECTED_COST_MIN = 1.20
FRONT_MIN_R = 1.30
WARMUP_BARS = 1600
BAR_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000}

OUTDIR = Path("backtest_output_v150_6m")
OUTDIR.mkdir(parents=True, exist_ok=True)


def fmt_dt(ms: int | None) -> str:
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def okx_get(path: str, params: dict, attempts: int = 10):
    url = HOST + path + "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json", "User-Agent": "KAYTRADE-V150-Research/1.0"}
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
            time.sleep(min(8.0, 0.8 * attempt))
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
            print(f"{bar}: {len(rows):,} bars; oldest={fmt_dt(oldest)}", flush=True)
        # OKX public history endpoint: stay below common 10 req/s envelope.
        time.sleep(0.11)
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
                try:
                    out[t] = float(r["fundingRate"])
                except Exception:
                    pass
        if oldest >= cursor:
            break
        cursor = oldest
        if oldest < START_MS:
            break
        time.sleep(0.12)
    result = sorted(out.items())
    print(f"Fetched funding settlements: {len(result)}")
    return result


def ema(values, period):
    a = 2.0 / (period + 1)
    v = values[0]
    out = [v]
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
        if i >= n - 1:
            m = s / n
            means[i] = m
            sds[i] = math.sqrt(max(0.0, ss / n - m*m))
    return means, sds


def wilder_series(raw, length, n=14):
    out = [math.nan] * length
    if len(raw) < n:
        return out
    v = sum(raw[:n]) / n
    out[n] = v
    for j in range(n, len(raw)):
        v = (v * (n - 1) + raw[j]) / n
        out[j+1] = v
    return out


def compute_indicators(rows):
    n = len(rows); close = [r["c"] for r in rows]
    e5, e10, e20, e50, e200 = (ema(close, p) for p in (5, 10, 20, 50, 200))
    gains = [max(close[i]-close[i-1], 0.0) for i in range(1, n)]
    losses = [max(close[i-1]-close[i], 0.0) for i in range(1, n)]
    avg_g = wilder_series(gains, n); avg_l = wilder_series(losses, n)
    tr = [max(rows[i]["h"]-rows[i]["l"], abs(rows[i]["h"]-close[i-1]), abs(rows[i]["l"]-close[i-1])) for i in range(1, n)]
    atr = wilder_series(tr, n)
    mean20, sd20 = rolling_mean_std(close, 20)
    karr = [math.nan] * n; darr = [math.nan] * n; jarr = [math.nan] * n
    cup = [False] * n; cdown = [False] * n
    k = d = 50.0
    for i in range(8, n):
        low = min(rows[j]["l"] for j in range(i-8, i+1))
        high = max(rows[j]["h"] for j in range(i-8, i+1))
        rsv = 50.0 if high == low else 100.0 * (close[i] - low) / (high - low)
        pk, pd = k, d
        k = (2*k + rsv) / 3; d = (2*d + k) / 3
        karr[i] = k; darr[i] = d; jarr[i] = 3*k - 2*d
        cup[i] = pk <= pd and k > d; cdown[i] = pk >= pd and k < d
    e12 = ema(close, 12); e26 = ema(close, 26)
    dif = [a-b for a, b in zip(e12, e26)]; dea = ema(dif, 9)
    hist = [2*(a-b) for a, b in zip(dif, dea)]
    out = []
    for i in range(n):
        g, l = avg_g[i], avg_l[i]
        if math.isnan(g) or math.isnan(l): rsi = math.nan
        elif g == l == 0: rsi = 50.0
        elif l == 0: rsi = 100.0
        else: rsi = 100 - 100 / (1 + g/l)
        out.append({
            "ema5": e5[i], "ema10": e10[i], "ema20": e20[i], "ema50": e50[i], "ema200": e200[i],
            "up": i > 0 and e20[i] > e20[i-1] and e50[i] > e50[i-1],
            "down": i > 0 and e20[i] < e20[i-1] and e50[i] < e50[i-1],
            "rsi": rsi, "atr": atr[i], "middle": mean20[i],
            "upper": mean20[i] + 2*sd20[i] if not math.isnan(mean20[i]) else math.nan,
            "lower": mean20[i] - 2*sd20[i] if not math.isnan(mean20[i]) else math.nan,
            "k": karr[i], "d": darr[i], "j": jarr[i], "cross_up": cup[i], "cross_down": cdown[i],
            "dif": dif[i], "dea": dea[i], "hist": hist[i], "prev_hist": hist[i-1] if i else hist[i],
        })
    return out


def floor_tick(value, tick):
    return float((Decimal(str(value))/Decimal(str(tick))).to_integral_value(rounding=ROUND_DOWN)*Decimal(str(tick)))


def ceil_tick(value, tick):
    return float((Decimal(str(value))/Decimal(str(tick))).to_integral_value(rounding=ROUND_UP)*Decimal(str(tick)))


def floor_lot(value, lot):
    return float((Decimal(str(value))/Decimal(str(lot))).to_integral_value(rounding=ROUND_DOWN)*Decimal(str(lot)))


def mapped_index(ts_list, signal_close_ms, step):
    return bisect.bisect_right(ts_list, signal_close_ms - step) - 1


class ZoneCache:
    def __init__(self, data, inds, lookback):
        self.data = data; self.inds = inds; self.lookback = lookback; self.cache = {}

    def zones(self, end):
        if end in self.cache:
            return self.cache[end]
        atr = float(self.inds[end]["atr"])
        if not math.isfinite(atr) or atr <= 0:
            self.cache[end] = []; return []
        start = max(0, end + 1 - self.lookback); radius = 3; pts = []
        for i in range(start + radius, end + 1 - radius):
            left = self.data[i-radius:i]; right = self.data[i+1:i+radius+1]; row = self.data[i]
            if row["l"] <= min(r["l"] for r in left+right) and (row["l"] < min(r["l"] for r in left) or row["l"] < min(r["l"] for r in right)):
                pts.append({"kind": "support", "price": float(row["l"]), "i": i})
            if row["h"] >= max(r["h"] for r in left+right) and (row["h"] > max(r["h"] for r in left) or row["h"] > max(r["h"] for r in right)):
                pts.append({"kind": "resistance", "price": float(row["h"]), "i": i})
        tol = .25 * atr; clusters = []
        for p in pts:
            same = [z for z in clusters if z["kind"] == p["kind"] and abs(z["center"] - p["price"]) <= tol]
            if same:
                z = min(same, key=lambda x: abs(x["center"] - p["price"])); z["points"].append(p)
                z["center"] = sum(x["price"] for x in z["points"]) / len(z["points"]); z["last_i"] = max(z["last_i"], p["i"])
            else:
                clusters.append({"kind": p["kind"], "center": p["price"], "points": [p], "last_i": p["i"]})
        out = []
        for z in clusters:
            tests = len(z["points"])
            if tests < 2: continue
            center = z["center"]; after = self.data[z["last_i"]+1:end+1]
            broken = any(r["c"] < center-.3*atr for r in after) if z["kind"] == "support" else any(r["c"] > center+.3*atr for r in after)
            if broken: continue
            age = max(0, end-z["last_i"]); recency = max(0.0, 1.0-age/max(1, self.lookback))
            out.append({"kind": z["kind"], "price": center, "tests": tests, "age": age, "recency": recency, "strong": tests >= 3 and recency >= .25})
        self.cache[end] = out
        return out


def nearest(zones, price, kind, ahead=None, strong_only=False):
    c = []
    for z in zones:
        if z["kind"] != kind or (strong_only and not z.get("strong")): continue
        if ahead == "above" and z["price"] <= price: continue
        if ahead == "below" and z["price"] >= price: continue
        c.append(z)
    return min(c, key=lambda z: abs(z["price"]-price)) if c else None


def reversal(data, i, buy):
    if i < 1: return False
    c, p = data[i], data[i-1]
    return (c["c"] > c["o"] and c["c"] > p["h"]) if buy else (c["c"] < c["o"] and c["c"] < p["l"])


def ema_reclaim(data, inds, i, buy):
    if i < 1: return False
    c, p = data[i], data[i-1]; cur, prev = inds[i], inds[i-1]
    return (p["c"] <= prev["ema20"] and c["c"] > cur["ema20"]) if buy else (p["c"] >= prev["ema20"] and c["c"] < cur["ema20"])


def setup_score(data, inds, i, buy):
    if i < 20: return 0.0
    c, cur = data[i], inds[i]
    boll = c["l"] <= cur["lower"] if buy else c["h"] >= cur["upper"]
    hist = [float(data[j]["v"]) for j in range(i-20, i)]; avg = sum(hist)/20
    ratio = float(c["v"])/avg if avg > 0 else 0.0
    returned = (c["l"] <= cur["lower"] and c["c"] > cur["lower"]) if buy else (c["h"] >= cur["upper"] and c["c"] < cur["upper"])
    volume_boll = returned and ratio >= 1.3
    kdj = (cur["j"] <= 30 and cur["cross_up"]) if buy else (cur["j"] >= 70 and cur["cross_down"])
    rev = reversal(data, i, buy)
    return min(1.5, .5*int(boll) + 1.0*int(volume_boll) + .5*int(kdj) + .5*int(rev))


def trigger_detail(data, inds, i, buy):
    if i < 1: return 0.0, {}
    a = ema_reclaim(data, inds, i, buy)
    b = inds[i]["cross_up"] if buy else inds[i]["cross_down"]
    r = reversal(data, i, buy)
    # Production V1.4.7/V1.5 inherited trigger components are each 0.5 and capped at 1.
    val = min(1.0, .5 * (int(a) + int(b) + int(r)))
    return val, {"ema_reclaim": a, "kdj": b, "reversal": r}


def trend_detail(data, inds, i, buy):
    if i < 1: return 0.0, False, False
    cur, prev = inds[i], inds[i-1]
    if buy:
        macd = cur["dif"] > cur["dea"] and cur["hist"] > 0 and cur["hist"] >= cur["prev_hist"]
        boll = data[i]["c"] > cur["middle"] and cur["middle"] > prev["middle"]
    else:
        macd = cur["dif"] < cur["dea"] and cur["hist"] < 0 and cur["hist"] <= cur["prev_hist"]
        boll = data[i]["c"] < cur["middle"] and cur["middle"] < prev["middle"]
    return float(int(macd)+int(boll)), bool(macd), bool(boll)


def trend_state(data, inds, i, buy):
    c, cur = data[i], inds[i]; close = float(c["c"])
    if buy:
        aligned = close > cur["ema200"] and cur["ema20"] > cur["ema50"] and cur["up"]
        opposite = close < cur["ema200"] and cur["ema20"] < cur["ema50"] and cur["down"]
    else:
        aligned = close < cur["ema200"] and cur["ema20"] < cur["ema50"] and cur["down"]
        opposite = close > cur["ema200"] and cur["ema20"] > cur["ema50"] and cur["up"]
    return "aligned" if aligned else "opposite" if opposite else "neutral"


def rsi_penalty(i15, i5, buy):
    a, b = float(i15["rsi"]), float(i5["rsi"])
    if buy:
        if a > 80 and b > 80: return -2.0
        if a > 75 and b > 75: return -1.0
    else:
        if a < 20 and b < 20: return -2.0
        if a < 25 and b < 25: return -1.0
    return 0.0


@dataclass
class Market:
    data: dict
    inds: dict
    ts: dict
    zones: dict

    def score(self, i1):
        signal_close = self.data["1m"][i1]["t"] + BAR_MS["1m"]
        idx = {tf: mapped_index(self.ts[tf], signal_close, BAR_MS[tf]) for tf in ("5m", "15m", "1H", "4H")}
        if min(idx.values()) < 1:
            return {"side": "观望", "scores": {}}
        i5, i15, i1h, i4h = idx["5m"], idx["15m"], idx["1H"], idx["4H"]
        price = float(self.data["1m"][i1]["c"])
        hz = self.zones["1H"].zones(i1h); mz = self.zones["15m"].zones(i15); qz = self.zones["4H"].zones(i4h)
        results = {}
        for side in ("做多", "做空"):
            buy = side == "做多"; kind = "support" if buy else "resistance"
            z15 = nearest(mz, price, kind)
            atr15 = float(self.inds["15m"][i15]["atr"])
            fav15 = .5 if z15 and atr15 > 0 and abs(z15["price"]-price)/atr15 <= .25 else 0.0
            setup = setup_score(self.data["15m"], self.inds["15m"], i15, buy)
            trig, trig_parts = trigger_detail(self.data["1m"], self.inds["1m"], i1, buy)
            tr5, macd5, boll5 = trend_detail(self.data["5m"], self.inds["5m"], i5, buy)
            tr15, macd15, boll15 = trend_detail(self.data["15m"], self.inds["15m"], i15, buy)
            rp = rsi_penalty(self.inds["15m"][i15], self.inds["5m"][i5], buy)
            adverse = "resistance" if buy else "support"; ahead = "above" if buy else "below"
            zh = nearest(hz, price, adverse, ahead); zq = nearest(qz, price, adverse, ahead)
            atr1h = float(self.inds["1H"][i1h]["atr"]); atr4h = float(self.inds["4H"][i4h]["atr"])
            s1 = -1.0 if zh and atr1h > 0 and abs(zh["price"]-price)/atr1h <= .25 else 0.0
            s4 = -1.5 if zq and atr4h > 0 and abs(zq["price"]-price)/atr4h <= .25 else 0.0
            hstate = trend_state(self.data["1H"], self.inds["1H"], i1h, buy)
            qstate = trend_state(self.data["4H"], self.inds["4H"], i4h, buy)
            hscore = 2.0 if hstate == "aligned" else 0.0
            qscore = 1.0 if qstate == "aligned" else (-1.0 if qstate == "opposite" else 0.0)

            # Production uses latest closed 15m price for forward-space check.
            fprice = float(self.data["15m"][i15]["c"])
            forward = []
            for tf, zs in (("1H", hz), ("15m", mz)):
                z = nearest(zs, fprice, adverse, ahead, strong_only=True)
                if z:
                    forward.append((abs(z["price"]-fprice), tf, z))
            front = min(forward, key=lambda x: x[0]) if forward else None
            risk_distance = atr15 * STOP_ATR
            front_r = front[0] / risk_distance if front and risk_distance > 0 else math.inf
            front_block = front_r < FRONT_MIN_R

            raw = fav15 + setup + trig + tr5 + tr15 + rp + s1 + s4 + hscore + qscore
            total = max(0.0, min(10.0, round(raw*2)/2))
            gate = trig > 0 and macd5 and hstate != "opposite" and not front_block
            eligible = bool(gate and total >= SCORE_THRESHOLD)
            results[side] = {
                "total": total, "raw": raw, "gate": gate, "eligible": eligible,
                "atr15": atr15, "atr1h": atr1h, "front_r": front_r,
                "components": {"fav15": fav15, "setup": setup, "trigger": trig,
                    "trend5": tr5, "trend5_macd": 1.0 if macd5 else 0.0, "trend5_boll": 1.0 if boll5 else 0.0,
                    "trend15": tr15, "trend15_macd": 1.0 if macd15 else 0.0, "trend15_boll": 1.0 if boll15 else 0.0,
                    "trend1h": hscore, "trend4h": qscore, "rsi": rp, "struct1h": s1, "struct4h": s4,
                    "trigger_ema_reclaim": .5 if trig_parts.get("ema_reclaim") else 0.0,
                    "trigger_kdj": .5 if trig_parts.get("kdj") else 0.0,
                    "trigger_reversal": .5 if trig_parts.get("reversal") else 0.0,
                },
                "hstate": hstate, "qstate": qstate,
            }
        eligible = [s for s in ("做多", "做空") if results[s]["eligible"]]
        if len(eligible) == 1:
            selected = eligible[0]
        elif len(eligible) == 2:
            a, b = results[eligible[0]]["total"], results[eligible[1]]["total"]
            selected = "观望" if abs(a-b) <= 1e-9 else max(eligible, key=lambda s: results[s]["total"])
        else:
            selected = "观望"
        return {"side": selected, "scores": results, "idx": idx, "price": price, "signal_close": signal_close}


@dataclass
class Trade:
    trade_id: int; side: str; score: float; signal_ms: int; submitted_ms: int
    entry: float; qty: float; notional: float; sl: float; tp: float; est_loss: float; atr15: float
    status: str = "pending"; fill_ms: int | None = None; exit_ms: int | None = None
    exit_price: float | None = None; reason: str = ""; entry_fee: float = 0.0; exit_fee: float = 0.0
    funding: float = 0.0; start_equity: float = 0.0
    def direction(self): return 1.0 if self.side == "做多" else -1.0


class Simulator:
    def __init__(self, market, meta, funding):
        self.m = market; self.meta = meta; self.funding = funding; self.funding_i = 0
        self.cash = CAPITAL; self.peak_equity = CAPITAL; self.max_dd = 0.0; self.max_dd_pct = 0.0
        self.daily_day = None; self.daily_peak = CAPITAL; self.daily_block_day = None
        self.streak_day = None; self.loss_streak = 0; self.last_close_ms = -10**18
        self.trade: Trade | None = None; self.trades = []; self.next_trade = 1
        self.daily_equity = {}; self.stats = defaultdict(int); self.stress_extra = 0.0

    def mark_equity(self, price):
        if self.trade and self.trade.status == "open":
            return self.cash + (price-self.trade.entry)*self.trade.qty*self.trade.direction()
        return self.cash

    def update_day(self, ms, equity):
        day = datetime.fromtimestamp(ms/1000, tz=timezone.utc).astimezone(SH_TZ).strftime("%Y-%m-%d")
        if self.daily_day != day:
            self.daily_day = day; self.daily_peak = equity; self.daily_block_day = None
        else:
            self.daily_peak = max(self.daily_peak, equity)
        # Exact inherited V1.5 behavior: streak is China-calendar-day scoped.
        if self.streak_day != day:
            self.streak_day = day; self.loss_streak = 0
        if self.daily_peak - equity >= DAILY_LOSS - 1e-12:
            self.daily_block_day = day
        return day

    def daily_remaining(self, equity):
        return max(0.0, DAILY_LOSS - max(0.0, self.daily_peak-equity))

    def can_signal(self, signal_ms):
        day = datetime.fromtimestamp(signal_ms/1000, tz=timezone.utc).astimezone(SH_TZ).strftime("%Y-%m-%d")
        if self.daily_block_day == day:
            self.stats["daily_risk_blocks"] += 1; return False
        if self.loss_streak >= CONSECUTIVE_LOSSES:
            self.stats["streak_blocks"] += 1; return False
        if self.trade is not None:
            return False  # V1.5: no add-on / upgrade entries.
        if signal_ms - self.last_close_ms < COOLDOWN_MINUTES*60_000:
            self.stats["cooldown_blocks"] += 1; return False
        return True

    def plan(self, side, signal_price, atr15, equity, daily_remaining):
        buy = side == "做多"; tick = self.meta["tickSz"]
        entry = floor_tick(signal_price, tick) if buy else ceil_tick(signal_price, tick)
        dist = atr15 * STOP_ATR
        sl = floor_tick(entry-dist, tick) if buy else ceil_tick(entry+dist, tick)
        tp = ceil_tick(entry+REWARD_R*dist, tick) if buy else floor_tick(entry-REWARD_R*dist, tick)
        if not ((sl < entry < tp) if buy else (tp < entry < sl)):
            return None
        maker = MAKER_BPS/10000; taker = TAKER_BPS/10000; slip = SLIPPAGE_BPS/10000
        # Same V1.5 risk sizing budget: entry + stop market exit + slippage budget.
        per_btc = abs(entry-sl) + entry*max(maker, taker) + sl*(taker+slip)
        risk_capital = min(CAPITAL, equity)
        base_risk = min(RISK_USDT, risk_capital*RISK_PCT/100)
        risk = min(base_risk, daily_remaining)
        notional_cap = min(OPENING_NOTIONAL_CAP, risk_capital*LEVERAGE, max(0.0, equity)*.9*LEVERAGE)
        unit = self.meta["ctVal"] * self.meta["ctMult"]
        contracts = floor_lot(min(risk/per_btc, notional_cap/entry)/unit, self.meta["lotSz"])
        if contracts + 1e-12 < self.meta["minSz"] or contracts <= 0:
            return None
        qty = contracts*unit; notional = qty*entry; est = qty*per_btc
        expected_cost_per_btc = entry*maker + tp*taker
        multiple = (REWARD_R*dist)/expected_cost_per_btc if expected_cost_per_btc > 0 else math.inf
        if multiple < EXPECTED_COST_MIN:
            return None
        return entry, qty, notional, sl, tp, est, multiple

    def submit(self, sig, price):
        side = sig["side"]
        if side == "观望": return
        row = sig["scores"][side]; signal_ms = sig["signal_close"]
        if not self.can_signal(signal_ms): return
        eq = self.mark_equity(price); remain = self.daily_remaining(eq)
        p = self.plan(side, sig["price"], row["atr15"], eq, remain)
        if p is None:
            self.stats["sizing_or_cost_skips"] += 1; return
        entry, qty, notional, sl, tp, est, _ = p
        self.trade = Trade(self.next_trade, side, row["total"], signal_ms, signal_ms,
                           entry, qty, notional, sl, tp, est, row["atr15"], start_equity=eq)
        self.next_trade += 1; self.trades.append(self.trade); self.stats["orders_submitted"] += 1

    def fill_pending(self, bar):
        t = self.trade
        if not t or t.status != "pending": return
        touched = bar["l"] <= t.entry if t.side == "做多" else bar["h"] >= t.entry
        if touched:
            t.status = "open"; t.fill_ms = bar["t"]
            t.entry_fee = t.qty*t.entry*MAKER_BPS/10000; self.cash -= t.entry_fee
            self.stats["orders_filled"] += 1
            self.handle_exit(bar, just_filled=True)
        elif bar["t"] + 60_000 >= t.submitted_ms + 60_000:
            t.status = "cancelled"; t.reason = "limit_unfilled_60s"; t.exit_ms = bar["t"] + 60_000
            self.stats["orders_cancelled"] += 1; self.trade = None

    def handle_exit(self, bar, just_filled=False):
        t = self.trade
        if not t or t.status != "open": return
        d = t.direction()
        hit_sl = bar["l"] <= t.sl if d > 0 else bar["h"] >= t.sl
        hit_tp = bar["h"] >= t.tp if d > 0 else bar["l"] <= t.tp
        # Conservative OHLC ordering: if both touched in the same minute, SL first.
        if hit_sl:
            # V1.5 production uses market-on-trigger SL. If candle opens through stop,
            # use the adverse open; otherwise use trigger price.
            if d > 0:
                px = min(t.sl, bar["o"]) if bar["o"] < t.sl else t.sl
            else:
                px = max(t.sl, bar["o"]) if bar["o"] > t.sl else t.sl
            self.close_trade(px, bar["t"], "SL")
            return
        if hit_tp:
            # Market-on-trigger TP; favorable gaps may execute no worse than trigger.
            if d > 0:
                px = max(t.tp, bar["o"]) if bar["o"] > t.tp else t.tp
            else:
                px = min(t.tp, bar["o"]) if bar["o"] < t.tp else t.tp
            self.close_trade(px, bar["t"], "TP_2R")

    def close_trade(self, px, ms, reason):
        t = self.trade
        if not t or t.status == "closed": return
        t.status = "closed"; t.exit_ms = ms; t.exit_price = float(px); t.reason = reason
        gross = (t.exit_price-t.entry)*t.qty*t.direction()
        t.exit_fee = t.qty*t.exit_price*TAKER_BPS/10000
        self.cash += gross - t.exit_fee
        self.stress_extra += t.qty*t.exit_price*SLIPPAGE_BPS/10000
        pnl = self.cash - t.start_equity
        self.loss_streak = self.loss_streak + 1 if pnl < 0 else 0
        self.last_close_ms = ms; self.stats["trades_closed"] += 1; self.trade = None

    def apply_funding_until(self, bar_close, price):
        while self.funding_i < len(self.funding) and self.funding[self.funding_i][0] <= bar_close:
            ft, rate = self.funding[self.funding_i]; self.funding_i += 1
            t = self.trade
            if ft < START_MS or not t or t.status != "open" or t.fill_ms is None or not (t.fill_ms < ft):
                continue
            amount = -t.direction()*t.qty*price*rate
            t.funding += amount; self.cash += amount

    def record_equity(self, ms, price):
        eq = self.mark_equity(price); day = self.update_day(ms, eq)
        self.peak_equity = max(self.peak_equity, eq); dd = self.peak_equity-eq
        self.max_dd = max(self.max_dd, dd)
        self.max_dd_pct = max(self.max_dd_pct, dd/self.peak_equity*100 if self.peak_equity else 0)
        self.daily_equity[day] = eq

    def finish(self, last_bar):
        if self.trade and self.trade.status == "pending":
            self.trade.status = "cancelled"; self.trade.reason = "period_end_unfilled"; self.trade.exit_ms = END_MS; self.trade = None
        if self.trade and self.trade.status == "open":
            self.close_trade(last_bar["c"], END_MS, "period_end_mark")
        self.record_equity(END_MS, last_bar["c"])


def analyze(sim, data1, funding_rows, meta):
    closed = [t for t in sim.trades if t.fill_ms is not None and t.exit_ms is not None and t.exit_price is not None]
    def net(t):
        gross = (t.exit_price-t.entry)*t.qty*t.direction()
        return gross-t.entry_fee-t.exit_fee+t.funding
    pnls = [net(t) for t in closed]; wins = [x for x in pnls if x > 0]; losses = [x for x in pnls if x < 0]
    gp, gl = sum(wins), -sum(losses); pf = gp/gl if gl > 0 else math.inf
    avg_win = statistics.mean(wins) if wins else 0; avg_loss = statistics.mean(losses) if losses else 0
    payoff = abs(avg_win/avg_loss) if avg_loss else math.inf
    start_px = next(r["o"] for r in data1 if r["t"] >= START_MS)
    end_px = [r for r in data1 if r["t"] < END_MS][-1]["c"]
    benchmark = (end_px/start_px-1)*100
    durations = [(t.exit_ms-t.fill_ms)/60000 for t in closed]
    by_side = {}; by_month = {}; by_reason = {}; by_score = {}
    for side in ("做多", "做空"):
        xs = [net(t) for t in closed if t.side == side]
        by_side[side] = {"trades": len(xs), "net_pnl": sum(xs), "win_rate_pct": (sum(x>0 for x in xs)/len(xs)*100 if xs else 0)}
    for t in closed:
        month = datetime.fromtimestamp(t.exit_ms/1000, tz=timezone.utc).strftime("%Y-%m")
        by_month.setdefault(month, {"trades": 0, "net_pnl": 0.0}); by_month[month]["trades"] += 1; by_month[month]["net_pnl"] += net(t)
        by_reason[t.reason] = by_reason.get(t.reason, 0) + 1
        key = f"{t.score:.1f}"; by_score.setdefault(key, {"trades": 0, "wins": 0, "net_pnl": 0.0})
        by_score[key]["trades"] += 1; by_score[key]["wins"] += int(net(t)>0); by_score[key]["net_pnl"] += net(t)
    for row in by_score.values():
        row["win_rate_pct"] = row["wins"]/row["trades"]*100 if row["trades"] else 0
    maxwin = maxloss = runw = runl = 0
    for x in pnls:
        if x > 0: runw += 1; runl = 0; maxwin = max(maxwin, runw)
        elif x < 0: runl += 1; runw = 0; maxloss = max(maxloss, runl)
        else: runw = runl = 0
    fees = sum(t.entry_fee+t.exit_fee for t in closed); funding = sum(t.funding for t in closed)
    net_pnl = sim.cash-CAPITAL; stress = net_pnl-sim.stress_extra
    return {
        "strategy_version": VERSION, "period_start": START.isoformat(), "period_end": END.isoformat(), "days": (END-START).days,
        "instrument": INST, "start_price": start_px, "end_price": end_px, "btc_buy_hold_pct": benchmark,
        "starting_capital": CAPITAL, "opening_notional_cap": OPENING_NOTIONAL_CAP, "leverage": LEVERAGE,
        "ending_equity": sim.cash, "net_pnl": net_pnl, "net_return_pct": net_pnl/CAPITAL*100,
        "stress_net_pnl": stress, "stress_return_pct": stress/CAPITAL*100,
        "trades": len(closed), "orders_submitted": sim.stats["orders_submitted"], "orders_filled": sim.stats["orders_filled"], "orders_cancelled": sim.stats["orders_cancelled"],
        "win_rate_pct": len(wins)/len(closed)*100 if closed else 0, "profit_factor": pf,
        "expectancy": statistics.mean(pnls) if pnls else 0, "avg_win": avg_win, "avg_loss": avg_loss, "payoff_ratio": payoff,
        "max_drawdown_usdt": sim.max_dd, "max_drawdown_pct": sim.max_dd_pct,
        "max_consecutive_wins": maxwin, "max_consecutive_losses": maxloss,
        "fees": fees, "funding_pnl": funding, "stress_extra_slippage": sim.stress_extra,
        "avg_hold_min": statistics.mean(durations) if durations else 0, "median_hold_min": statistics.median(durations) if durations else 0,
        "daily_risk_blocks": sim.stats["daily_risk_blocks"], "streak_blocks": sim.stats["streak_blocks"], "cooldown_blocks": sim.stats["cooldown_blocks"],
        "sizing_or_cost_skips": sim.stats["sizing_or_cost_skips"], "funding_settlements": len(funding_rows), "meta": meta,
        "by_side": by_side, "by_month": by_month, "by_reason": by_reason, "by_score": by_score,
    }, net, closed


def write_outputs(sim, metrics, net, closed):
    with (OUTDIR/"V150_BTC_SWAP_6M_Trades.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["trade_id","side","score","signal_utc","fill_utc","entry","qty_btc","notional_usdt","sl","tp","exit_utc","exit_price","exit_reason","entry_fee","exit_fee","funding_pnl","net_pnl","hold_min"])
        for t in closed:
            w.writerow([t.trade_id,t.side,t.score,fmt_dt(t.signal_ms),fmt_dt(t.fill_ms),t.entry,t.qty,t.notional,t.sl,t.tp,fmt_dt(t.exit_ms),t.exit_price,t.reason,t.entry_fee,t.exit_fee,t.funding,net(t),(t.exit_ms-t.fill_ms)/60000])
    with (OUTDIR/"V150_BTC_SWAP_6M_DailyEquity.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["china_date","ending_equity_usdt","cumulative_return_pct"])
        for d, e in sorted(sim.daily_equity.items()): w.writerow([d,e,(e/CAPITAL-1)*100])
    (OUTDIR/"metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    def num(x): return "∞" if math.isinf(x) else f"{x:.3f}"
    lines = [
        "# KAYTRADE V1.5.0 — BTC-USDT-SWAP 183天历史回测", "",
        f"**区间：** {START:%Y-%m-%d %H:%M} UTC → {END:%Y-%m-%d %H:%M} UTC（183天）  ",
        "**规则：** V1.5.0固定6分开仓；只有开仓信号1×仓位；无二/三级信号、无升级加仓；V1.4.7 Hard Gates保持；15m ATR×1止损、整仓2R止盈。  ",
        f"**源码默认资金：** {CAPITAL:.0f}U；开仓名义上限{OPENING_NOTIONAL_CAP:.0f}U；{LEVERAGE}×；基础风险{RISK_USDT:.0f}U/1%；日回撤{DAILY_LOSS:.0f}U；三连亏当日停开、次日恢复；平仓后{COOLDOWN_MINUTES}分钟冷却。", "",
        "## 核心结果", "", "| 指标 | 结果 |", "|---|---:|",
        f"| 期末权益 | {metrics['ending_equity']:.4f} U |",
        f"| 净收益 | {metrics['net_pnl']:+.4f} U |",
        f"| 收益率 | {metrics['net_return_pct']:+.4f}% |",
        f"| 压力收益率（每次退出额外5bps） | {metrics['stress_return_pct']:+.4f}% |",
        f"| 完整交易 | {metrics['trades']} |",
        f"| 胜率 | {metrics['win_rate_pct']:.2f}% |",
        f"| Profit Factor | {num(metrics['profit_factor'])} |",
        f"| 单笔期望 | {metrics['expectancy']:+.5f} U |",
        f"| 平均盈利/亏损 | {metrics['avg_win']:+.4f} / {metrics['avg_loss']:+.4f} U |",
        f"| 盈亏金额比 | {num(metrics['payoff_ratio'])} |",
        f"| 最大回撤 | {metrics['max_drawdown_usdt']:.4f} U / {metrics['max_drawdown_pct']:.3f}% |",
        f"| 最大连续赢/亏 | {metrics['max_consecutive_wins']} / {metrics['max_consecutive_losses']} |",
        f"| 手续费 | {metrics['fees']:.4f} U |",
        f"| Funding | {metrics['funding_pnl']:+.4f} U |",
        f"| 平均/中位持仓 | {metrics['avg_hold_min']:.1f} / {metrics['median_hold_min']:.1f} min |",
        f"| BTC同期买入持有 | {metrics['btc_buy_hold_pct']:+.2f}% |", "",
        "## 多空", "", "| 方向 | 交易 | 胜率 | 净PnL |", "|---|---:|---:|---:|",
    ]
    for side, row in metrics["by_side"].items():
        lines.append(f"| {side} | {row['trades']} | {row['win_rate_pct']:.2f}% | {row['net_pnl']:+.4f} U |")
    lines += ["", "## 月度", "", "| 月份 | 交易 | 净PnL |", "|---|---:|---:|"]
    for month, row in sorted(metrics["by_month"].items()):
        lines.append(f"| {month} | {row['trades']} | {row['net_pnl']:+.4f} U |")
    lines += ["", "## 分数桶", "", "| 分数 | 交易 | 胜率 | 净PnL |", "|---:|---:|---:|---:|"]
    for score, row in sorted(metrics["by_score"].items(), key=lambda kv: float(kv[0])):
        lines.append(f"| {score} | {row['trades']} | {row['win_rate_pct']:.2f}% | {row['net_pnl']:+.4f} U |")
    lines += ["", "## 风控/执行", "",
        f"- 开仓提交/成交/取消：{metrics['orders_submitted']} / {metrics['orders_filled']} / {metrics['orders_cancelled']}",
        f"- 日回撤拦截：{metrics['daily_risk_blocks']}；三连亏当日拦截：{metrics['streak_blocks']}；30m冷却拦截：{metrics['cooldown_blocks']}；仓位/成本过滤：{metrics['sizing_or_cost_skips']}",
        "- 限价开仓只允许下一根1m内触及成交；TP/SL按V1.5交易所market-on-trigger语义模拟；同一1m同时触及TP/SL时保守按SL优先。",
        "- OHLC无法还原真实盘口、排队与瞬时滑点；基准含maker 2bps开仓+taker 5bps退出+历史Funding，另提供退出额外5bps的压力情景。",
        "- 回测仅用于研究，不代表未来收益。",
    ]
    (OUTDIR/"V150_BTC_SWAP_6M_Backtest_Report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    print(f"KAYTRADE V{VERSION} standalone six-month backtest")
    print("Window:", START.isoformat(), "->", END.isoformat())
    meta = fetch_instrument(); print("Instrument:", meta)
    data = {tf: fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = fetch_funding()
    inds = {tf: compute_indicators(rows) for tf, rows in data.items()}
    ts = {tf: [r["t"] for r in rows] for tf, rows in data.items()}
    zones = {"15m": ZoneCache(data["15m"], inds["15m"], 160), "1H": ZoneCache(data["1H"], inds["1H"], 120), "4H": ZoneCache(data["4H"], inds["4H"], 180)}
    market = Market(data, inds, ts, zones); sim = Simulator(market, meta, funding)
    d1 = data["1m"]; start_i = bisect.bisect_left(ts["1m"], START_MS)
    if start_i < 1: raise RuntimeError("not enough 1m warmup")
    for i in range(start_i, len(d1)):
        bar = d1[i]; bar_close = bar["t"] + 60_000
        if bar["t"] >= END_MS: break
        sim.apply_funding_until(bar["t"], bar["o"])
        sim.fill_pending(bar)
        sim.handle_exit(bar)
        sim.apply_funding_until(bar_close, bar["c"])
        sim.record_equity(bar_close, bar["c"])
        sig = market.score(i)
        sim.submit(sig, bar["c"])
        if (i-start_i) % 10000 == 0:
            print(f"progress {i-start_i:,}/{len(d1)-start_i:,}; equity={sim.mark_equity(bar['c']):.4f}; trades={sim.stats['trades_closed']}", flush=True)
    last = [r for r in d1 if r["t"] < END_MS][-1]
    sim.finish(last)
    metrics, net, closed = analyze(sim, d1, funding, meta)
    write_outputs(sim, metrics, net, closed)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print("OUTPUT_DIR", OUTDIR.resolve())


if __name__ == "__main__":
    main()

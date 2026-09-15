#!/usr/bin/env python3
"""Fast reconstruction of 1H/2H/3H/4H path state for the completed 720D baseline trades.

Reads the prior Run 34999103434 trades CSV downloaded by the workflow, then fetches only
the first four hours of 1m OKX candles around each filled trade. No strategy is rerun.
This is intended to measure pure time/path correlation without changing sequencing.
"""
from __future__ import annotations

import csv
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOST = "https://www.okx.com"
INST = "BTC-USDT-SWAP"
HOURS = (1, 2, 3, 4)
BAR_MS = 60_000


def okx_get(params, attempts=8):
    url = HOST + "/api/v5/market/history-candles?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept":"application/json","User-Agent":"KAYTRADE-hourly-path/1.0"})
    last = None
    for n in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("code") != "0":
                raise RuntimeError(payload)
            return payload.get("data") or []
        except Exception as exc:
            last = exc
            if n + 1 < attempts:
                time.sleep(min(5.0, 0.6 * (n + 1)))
    raise RuntimeError(last)


def fetch_1m_window(start_ms, end_ms):
    """Return closed 1m bars with start_ms < bar.t < end_ms, sorted ascending."""
    rows = {}
    cursor = int(end_ms) + BAR_MS
    while True:
        batch = okx_get({"instId": INST, "bar": "1m", "limit": "100", "after": str(cursor)})
        if not batch:
            break
        oldest = cursor
        for r in batch:
            t = int(r[0]); oldest = min(oldest, t)
            if str(r[8]) != "1":
                continue
            if start_ms < t < end_ms:
                o,h,l,c = map(float, (r[1],r[2],r[3],r[4]))
                rows[t] = {"t":t,"o":o,"h":h,"l":l,"c":c}
        if oldest <= start_ms + BAR_MS or oldest >= cursor:
            break
        cursor = oldest
        time.sleep(0.11)
    return [rows[t] for t in sorted(rows)]


def side_dir(side):
    return 1.0 if side == "做多" else -1.0


def reconstruct_trade(r):
    entry_time = int(float(r["entry_time"]))
    exit_time = int(float(r["exit_time"]))
    entry = float(r["entry"]); stop = float(r["stop"])
    risk = abs(entry - stop)
    if risk <= 0:
        return []
    max_end = min(entry_time + 4 * 60 * 60_000, exit_time)
    bars = fetch_1m_window(entry_time, max_end)
    by_t = {int(b["t"]): b for b in bars}
    out = []
    mfe = 0.0; mae = 0.0
    i = 0
    sorted_bars = bars
    for hour in HOURS:
        checkpoint = entry_time + hour * 60 * 60_000
        # Exit has intrabar priority in the audited simulator; require strict survival past checkpoint.
        if exit_time <= checkpoint:
            continue
        last_t = checkpoint - BAR_MS
        while i < len(sorted_bars) and int(sorted_bars[i]["t"]) <= last_t:
            b = sorted_bars[i]
            if r["side"] == "做多":
                mfe = max(mfe, max(0.0, float(b["h"]) - entry))
                mae = max(mae, max(0.0, entry - float(b["l"])))
            else:
                mfe = max(mfe, max(0.0, entry - float(b["l"])))
                mae = max(mae, max(0.0, float(b["h"]) - entry))
            i += 1
        b = by_t.get(last_t)
        if not b:
            continue
        mark = float(b["c"])
        current_r = (mark - entry) * side_dir(r["side"]) / risk
        out.append({
            "opportunity_id": r["opportunity_id"], "side": r["side"],
            "hour": hour, "checkpoint": checkpoint,
            "current_r": current_r, "mfe_r_so_far": mfe / risk, "mae_r_so_far": mae / risk,
            "final_reason": r["reason"], "final_net_pnl": float(r["net_pnl"]),
            "final_realized_r": float(r["realized_r"]), "final_hold_min": float(r["hold_min"]),
            "final_mfe_r": float(r["mfe_r"]), "final_mae_r": float(r["mae_r"]),
            "delta_r_if_exit_now": current_r - float(r["realized_r"]),
        })
    return out


def stats(rows):
    rows=list(rows); n=len(rows)
    losses=[x for x in rows if x["final_net_pnl"] < 0]
    wins=[x for x in rows if x["final_net_pnl"] > 0]
    return {
        "count":n,"losses":len(losses),"wins":len(wins),
        "loss_rate_pct":100*len(losses)/n if n else 0.0,
        "avg_current_r":sum(x["current_r"] for x in rows)/n if n else 0.0,
        "avg_mfe_r_so_far":sum(x["mfe_r_so_far"] for x in rows)/n if n else 0.0,
        "sum_delta_r_if_exit_now":sum(x["delta_r_if_exit_now"] for x in rows),
        "saved_r_on_losers":sum(max(x["delta_r_if_exit_now"],0.0) for x in losses),
        "false_kill_r_on_winners":sum(max(-x["delta_r_if_exit_now"],0.0) for x in wins),
    }


def analyse(rows):
    result={"by_hour":{},"candidates":[]}
    r_bins=[(-99,-0.8),(-0.8,-0.5),(-0.5,-0.3),(-0.3,0),(0,0.25),(0.25,0.5),(0.5,99)]
    for h in HOURS:
        hr=[x for x in rows if x["hour"]==h]
        result["by_hour"][str(h)]={
            "all":stats(hr),
            "mfe_lt_025":stats([x for x in hr if x["mfe_r_so_far"]<0.25]),
            "mfe_lt_050":stats([x for x in hr if x["mfe_r_so_far"]<0.50]),
            "r_bins":[],
        }
        for lo,hi in r_bins:
            b=[x for x in hr if x["current_r"]>lo and x["current_r"]<=hi]
            item=stats(b); item.update({"r_gt":lo,"r_le":hi})
            result["by_hour"][str(h)]["r_bins"].append(item)
        for rmax in (-0.30,-0.50,-0.80):
            for mfe in (None,0.25,0.50,0.80):
                sel=[x for x in hr if x["current_r"]<=rmax and (mfe is None or x["mfe_r_so_far"]<mfe)]
                item=stats(sel); item.update({"hour":h,"current_r_le":rmax,"mfe_lt":mfe})
                result["candidates"].append(item)
    return result


def write_csv(rows,path):
    if not rows:
        path.write_text("",encoding="utf-8"); return
    keys=list(rows[0])
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)


def main():
    source=Path("prior_artifact/trades_720d.csv")
    trades=list(csv.DictReader(source.open(encoding="utf-8")))
    snapshots=[]
    for idx,r in enumerate(trades,1):
        snapshots.extend(reconstruct_trade(r))
        if idx%20==0:
            print(f"processed {idx}/{len(trades)} trades snapshots={len(snapshots)}",flush=True)
    analysis=analyse(snapshots)
    out=Path("hourly_path_fast_720d"); out.mkdir(exist_ok=True)
    write_csv(snapshots,out/"hourly_path_snapshots.csv")
    (out/"hourly_path_analysis.json").write_text(json.dumps(analysis,ensure_ascii=False,indent=2),encoding="utf-8")
    print("HOURLY_PATH_ANALYSIS",json.dumps(analysis,ensure_ascii=False),flush=True)
    print("SNAPSHOTS",len(snapshots),flush=True)

if __name__=="__main__":
    main()

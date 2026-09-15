#!/usr/bin/env python3
"""720D hourly Early Exit diagnostics on the no-Early-Exit Volume Hard Gate baseline.

Baseline execution is unchanged from Run 34999103434. This script does NOT alter exits.
It only snapshots surviving positions exactly at 1H/2H/3H/4H after fill, records
Current R / MFE / MAE / current Early Exit score components, then joins each snapshot
with the untouched baseline final result.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import run_v162_volume_hard_gate_720d_2000u as bt
import run_v162_early_exit_score_180d_2000u as ee

research = bt.research
HOURS = (1, 2, 3, 4)
LIMIT_PROTECTION_BPS = 5.0
SNAPSHOTS = []
_ORIGINAL_PROCESS_EXIT = None


def _risk(p):
    return abs(float(p.limit) - float(p.stop))


def _current_r(p, mark):
    risk = _risk(p)
    if risk <= 0:
        return 0.0
    return ((float(mark) - float(p.limit)) * research.side_dir(p.side)) / risk


def _hypo_exit_net(pos, mark):
    p = pos.pending
    bps = (LIMIT_PROTECTION_BPS + 0.0) / 10000.0
    exit_px = float(mark) * (1.0 - bps if p.side == "做多" else 1.0 + bps)
    gross = (exit_px - float(p.limit)) * float(p.quantity_btc) * research.side_dir(p.side)
    exit_fee = exit_px * float(p.quantity_btc) * research.TAKER_BPS / 10000.0
    return gross - float(pos.entry_fee) - exit_fee + float(pos.funding_pnl), exit_px


def _threshold_c(current_r):
    if current_r <= -0.80:
        return 2.0
    if current_r <= -0.50:
        return 3.0
    return None


def _process_exit_with_hourly_snapshots(self, bar):
    if not self.position:
        return

    # Preserve exact baseline SL/TP priority and state updates first.
    _ORIGINAL_PROCESS_EXIT(self, bar)
    if not self.position:
        return

    pos = self.position
    p = pos.pending
    close_ms = int(bar["t"]) + 60_000
    hold_ms = close_ms - int(pos.fill_time)
    if hold_ms <= 0:
        return

    seen = getattr(self, "_hourly_snapshot_seen", None)
    if seen is None:
        seen = set()
        self._hourly_snapshot_seen = seen

    for hour in HOURS:
        target_ms = hour * 60 * 60_000
        key = (str(p.opportunity_id), hour)
        if key in seen or hold_ms != target_ms:
            continue
        seen.add(key)

        risk = _risk(p)
        if risk <= 0:
            continue
        mark = float(bar["c"])
        current_r = _current_r(p, mark)
        mfe_r = float(pos.mfe) / risk
        mae_r = float(pos.mae) / risk
        hypo_net, hypo_exit_px = _hypo_exit_net(pos, mark)

        feat = ee._FEATURE_BY_CLOSE_MS.get(close_ms)
        ee_score = None
        ee_components = {}
        c_threshold = _threshold_c(current_r)
        if feat:
            ee_score, ee_components, _ = ee._score_early_exit(
                p.side, feat, pos, risk, hold_ms, current_r
            )

        SNAPSHOTS.append({
            "opportunity_id": str(p.opportunity_id),
            "side": p.side,
            "entry_time": int(pos.fill_time),
            "hour": hour,
            "snapshot_time": close_ms,
            "entry": float(p.limit),
            "mark": mark,
            "hypo_exit_px": hypo_exit_px,
            "current_r": current_r,
            "mfe_r_so_far": mfe_r,
            "mae_r_so_far": mae_r,
            "hypo_exit_net_pnl": hypo_net,
            "early_exit_score": ee_score,
            "c_threshold": c_threshold,
            "c_would_exit": bool(c_threshold is not None and ee_score is not None and ee_score >= c_threshold),
            "early_exit_components": ee_components,
        })


def _write_csv(rows, path):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    for row in rows:
        for k in row:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _stats(rows):
    rows = list(rows)
    n = len(rows)
    losses = [r for r in rows if r["final_net_pnl"] < 0]
    wins = [r for r in rows if r["final_net_pnl"] > 0]
    delta = sum(r["hypo_minus_final_pnl"] for r in rows)
    saved = sum(max(r["hypo_minus_final_pnl"], 0.0) for r in losses)
    false_cost = sum(max(-r["hypo_minus_final_pnl"], 0.0) for r in wins)
    return {
        "count": n,
        "final_losses": len(losses),
        "final_wins": len(wins),
        "final_loss_rate_pct": 100.0 * len(losses) / n if n else 0.0,
        "avg_current_r": sum(r["current_r"] for r in rows) / n if n else 0.0,
        "avg_mfe_r_so_far": sum(r["mfe_r_so_far"] for r in rows) / n if n else 0.0,
        "hypo_minus_final_pnl_total": delta,
        "saved_on_final_losers": saved,
        "false_kill_cost_on_final_winners": false_cost,
    }


def _candidate(rows, hour, current_r_max, mfe_max=None, require_c_score=False):
    selected = [r for r in rows if r["hour"] == hour and r["current_r"] <= current_r_max]
    if mfe_max is not None:
        selected = [r for r in selected if r["mfe_r_so_far"] < mfe_max]
    if require_c_score:
        selected = [r for r in selected if r["c_would_exit"]]
    s = _stats(selected)
    s.update({
        "hour": hour,
        "current_r_max": current_r_max,
        "mfe_max": mfe_max,
        "require_c_score": require_c_score,
    })
    return s


def _analyse(rows):
    by_hour = {}
    r_bins = [(-99.0, -0.80), (-0.80, -0.50), (-0.50, -0.30), (-0.30, 0.0), (0.0, 0.25), (0.25, 0.50), (0.50, 99.0)]
    for hour in HOURS:
        hr = [r for r in rows if r["hour"] == hour]
        losses = [r for r in hr if r["final_net_pnl"] < 0]
        wins = [r for r in hr if r["final_net_pnl"] > 0]
        bins = []
        for lo, hi in r_bins:
            bucket = [r for r in hr if r["current_r"] > lo and r["current_r"] <= hi]
            item = _stats(bucket)
            item.update({"r_gt": lo, "r_le": hi})
            bins.append(item)
        by_hour[str(hour)] = {
            "all": _stats(hr),
            "final_losers": _stats(losses),
            "final_winners": _stats(wins),
            "current_r_bins": bins,
            "current_C_signal": _stats([r for r in hr if r["c_would_exit"]]),
        }

    candidates = []
    for hour in HOURS:
        for rmax in (-0.30, -0.50, -0.80):
            candidates.append(_candidate(rows, hour, rmax, None, False))
            for mfe in (0.25, 0.50, 0.80):
                candidates.append(_candidate(rows, hour, rmax, mfe, False))
        # Current C score trigger at each exact hour, then MFE filters.
        candidates.append(_candidate(rows, hour, 99.0, None, True))
        for mfe in (0.25, 0.50, 0.80):
            candidates.append(_candidate(rows, hour, 99.0, mfe, True))

    candidates.sort(key=lambda x: (x["hour"], -x["hypo_minus_final_pnl_total"], -x["count"]))
    return {"by_hour": by_hour, "candidate_rules": candidates}


def main():
    global _ORIGINAL_PROCESS_EXIT
    start, end = bt._configure()
    bt._verify_lock()

    print(
        "V164_HOURLY_EARLY_EXIT_DEEPDIVE_720D "
        f"start={start.isoformat()} end={end.isoformat()} baseline=no_early_exit "
        "snapshots=1H,2H,3H,4H baseline_exit_unchanged",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in values] for tf, values in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    # Prime the same Early Exit feature engine used by the current C research,
    # but only for diagnostics. It never changes the untouched baseline exits.
    ee.research = research
    ee._prime_feature_map(data["5m"], data["15m"], data["1H"])

    _ORIGINAL_PROCESS_EXIT = research.Simulator.process_exit
    research.Simulator.process_exit = _process_exit_with_hourly_snapshots
    try:
        sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    finally:
        research.Simulator.process_exit = _ORIGINAL_PROCESS_EXIT

    finals = {str(r.get("opportunity_id")): r for r in sim.trades if r.get("opportunity_id")}
    joined = []
    for snap in SNAPSHOTS:
        final = finals.get(snap["opportunity_id"])
        if not final:
            continue
        row = dict(snap)
        row.update({
            "final_reason": str(final.get("reason") or ""),
            "final_net_pnl": float(final.get("net_pnl") or 0.0),
            "final_realized_r": float(final.get("realized_r") or 0.0),
            "final_hold_min": float(final.get("hold_min") or 0.0),
            "final_mfe_r": float(final.get("mfe_r") or 0.0),
            "final_mae_r": float(final.get("mae_r") or 0.0),
        })
        row["hypo_minus_final_pnl"] = row["hypo_exit_net_pnl"] - row["final_net_pnl"]
        joined.append(row)

    analysis = _analyse(joined)
    out = Path("backtest_output_v164_hourly_early_exit_deepdive_720d")
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(joined, out / "hourly_snapshots_720d.csv")
    (out / "hourly_analysis_720d.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "baseline_metrics_720d.json").write_text(json.dumps(dict(metrics), ensure_ascii=False, indent=2), encoding="utf-8")

    print("BASELINE_METRICS", json.dumps(dict(metrics), ensure_ascii=False), flush=True)
    print("HOURLY_ANALYSIS", json.dumps(analysis, ensure_ascii=False), flush=True)
    print("SNAPSHOT_ROWS", len(joined), flush=True)
    print("OUTPUT_DIR", out.resolve(), flush=True)


if __name__ == "__main__":
    main()

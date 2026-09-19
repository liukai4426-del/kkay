#!/usr/bin/env python3
"""Diagnostic: V1.7.0 360D short entry timing mismatch.

Find short opportunities where:
1) an earlier state inside the same closed-15m BOLL opportunity has score >=5 and <6,
   all inherited Hard Gates pass, current short signed Entry Drift <=0.20 ATR5,
   and PEE4 Lock1H is not active;
2) a later state in the same opportunity reaches score >=6 with inherited Hard Gates
   still passing, but signed short Entry Drift >0.20 ATR5.

Then independently replay the earliest score>=5 candidate using the exact entry
execution, SL/TP and PEE4 exit stack. This is a retrospective diagnostic only:
each candidate is replayed independently from 2000U and does not alter the
subsequent portfolio trade sequence.
"""
from __future__ import annotations

import bisect
import copy
import csv
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path

import run_v170_4h_neutralplus1_short_drift020_360d as v170

OUT = Path("backtest_output_v170_short_score5_timing_diagnostic_360d")
VARIANT = "boll5_overext035_pee3_mfe06"
TRACE = defaultdict(list)
RESULT_SNAPSHOTS = {}


def _finite(v, default=math.nan):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _pf(rows):
    gp = sum(max(_finite(r.get("net_pnl"), 0.0), 0.0) for r in rows)
    gl = -sum(min(_finite(r.get("net_pnl"), 0.0), 0.0) for r in rows)
    return gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0)


def _pack(rows):
    rows = list(rows)
    wins = [r for r in rows if _finite(r.get("net_pnl"), 0.0) > 0]
    net = sum(_finite(r.get("net_pnl"), 0.0) for r in rows)
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(rows) - len(wins),
        "win_rate_pct": (100.0 * len(wins) / len(rows)) if rows else 0.0,
        "net_pnl": net,
        "profit_factor": _pf(rows),
        "avg_net_pnl": (net / len(rows)) if rows else 0.0,
        "sum_realized_r": sum(_finite(r.get("realized_r"), 0.0) for r in rows),
        "avg_realized_r": (
            sum(_finite(r.get("realized_r"), 0.0) for r in rows) / len(rows)
            if rows else 0.0
        ),
        "exit_reasons": dict(Counter(str(r.get("reason") or "unknown") for r in rows)),
    }


def _state_from(result, now_ms, mark):
    opp = result.get("opportunity")
    if not isinstance(opp, dict) or str(opp.get("side") or "") != "做空":
        return None
    oid = str(opp.get("id") or result.get("opportunity_id") or "")
    if not oid:
        return None
    score = (result.get("scores") or {}).get("做空") or {}
    conf = score.get("confirmations") or {}
    ref = _finite(opp.get("trigger_reference"))
    atr5 = max(_finite(opp.get("atr5"), 0.0), 1e-12)
    signed_drift = (ref - float(mark)) / atr5 if math.isfinite(ref) else math.nan
    blockers = [str(x) for x in (conf.get("blockers") or [])]
    required = dict(conf.get("required") or {})
    state = {
        "opportunity_id": oid,
        "now_ms": int(now_ms),
        "signal_close_ms": int(opp.get("signal_close_ms") or 0),
        "signal_bar_t": int(opp.get("signal_bar_t") or 0),
        "mark": float(mark),
        "trigger_reference": ref,
        "atr5": atr5,
        "signed_short_drift_atr5": signed_drift,
        "abs_drift_atr5": abs(signed_drift) if math.isfinite(signed_drift) else math.nan,
        "score": _finite(score.get("total"), 0.0),
        "gate": bool(score.get("gate")),
        "eligible": bool(score.get("eligible")),
        "blockers": blockers,
        "required": required,
        "layers": dict(score.get("layers") or {}),
        "lock1h_active": int(now_ms) < int(v170._LOCK_UNTIL_MS),
        "4h_state": str(conf.get("4H_trend_state_actual") or conf.get("4H_trend_state") or ""),
        "macd_adverse": bool(conf.get("5m_macd_adverse") or conf.get("macd_explicit_adverse")),
        "rsi5": _finite(conf.get("rsi5")),
        "signal_age_min": max(
            0.0,
            (int(now_ms) - int(opp.get("signal_close_ms") or now_ms)) / 60_000.0,
        ),
    }
    return state


def _is_early_score5(state):
    if not state:
        return False
    return (
        5.0 <= float(state["score"]) < 6.0
        and bool(state["gate"])
        and not bool(state["lock1h_active"])
        and math.isfinite(float(state["signed_short_drift_atr5"]))
        and float(state["signed_short_drift_atr5"]) <= v170.SHORT_ENTRY_DRIFT_MAX_ATR5
    )


def _is_later_score6_drift_block(state):
    if not state:
        return False
    return (
        float(state["score"]) >= 6.0
        and bool(state["gate"])
        and not bool(state["lock1h_active"])
        and math.isfinite(float(state["signed_short_drift_atr5"]))
        and float(state["signed_short_drift_atr5"]) > v170.SHORT_ENTRY_DRIFT_MAX_ATR5
    )


def _write_csv(rows, path):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for row in rows:
            item = dict(row)
            for k, v in list(item.items()):
                if isinstance(v, (dict, list, tuple)):
                    item[k] = json.dumps(v, ensure_ascii=False, sort_keys=True)
            w.writerow(item)


def _replay_candidate(candidate, result_snapshot, meta, data, ts, funding, research, original_submit):
    """Independent exact-stack replay from the first score>=5 state."""
    result = copy.deepcopy(result_snapshot)
    row = (result.get("scores") or {}).get("做空") or {}
    original_score = float(candidate["early_score"])

    # Pass the existing threshold check without altering any gate/component.
    # Restore the recorded score on the pending leg immediately after submit.
    row["gate"] = True
    row["eligible"] = True
    row["position_multiplier"] = 1.0
    row["total"] = 6.0
    row["raw"] = max(_finite(row.get("raw"), original_score), 6.0)
    result["side"] = "做空"

    v170._LOCK_UNTIL_MS = 0
    v170._LOCK_EVENTS = []
    sim = research.Simulator(meta, funding, variant=VARIANT)
    original_submit(sim, result, int(candidate["early_ms"]), float(candidate["early_mark"]))
    if sim.pending is None:
        return {
            **candidate,
            "counterfactual_status": "submit_failed",
            "counterfactual_reason": "exact execution/sizing gate rejected the score5 candidate",
        }

    sim.pending.score = original_score
    sim.pending.factors["diagnostic_original_score"] = original_score
    sim.pending.factors["diagnostic_rule"] = "score>=5; all Hard Gates pass; signed short drift<=0.20"
    sim.pending.factors["diagnostic_pattern"] = "later score>=6 but signed short drift>0.20"

    d1 = data["1m"]
    start_i = bisect.bisect_left(ts["1m"], int(candidate["early_ms"]))
    last_bar = None
    filled = False
    for i in range(start_i, len(d1)):
        bar = d1[i]
        if int(bar["t"]) >= int(research.END_MS):
            break
        last_bar = bar
        close_ms = int(bar["t"]) + 60_000
        sim.apply_funding_until(int(bar["t"]), float(bar["o"]))
        before_pos = bool(sim.position)
        sim.process_pending(bar)
        filled = filled or bool(sim.position) or before_pos
        sim.process_exit(bar)
        sim.apply_funding_until(close_ms, float(bar["c"]))
        sim.mark_risk(close_ms, float(bar["c"]))
        if sim.trades:
            break
        if sim.pending is None and sim.position is None:
            # The 60s LIMIT expired without fill.
            break

    if not sim.trades and sim.position and last_bar is not None:
        sim.finish(last_bar)

    if not sim.trades:
        return {
            **candidate,
            "counterfactual_status": "unfilled" if not filled else "open_no_exit",
            "counterfactual_reason": "counterfactual LIMIT did not produce a closed trade",
        }

    trade = dict(sim.trades[0])
    return {
        **candidate,
        "counterfactual_status": "closed",
        "counterfactual_entry": trade.get("entry"),
        "counterfactual_exit": trade.get("exit"),
        "counterfactual_exit_reason": trade.get("reason"),
        "counterfactual_net_pnl": trade.get("net_pnl"),
        "counterfactual_realized_r": trade.get("realized_r"),
        "counterfactual_mfe_r": trade.get("mfe_r"),
        "counterfactual_mae_r": trade.get("mae_r"),
        "counterfactual_hold_min": trade.get("hold_min"),
        "counterfactual_entry_time": trade.get("entry_time"),
        "counterfactual_exit_time": trade.get("exit_time"),
    }


def main():
    started = time.perf_counter()
    v170._patch()
    start, end = v170.src.configure()
    v170.verify(start, end)
    v170.src.verify_lock = lambda a, b: None

    entry = v170.src.base
    research = v170.src.research
    meta, data, funding, cache_manifest = entry.load_market(research.base, entry.TIMEFRAMES)
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)
    v170.src._prime_feature_map(data["5m"], data["15m"], data["1H"])

    original_submit = research.Simulator.submit

    def traced_submit(self, result, now_ms, mark):
        state = _state_from(result, now_ms, mark)
        if state is not None:
            oid = state["opportunity_id"]
            TRACE[oid].append(state)
            if _is_early_score5(state):
                RESULT_SNAPSHOTS.setdefault((oid, int(now_ms)), copy.deepcopy(result))
        return original_submit(self, result, now_ms, mark)

    research.Simulator.submit = traced_submit
    sim, baseline_metrics = research.run(data, ts, meta, funding, variant=VARIANT)
    research.Simulator.submit = original_submit

    patterns = []
    for oid, states in TRACE.items():
        states = sorted(states, key=lambda x: int(x["now_ms"]))
        early = next((s for s in states if _is_early_score5(s)), None)
        if early is None:
            continue
        later = next(
            (s for s in states if int(s["now_ms"]) > int(early["now_ms"]) and _is_later_score6_drift_block(s)),
            None,
        )
        if later is None:
            continue
        patterns.append({
            "opportunity_id": oid,
            "signal_close_ms": early["signal_close_ms"],
            "early_ms": early["now_ms"],
            "early_delay_min": (early["now_ms"] - early["signal_close_ms"]) / 60_000.0,
            "early_mark": early["mark"],
            "early_score": early["score"],
            "early_signed_drift_atr5": early["signed_short_drift_atr5"],
            "early_abs_drift_atr5": early["abs_drift_atr5"],
            "early_4h_state": early["4h_state"],
            "early_macd_adverse": early["macd_adverse"],
            "early_rsi5": early["rsi5"],
            "early_layers": early["layers"],
            "later_ms": later["now_ms"],
            "later_delay_from_early_min": (later["now_ms"] - early["now_ms"]) / 60_000.0,
            "later_score": later["score"],
            "later_mark": later["mark"],
            "later_signed_drift_atr5": later["signed_short_drift_atr5"],
            "later_abs_drift_atr5": later["abs_drift_atr5"],
            "later_layers": later["layers"],
        })

    replay_rows = []
    for p in sorted(patterns, key=lambda x: int(x["early_ms"])):
        snap = RESULT_SNAPSHOTS.get((p["opportunity_id"], int(p["early_ms"])))
        if snap is None:
            replay_rows.append({**p, "counterfactual_status": "missing_snapshot"})
            continue
        replay_rows.append(
            _replay_candidate(p, snap, meta, data, ts, funding, research, original_submit)
        )

    closed = [r for r in replay_rows if r.get("counterfactual_status") == "closed"]
    summary = _pack([
        {
            "net_pnl": r.get("counterfactual_net_pnl"),
            "realized_r": r.get("counterfactual_realized_r"),
            "reason": r.get("counterfactual_exit_reason"),
        }
        for r in closed
    ])

    payload = {
        "research": "V1.7.0 360D short score5 timing mismatch diagnostic",
        "source_strategy": "4H aligned/neutral +1; explicit 4H opposite blocked; Short signed Entry Drift >0.20 blocked",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": 360},
        "diagnostic_definition": {
            "early_candidate": "short; score>=5 and <6; inherited Hard Gates pass; PEE4 Lock1H inactive; signed short drift<=0.20 ATR5",
            "later_mismatch": "same opportunity later reaches score>=6 with inherited Hard Gates pass, but signed short drift>0.20 ATR5",
            "counterfactual_entry": "first early_candidate minute; force only the 6-point threshold check to pass; all execution/sizing/structure/cost/SL gates remain exact",
            "counterfactual_exit": "exact normal SL/TP + PEE4 stack",
            "portfolio_note": "each candidate replayed independently from 2000U; later portfolio sequence/Lock1H interactions are not recomputed",
        },
        "baseline_metrics": baseline_metrics,
        "baseline_trade_count": len(sim.trades),
        "trace": {
            "short_opportunities_seen": len(TRACE),
            "pattern_opportunities": len(patterns),
            "counterfactual_closed": len(closed),
            "counterfactual_unfilled_or_rejected": len(replay_rows) - len(closed),
        },
        "counterfactual_summary": summary,
        "score_distribution": dict(Counter(f"{float(r['early_score']):.1f}" for r in replay_rows)),
        "later_delay_min_distribution": {
            "<=2": sum(float(r["later_delay_from_early_min"]) <= 2 for r in replay_rows),
            "3-5": sum(2 < float(r["later_delay_from_early_min"]) <= 5 for r in replay_rows),
            ">5": sum(float(r["later_delay_from_early_min"]) > 5 for r in replay_rows),
        },
        "cache": cache_manifest,
        "timing_sec": {"total": time.perf_counter() - started},
        "rows": replay_rows,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result_v170_short_score5_timing_diagnostic_360d.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_csv(replay_rows, OUT / "short_score5_timing_candidates_360d.csv")
    print("DIAGNOSTIC_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

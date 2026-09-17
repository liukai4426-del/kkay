#!/usr/bin/env python3
"""KAYTRADE V1.6.8 ADX30m + PEE3 fixed 180D research backtest.

This runner is the direct PEE3 counterpart to the fixed ADX30m 180D run:
- same 180D market window;
- same fixed ADX30m entry model and execution-window correction;
- same risk / TP / SL / scoring parameters;
- only PEE 3.0 is added to position management during the first 4 hours.

The legacy simulator is invoked with variant='baseline' only as a compatibility
label; the installed entry model is still HistoricalV168Adx30mModel and PEE3 is
installed through Simulator.process_exit.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

import run_v168_adx30m_pee3_compare as pee

entry = pee.entry
base = pee.base
research = pee.research

DAYS = 180
OUT = Path("backtest_output_v168_adx30m_pee3_180d")


def verify_lock():
    entry.verify_lock(DAYS)
    assert entry.HistoricalV168Adx30mModel.ENTRY_WINDOW_MS == 1_800_000
    assert entry.HistoricalV168Adx30mModel.THRESHOLD == 6.0
    assert entry.ADX_PERIOD == 14
    assert entry.ADX_THRESHOLD == 25.0
    assert entry.ADX_SCORE == 1.0
    assert pee.PEE_VERSION == "3.0"
    assert pee.MONITOR_MAX_MS == 4 * 60 * 60_000
    assert pee.STRUCTURE_BREAK_ATR == 0.25


def main():
    started = time.perf_counter()
    start, end = pee.configure(DAYS)
    verify_lock()
    OUT.mkdir(parents=True, exist_ok=True)

    print(
        "V168_ADX30M_PEE3_FIXED_180D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "entry=V1.6.8_ADX30m_fixed", "signal_lifecycle=30m",
        "adx5=ADX14_DI_plus1", "adx_threshold=25",
        "PEE=3.0", "monitor=0..4H", "mfe_cutoff=0.80R",
        "current_r_gate=-0.50R", flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = base.load_market(research.base, base.TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)
    pee._prime_feature_map(data["5m"], data["15m"], data["1H"])

    sim_started = time.perf_counter()
    research.Simulator.process_exit = pee._process_exit_pee3
    # Compatibility label only. ADX30m entry model was installed by configure().
    sim, raw_metrics = research.run(data, ts, meta, funding, variant="baseline")
    sim_elapsed = time.perf_counter() - sim_started

    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    metrics["days"] = DAYS
    metrics["label"] = "V1.6.8 ADX30m + PEE 3.0 — 180D"
    pee_exits = [r for r in rows if str(r.get("reason") or "") == pee.EXIT_REASON]
    stage_counts = Counter(str(r.get("early_exit_stage") or "unknown") for r in pee_exits)
    path_counts = Counter(str(r.get("early_exit_path") or "unknown") for r in pee_exits)
    analysis = base._analysis(rows)
    elapsed = time.perf_counter() - started

    payload = {
        "research": "KAYTRADE V1.6.8 ADX30m + PEE3 fixed 180D",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": entry.CAPITAL,
        "strategy_lock": {
            "version": entry.VERSION,
            "build": entry.BUILD,
            "boll_timeframe": "15m latest CLOSED outer only",
            "boll_signal_lifetime_ms": entry.BOLL_WINDOW_MS,
            "boll_signal_lifetime": "30 minutes from closed 15m trigger",
            "boll_outer_score": 2.0,
            "4h": "aligned mandatory Hard Gate and +1",
            "1h_ema9_26_score_enabled": False,
            "5m_adx_period": entry.ADX_PERIOD,
            "5m_adx_threshold": entry.ADX_THRESHOLD,
            "5m_adx_score": entry.ADX_SCORE,
            "5m_adx_hard_gate": True,
            "5m_macd": "explicit adverse blocks; improving +1",
            "5m_rsi": "30-70 Hard Gate",
            "score_threshold": entry.THRESHOLD,
            "position": "fixed 1x",
            "entry": "LIMIT, audited closed-1m historical fill proxy",
            "stop": "1H ATR x1",
            "tp": "2R full position",
            "break_even": False,
            "pee_version": pee.PEE_VERSION,
            "pee_monitor_minutes": 240,
            "pee_current_r_gate": -0.50,
            "pee_mfe_cutoff_r": 0.80,
            "pee_structure_break_atr": pee.STRUCTURE_BREAK_ATR,
        },
        "metrics": metrics,
        "analysis": analysis,
        "pee3": {
            "early_exit_count": len(pee_exits),
            "stage_counts": dict(stage_counts),
            "path_counts": dict(path_counts),
        },
        "simulator_stats": dict(sim.stats),
        "top_blockers": sim.blockers.most_common(30),
        "transitions": dict(sim.transitions),
        "cache": cache_manifest,
        "correction": {
            "adx30m_execution_window_missing_now_ms_fix_retained": True,
            "legacy_variant_label": "baseline",
            "entry_strategy_changed": False,
            "position_management_change": "PEE3 only",
        },
        "timing_sec": {
            "market_load": market_elapsed,
            "simulation": sim_elapsed,
            "total": elapsed,
        },
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m proxy; order-book queue position is unavailable.",
            "Research-only PEE3 comparison run; production strategy files are unchanged.",
        ],
    }

    result_path = OUT / "result_v168_adx30m_pee3_180d.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    base._write_csv(rows, OUT / "trades_v168_adx30m_pee3_180d.csv")
    (OUT / "README.txt").write_text(
        "KAYTRADE V1.6.8 fixed ADX30m + PEE 3.0 180D research backtest.\n"
        "Same corrected ADX30m entry path as the fixed No-PEE 180D group; only PEE3 position management is added.\n",
        encoding="utf-8",
    )

    print("V168_ADX30M_PEE3_FIXED_180D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(
        f"V168_ADX30M_PEE3_FIXED_180D_TIMING total={elapsed:.2f}s "
        f"market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()

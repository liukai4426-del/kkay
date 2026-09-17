#!/usr/bin/env python3
"""KAYTRADE V1.6.8 R2 + PEE3, 365D research backtest.

Exact V1.6.8 R2 entry semantics are preserved:
- latest CLOSED 15m BOLL outer signal only;
- signal valid until the next 15m candle closes (15m lifecycle);
- 4H aligned Hard Gate +1;
- 1H EMA9/26 aligned +1 score, not a hard gate;
- 5m MACD explicit adverse blocks; improving +1;
- KDJ score removed;
- 5m RSI 30-70 Hard Gate;
- Volume >=1.20x prior-20 5m average Hard Gate;
- score >=6.0, fixed 1x LIMIT, 1H ATR x1 SL, 2R full TP, No-BE.

Only delta: PEE 3.0 position management during the first 4 hours.
No ADX score/filter and no 30m BOLL lifecycle.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import run_v168_180d_backtest_r2 as base
import run_v168_adx30m_pee3_compare as pee

DAYS = 365
BOLL_WINDOW_MS = 15 * 60 * 1000
OUT = Path("backtest_output_v168_pee3_365d")
VERSION = "1.6.8-PEE3-research"
BUILD = "1680-pee3-365d"


def configure():
    # Restore exact V1.6.8 R2 / 15m lifecycle before configuring the longer window.
    base.DAYS = DAYS
    base.FIXED_START = base.FIXED_END - timedelta(days=DAYS)
    base.BOLL_WINDOW_MS = BOLL_WINDOW_MS
    base.HistoricalV168Model.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    base.OUT = OUT

    # PEE helper imports an ADX research module, but we do not call its configure()
    # and explicitly pin the proven V1.6.8 15m lifecycle here.
    base.proven.BOLL_WINDOW_MS = BOLL_WINDOW_MS
    base.proven.Model.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    base.proven.prod.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    base.proven.prod.TIME_WINDOW_ENABLED = True

    start, end = base.configure()
    base.research.VERSION = VERSION
    base.research.BUILD = BUILD
    base.research.model = base.HistoricalV168Model
    base.research.Simulator.submit = base._submit_v168_r2
    base.research.Simulator.process_pending = base.proven._ORIG_PENDING
    base.research.Simulator.process_exit = base.proven._ORIG_EXIT
    base.research.Simulator.finish = base.proven._ORIG_FINISH
    return start, end


def verify_lock(start, end):
    assert DAYS == 365
    assert (end - start).days == 365
    assert BOLL_WINDOW_MS == 900_000
    assert base.HistoricalV168Model.VERSION == "1.6.8"
    assert base.HistoricalV168Model.BUILD == "1680"
    assert base.HistoricalV168Model.ENTRY_WINDOW_MS == 900_000
    assert base.HistoricalV168Model.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert base.HistoricalV168Model.FIVE_MINUTE_BOLL_ENABLED is False
    assert base.HistoricalV168Model.BOLL_OUTER_SCORE == 2.0
    assert base.HistoricalV168Model.THRESHOLD == 6.0
    assert base.EMA_FAST == 9 and base.EMA_SLOW == 26
    assert pee.PEE_VERSION == "3.0"
    assert pee.MONITOR_MAX_MS == 4 * 60 * 60_000
    assert pee.STRUCTURE_BREAK_ATR == 0.25


def main():
    started = time.perf_counter()
    start, end = configure()
    verify_lock(start, end)
    OUT.mkdir(parents=True, exist_ok=True)

    print(
        "V168_PEE3_365D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "baseline=V1.6.8_R2", "boll=15m_latest_closed_outer_only",
        "signal_lifecycle=15m_until_next_closed_15m",
        "boll_score=2", "4h=aligned_required_plus1",
        "ema1h=EMA9_26_plus1_PRESERVED",
        "macd=5m_explicit_adverse_only_plus1_if_improving",
        "adx=OFF", "PEE=3.0", "monitor=0..4H",
        "pee_current_r_gate=-0.50R", "pee_mfe_cutoff=0.80R",
        "threshold=6", "sl=1H_ATR_x1", "tp=2R", "be=OFF",
        flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = base.load_market(base.research.base, base.TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    base.research.cache_patch.prime_one_minute(data["1m"], base.research.MODEL_WINDOW)
    pee._prime_feature_map(data["5m"], data["15m"], data["1H"])

    sim_started = time.perf_counter()
    base.research.Simulator.process_exit = pee._process_exit_pee3
    sim, raw_metrics = base.research.run(data, ts, meta, funding, variant="baseline")
    sim_elapsed = time.perf_counter() - sim_started

    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    metrics["days"] = DAYS
    metrics["strategy_version"] = VERSION
    metrics["build"] = BUILD
    analysis = base._analysis(rows)
    pee_exits = [r for r in rows if str(r.get("reason") or "") == pee.EXIT_REASON]
    stage_counts = Counter(str(r.get("early_exit_stage") or "unknown") for r in pee_exits)
    path_counts = Counter(str(r.get("early_exit_path") or "unknown") for r in pee_exits)
    elapsed = time.perf_counter() - started

    payload = {
        "research": "KAYTRADE V1.6.8 R2 + PEE3 365D",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": base.CAPITAL,
        "strategy_lock": {
            "version": "1.6.8",
            "baseline": "V1.6.8 Build1680 R2",
            "research_build": BUILD,
            "boll_timeframe": "15m latest CLOSED outer only",
            "boll_signal_lifetime_ms": BOLL_WINDOW_MS,
            "boll_signal_lifetime": "until next closed 15m candle",
            "boll_outer_score": base.BOLL_OUTER_SCORE,
            "4h": "aligned mandatory Hard Gate and +1 score; neutral/opposite blocked",
            "1h_ema": "EMA9>EMA26 long / EMA9<EMA26 short = +1; scoring only; PRESERVED",
            "5m_macd": "explicit adverse/opposite blocks; improvement +1 retained",
            "5m_kdj_score": 0.0,
            "5m_rsi": "30-70 Hard Gate",
            "5m_volume": "<1.20x prior-20 average Hard Gate; no score",
            "5m_adx_score_enabled": False,
            "5m_adx_hard_gate": False,
            "score_threshold": base.THRESHOLD,
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
            "baseline_proven_signal_path_reused": True,
            "boll_15m_refresh_semantics_preserved": True,
            "adx_enabled": False,
            "only_strategy_delta": "PEE3 position management during first 4H",
        },
        "timing_sec": {
            "market_load": market_elapsed,
            "simulation": sim_elapsed,
            "total": elapsed,
        },
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m proxy; order-book queue position is unavailable.",
            "Research-only PEE3 run; production strategy files are unchanged.",
        ],
    }

    result_path = OUT / "result_v168_pee3_365d.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    base._write_csv(rows, OUT / "trades_v168_pee3_365d.csv")
    (OUT / "README.txt").write_text(
        "KAYTRADE V1.6.8 R2 + PEE3 365D. Exact original 15m BOLL refresh/lifecycle retained; no ADX; no BE.\n",
        encoding="utf-8",
    )
    print("V168_PEE3_365D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"V168_PEE3_365D_TIMING total={elapsed:.2f}s market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""KAYTRADE V1.6.8 R2 + 1H EMA26 pullback score + PEE3 with 0.70R MFE cutoff, 360D.

Entry model is identical to run_v168_ema26_pullback_360d.py.
Only additional delta: PEE3 during first 4H, but once MFE >= +0.70R the PEE3 protection
is permanently disabled for that trade. No ADX and no BE.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

import run_v168_ema26_pullback_360d as ema
import run_v168_adx30m_pee3_compare as pee

base = ema.base
DAYS = 360
MFE_CUTOFF_R = 0.70
OUT = Path("backtest_output_v168_ema26_pee3_mfe07_360d")
VERSION = "1.6.8-EMA26-PULLBACK-PEE3-MFE07-research"
BUILD = "1680-ema26-pb-pee3-mfe07-360d"


def configure():
    ema.OUT = OUT
    start, end = ema.configure()
    base.research.VERSION = VERSION
    base.research.BUILD = BUILD
    base.research.model = ema.HistoricalV168EMA26PullbackModel
    base.research.Simulator.submit = ema._submit
    base.research.Simulator.process_pending = base.proven._ORIG_PENDING
    base.research.Simulator.process_exit = base.proven._ORIG_EXIT
    base.research.Simulator.finish = base.proven._ORIG_FINISH
    return start, end


def verify_lock(start, end):
    ema.verify_lock(start, end)
    assert DAYS == 360
    assert (end - start).days == 360
    assert MFE_CUTOFF_R == 0.70
    assert ema.TOL_ATR15 == 0.25
    assert ema.HistoricalV168EMA26PullbackModel.ENTRY_WINDOW_MS == 900_000
    assert ema.HistoricalV168EMA26PullbackModel.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert pee.PEE_VERSION == "3.0"
    assert pee.MONITOR_MAX_MS == 4 * 60 * 60_000


def _process_exit_pee3_mfe07(self, bar):
    if not self.position:
        return
    n_before = len(self.trades)
    base.proven._ORIG_EXIT(self, bar)
    if len(self.trades) > n_before or not self.position:
        return

    close_ms = int(bar["t"]) + 60_000
    feat = pee._FEATURE_BY_CLOSE_MS.get(close_ms)
    if not feat:
        return

    pos, p = self.position, self.position.pending
    hold_ms = close_ms - int(pos.fill_time)
    if hold_ms < 0 or hold_ms > pee.MONITOR_MAX_MS or int(feat["five_bar_start_ms"]) < int(pos.fill_time):
        return

    risk = abs(float(p.limit) - float(p.stop))
    if risk <= 0.0:
        return
    current_r = ((float(bar["c"]) - float(p.limit)) * base.research.side_dir(p.side)) / risk
    mfe_r = float(pos.mfe) / risk

    # PEE3 is permanently disabled for this trade once it has proved itself to +0.70R.
    if mfe_r >= MFE_CUTOFF_R:
        p.factors["pee3_mfe07_disabled"] = True
        p.factors["pee3_mfe_cutoff_r"] = MFE_CUTOFF_R
        return
    if bool(p.factors.get("pee3_mfe07_disabled")):
        return
    if current_r > -0.50:
        return

    hard_count, hard, soft_score, components = pee._soft_and_hard(
        p.side, feat, pos, hold_ms, current_r, mfe_r
    )
    req = pee._decision(hold_ms, current_r, mfe_r, hard_count, hard, soft_score, components)
    if req is None:
        return

    self.stats["pee3_evaluated"] += 1
    self.stats[f"pee3_eval_{req['stage']}"] += 1
    self.stats[f"pee3_path_{req['path']}"] += 1
    if not req.get("allow"):
        self.stats["pee3_blocked"] += 1
        return

    p.factors["pee3_mfe_cutoff_r"] = MFE_CUTOFF_R
    p.factors["pee3_mfe07_disabled"] = False
    before = len(self.trades)
    pee._close_pee3(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req)
    if len(self.trades) > before:
        self.trades[-1]["pee3_mfe_cutoff_r"] = MFE_CUTOFF_R
        self.trades[-1]["strategy_version"] = VERSION
        self.trades[-1]["build"] = BUILD


def main():
    started = time.perf_counter()
    start, end = configure()
    verify_lock(start, end)
    OUT.mkdir(parents=True, exist_ok=True)

    print(
        "V168_EMA26_PEE3_MFE07_360D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "baseline=V1.6.8_R2_EMA26_PULLBACK",
        "ema1h_trend_score=REMOVED", "ema26_pullback=PLUS1",
        "ema26_zone=0.25x_ATR15", "PEE=3.0", "monitor=0..4H",
        "pee_current_r_gate=-0.50R", "pee_mfe_cutoff=0.70R",
        "adx=OFF", "be=OFF", "threshold=6", "sl=1H_ATR_x1", "tp=2R",
        flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = base.load_market(base.research.base, base.TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    base.research.cache_patch.prime_one_minute(data["1m"], base.research.MODEL_WINDOW)
    pee._prime_feature_map(data["5m"], data["15m"], data["1H"])

    sim_started = time.perf_counter()
    base.research.Simulator.process_exit = _process_exit_pee3_mfe07
    sim, raw_metrics = base.research.run(data, ts, meta, funding, variant="ema26_pee3_mfe07")
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
        "research": "KAYTRADE V1.6.8 R2 EMA26 pullback + PEE3 MFE0.70 360D",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": base.CAPITAL,
        "strategy_lock": {
            "baseline": "V1.6.8 R2 + EMA26 pullback score",
            "boll_timeframe": "15m latest CLOSED outer only",
            "boll_signal_lifetime_ms": base.BOLL_WINDOW_MS,
            "4h": "aligned mandatory Hard Gate and +1 score",
            "1h_ema9_26_trend_score": 0.0,
            "1h_ema26_pullback_score": 1.0,
            "ema26_pullback_tolerance_atr15": ema.TOL_ATR15,
            "5m_macd": "explicit adverse blocks; improving +1",
            "5m_rsi": "30-70 Hard Gate",
            "5m_volume": ">=1.20x prior-20 average Hard Gate; no score",
            "score_threshold": base.THRESHOLD,
            "stop": "1H ATR x1",
            "tp": "2R full position",
            "break_even": False,
            "adx": False,
            "pee_version": pee.PEE_VERSION,
            "pee_monitor_minutes": 240,
            "pee_current_r_gate": -0.50,
            "pee_mfe_cutoff_r": MFE_CUTOFF_R,
        },
        "metrics": metrics,
        "analysis": analysis,
        "pee3": {
            "early_exit_count": len(pee_exits),
            "stage_counts": dict(stage_counts),
            "path_counts": dict(path_counts),
            "mfe_cutoff_r": MFE_CUTOFF_R,
        },
        "simulator_stats": dict(sim.stats),
        "top_blockers": sim.blockers.most_common(30),
        "transitions": dict(sim.transitions),
        "cache": cache_manifest,
        "correction": {
            "proven_v168_r2_signal_path_retained": True,
            "boll_15m_refresh_semantics_preserved": True,
            "ema26_pullback_score_retained": True,
            "only_additional_delta": "PEE3 first 4H with permanent disable once MFE >= +0.70R",
            "production_strategy_variables_changed": False,
        },
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": elapsed},
    }

    (OUT / "result_v168_ema26_pee3_mfe07_360d.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base._write_csv(rows, OUT / "trades_v168_ema26_pee3_mfe07_360d.csv")
    (OUT / "README.txt").write_text(
        "V1.6.8 R2 + EMA26 pullback + PEE3 first 4H, MFE >= +0.70R permanently disables PEE3.\n",
        encoding="utf-8",
    )
    print("V168_EMA26_PEE3_MFE07_360D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"V168_EMA26_PEE3_MFE07_360D_TIMING total={elapsed:.2f}s market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    main()

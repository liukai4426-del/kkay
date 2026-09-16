#!/usr/bin/env python3
"""Fast, semantics-locked wrapper for the V1.6.6 360D entry-expansion research.

Speed changes only:
1) market data are loaded once (with persistent on-disk cache support);
2) 1m EMA20 cache is primed once before fork;
3) variants run in isolated forked processes in parallel batches;
4) parent merges the exact same trade/metric outputs.

Trading rules, historical data source, LIMIT-fill proxy, fees, funding, SL/TP and
all strategy gates remain delegated to run_v166_entry_expansion_4way_360d.py.
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import time
from pathlib import Path

import run_v166_entry_expansion_4way_360d as slow
from research_backtest_data_cache import load_market

OUT = Path("backtest_output_v166_entry_expansion_4way_360d_fast")
VARIANTS = slow.VARIANTS
TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H")
_SHARED_DATA = None
_SHARED_TS = None
_SHARED_META = None
_SHARED_FUNDING = None


def _metric_lock(summary, expected):
    for key, want in expected.items():
        got = summary.get(key)
        if isinstance(want, float):
            assert math.isclose(float(got), want, rel_tol=1e-11, abs_tol=1e-11), (key, got, want)
        else:
            assert got == want, (key, got, want)


def _install_variant_source(variant):
    # Exact copy of the source-switching logic in the audited serial runner.
    if variant == "boll_middle_restored":
        slow.v163.boll_entry_signal = slow.ORIG_CORE_BOLL

        def middle_apply(row, path):
            if path == slow.v163.MIDDLE_PATH:
                return row
            return slow.ORIG_APPLY(row, path)

        slow.v163._apply_outer_rules = middle_apply
    else:
        slow.v163.boll_entry_signal = slow.ORIG_BOLL
        slow.v163._apply_outer_rules = slow.ORIG_APPLY


def _run_one(variant):
    global _SHARED_DATA, _SHARED_TS, _SHARED_META, _SHARED_FUNDING
    started = time.perf_counter()
    slow.configure()
    _install_variant_source(variant)

    slow.prod.ENTRY_WINDOW_MS = 300_000
    slow.prod.TIME_WINDOW_ENABLED = True
    slow.prod.THRESHOLD = 6.0
    slow.prod._patch_v164_base()

    model = slow.make_model(variant)
    slow.research.model = model
    slow.research.Simulator.submit = slow.submit_variant
    slow.research.Simulator.process_pending = slow.ORIG_PENDING
    slow.research.Simulator.process_exit = slow.ORIG_EXIT
    slow.research.Simulator.finish = slow.ORIG_FINISH

    sim, metrics = slow.research.run(
        _SHARED_DATA,
        _SHARED_TS,
        _SHARED_META,
        _SHARED_FUNDING,
        variant="baseline",
    )
    rows = list(sim.trades)
    summary = slow.metric(dict(metrics))
    elapsed = time.perf_counter() - started

    OUT.mkdir(parents=True, exist_ok=True)
    slow.write_csv(rows, OUT / f"trades_{variant}_360d.csv")
    (OUT / f"metrics_{variant}_360d.json").write_text(
        json.dumps(dict(metrics) | {
            "variant": variant,
            "days": 360,
            "boll_lifetime_min": 5,
            "fast_runner": True,
            "worker_elapsed_sec": elapsed,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / f"worker_{variant}.json").write_text(
        json.dumps({"variant": variant, "summary": summary, "rows": rows, "elapsed_sec": elapsed}, ensure_ascii=False),
        encoding="utf-8",
    )
    print("FAST_RESULT", variant, json.dumps(summary, ensure_ascii=False), f"elapsed={elapsed:.2f}s", flush=True)


def _run_parallel(workers):
    # Linux GitHub runner: fork inherits the already-fetched immutable market data
    # without pickling/copying hundreds of thousands of candle dictionaries.
    ctx = mp.get_context("fork")
    for offset in range(0, len(VARIANTS), workers):
        batch = VARIANTS[offset:offset + workers]
        procs = []
        print("FAST_BATCH_START", list(batch), f"workers={workers}", flush=True)
        for variant in batch:
            p = ctx.Process(target=_run_one, args=(variant,), name=f"bt-{variant}")
            p.start()
            procs.append((variant, p))
        failed = []
        for variant, proc in procs:
            proc.join()
            if proc.exitcode != 0:
                failed.append((variant, proc.exitcode))
        if failed:
            raise RuntimeError(f"variant workers failed: {failed}")


def _load_workers():
    requested = int(os.environ.get("BACKTEST_WORKERS", "0") or 0)
    cpus = max(1, os.cpu_count() or 1)
    if requested > 0:
        return max(1, min(requested, len(VARIANTS)))
    return max(1, min(3, cpus, len(VARIANTS)))


def main():
    global _SHARED_DATA, _SHARED_TS, _SHARED_META, _SHARED_FUNDING
    total_started = time.perf_counter()
    start, end = slow.configure()
    OUT.mkdir(parents=True, exist_ok=True)
    print(
        "V166_FAST_360D",
        start.isoformat(), end.isoformat(),
        "BOLL_LIFETIME=5m",
        "strategy_semantics=LOCKED",
        flush=True,
    )

    fetch_started = time.perf_counter()
    meta, data, funding, cache_manifest = load_market(slow.research.base, TIMEFRAMES)
    fetch_elapsed = time.perf_counter() - fetch_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}

    # This is the same audited cache used by the serial runner. Prime it once;
    # children inherit it copy-on-write.
    slow.research.cache_patch.prime_one_minute(data["1m"], slow.research.MODEL_WINDOW)

    _SHARED_META = meta
    _SHARED_DATA = data
    _SHARED_FUNDING = funding
    _SHARED_TS = ts

    workers = _load_workers()
    sim_started = time.perf_counter()
    _run_parallel(workers)
    sim_elapsed = time.perf_counter() - sim_started

    payloads = {}
    allrows = {}
    summaries = {}
    worker_elapsed = {}
    for variant in VARIANTS:
        payload = json.loads((OUT / f"worker_{variant}.json").read_text(encoding="utf-8"))
        payloads[variant] = payload
        allrows[variant] = payload["rows"]
        summaries[variant] = payload["summary"]
        worker_elapsed[variant] = payload["elapsed_sec"]

    # Regression lock against Run 35064436700. Any strategy-result drift fails.
    baseline_expected = {
        "trades": 9,
        "wins": 4,
        "losses": 5,
        "net_pnl": 16.981453410167433,
        "profit_factor": 1.1918026758327422,
    }
    threshold_expected = {
        "trades": 12,
        "wins": 6,
        "losses": 6,
        "net_pnl": 33.53256686124314,
        "profit_factor": 1.309324934632984,
    }
    _metric_lock(summaries["baseline"], baseline_expected)
    _metric_lock(summaries["threshold_5_5"], threshold_expected)

    baseline_rows = allrows["baseline"]
    baseline_ids = {str(r.get("opportunity_id") or "") for r in baseline_rows}
    incremental = {}
    for variant in VARIANTS:
        ids = {str(r.get("opportunity_id") or "") for r in allrows[variant]}
        added = [r for r in allrows[variant] if str(r.get("opportunity_id") or "") not in baseline_ids]
        lost = [r for r in baseline_rows if str(r.get("opportunity_id") or "") not in ids]
        incremental[variant] = {
            "added_vs_baseline": slow.stats(added),
            "lost_from_baseline": slow.stats(lost),
            "shared": len(ids & baseline_ids),
        }
        slow.write_csv(added, OUT / f"added_{variant}_360d.csv")

    total_elapsed = time.perf_counter() - total_started
    comparison = {
        "research": "V1.6.6 entry expansion 360D — accelerated execution benchmark",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "boll_signal_lifetime": "5m exact",
        "strategy_semantics": "identical to Run 35064436700 serial runner",
        "workers": workers,
        "cache": cache_manifest,
        "timing_sec": {
            "market_load": fetch_elapsed,
            "parallel_simulation_wall": sim_elapsed,
            "total_wall": total_elapsed,
            "worker_elapsed": worker_elapsed,
        },
        "variants": summaries,
        "incremental": incremental,
    }
    (OUT / "comparison_360d_fast.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "SPEED_REPORT.md").write_text(
        "\n".join([
            "# KAYTRADE Backtest Speed V1",
            "",
            f"Market load: {fetch_elapsed/60:.2f} min",
            f"Parallel simulation wall time: {sim_elapsed/60:.2f} min",
            f"Total wall time: {total_elapsed/60:.2f} min",
            f"Workers: {workers}",
            f"Cache hits: {cache_manifest['hits']}/{cache_manifest['objects']}",
            "",
            "Regression lock: PASS only if baseline and threshold_5_5 reproduce Run 35064436700 exactly.",
        ]), encoding="utf-8"
    )
    print("FAST_COMPARISON", json.dumps(comparison, ensure_ascii=False, indent=2), flush=True)
    print("FAST_REGRESSION_LOCK_PASS", flush=True)


if __name__ == "__main__":
    main()

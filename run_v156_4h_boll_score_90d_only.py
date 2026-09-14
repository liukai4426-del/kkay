#!/usr/bin/env python3
"""Experiment-only 90D runner for V1.5.6 mandatory 4H BOLL gate + score."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import run_v1544_180d_fixed as compat
research = compat.research
import v156_4h_boll_score_model as experiment

OUTDIR = Path("backtest_output_v156_4h_boll_score_90d_only")
OUTDIR.mkdir(parents=True, exist_ok=True)
END = research.END
START = END - timedelta(days=90)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)


def main():
    research.START = START
    research.END = END
    research.START_MS = START_MS
    research.END_MS = END_MS
    research.COOLDOWN_MS = 0
    for name, value in {
        "START": START, "END": END, "START_MS": START_MS, "END_MS": END_MS,
        "COOLDOWN_MINUTES": 0,
    }.items():
        setattr(research.base, name, value)

    research.model.evaluate = experiment.evaluate
    research.model.execution_checks = experiment.execution_checks

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    metrics.update({
        "label": "V1.5.6 + mandatory 4H BOLL region +1; 15m pullback +1",
        "days": 90,
        "start_utc": START.isoformat(),
        "end_utc": END.isoformat(),
        "cooldown_minutes": 0,
        "loss_pause_enabled": False,
        "path_c_enabled": False,
        "requested_rule": {
            "4h_boll_required": True,
            "4h_boll_score": 1.0,
            "long_4h_zone": "closed 4H close between BOLL middle and upper inclusive",
            "short_4h_zone": "closed 4H close between BOLL lower and middle inclusive",
            "15m_correct_direction_pullback_score": 1.0,
            "score_threshold": 6.0,
        },
    })
    (OUTDIR / "experiment_metrics_90d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTDIR / "experiment_trades_90d.json").write_text(json.dumps(sim.trades, ensure_ascii=False, indent=2), encoding="utf-8")
    print("V156_4H_BOLL_90D_EXPERIMENT")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

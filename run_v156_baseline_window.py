#!/usr/bin/env python3
"""KAYTRADE V1.5.6 current-core baseline backtest for a requested horizon.

V1.5.6 is a UI/log release and preserves the V1.5.5/Build1551 trading core,
which in turn preserves the verified V1.5.4 Build1544 strategy model.

This runner intentionally applies NO experimental score overlays.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import timedelta
from pathlib import Path

import run_v1544_180d_fixed as compat

research = compat.research
import v154_model as production


def _write_trade_csv(rows, path: Path):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for row in rows:
            out = dict(row)
            for key, value in list(out.items()):
                if isinstance(value, (dict, list, tuple)):
                    out[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            w.writerow(out)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    if days not in (90, 180):
        raise SystemExit("days must be 90 or 180")

    end = research.END
    start = end - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms
    # Current V1.5.6 semantics: post-close cooldown OFF.
    research.COOLDOWN_MS = 0
    for name, value in {
        "START": start,
        "END": end,
        "START_MS": start_ms,
        "END_MS": end_ms,
        "COOLDOWN_MINUTES": 0,
    }.items():
        setattr(research.base, name, value)

    # Explicitly route the research harness through the exact production model.
    research.model.evaluate = production.evaluate
    research.model.execution_checks = production.execution_checks

    assert production.THRESHOLD == 6.0
    assert production.ENTRY_WINDOW_MS == 0
    assert production.TIME_WINDOW_ENABLED is False
    assert research.build1544.PATH_C_ENABLED is False
    assert research.STOP_ATR == 1.0
    assert research.REWARD_R == 2.0

    print(f"V156_BASELINE_CONFIG days={days} start={start.isoformat()} end={end.isoformat()}", flush=True)

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    metrics.update({
        "label": "KAYTRADE V1.5.6 current production trading core",
        "release": "1.5.6",
        "release_build": 1560,
        "trading_core": "V1.5.5 Build1551 -> V1.5.4 Build1544",
        "days": days,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "score_threshold": 6.0,
        "cooldown_minutes": 0,
        "loss_pause_enabled": False,
        "path_c_enabled": False,
        "entry_order_type": "LIMIT",
        "boll_signal_wall_clock_expiry": False,
        "one_minute_technical_confirmation": False,
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        "research_notional_cap_usdt": research.FIRST_SIGNAL_NOTIONAL,
        "research_leverage": research.LEVERAGE,
    })

    outdir = Path(f"backtest_output_v156_baseline_{days}d")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"metrics_{days}d.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_trade_csv(sim.trades, outdir / f"trades_{days}d.csv")

    report = [
        f"# KAYTRADE V1.5.6 Current-Core Baseline — {days}D",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Strategy lock",
        "- No 4H BOLL experiment or score overlay.",
        "- Score threshold 6.0.",
        "- 5m BOLL entry trigger remains mandatory.",
        "- Path C OFF; post-close cooldown OFF; consecutive-loss pause OFF.",
        "- LIMIT entry; 1.0x 1H ATR stop; 2R full-position take profit.",
        "",
        "## Result",
        f"- Trades: {metrics['trades']}",
        f"- Wins / losses: {metrics['wins']} / {metrics['losses']}",
        f"- Win rate: {metrics['win_rate_pct']:.2f}%",
        f"- Net PnL: {metrics['net_pnl']:+.2f} U ({metrics['net_return_pct']:+.3f}%)",
        f"- Profit factor: {metrics['profit_factor']:.3f}",
        f"- Expectancy: {metrics['expectancy']:+.3f} U/trade",
        f"- Max DD: {metrics['max_drawdown_usdt']:.2f} U ({metrics['max_drawdown_pct']:.3f}%)",
        f"- Fees: {metrics['fees']:.2f} U",
        "",
        "Historical simulation only. Historical LIMIT execution uses the research harness proxy rather than historical orderbook bid/ask.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print(f"V156_BASELINE_{days}D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

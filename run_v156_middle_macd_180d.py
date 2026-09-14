#!/usr/bin/env python3
"""KAYTRADE V1.5.6 180D research: middle_rsi requires existing 5m MACD improvement.

Exactly one strategy experiment is applied on top of the locked V1.5.6 baseline:
- only middle_rsi is blocked unless the existing macd_improving layer is true.
- outer-band paths are unchanged.
- the harness keeps the selected BOLL path and opportunity lifecycle unchanged.
- variant_accept is checked again inside Simulator.submit immediately before order
  preparation/execution checks, so a blocked middle signal cannot submit a LIMIT.
"""
from __future__ import annotations

import csv
import json
from datetime import timedelta
from pathlib import Path

import run_v1544_180d_fixed as compat

research = compat.research
import v154_model as production

DAYS = 180
VARIANT = "middle_requires_macd"
OUTDIR = Path("backtest_output_v156_middle_macd_180d")


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


def _verify_single_change_lock():
    # The experiment must reuse the formal score layer; no alternate MACD rule.
    mid = {"signal_path": "middle_rsi"}
    outer_long = {"signal_path": "lower_band"}
    outer_short = {"signal_path": "upper_band"}
    no_macd = {"total": 8.0, "layers": {"macd_improving": 0.0}}
    yes_macd = {"total": 6.0, "layers": {"macd_improving": 1.0}}

    before = dict(mid)
    assert research.variant_accept(VARIANT, no_macd, mid) is False
    assert research.variant_accept(VARIANT, yes_macd, mid) is True
    assert research.variant_accept(VARIANT, no_macd, outer_long) is True
    assert research.variant_accept(VARIANT, no_macd, outer_short) is True
    assert mid == before  # filtering never rewrites/reclassifies the opportunity.

    # Formal model still owns MACD calculation and all baseline rules.
    assert production.THRESHOLD == 6.0
    assert production.ENTRY_WINDOW_MS == 0 and production.TIME_WINDOW_ENABLED is False
    assert research.build1544.PATH_C_ENABLED is False
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0
    assert research.COOLDOWN_MS == 0


def main():
    end = research.END
    start = end - timedelta(days=DAYS)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms
    research.COOLDOWN_MS = 0
    for name, value in {
        "START": start,
        "END": end,
        "START_MS": start_ms,
        "END_MS": end_ms,
        "COOLDOWN_MINUTES": 0,
    }.items():
        setattr(research.base, name, value)

    # Route the simulator through the unchanged formal V1.5.6 trading model.
    # Its score layer `macd_improving` is produced by the existing _macd_state
    # definition from closed 5m data. The only added gate is VARIANT at submit.
    research.model.evaluate = production.evaluate
    research.model.execution_checks = production.execution_checks

    _verify_single_change_lock()

    print(
        f"V156_MIDDLE_MACD_CONFIG days={DAYS} start={start.isoformat()} end={end.isoformat()} "
        f"variant={VARIANT}", flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant=VARIANT)
    metrics = dict(metrics)
    metrics.update({
        "label": "KAYTRADE V1.5.6 middle_rsi requires existing 5m MACD improvement",
        "release": "1.5.6",
        "experiment": "middle_rsi_macd_improving_required",
        "single_strategy_change": True,
        "days": DAYS,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "score_threshold": 6.0,
        "middle_rsi_macd_required": True,
        "macd_definition": "reuse existing macd_improving / _macd_state; no cross/zero-axis/sign rules",
        "outer_band_rules_changed": False,
        "path_reclassification": False,
        "opportunity_lifecycle_changed": False,
        "submit_recheck": "Simulator.submit variant_accept before order preparation/execution_checks",
        "closed_5m_only": True,
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

    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "metrics_180d.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_trade_csv(sim.trades, OUTDIR / "trades_180d.csv")

    report = [
        "# KAYTRADE V1.5.6 — Middle RSI Requires Existing 5m MACD Improvement — 180D",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Single-change lock",
        "- middle_rsi long: existing 5m macd_improving must be true.",
        "- middle_rsi short: existing 5m macd_improving must be true.",
        "- Reuses formal _macd_state/macd_improving definition only; no MACD cross, zero-axis or sign rule added.",
        "- Uses only closed 5m bars provided by the audited closed_slice harness.",
        "- A blocked middle_rsi remains middle_rsi; it is not reclassified as an outer-band path.",
        "- Blocking does not consume/destroy the opportunity; normal replacement/invalidation/dedup remain unchanged.",
        "- Submit path rechecks the middle MACD gate immediately before LIMIT preparation/execution checks.",
        "- Everything else remains baseline: score>=6, Path C OFF, cooldown OFF, loss-pause OFF, LIMIT entry, 1H ATR stop, 2R TP.",
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
    (OUTDIR / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V156_MIDDLE_MACD_180D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", OUTDIR.resolve(), flush=True)


if __name__ == "__main__":
    main()

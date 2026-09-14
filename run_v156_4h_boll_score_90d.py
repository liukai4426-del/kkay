#!/usr/bin/env python3
"""Paired 90-day backtest for the V1.5.6 4H-BOLL gate/score experiment.

Runs the current V1.5.6/Build1544 trading core and the requested experiment on
exactly the same market data and execution assumptions. Production is untouched.
"""
from __future__ import annotations

import csv
import json
from datetime import timedelta
from pathlib import Path

import run_v1544_180d_fixed as compat

research = compat.research
import v154_model as production
import v156_4h_boll_score_model as experiment

OUTDIR = Path("backtest_output_v156_4h_boll_score_90d")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Reuse the audited endpoint of the existing research harness and shorten only
# the horizon to 90 days. This keeps the data/execution comparison apples-to-apples.
END = research.END
START = END - timedelta(days=90)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)

PROD_EVALUATE = production.evaluate
PROD_EXECUTION_CHECKS = production.execution_checks


def _configure_window_and_live_risk_semantics():
    research.START = START
    research.END = END
    research.START_MS = START_MS
    research.END_MS = END_MS
    # V1.5.6 inherits Build1551: post-close cooldown is OFF.
    research.COOLDOWN_MS = 0
    for name, value in {
        "START": START,
        "END": END,
        "START_MS": START_MS,
        "END_MS": END_MS,
        "COOLDOWN_MINUTES": 0,
    }.items():
        setattr(research.base, name, value)


def _load_data():
    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)
    return meta, data, ts, funding


def _run_with(evaluate_fn, execution_checks_fn, data_bundle, label):
    meta, data, ts, funding = data_bundle
    research.model.evaluate = evaluate_fn
    research.model.execution_checks = execution_checks_fn
    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    metrics.update({
        "label": label,
        "days": 90,
        "start_utc": START.isoformat(),
        "end_utc": END.isoformat(),
        "cooldown_minutes": 0,
        "loss_pause_enabled": False,
        "path_c_enabled": False,
        "entry_order_type": "LIMIT",
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
    })
    return sim, metrics


def _trade_ids(sim):
    return {str(r.get("opportunity_id") or "") for r in sim.trades if r.get("opportunity_id")}


def _delta(a, b, key):
    return float(b.get(key) or 0.0) - float(a.get(key) or 0.0)


def _write_trade_csv(rows, path):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for row in rows:
            out = dict(row)
            for key in ("score_components", "trigger_combination"):
                if isinstance(out.get(key), (dict, list)):
                    out[key] = json.dumps(out[key], ensure_ascii=False, sort_keys=True)
            w.writerow(out)


def main():
    _configure_window_and_live_risk_semantics()
    assert (END - START).days == 90
    assert experiment.THRESHOLD == 6.0
    assert experiment.ENTRY_WINDOW_MS == 0 and experiment.TIME_WINDOW_ENABLED is False
    assert float(experiment.COST_MAX_R) == 0.30
    assert research.build1544.PATH_C_ENABLED is False
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0

    data_bundle = _load_data()

    print("RUN baseline_v156_current", START, END, flush=True)
    base_sim, base_metrics = _run_with(
        PROD_EVALUATE, PROD_EXECUTION_CHECKS, data_bundle, "V1.5.6 current scoring"
    )

    print("RUN experiment_v156_4h_boll_gate_score", START, END, flush=True)
    exp_sim, exp_metrics = _run_with(
        experiment.evaluate, experiment.execution_checks, data_bundle,
        "V1.5.6 + mandatory 4H BOLL region +1; 15m pullback +1",
    )

    base_ids = _trade_ids(base_sim)
    exp_ids = _trade_ids(exp_sim)
    comparison = {
        "window_days": 90,
        "start_utc": START.isoformat(),
        "end_utc": END.isoformat(),
        "baseline": base_metrics,
        "experiment": exp_metrics,
        "delta_experiment_minus_baseline": {
            "trades": int(exp_metrics["trades"]) - int(base_metrics["trades"]),
            "net_pnl": _delta(base_metrics, exp_metrics, "net_pnl"),
            "net_return_pct": _delta(base_metrics, exp_metrics, "net_return_pct"),
            "win_rate_pct_points": _delta(base_metrics, exp_metrics, "win_rate_pct"),
            "profit_factor": _delta(base_metrics, exp_metrics, "profit_factor"),
            "expectancy": _delta(base_metrics, exp_metrics, "expectancy"),
            "max_drawdown_usdt": _delta(base_metrics, exp_metrics, "max_drawdown_usdt"),
            "fees": _delta(base_metrics, exp_metrics, "fees"),
        },
        "opportunity_trade_overlap": {
            "baseline_unique_trade_opportunities": len(base_ids),
            "experiment_unique_trade_opportunities": len(exp_ids),
            "shared": len(base_ids & exp_ids),
            "removed_vs_baseline": len(base_ids - exp_ids),
            "added_vs_baseline": len(exp_ids - base_ids),
        },
        "requested_rule": {
            "4h_boll_required": True,
            "4h_boll_score": 1.0,
            "long_4h_zone": "closed 4H close between BOLL middle and upper inclusive",
            "short_4h_zone": "closed 4H close between BOLL lower and middle inclusive",
            "15m_correct_direction_pullback_score": 1.0,
            "score_threshold": 6.0,
        },
        "shared_execution_assumptions": {
            "path_c": "OFF",
            "post_close_cooldown": "OFF",
            "consecutive_loss_pause": "OFF",
            "entry": "LIMIT proxy from closed 1m close; fill only if next 1m range touches",
            "stop": "1.0x 1H ATR",
            "take_profit": "2R full position",
            "cost_gate": "0.30R",
            "research_notional_cap_usdt": research.FIRST_SIGNAL_NOTIONAL,
            "research_leverage": research.LEVERAGE,
        },
        "caveats": [
            "Historical LIMIT entries use the audited closed-1m-price proxy because historical bid/ask orderbook is unavailable.",
            "OKX REST funding-history coverage can be incomplete for the full window; inspect funding coverage before treating funding PnL as complete.",
            "This is historical simulation and not a claim of future profitability.",
        ],
    }

    (OUTDIR / "comparison_90d.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTDIR / "baseline_metrics_90d.json").write_text(
        json.dumps(base_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTDIR / "experiment_metrics_90d.json").write_text(
        json.dumps(exp_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_trade_csv(base_sim.trades, OUTDIR / "baseline_trades_90d.csv")
    _write_trade_csv(exp_sim.trades, OUTDIR / "experiment_trades_90d.csv")

    d = comparison["delta_experiment_minus_baseline"]
    report = [
        "# V1.5.6 4H BOLL Gate + Score — 90D paired backtest",
        "",
        f"Window: {START.isoformat()} -> {END.isoformat()}",
        "",
        "## Requested experiment",
        "- 4H BOLL is mandatory: long middle→upper; short lower→middle.",
        "- Passing 4H BOLL adds +1.",
        "- 15m correct-direction pullback resonance is reduced from +2 to +1.",
        "- Score threshold remains 6.0; other Hard Blocks/execution rules are unchanged.",
        "",
        "## Baseline",
        f"- Trades: {base_metrics['trades']}",
        f"- Net PnL: {base_metrics['net_pnl']:+.2f} U ({base_metrics['net_return_pct']:+.3f}%)",
        f"- Win rate: {base_metrics['win_rate_pct']:.2f}%",
        f"- Profit factor: {base_metrics['profit_factor']:.3f}",
        f"- Max DD: {base_metrics['max_drawdown_usdt']:.2f} U ({base_metrics['max_drawdown_pct']:.3f}%)",
        "",
        "## Experiment",
        f"- Trades: {exp_metrics['trades']}",
        f"- Net PnL: {exp_metrics['net_pnl']:+.2f} U ({exp_metrics['net_return_pct']:+.3f}%)",
        f"- Win rate: {exp_metrics['win_rate_pct']:.2f}%",
        f"- Profit factor: {exp_metrics['profit_factor']:.3f}",
        f"- Max DD: {exp_metrics['max_drawdown_usdt']:.2f} U ({exp_metrics['max_drawdown_pct']:.3f}%)",
        "",
        "## Delta (experiment - baseline)",
        f"- Trades: {d['trades']:+d}",
        f"- Net PnL: {d['net_pnl']:+.2f} U",
        f"- Win rate: {d['win_rate_pct_points']:+.2f} pp",
        f"- Profit factor: {d['profit_factor']:+.3f}",
        f"- Max DD: {d['max_drawdown_usdt']:+.2f} U",
        "",
        "## Caveat",
        "Historical simulation only; LIMIT execution is a closed-1m proxy and funding coverage may be incomplete.",
    ]
    (OUTDIR / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V156_4H_BOLL_90D_COMPARISON")
    print(json.dumps(comparison, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", OUTDIR.resolve(), flush=True)


if __name__ == "__main__":
    main()

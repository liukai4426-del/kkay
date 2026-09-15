#!/usr/bin/env python3
"""V1.6.4 strict 180D B/C Early Exit comparison.

Entry/risk rules are locked to release V1.6.4:
- 5m BOLL outer-band only, RSI 30..70, 5m MACD improvement, 4H aligned;
- 5m BOLL outer trigger = 2.5 score points;
- signal-volume score removed;
- signal 5m Volume >= 1.20x prior-20 average is a hard opening block;
- 1x LIMIT entry, 1H ATR x1 initial SL, 2R full TP, BE off.

B = current Early Exit Score, first 4H only:
    <= -0.30R => score >= 4
    <= -0.50R => score >= 3
    <= -0.80R => score >= 2
C = improved Early Exit Score, first 4H only:
    no ordinary early exit above -0.50R
    <= -0.50R => score >= 3
    <= -0.80R => score >= 2
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import run_v162_early_exit_score_180d_2000u as old
import v163_model
import v164_model as production

research = old.research
baseline = old.baseline

DAYS = 180
EXIT_REASON = old.EXIT_REASON


def _configure():
    # Reuse the audited 180D historical simulator and Early Exit feature engine,
    # but route all entry evaluation through the formal V1.6.4 production model.
    old.production = production
    # old._configure_180d() calls this compatibility hook; V1.6.4 evaluate itself
    # also installs the V1.6.3 outer-only source defensively.
    production._install_signal_patch = v163_model._install_signal_patch
    start, end = old._configure_180d()
    research.model = production
    research.VERSION = production.VERSION
    research.BUILD = "1640-early-exit-bc-180d"
    return start, end


def _threshold_b(current_r):
    return old._dynamic_threshold(current_r)


def _threshold_c(current_r):
    if current_r <= -0.80:
        return 2.0
    if current_r <= -0.50:
        return 3.0
    return None


def _make_process_exit(threshold_fn, rule_name):
    def _process_exit(self, bar):
        if not self.position:
            return
        n_before = len(self.trades)
        old._ORIGINAL_PROCESS_EXIT(self, bar)
        if len(self.trades) > n_before or not self.position:
            return

        pos = self.position
        p = pos.pending
        close_ms = int(bar["t"]) + 60_000
        feat = old._FEATURE_BY_CLOSE_MS.get(close_ms)
        if not feat:
            return

        hold_ms = close_ms - int(pos.fill_time)
        if hold_ms < 0 or hold_ms > old.MONITOR_MAX_MS:
            return
        if int(feat["five_bar_start_ms"]) < int(pos.fill_time):
            return

        risk = old._original_risk_distance(p)
        if risk <= 0.0:
            return
        mark = float(bar["c"])
        current_r = ((mark - float(p.limit)) * research.side_dir(p.side)) / risk
        threshold = threshold_fn(current_r)
        if threshold is None:
            return

        score, components, mfe_r = old._score_early_exit(
            p.side, feat, pos, risk, hold_ms, current_r
        )
        self.stats["early_exit_score_evaluated"] += 1
        if score < threshold:
            return
        old._close_early_exit(
            self, bar, feat, current_r, score, threshold, components, mfe_r
        )
        if self.trades:
            self.trades[-1]["early_exit_rule"] = rule_name
    return _process_exit


def _variant_summary(sim, metrics):
    exits = [r for r in sim.trades if str(r.get("reason")) == EXIT_REASON]
    thresholds = Counter(str(float(r.get("early_exit_threshold") or 0.0)) for r in exits)
    def avg(key):
        return sum(float(r.get(key) or 0.0) for r in exits) / len(exits) if exits else 0.0
    return {
        "trades": metrics["trades"],
        "wins": metrics["wins"],
        "losses": metrics["losses"],
        "win_rate_pct": metrics["win_rate_pct"],
        "net_pnl": metrics["net_pnl"],
        "net_return_pct": metrics["net_return_pct"],
        "profit_factor": metrics["profit_factor"],
        "expectancy": metrics["expectancy"],
        "max_drawdown_usdt": metrics["max_drawdown_usdt"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "early_exit_count": len(exits),
        "early_exit_avg_r": avg("realized_r"),
        "early_exit_avg_hold_min": avg("hold_min"),
        "threshold_counts": dict(thresholds),
    }


def _compare(b_sim, b_metrics, c_sim, c_metrics):
    b = _variant_summary(b_sim, b_metrics)
    c = _variant_summary(c_sim, c_metrics)
    b_by_id = {str(r.get("opportunity_id")): r for r in b_sim.trades if r.get("opportunity_id")}
    c_by_id = {str(r.get("opportunity_id")): r for r in c_sim.trades if r.get("opportunity_id")}

    shallow = []
    for r in b_sim.trades:
        if str(r.get("reason")) != EXIT_REASON:
            continue
        if float(r.get("early_exit_threshold") or 0.0) != 4.0:
            continue
        oid = str(r.get("opportunity_id") or "")
        c_row = c_by_id.get(oid)
        shallow.append({
            "opportunity_id": oid,
            "side": r.get("side"),
            "entry_time": r.get("entry_time"),
            "b_exit_time": r.get("exit_time"),
            "b_hold_min": r.get("hold_min"),
            "b_current_r": r.get("early_exit_current_r"),
            "b_score": r.get("early_exit_score"),
            "b_net_pnl": r.get("net_pnl"),
            "c_reason": c_row.get("reason") if c_row else None,
            "c_hold_min": c_row.get("hold_min") if c_row else None,
            "c_net_pnl": c_row.get("net_pnl") if c_row else None,
            "c_realized_r": c_row.get("realized_r") if c_row else None,
            "c_minus_b_pnl": (float(c_row.get("net_pnl") or 0.0) - float(r.get("net_pnl") or 0.0)) if c_row else None,
        })

    shallow_outcomes = Counter(str(x.get("c_reason") or "UNMATCHED") for x in shallow)
    matched_delta = sum(float(x.get("c_minus_b_pnl") or 0.0) for x in shallow if x.get("c_minus_b_pnl") is not None)
    return {
        "B_current_early_exit": b,
        "C_no_shallow_early_exit": c,
        "delta_C_minus_B": {
            "trades": c_metrics["trades"] - b_metrics["trades"],
            "wins": c_metrics["wins"] - b_metrics["wins"],
            "losses": c_metrics["losses"] - b_metrics["losses"],
            "win_rate_pct_points": c_metrics["win_rate_pct"] - b_metrics["win_rate_pct"],
            "net_pnl": c_metrics["net_pnl"] - b_metrics["net_pnl"],
            "net_return_pct_points": c_metrics["net_return_pct"] - b_metrics["net_return_pct"],
            "profit_factor": c_metrics["profit_factor"] - b_metrics["profit_factor"],
            "expectancy": c_metrics["expectancy"] - b_metrics["expectancy"],
            "max_drawdown_usdt": c_metrics["max_drawdown_usdt"] - b_metrics["max_drawdown_usdt"],
            "max_drawdown_pct_points": c_metrics["max_drawdown_pct"] - b_metrics["max_drawdown_pct"],
        },
        "B_shallow_exit_count": len(shallow),
        "B_shallow_exit_C_outcomes": dict(shallow_outcomes),
        "B_shallow_matched_C_minus_B_pnl": matched_delta,
        "B_shallow_details": shallow,
        "common_opportunities": len(set(b_by_id) & set(c_by_id)),
    }


def _write_rows(rows, path):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main():
    start, end = _configure()
    old._verify_lock()
    assert production.VERSION == "1.6.4"
    assert production.VOLUME_HARD_GATE == 1.20
    assert production.BOLL_OUTER_SCORE == 2.50
    assert _threshold_b(-0.30) == 4.0
    assert _threshold_b(-0.50) == 3.0
    assert _threshold_b(-0.80) == 2.0
    assert _threshold_c(-0.49) is None
    assert _threshold_c(-0.50) == 3.0
    assert _threshold_c(-0.80) == 2.0

    old._ORIGINAL_PROCESS_EXIT = research.Simulator.process_exit

    print(
        "V164_EARLY_EXIT_BC_180D_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} "
        "entry=V1.6.4 outer2.5 volume<1.20x_hard_gate "
        "B=-0.30R/4,-0.50R/3,-0.80R/2 "
        "C=no_shallow,-0.50R/3,-0.80R/2 monitor=0..240m",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)
    old._prime_feature_map(data["5m"], data["15m"], data["1H"])

    research.Simulator.process_exit = _make_process_exit(_threshold_b, "B_CURRENT")
    b_sim, b_metrics = research.run(data, ts, meta, funding, variant="baseline")
    b_metrics = dict(b_metrics)

    research.Simulator.process_exit = _make_process_exit(_threshold_c, "C_NO_SHALLOW")
    c_sim, c_metrics = research.run(data, ts, meta, funding, variant="baseline")
    c_metrics = dict(c_metrics)

    common = {
        "release_base": "1.6.4",
        "days": DAYS,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "capital": old.CAPITAL,
        "base_position_notional_usdt": old.BASE_POSITION_NOTIONAL,
        "leverage": old.LEVERAGE,
        "base_risk_usdt": old.RISK_USDT,
        "score_threshold": production.THRESHOLD,
        "middle_enabled": False,
        "outer_only": True,
        "outer_rsi_min": production.RSI_MIN,
        "outer_rsi_max": production.RSI_MAX,
        "outer_macd_improving_required": True,
        "outer_4h_aligned_required": True,
        "boll_outer_score": production.BOLL_OUTER_SCORE,
        "volume_score_enabled": False,
        "volume_score_points": 0.0,
        "volume_hard_gate_enabled": True,
        "volume_ratio_threshold": production.VOLUME_HARD_GATE,
        "be_enabled": False,
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        "early_exit_monitor_minutes": 240,
        "early_exit_eval_timeframe": "closed_5m",
    }
    b_metrics.update(common | {
        "label": "B — V1.6.4 + current Early Exit Score 180D",
        "research_variant": "v164_early_exit_B_current_180d",
        "early_exit_dynamic_thresholds": {"-0.30R": 4.0, "-0.50R": 3.0, "-0.80R": 2.0},
    })
    c_metrics.update(common | {
        "label": "C — V1.6.4 + no shallow Early Exit 180D",
        "research_variant": "v164_early_exit_C_no_shallow_180d",
        "early_exit_dynamic_thresholds": {"-0.50R": 3.0, "-0.80R": 2.0},
        "shallow_loss_ordinary_exit_disabled": True,
    })

    comparison = _compare(b_sim, b_metrics, c_sim, c_metrics)
    out = Path("backtest_output_v164_early_exit_bc_180d_2000u")
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics_B_180d.json").write_text(json.dumps(b_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "metrics_C_180d.json").write_text(json.dumps(c_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "comparison_BC_180d.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline.base.prior._write_trade_csv(b_sim.trades, out / "trades_B_180d.csv")
    baseline.base.prior._write_trade_csv(c_sim.trades, out / "trades_C_180d.csv")
    _write_rows(comparison["B_shallow_details"], out / "B_shallow_exit_vs_C.csv")

    b = comparison["B_current_early_exit"]
    c = comparison["C_no_shallow_early_exit"]
    d = comparison["delta_C_minus_B"]
    report = f"""# KAYTRADE V1.6.4 Early Exit B/C — 180D / 2000U

Window: `{start.isoformat()} -> {end.isoformat()}`

Locked entry model: V1.6.4 outer-only, BOLL outer=2.5, Volume <1.20x prior-20 hard gate, RSI30-70, 5m MACD improve, 4H aligned, 1x LIMIT, 1H ATR x1 SL, 2R full TP, No-BE.

## B — current Early Exit
- Trades: {b['trades']}
- Win rate: {b['win_rate_pct']:.2f}%
- Net PnL: {b['net_pnl']:.2f} U
- PF: {b['profit_factor']:.4f}
- Expectancy: {b['expectancy']:.4f} U/trade
- Max DD: {b['max_drawdown_usdt']:.2f} U / {b['max_drawdown_pct']:.2f}%
- Early exits: {b['early_exit_count']}
- Threshold counts: {b['threshold_counts']}

## C — no shallow ordinary Early Exit
- Trades: {c['trades']}
- Win rate: {c['win_rate_pct']:.2f}%
- Net PnL: {c['net_pnl']:.2f} U
- PF: {c['profit_factor']:.4f}
- Expectancy: {c['expectancy']:.4f} U/trade
- Max DD: {c['max_drawdown_usdt']:.2f} U / {c['max_drawdown_pct']:.2f}%
- Early exits: {c['early_exit_count']}
- Threshold counts: {c['threshold_counts']}

## C minus B
- Net PnL: {d['net_pnl']:+.2f} U
- PF: {d['profit_factor']:+.4f}
- Expectancy: {d['expectancy']:+.4f} U/trade
- Max DD: {d['max_drawdown_usdt']:+.2f} U / {d['max_drawdown_pct_points']:+.2f} pp
- B shallow exits: {comparison['B_shallow_exit_count']}
- Their C outcomes: {comparison['B_shallow_exit_C_outcomes']}
- Matched shallow C-minus-B PnL: {comparison['B_shallow_matched_C_minus_B_pnl']:+.2f} U
"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")
    print(report, flush=True)
    print("COMPARISON_JSON", flush=True)
    print(json.dumps(comparison, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", out.resolve(), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Strict A/B: KAYTRADE V1.6.1, outer 4H alignment ON, +1R->BE OFF.

Uses the exact same fixed 360-day window and 2,000U profile as the completed
V1.6.1 baseline run so the only intended exit-rule difference is disabling BE.
Entry logic stays V1.6.1: middle MACD gate, outer 4H aligned-only, outer 2x.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_v161_360d_2000u as base

research = base.research
production = base.production

DAYS = 360
CAPITAL = 2_000.0
BASE_POSITION_NOTIONAL = 2_000.0
LEVERAGE = 5
RISK_USDT = 20.0
RISK_PCT = 1.0
DAILY_LOSS = 60.0
STOP_ATR = 1.0
REWARD_R = 2.0
COOLDOWN_MS = 0
BE_ENABLED = False

# Exact window from Run 34892345237 for strict A/B comparability.
FIXED_END = datetime(2026, 9, 14, 20, 20, tzinfo=timezone.utc)
FIXED_START = FIXED_END - timedelta(days=DAYS)

# Capture audited simulator behavior before base._configure() installs BE hooks.
_ORIGINAL_PROCESS_PENDING = research.Simulator.process_pending
_ORIGINAL_PROCESS_EXIT = research.Simulator.process_exit
_ORIGINAL_FINISH = research.Simulator.finish


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


def _configure():
    # Reuse all production-entry wiring from the completed V1.6.1 backtest.
    base._configure()

    start, end = FIXED_START, FIXED_END
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms
    research.SPLIT_MS = int((end - timedelta(days=60)).timestamp() * 1000)
    research.CAPITAL = CAPITAL
    research.LEVERAGE = LEVERAGE
    research.RISK_USDT = RISK_USDT
    research.RISK_PCT = RISK_PCT
    research.DAILY_LOSS = DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = BASE_POSITION_NOTIONAL
    research.STOP_ATR = STOP_ATR
    research.REWARD_R = REWARD_R
    research.COOLDOWN_MS = COOLDOWN_MS
    research.VERSION = "1.6.1"
    research.BUILD = "1610-no-be-ab"

    for name, value in {
        "START": start,
        "END": end,
        "START_MS": start_ms,
        "END_MS": end_ms,
        "CAPITAL": CAPITAL,
        "LEVERAGE": LEVERAGE,
        "RISK_USDT": RISK_USDT,
        "RISK_PCT": RISK_PCT,
        "DAILY_LOSS": DAILY_LOSS,
        "COOLDOWN_MINUTES": 0,
        "STOP_ATR": STOP_ATR,
        "REWARD_R": REWARD_R,
    }.items():
        setattr(research.base, name, value)

    research.model = production
    research.Simulator.submit = base._submit_v161
    # Critical A/B switch: restore audited original exits, so SL stays at the
    # original 1H ATR stop for the whole trade and no BE amendment is possible.
    research.Simulator.process_pending = _ORIGINAL_PROCESS_PENDING
    research.Simulator.process_exit = _ORIGINAL_PROCESS_EXIT
    research.Simulator.finish = _ORIGINAL_FINISH
    return start, end


def _verify_lock():
    assert production.VERSION == "1.6.1"
    assert production.THRESHOLD == 6.0
    assert production.ENTRY_WINDOW_MS == 0
    assert production.TIME_WINDOW_ENABLED is False
    assert research.build1544.PATH_C_ENABLED is False
    assert research.COOLDOWN_MS == 0
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0
    assert research.CAPITAL == 2_000.0
    assert research.FIRST_SIGNAL_NOTIONAL == 2_000.0
    assert research.RISK_USDT == 20.0 and research.DAILY_LOSS == 60.0
    assert BE_ENABLED is False
    assert research.Simulator.process_exit is _ORIGINAL_PROCESS_EXIT
    assert research.Simulator.process_pending is _ORIGINAL_PROCESS_PENDING

    middle = {
        "total": 6.5, "gate": True, "eligible": True, "position_multiplier": 1.0,
        "layers": {"macd_improving": 1.0},
        "confirmations": {"required": {}, "4H_trend_state": "neutral"},
        "reason": "ok", "level": "ok",
    }
    production._apply_row_rules(middle, "middle_rsi")
    assert middle["eligible"] is True and middle["position_multiplier"] == 1.0

    outer_neutral = {
        "total": 6.5, "gate": True, "eligible": True, "position_multiplier": 1.0,
        "layers": {"macd_improving": 0.0},
        "confirmations": {"required": {}, "4H_trend_state": "neutral"},
        "reason": "ok", "level": "ok",
    }
    production._apply_row_rules(outer_neutral, "lower_band")
    assert outer_neutral["eligible"] is False and outer_neutral["position_multiplier"] == 0.0

    outer_aligned = {
        "total": 6.5, "gate": True, "eligible": True, "position_multiplier": 1.0,
        "layers": {"macd_improving": 0.0},
        "confirmations": {"required": {}, "4H_trend_state": "aligned"},
        "reason": "ok", "level": "ok",
    }
    production._apply_row_rules(outer_aligned, "upper_band")
    assert outer_aligned["eligible"] is True and outer_aligned["position_multiplier"] == 2.0


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V161_4H_ONLY_NO_BE_360D_2000U_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} capital={CAPITAL:.0f} "
        f"base_notional={BASE_POSITION_NOTIONAL:.0f} leverage={LEVERAGE} "
        f"base_risk={RISK_USDT:.0f} daily_loss={DAILY_LOSS:.0f} "
        "outer_4h=aligned_required be=OFF",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    metrics.update({
        "label": "KAYTRADE V1.6.1 — outer 4H aligned only, BE OFF — strict 360D A/B",
        "release": "1.6.1",
        "release_build": 1610,
        "ab_variant": "outer_4h_aligned_be_off",
        "days": DAYS,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "capital": CAPITAL,
        "profile_semantics": "2000U strategy capital/equity and 2000U base position notional cap",
        "base_position_notional_usdt": BASE_POSITION_NOTIONAL,
        "middle_position_multiplier": 1.0,
        "outer_position_multiplier": 2.0,
        "outer_position_notional_cap_usdt": BASE_POSITION_NOTIONAL * 2.0,
        "leverage": LEVERAGE,
        "base_risk_usdt": RISK_USDT,
        "risk_pct": RISK_PCT,
        "daily_loss_usdt": DAILY_LOSS,
        "score_threshold": 6.0,
        "middle_macd_required": True,
        "outer_4h_aligned_required": True,
        "be_enabled": False,
        "be_trigger_r": None,
        "be_armed_trades": 0,
        "be_exit_trades": 0,
        "path_c_enabled": False,
        "cooldown_minutes": 0,
        "loss_pause_enabled": False,
        "entry_order_type": "LIMIT",
        "boll_signal_wall_clock_expiry": False,
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        "ab_reference_run": 34892345237,
        "ab_reference_be_enabled": True,
    })

    outdir = Path("backtest_output_v161_4h_only_no_be_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# KAYTRADE V1.6.1 — 4H Aligned Only / BE OFF — 360D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Strict A/B lock",
        "- Same fixed window as Run 34892345237.",
        "- Outer BOLL still requires 4H aligned and remains 2x single LIMIT.",
        "- Middle still requires formal 5m MACD improving and remains 1x.",
        "- +1R -> BE is disabled; original 1H ATR stop remains active until SL/TP.",
        "- TP remains full-position 2R.",
        "",
        "## Result",
        f"- Trades: {metrics['trades']}",
        f"- Wins / losses: {metrics['wins']} / {metrics['losses']}",
        f"- Win rate: {metrics['win_rate_pct']:.2f}%",
        f"- Ending equity: {metrics['ending_equity']:.2f} U",
        f"- Net PnL: {metrics['net_pnl']:+.2f} U ({metrics['net_return_pct']:+.2f}%)",
        f"- Profit factor: {metrics['profit_factor']:.3f}",
        f"- Expectancy: {metrics['expectancy']:+.3f} U/trade",
        f"- Max DD: {metrics['max_drawdown_usdt']:.2f} U ({metrics['max_drawdown_pct']:.2f}%)",
        f"- Fees: {metrics['fees']:.2f} U; Funding: {metrics['funding_pnl']:+.2f} U",
        "",
        "Historical simulation only. LIMIT fills use the audited closed-1m proxy; historical tick/orderbook sequence is unavailable.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V161_4H_ONLY_NO_BE_360D_2000U_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

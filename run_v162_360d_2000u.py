#!/usr/bin/env python3
"""KAYTRADE V1.6.2 strict 360-day BTC-USDT-SWAP backtest, 2,000U profile.

Strict comparison against Run 34925577278 (V1.6.1 4H-only / no-BE):
- exact same fixed 360-day window;
- same audited historical simulator and LIMIT fill model;
- same 2,000U capital, 5x leverage, 20U base risk, 60U daily loss budget;
- same 1x 1H ATR initial SL and 2R full-position TP;
- BE remains disabled;
- V1.6.2 change: ALL BOLL paths require 4H aligned;
- V1.6.2 change: ALL initial BOLL entries are 1x (outer 2x removed).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import run_v161_4h_only_no_be_360d_2000u as prior
import v162_model as production

research = prior.research

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
ALL_BOLL_POSITION_MULTIPLIER = 1.0
REFERENCE_RUN = 34925577278


def _submit_v162(self, result, now_ms, mark):
    """Mirror V1.6.2 live entry gates and 1x sizing for every BOLL path."""
    opp = result.get("opportunity")
    if not isinstance(opp, dict):
        return
    side = str(opp.get("side") or "")
    score = (result.get("scores") or {}).get(side) or {}
    if not score.get("eligible") or float(score.get("total") or 0.0) < production.THRESHOLD:
        return

    self.stats["eligible_states"] += 1
    if not research.variant_accept(self.variant, score, opp):
        self.stats["variant_filter_blocks"] += 1
        return
    if not self.can_submit(now_ms):
        return

    path = str(opp.get("signal_path") or "")
    multiplier = ALL_BOLL_POSITION_MULTIPLIER

    # V1.6.2 middle path keeps the formal 5m MACD improving hard gate.
    if path == production.MIDDLE_PATH and not production._macd_improving(score):
        self.stats["middle_macd_presubmit_blocks"] += 1
        return

    # V1.6.2: every BOLL path, middle and outer, requires 4H aligned.
    confirmations = score.get("confirmations") or {}
    if path in (production.MIDDLE_PATH, *production.OUTER_PATHS):
        if str(confirmations.get("4H_trend_state") or "") != "aligned":
            self.stats["all_boll_4h_presubmit_blocks"] += 1
            return

    entry = float(mark)
    stop_distance = float(opp["atr1h"]) * research.STOP_ATR
    direction = research.side_dir(side)
    stop = entry - direction * stop_distance
    target = entry + direction * stop_distance * research.REWARD_R

    eq = self.equity(mark)
    risk_capital = min(research.CAPITAL, eq)
    base_risk = min(research.RISK_USDT, risk_capital * research.RISK_PCT / 100.0)
    risk_budget = min(base_risk * multiplier, self.daily_remaining(now_ms, mark))
    if risk_budget <= 0:
        self.stats["sizing_skips"] += 1
        return

    maker = research.MAKER_BPS / 10000.0
    taker = research.TAKER_BPS / 10000.0
    slip = research.SLIPPAGE_BPS / 10000.0
    risk_entry_fee = max(maker, taker)
    per_btc = stop_distance + entry * risk_entry_fee + stop * (taker + slip)

    requested_notional_cap = research.FIRST_SIGNAL_NOTIONAL * multiplier
    notional_cap = min(
        requested_notional_cap,
        risk_capital * research.LEVERAGE,
        max(eq, 0.0) * 0.9 * research.LEVERAGE,
    )
    btc = research.round_contract_btc(min(risk_budget / per_btc, notional_cap / entry), self.meta)
    if btc <= 0:
        self.stats["sizing_skips"] += 1
        return

    expected_cost_per_btc = entry * maker + target * taker
    expected_fee_multiple = 2.0 * stop_distance / expected_cost_per_btc if expected_cost_per_btc > 0 else math.inf
    if expected_fee_multiple < research.EXPECTED_COST_MIN:
        self.stats["fee_multiple_blocks"] += 1
        return

    worst_roundtrip_cost = btc * (entry * max(maker, taker) + target * (taker + slip))
    plan = {
        "px": str(entry),
        "stop_distance": stop_distance,
        "btc": btc,
        "worst_roundtrip_cost": worst_roundtrip_cost,
    }
    ok, diag, blockers = production.execution_checks(plan, opp, score)
    if not ok:
        self.stats["execution_gate_blocks"] += 1
        for reason in blockers:
            self.blockers[reason] += 1
        return

    factors = self._factors(score, opp, now_ms, entry)
    factors.update({
        "position_multiplier": multiplier,
        "base_position_notional_cap": research.FIRST_SIGNAL_NOTIONAL,
        "requested_notional_cap": requested_notional_cap,
        "effective_notional_cap": notional_cap,
        "all_boll_4h_aligned_required": path in (production.MIDDLE_PATH, *production.OUTER_PATHS),
        "outer_4h_aligned_required": path in production.OUTER_PATHS,
        "middle_4h_aligned_required": path == production.MIDDLE_PATH,
        "v162_all_boll_1x": True,
    })
    self.pending = research.PendingEntry(
        side=side,
        limit=entry,
        stop=stop,
        target=target,
        quantity_btc=btc,
        score=float(score["total"]),
        opportunity_id=str(opp["id"]),
        signal_bar_t=int(opp.get("signal_bar_t") or 0),
        signal_close_ms=int(opp.get("signal_close_ms") or 0),
        submitted_ms=int(now_ms),
        expires_ms=int(now_ms) + research.ORDER_TTL_MS,
        factors=factors,
        score_components=dict(score.get("layers") or {}),
        trigger_combination=dict((score.get("confirmations") or {}).get("trigger") or {}),
        front_r=float(diag.get("front_r", math.inf)),
        cost_r=float(diag.get("cost_r", math.inf)),
    )
    self.stats["limit_submitted"] += 1
    self.stats["all_boll_1x_submitted"] += 1
    if path in production.OUTER_PATHS:
        self.stats["outer_1x_submitted"] += 1
    elif path == production.MIDDLE_PATH:
        self.stats["middle_1x_submitted"] += 1


def _configure():
    # Reuse the exact fixed-window no-BE simulator configuration from the
    # completed V1.6.1 comparison run, then swap in V1.6.2 production semantics.
    start, end = prior._configure()
    research.model = production
    research.Simulator.submit = _submit_v162
    research.VERSION = "1.6.2"
    research.BUILD = "1620"
    research.CAPITAL = CAPITAL
    research.LEVERAGE = LEVERAGE
    research.RISK_USDT = RISK_USDT
    research.RISK_PCT = RISK_PCT
    research.DAILY_LOSS = DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = BASE_POSITION_NOTIONAL
    research.STOP_ATR = STOP_ATR
    research.REWARD_R = REWARD_R
    research.COOLDOWN_MS = COOLDOWN_MS
    return start, end


def _sample_row(state, macd=1.0):
    return {
        "total": 6.5,
        "gate": True,
        "eligible": True,
        "position_multiplier": 1.0,
        "layers": {"macd_improving": macd},
        "confirmations": {"required": {}, "4H_trend_state": state},
        "reason": "ok",
        "level": "ok",
    }


def _verify_lock():
    assert production.VERSION == "1.6.2"
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
    assert ALL_BOLL_POSITION_MULTIPLIER == 1.0
    # No-BE simulator exits must remain the audited originals.
    assert research.Simulator.process_exit is prior._ORIGINAL_PROCESS_EXIT
    assert research.Simulator.process_pending is prior._ORIGINAL_PROCESS_PENDING

    middle_neutral = _sample_row("neutral", 1.0)
    production._apply_row_rules(middle_neutral, production.MIDDLE_PATH)
    assert middle_neutral["eligible"] is False

    middle_aligned = _sample_row("aligned", 1.0)
    production._apply_row_rules(middle_aligned, production.MIDDLE_PATH)
    assert middle_aligned["eligible"] is True

    outer_neutral = _sample_row("neutral", 0.0)
    production._apply_row_rules(outer_neutral, "lower_band")
    assert outer_neutral["eligible"] is False

    outer_aligned = _sample_row("aligned", 0.0)
    production._apply_row_rules(outer_aligned, "upper_band")
    assert outer_aligned["eligible"] is True


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V162_360D_2000U_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} capital={CAPITAL:.0f} "
        f"base_notional={BASE_POSITION_NOTIONAL:.0f} leverage={LEVERAGE} "
        f"base_risk={RISK_USDT:.0f} daily_loss={DAILY_LOSS:.0f} "
        "all_boll_4h=aligned_required all_boll_position=1x be=OFF",
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
        "label": "KAYTRADE V1.6.2 — all BOLL 4H aligned / all 1x / BE OFF — strict 360D",
        "release": "1.6.2",
        "release_build": 1620,
        "ab_variant": "all_boll_4h_aligned_all_1x_be_off",
        "days": DAYS,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "capital": CAPITAL,
        "profile_semantics": "2000U strategy capital/equity and 2000U base position notional cap",
        "base_position_notional_usdt": BASE_POSITION_NOTIONAL,
        "middle_position_multiplier": 1.0,
        "outer_position_multiplier": 1.0,
        "middle_position_notional_cap_usdt": BASE_POSITION_NOTIONAL,
        "outer_position_notional_cap_usdt": BASE_POSITION_NOTIONAL,
        "leverage": LEVERAGE,
        "base_risk_usdt": RISK_USDT,
        "risk_pct": RISK_PCT,
        "daily_loss_usdt": DAILY_LOSS,
        "score_threshold": 6.0,
        "middle_macd_required": True,
        "middle_4h_aligned_required": True,
        "outer_4h_aligned_required": True,
        "all_boll_4h_aligned_required": True,
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
        "reference_run": REFERENCE_RUN,
        "reference_release": "1.6.1",
        "reference_outer_multiplier": 2.0,
        "reference_middle_4h_required": False,
    })

    outdir = Path("backtest_output_v162_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    prior._write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# KAYTRADE V1.6.2 — 360D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Strict comparison lock",
        f"- Reference: V1.6.1 Run {REFERENCE_RUN}.",
        "- Same fixed 360-day window and audited historical data/simulator.",
        "- All 5m BOLL middle/outer paths require 4H aligned.",
        "- Middle still requires formal 5m MACD improving.",
        "- All initial BOLL entries are 1x LIMIT; outer 2x is removed.",
        "- +1R BE remains disabled; original 1H ATR stop remains until SL or 2R TP.",
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

    print("V162_360D_2000U_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

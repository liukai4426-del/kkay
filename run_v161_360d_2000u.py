#!/usr/bin/env python3
"""KAYTRADE V1.6.1 rolling 360-day BTC-USDT-SWAP backtest, 2,000U profile.

Uses the audited Build1544 historical simulator/data layer while routing entries
through the exact V1.6.1 production model. V1.6.1 semantics reproduced here:
- middle_rsi: 1x base position and formal 5m macd_improving hard gate;
- lower_band / upper_band: single 2x base LIMIT entry, only when 4H is aligned;
- initial SL = 1x 1H ATR, full-position TP = 2R;
- once +1R is reached, SL moves to entry (strict BE, no fee offset).

Historical BE note: only 1m OHLC is available, not tick sequence. To avoid using
future intrabar ordering, the BE amendment becomes effective from the next 1m
bar after a bar first reaches +1R. This is explicitly reported in the metrics.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_v1544_180d_fixed as compat

research = compat.research
import v161_model as production

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
BE_TRIGGER_R = 1.0
BE_INTRABAR_POLICY = "next_1m_bar_after_first_1R_touch"

_ORIGINAL_PROCESS_PENDING = research.Simulator.process_pending
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


def _process_pending_v161(self, bar):
    before = self.position
    _ORIGINAL_PROCESS_PENDING(self, bar)
    if before is None and self.position is not None:
        self.position.be_armed = False
        self.position.be_armed_time = 0
        self.position.be_trigger_price = 0.0


def _close_v161(self, bar, reason, trigger, effective_stop, be_armed):
    pos = self.position
    p = pos.pending
    slip = (research.SLIPPAGE_BPS + self.stress_extra_bps) / 10000.0
    exit_px = float(trigger) * (1.0 - slip if p.side == "做多" else 1.0 + slip)
    gross = (exit_px - p.limit) * p.quantity_btc * research.side_dir(p.side)
    exit_fee = exit_px * p.quantity_btc * research.TAKER_BPS / 10000.0
    net = gross - pos.entry_fee - exit_fee + pos.funding_pnl
    exit_ms = int(bar["t"]) + 60_000
    self.cash += net
    initial_risk = abs(p.limit - p.stop)
    stop_money = initial_risk * p.quantity_btc
    row = {
        "variant": self.variant,
        "side": p.side,
        "score": p.score,
        "boll_path": p.factors["boll_path"],
        "entry": p.limit,
        "stop": p.stop,
        "initial_stop": p.stop,
        "effective_stop_at_exit": effective_stop,
        "target": p.target,
        "exit": exit_px,
        "reason": reason,
        "quantity_btc": p.quantity_btc,
        "net_pnl": net,
        "gross_pnl": gross,
        "entry_fee": pos.entry_fee,
        "exit_fee": exit_fee,
        "funding_pnl": pos.funding_pnl,
        "realized_r": net / stop_money if stop_money > 0 else 0.0,
        "mfe_r": pos.mfe / initial_risk if initial_risk > 0 else 0.0,
        "mae_r": pos.mae / initial_risk if initial_risk > 0 else 0.0,
        "front_r": p.front_r,
        "cost_r": p.cost_r,
        "entry_time": pos.fill_time,
        "exit_time": exit_ms,
        "hold_min": (exit_ms - pos.fill_time) / 60_000.0,
        "opportunity_id": p.opportunity_id,
        "score_components": dict(p.score_components),
        "trigger_combination": dict(p.trigger_combination),
        "be_trigger_r": BE_TRIGGER_R,
        "be_armed": bool(be_armed),
        "be_armed_time": int(getattr(pos, "be_armed_time", 0) or 0),
        "be_trigger_price": float(getattr(pos, "be_trigger_price", 0.0) or 0.0),
        "be_exit": reason == "BE",
        "be_intrabar_policy": BE_INTRABAR_POLICY,
    }
    row.update(p.factors)
    self.trades.append(row)
    self.position = None
    self.last_close_ms = exit_ms
    self.stats["closed"] += 1
    if reason == "BE":
        self.stats["be_exits"] += 1


def _process_exit_v161(self, bar):
    if not self.position:
        return
    pos = self.position
    p = pos.pending
    if int(bar["t"]) <= int(pos.fill_bar_t):
        return

    high, low = float(bar["h"]), float(bar["l"])
    initial_risk = abs(p.limit - p.stop)
    if initial_risk <= 0:
        return
    long_side = p.side == "做多"

    if long_side:
        pos.mfe = max(pos.mfe, max(0.0, high - p.limit))
        pos.mae = max(pos.mae, max(0.0, p.limit - low))
        hit_tp = high >= p.target
        hit_initial_sl = low <= p.stop
        hit_be_stop = low <= p.limit
        hit_1r = high >= p.limit + initial_risk * BE_TRIGGER_R
        be_trigger_price = p.limit + initial_risk * BE_TRIGGER_R
    else:
        pos.mfe = max(pos.mfe, max(0.0, p.limit - low))
        pos.mae = max(pos.mae, max(0.0, high - p.limit))
        hit_tp = low <= p.target
        hit_initial_sl = high >= p.stop
        hit_be_stop = high >= p.limit
        hit_1r = low <= p.limit - initial_risk * BE_TRIGGER_R
        be_trigger_price = p.limit - initial_risk * BE_TRIGGER_R

    # Once BE was armed on a prior 1m bar, entry price is the effective SL.
    if bool(getattr(pos, "be_armed", False)):
        if hit_be_stop:
            _close_v161(self, bar, "BE", p.limit, p.limit, True)
            return
        if hit_tp:
            _close_v161(self, bar, "TP2R", p.target, p.limit, True)
            return
        return

    # Preserve the audited simulator's conservative SL-first ordering when the
    # same 1m bar spans both initial SL and TP. A +1R touch on that ambiguous bar
    # does not retroactively rescue an initial-stop event.
    if hit_initial_sl:
        if hit_1r:
            self.stats["be_same_bar_initial_sl_ambiguous"] += 1
        _close_v161(self, bar, "SL", p.stop, p.stop, False)
        return
    if hit_tp:
        _close_v161(self, bar, "TP2R", p.target, p.stop, False)
        return

    # Historical 1m bars do not reveal whether price returned to entry before or
    # after first touching +1R. Therefore the live SL amendment is activated only
    # from the next bar, avoiding look-ahead assumptions about intrabar sequence.
    if hit_1r:
        pos.be_armed = True
        pos.be_armed_time = int(bar["t"]) + 60_000
        pos.be_trigger_price = float(be_trigger_price)
        self.stats["be_armed"] += 1


def _finish_v161(self, last_bar):
    before = len(self.trades)
    armed = bool(self.position and getattr(self.position, "be_armed", False))
    armed_time = int(getattr(self.position, "be_armed_time", 0) or 0) if self.position else 0
    trigger_price = float(getattr(self.position, "be_trigger_price", 0.0) or 0.0) if self.position else 0.0
    _ORIGINAL_FINISH(self, last_bar)
    if len(self.trades) > before:
        row = self.trades[-1]
        row["be_trigger_r"] = BE_TRIGGER_R
        row["be_armed"] = armed
        row["be_armed_time"] = armed_time
        row["be_trigger_price"] = trigger_price
        row["be_exit"] = False
        row["be_intrabar_policy"] = BE_INTRABAR_POLICY
        row["initial_stop"] = row.get("stop")
        row["effective_stop_at_exit"] = row.get("entry") if armed else row.get("stop")


def _submit_v161(self, result, now_ms, mark):
    """Mirror V1.6.1 live entry hard gates and path-dependent sizing."""
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
    multiplier = 2.0 if path in production.OUTER_PATHS else 1.0

    if path == production.MIDDLE_PATH and not production._macd_improving(score):
        self.stats["middle_macd_presubmit_blocks"] += 1
        return

    confirmations = score.get("confirmations") or {}
    if path in production.OUTER_PATHS and str(confirmations.get("4H_trend_state") or "") != "aligned":
        self.stats["outer_4h_presubmit_blocks"] += 1
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
        "outer_4h_aligned_required": path in production.OUTER_PATHS,
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
    if multiplier == 2.0:
        self.stats["outer_2x_submitted"] += 1
    else:
        self.stats["middle_1x_submitted"] += 1


def _configure():
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
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
    research.BUILD = "1610"

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
    research.Simulator.submit = _submit_v161
    research.Simulator.process_pending = _process_pending_v161
    research.Simulator.process_exit = _process_exit_v161
    research.Simulator.finish = _finish_v161
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


def _be_summary(rows, stats):
    armed = [r for r in rows if r.get("be_armed")]
    exits = [r for r in rows if r.get("reason") == "BE"]
    return {
        "be_trigger_r": BE_TRIGGER_R,
        "be_intrabar_policy": BE_INTRABAR_POLICY,
        "be_armed_trades": len(armed),
        "be_exit_trades": len(exits),
        "be_exit_net_pnl": sum(float(r.get("net_pnl") or 0.0) for r in exits),
        "be_same_bar_initial_sl_ambiguous": int(stats.get("be_same_bar_initial_sl_ambiguous", 0)),
    }


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V161_360D_2000U_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} capital={CAPITAL:.0f} "
        f"base_notional={BASE_POSITION_NOTIONAL:.0f} leverage={LEVERAGE} "
        f"base_risk={RISK_USDT:.0f} daily_loss={DAILY_LOSS:.0f} be=1R->entry",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    be = _be_summary(sim.trades, sim.stats)
    metrics.update({
        "label": "KAYTRADE V1.6.1 production model — 360D 2000U profile",
        "release": "1.6.1",
        "release_build": 1610,
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
        "path_c_enabled": False,
        "cooldown_minutes": 0,
        "loss_pause_enabled": False,
        "entry_order_type": "LIMIT",
        "boll_signal_wall_clock_expiry": False,
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        **be,
    })

    outdir = Path("backtest_output_v161_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# KAYTRADE V1.6.1 — 360D / 2000U Backtest",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Profile",
        "- Capital: 2,000 U; base position notional cap: 2,000 U; leverage: 5x.",
        "- Base risk: 20 U; daily loss budget: 60 U.",
        "- Middle: 1x + formal 5m macd_improving required.",
        "- Outer BOLL: 2x single LIMIT and 4H aligned required.",
        "- Initial SL: 1x 1H ATR; TP: full position 2R.",
        "- +1R -> SL to entry; historical BE becomes active on the next 1m bar after first +1R touch.",
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
        f"- BE armed: {be['be_armed_trades']} trades; BE exits: {be['be_exit_trades']} trades",
        "",
        "Historical simulation only. LIMIT fills use the audited closed-1m proxy; historical tick/orderbook sequence is unavailable.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V161_360D_2000U_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

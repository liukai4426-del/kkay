#!/usr/bin/env python3
"""Strict 360D A/B: first-4H adverse structure break + 5m MACD deterioration.

Reference baseline: Run 34952397559 / research/v162-outer-rsi-macd-360d-2000u.
Only experimental position-management change:
- monitor only the first 4 hours after fill;
- require a FULLY CLOSED 15m candle formed after the fill;
- define the adverse structure from the PREVIOUS closed-15m state using the
  existing confirmed horizontal-zone algorithm (>=2 tests, not previously broken);
- LONG: 15m close breaks below the nearest confirmed support by >=0.25 ATR15;
  SHORT: mirrored break above the nearest confirmed resistance by >=0.25 ATR15;
- at the same 15m close, the last three CLOSED 5m MACD histogram values must show
  two consecutive changes against the held direction;
- if current mark-to-market R < 0: exit immediately with a marketable LIMIT proxy;
- if current mark-to-market R >= +1R: do not exit; move SL to entry (BE);
- if 0 <= current R < +1R: do nothing.

Original intrabar SL/TP has priority before the closed-candle management rule.
Entry model, Volume +0.5 score, 4H alignment, RSI30-70, outer-only, 1H ATR
initial SL, 2R TP and all other gates remain unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import run_v162_outer_rsi30_70_macd_360d_2000u as baseline
import strategy
import v152_model as model152

research = baseline.research
production = baseline.production

REFERENCE_RUN = 34952397559
STRUCTURE_BREAK_ATR = 0.25
MONITOR_MAX_MS = 4 * 60 * 60_000
EXIT_REASON = "STRUCTURE_MACD_LOSS_LIMIT_EXIT_4H"
LIMIT_PROTECTION_BPS = 5.0

_ORIGINAL_PROCESS_EXIT = None
_SIGNAL_BY_CLOSE_MS = {}


def _macd_adverse(side, a, b, c):
    """Two consecutive histogram changes against the held direction; no zero-axis requirement."""
    if side == "做多":
        return c < b < a
    return c > b > a


def _prime_adverse_signal_map(five, quarter):
    """Precompute only information available at each fully closed 15m timestamp."""
    global _SIGNAL_BY_CLOSE_MS
    q_ind = research.base.compute_indicators(quarter)
    hist = model152._macd_hist(five)
    five_by_close = {int(r["t"]) + 5 * 60_000: i for i, r in enumerate(five)}
    out = {}

    for i in range(1, len(quarter)):
        close_ms = int(quarter[i]["t"]) + 15 * 60_000
        fi = five_by_close.get(close_ms)
        if fi is None or fi < 2:
            continue

        atr15 = float(q_ind[i].get("atr") or 0.0)
        atr_prev = float(q_ind[i - 1].get("atr") or 0.0)
        if atr15 <= 0.0 or atr_prev <= 0.0:
            continue

        # The structure must already exist before the signal candle begins.
        # strategy._zones itself only uses confirmed swing points and rejects
        # zones already broken by previous closed candles.
        prev_rows = quarter[max(0, i - 220):i]
        if len(prev_rows) < 20:
            continue
        zones = strategy._zones(prev_rows, atr_prev, 160)
        prev_close = float(prev_rows[-1]["c"])
        support = strategy._nearest(zones, prev_close, "support")
        resistance = strategy._nearest(zones, prev_close, "resistance")
        current_close = float(quarter[i]["c"])

        support_px = float(support["price"]) if support else None
        resistance_px = float(resistance["price"]) if resistance else None
        long_break_atr = ((support_px - current_close) / atr15) if support_px is not None else 0.0
        short_break_atr = ((current_close - resistance_px) / atr15) if resistance_px is not None else 0.0
        long_break = support_px is not None and long_break_atr >= STRUCTURE_BREAK_ATR
        short_break = resistance_px is not None and short_break_atr >= STRUCTURE_BREAK_ATR

        a, b, c = float(hist[fi - 2]), float(hist[fi - 1]), float(hist[fi])
        long_macd = _macd_adverse("做多", a, b, c)
        short_macd = _macd_adverse("做空", a, b, c)

        if not ((long_break and long_macd) or (short_break and short_macd)):
            continue

        out[close_ms] = {
            "bar_start_ms": int(quarter[i]["t"]),
            "atr15": atr15,
            "support": support_px,
            "resistance": resistance_px,
            "long_break": bool(long_break),
            "short_break": bool(short_break),
            "long_break_atr": float(long_break_atr),
            "short_break_atr": float(short_break_atr),
            "long_macd_adverse": bool(long_macd),
            "short_macd_adverse": bool(short_macd),
            "macd_a": a,
            "macd_b": b,
            "macd_c": c,
            "five_signal_bar_start": int(five[fi]["t"]),
        }
    _SIGNAL_BY_CLOSE_MS = out


def _original_risk_distance(p):
    value = p.factors.get("research_original_risk_distance") if isinstance(p.factors, dict) else None
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if value > 0.0:
        return value
    # Target remains fixed at 2R even after BE changes p.stop.
    return abs(float(p.target) - float(p.limit)) / float(research.REWARD_R)


def _ensure_original_risk(p):
    if not isinstance(p.factors, dict):
        return
    if float(p.factors.get("research_original_risk_distance") or 0.0) <= 0.0:
        p.factors["research_original_stop"] = float(p.stop)
        p.factors["research_original_risk_distance"] = abs(float(p.limit) - float(p.stop))


def _repair_r_diagnostics(row, pos, p):
    """Keep R/MFE/MAE based on the original 1H-ATR risk after a BE move."""
    risk = _original_risk_distance(p)
    if risk <= 0.0:
        return
    risk_money = risk * float(p.quantity_btc)
    row["realized_r"] = float(row.get("net_pnl") or 0.0) / risk_money if risk_money > 0 else 0.0
    row["mfe_r"] = float(pos.mfe) / risk
    row["mae_r"] = float(pos.mae) / risk
    row["research_original_stop"] = float(p.factors.get("research_original_stop") or p.stop)
    row["research_original_risk_distance"] = risk


def _close_loss_with_marketable_limit(self, bar, signal, current_r):
    """Historical proxy for an immediate aggressive limit close after the signal is confirmed.

    With no historical order book, use the closed 1m mark and a 5bp protective
    limit worse than mark. This is intentionally conservative and charged taker fee.
    """
    pos = self.position
    p = pos.pending
    close_ms = int(bar["t"]) + 60_000
    mark = float(bar["c"])
    direction = research.side_dir(p.side)

    bps = (LIMIT_PROTECTION_BPS + self.stress_extra_bps) / 10000.0
    limit_px = mark * (1.0 - bps if p.side == "做多" else 1.0 + bps)
    gross = (limit_px - p.limit) * p.quantity_btc * direction
    exit_fee = limit_px * p.quantity_btc * research.TAKER_BPS / 10000.0
    net = gross - pos.entry_fee - exit_fee + pos.funding_pnl
    self.cash += net

    risk = _original_risk_distance(p)
    risk_money = risk * p.quantity_btc
    row = {
        "variant": self.variant,
        "side": p.side,
        "score": p.score,
        "boll_path": p.factors["boll_path"],
        "entry": p.limit,
        "stop": p.stop,
        "target": p.target,
        "exit": limit_px,
        "reason": EXIT_REASON,
        "quantity_btc": p.quantity_btc,
        "net_pnl": net,
        "gross_pnl": gross,
        "entry_fee": pos.entry_fee,
        "exit_fee": exit_fee,
        "funding_pnl": pos.funding_pnl,
        "realized_r": net / risk_money if risk_money > 0 else 0.0,
        "mfe_r": pos.mfe / risk if risk > 0 else 0.0,
        "mae_r": pos.mae / risk if risk > 0 else 0.0,
        "front_r": p.front_r,
        "cost_r": p.cost_r,
        "entry_time": pos.fill_time,
        "exit_time": close_ms,
        "hold_min": (close_ms - pos.fill_time) / 60_000.0,
        "opportunity_id": p.opportunity_id,
        "score_components": dict(p.score_components),
        "trigger_combination": dict(p.trigger_combination),
        "exit_order_type": "MARKETABLE_LIMIT_PROXY",
        "exit_limit_protection_bps": LIMIT_PROTECTION_BPS,
        "exit_signal_current_r": float(current_r),
        "exit_structure_price": float(signal["support"] if p.side == "做多" else signal["resistance"]),
        "exit_structure_break_atr": float(signal["long_break_atr"] if p.side == "做多" else signal["short_break_atr"]),
        "exit_atr15": float(signal["atr15"]),
        "exit_macd_a": float(signal["macd_a"]),
        "exit_macd_b": float(signal["macd_b"]),
        "exit_macd_c": float(signal["macd_c"]),
        "exit_signal_15m_bar_start": int(signal["bar_start_ms"]),
        "exit_signal_5m_bar_start": int(signal["five_signal_bar_start"]),
        "exit_mark_before_limit_protection": mark,
    }
    row.update(p.factors)
    self.trades.append(row)
    self.position = None
    self.last_close_ms = close_ms
    self.stats["closed"] += 1
    self.stats["structure_macd_loss_limit_exit"] += 1


def _process_exit_with_structure_macd(self, bar):
    if not self.position:
        return

    pos_before = self.position
    p_before = pos_before.pending
    _ensure_original_risk(p_before)
    n_before = len(self.trades)

    # Preserve baseline intrabar SL/TP priority.
    _ORIGINAL_PROCESS_EXIT(self, bar)
    if len(self.trades) > n_before:
        _repair_r_diagnostics(self.trades[-1], pos_before, p_before)
        return
    if not self.position:
        return

    pos = self.position
    p = pos.pending
    close_ms = int(bar["t"]) + 60_000
    signal = _SIGNAL_BY_CLOSE_MS.get(close_ms)
    if not signal:
        return

    hold_ms = close_ms - int(pos.fill_time)
    if hold_ms < 0 or hold_ms > MONITOR_MAX_MS:
        return
    # Entire 15m signal candle must have formed after the position was filled.
    if int(signal["bar_start_ms"]) < int(pos.fill_time):
        return

    if p.side == "做多":
        valid = bool(signal["long_break"] and signal["long_macd_adverse"])
        structure_px = signal["support"]
        break_atr = signal["long_break_atr"]
    else:
        valid = bool(signal["short_break"] and signal["short_macd_adverse"])
        structure_px = signal["resistance"]
        break_atr = signal["short_break_atr"]
    if not valid or structure_px is None or float(break_atr) < STRUCTURE_BREAK_ATR:
        return

    self.stats["structure_macd_4h_signal"] += 1
    mark = float(bar["c"])
    risk = _original_risk_distance(p)
    if risk <= 0.0:
        return
    current_r = ((mark - float(p.limit)) * research.side_dir(p.side)) / risk

    if current_r < 0.0:
        _close_loss_with_marketable_limit(self, bar, signal, current_r)
        return

    if current_r >= 1.0:
        if not bool(p.factors.get("structure_macd_be_armed")):
            p.factors["structure_macd_be_armed"] = 1
            p.factors["structure_macd_be_armed_at"] = close_ms
            p.factors["structure_macd_be_signal_r"] = float(current_r)
            p.factors["structure_macd_be_structure_price"] = float(structure_px)
            p.factors["structure_macd_be_break_atr"] = float(break_atr)
            p.factors["structure_macd_be_macd_a"] = float(signal["macd_a"])
            p.factors["structure_macd_be_macd_b"] = float(signal["macd_b"])
            p.factors["structure_macd_be_macd_c"] = float(signal["macd_c"])
            p.stop = float(p.limit)
            self.stats["structure_macd_be_armed"] += 1
        return

    self.stats["structure_macd_midprofit_ignored"] += 1


def _configure_and_patch():
    global _ORIGINAL_PROCESS_EXIT
    start, end = baseline._configure()
    baseline._verify_lock()
    _ORIGINAL_PROCESS_EXIT = research.Simulator.process_exit
    research.Simulator.process_exit = _process_exit_with_structure_macd
    research.VERSION = production.VERSION
    research.BUILD = "1620-outer-rsi30-70-macd-structure-macd-4h-exit-be"
    return start, end


def _verify_exit_patch():
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.THRESHOLD == 6.0
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0
    assert baseline.BE_ENABLED is False
    assert research.Simulator.process_exit is _process_exit_with_structure_macd
    assert _ORIGINAL_PROCESS_EXIT is not _process_exit_with_structure_macd
    assert STRUCTURE_BREAK_ATR == 0.25
    assert MONITOR_MAX_MS == 4 * 60 * 60_000
    assert LIMIT_PROTECTION_BPS == 5.0
    assert _macd_adverse("做多", 3.0, 2.0, 1.0)
    assert not _macd_adverse("做多", 1.0, 2.0, 3.0)
    assert _macd_adverse("做空", 1.0, 2.0, 3.0)
    assert not _macd_adverse("做空", 3.0, 2.0, 1.0)


def main():
    start, end = _configure_and_patch()
    _verify_exit_patch()
    print(
        "V162_STRUCTURE_MACD_4H_EXIT_BE_360D_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} "
        "baseline=outer_only_rsi30_70_macd volume_score=+0.5 "
        "monitor=0..240min structure=previous_confirmed_15m_zone break=0.25ATR15 "
        "macd=two_consecutive_adverse_5m_changes loss=marketable_LIMIT_proxy "
        "profit_ge_1R=SL_to_BE original_sl=1H_ATR_x1 tp=2R",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    _prime_adverse_signal_map(data["5m"], data["15m"])
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    loss_exits = [r for r in sim.trades if str(r.get("reason")) == EXIT_REASON]
    be_rows = [r for r in sim.trades if int(float(r.get("structure_macd_be_armed") or 0)) == 1]
    loss_net = sum(float(r.get("net_pnl") or 0.0) for r in loss_exits)
    be_net = sum(float(r.get("net_pnl") or 0.0) for r in be_rows)
    be_sl = [r for r in be_rows if str(r.get("reason")) == "SL"]
    be_tp = [r for r in be_rows if str(r.get("reason")) == "TP2R"]

    metrics.update({
        "label": "V1.6.2 research — first-4H 15m structure break + 5m adverse MACD; loss LIMIT exit / >=1R BE — strict 360D",
        "release_base": "1.6.2",
        "research_variant": "outer_only_rsi30_70_macd_structure_break_macd_4h_loss_limit_be",
        "days": 360,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "capital": baseline.CAPITAL,
        "base_position_notional_usdt": baseline.BASE_POSITION_NOTIONAL,
        "leverage": baseline.LEVERAGE,
        "base_risk_usdt": baseline.RISK_USDT,
        "daily_loss_usdt": baseline.DAILY_LOSS,
        "score_threshold": 6.0,
        "entry_volume_score_retained": True,
        "entry_volume_score_points": 0.5,
        "outer_only": True,
        "outer_rsi_min": 30.0,
        "outer_rsi_max": 70.0,
        "outer_macd_improving_required": True,
        "outer_4h_aligned_required": True,
        "outer_position_multiplier": 1.0,
        "initial_be_enabled": False,
        "management_monitor_max_hold_min": 240,
        "management_requires_full_post_fill_15m_candle": True,
        "management_structure_source": "previous closed 15m confirmed strategy._zones; >=2 tests; previously-unbroken",
        "management_structure_break_atr15": STRUCTURE_BREAK_ATR,
        "management_macd_rule": "last three closed 5m MACD histogram values make two consecutive adverse changes; no zero-axis requirement",
        "management_loss_action": "immediate marketable LIMIT proxy at signal mark +/-5bps protection; taker fee",
        "management_profit_ge_1r_action": "move SL to entry BE; no immediate exit",
        "management_profit_0_to_1r_action": "no action",
        "structure_macd_signal_count": int(sim.stats.get("structure_macd_4h_signal", 0)),
        "structure_macd_loss_limit_exit_trades": len(loss_exits),
        "structure_macd_loss_limit_exit_net_pnl": loss_net,
        "structure_macd_loss_limit_exit_avg_pnl": loss_net / len(loss_exits) if loss_exits else 0.0,
        "structure_macd_loss_limit_exit_avg_hold_min": sum(float(r.get("hold_min") or 0.0) for r in loss_exits) / len(loss_exits) if loss_exits else 0.0,
        "structure_macd_be_armed_trades": len(be_rows),
        "structure_macd_be_armed_net_pnl": be_net,
        "structure_macd_be_stop_closes": len(be_sl),
        "structure_macd_be_tp2r_closes": len(be_tp),
        "structure_macd_midprofit_ignored_signals": int(sim.stats.get("structure_macd_midprofit_ignored", 0)),
        "entry_order_type": "LIMIT",
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        "reference_run": REFERENCE_RUN,
        "reference_variant": "outer_only_rsi30_70_macd_volume_score_retained",
    })

    outdir = Path("backtest_output_v162_structure_macd_4h_exit_be_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline.base.prior._write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# V1.6.2 Research — First-4H Structure Break + Adverse MACD — 360D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## A/B lock",
        f"- Reference Run: {REFERENCE_RUN}.",
        "- Entry model unchanged: outer-only, RSI30-70, formal 5m MACD improving, 4H aligned, Volume +0.5 retained.",
        "- Initial SL remains 1x 1H ATR; TP remains full 2R; no baseline BE.",
        "- Experimental management only during first 240 minutes after fill.",
        "- Signal 15m candle must be fully formed after fill.",
        "- Adverse structure = nearest confirmed 15m support (long) / resistance (short) from the previous closed-15m state.",
        "- Valid break = closed 15m close at least 0.25 ATR15 beyond that structure.",
        "- Same timestamp also requires two consecutive adverse changes in closed 5m MACD histogram.",
        "- If current R < 0: immediate marketable-limit historical proxy; if current R >= +1R: SL -> BE; otherwise no action.",
        "- Original SL/TP retains intrabar priority.",
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
        f"- Valid management signals: {metrics['structure_macd_signal_count']}",
        f"- Losing LIMIT exits: {len(loss_exits)}; net {loss_net:+.2f} U",
        f"- >=1R BE arms: {len(be_rows)}; BE-stop closes {len(be_sl)}; TP2R closes after BE {len(be_tp)}; group net {be_net:+.2f} U",
        f"- 0..1R signals ignored: {metrics['structure_macd_midprofit_ignored_signals']}",
        "",
        "Historical simulation only. The requested immediate LIMIT exit is modeled as a conservative marketable-limit proxy because historical order-book sequencing is unavailable.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V162_STRUCTURE_MACD_4H_EXIT_BE_360D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Strict 360D A/B on the successful outer-only RSI/MACD model.

Baseline: Run 34952397559 / research/v162-outer-rsi-macd-360d-2000u.
Only experimental change after a position is filled:
- wait for a FULLY CLOSED 5m candle formed after the fill;
- dynamically recompute the current CLOSED-15m pullback zone;
- require the same formal 15m pullback resonance used by V1.5.3 entry scoring;
- require 5m volume >= 1.2x the PREVIOUS 20 closed 5m average;
- if both occur on the same 5m candle and the position is mark-to-market losing,
  exit immediately at market.

No holding-time window is imposed. Original SL/TP retains intrabar priority.
Entry logic, Volume +0.5 score, 1H ATR SL, 2R TP, RSI30-70, 5m MACD
improving, 4H aligned and No-BE are unchanged.
"""
from __future__ import annotations

import bisect
import json
from pathlib import Path

import run_v162_outer_rsi30_70_macd_360d_2000u as baseline

research = baseline.research
production = baseline.production

REFERENCE_RUN = 34952397559
VOLUME_RATIO_THRESHOLD = 1.2
EXIT_REASON = "PULLBACK15_VOLUME_LOSS_EXIT"

_ORIGINAL_PROCESS_EXIT = None
_SIGNAL_BY_CLOSE_MS = {}


def _prime_dynamic_exit_map(five, quarter):
    global _SIGNAL_BY_CLOSE_MS
    q_ind = research.base.compute_indicators(quarter)
    q_ts = [int(r["t"]) for r in quarter]
    out = {}

    for i, row in enumerate(five):
        if i < 20:
            continue
        close_ms = int(row["t"]) + 5 * 60_000
        qi = bisect.bisect_right(q_ts, close_ms - 15 * 60_000) - 1
        if qi < 0:
            continue
        ind = q_ind[qi]
        atr15 = float(ind.get("atr") or 0.0)
        ema20 = float(ind.get("ema20") or 0.0)
        ema50 = float(ind.get("ema50") or 0.0)
        if atr15 <= 0.0 or ema20 <= 0.0 or ema50 <= 0.0:
            continue

        core_low = min(ema20, ema50)
        core_high = max(ema20, ema50)
        zone_low = core_low - 0.25 * atr15
        zone_high = core_high + 0.25 * atr15

        prev = five[i - 1]
        long_pullback = float(prev["c"]) > zone_high and float(row["l"]) <= zone_high
        short_pullback = float(prev["c"]) < zone_low and float(row["h"]) >= zone_low

        hist = [float(x["v"]) for x in five[i - 20:i]]
        avg = sum(hist) / len(hist) if hist else 0.0
        ratio = float(row["v"]) / avg if avg > 0 else 0.0
        volume_ok = ratio >= VOLUME_RATIO_THRESHOLD

        out[close_ms] = {
            "bar_start_ms": int(row["t"]),
            "volume_ok": bool(volume_ok),
            "volume_ratio": float(ratio),
            "long_pullback": bool(long_pullback),
            "short_pullback": bool(short_pullback),
            "zone_low": float(zone_low),
            "zone_high": float(zone_high),
            "atr15": float(atr15),
            "quarter_bar_t": int(quarter[qi]["t"]),
        }
    _SIGNAL_BY_CLOSE_MS = out


def _close_pullback_volume_loss(self, bar, signal):
    pos = self.position
    p = pos.pending
    close_ms = int(bar["t"]) + 60_000
    mark = float(bar["c"])
    direction = research.side_dir(p.side)

    slip = (research.SLIPPAGE_BPS + self.stress_extra_bps) / 10000.0
    exit_px = mark * (1.0 - slip if p.side == "做多" else 1.0 + slip)
    gross = (exit_px - p.limit) * p.quantity_btc * direction
    exit_fee = exit_px * p.quantity_btc * research.TAKER_BPS / 10000.0
    net = gross - pos.entry_fee - exit_fee + pos.funding_pnl
    self.cash += net

    stop_distance = abs(p.limit - p.stop)
    stop_money = stop_distance * p.quantity_btc
    row = {
        "variant": self.variant,
        "side": p.side,
        "score": p.score,
        "boll_path": p.factors["boll_path"],
        "entry": p.limit,
        "stop": p.stop,
        "target": p.target,
        "exit": exit_px,
        "reason": EXIT_REASON,
        "quantity_btc": p.quantity_btc,
        "net_pnl": net,
        "gross_pnl": gross,
        "entry_fee": pos.entry_fee,
        "exit_fee": exit_fee,
        "funding_pnl": pos.funding_pnl,
        "realized_r": net / stop_money if stop_money > 0 else 0.0,
        "mfe_r": pos.mfe / stop_distance if stop_distance > 0 else 0.0,
        "mae_r": pos.mae / stop_distance if stop_distance > 0 else 0.0,
        "front_r": p.front_r,
        "cost_r": p.cost_r,
        "entry_time": pos.fill_time,
        "exit_time": close_ms,
        "hold_min": (close_ms - pos.fill_time) / 60_000.0,
        "opportunity_id": p.opportunity_id,
        "score_components": dict(p.score_components),
        "trigger_combination": dict(p.trigger_combination),
        "exit_volume_ratio": float(signal["volume_ratio"]),
        "exit_15m_zone_low": float(signal["zone_low"]),
        "exit_15m_zone_high": float(signal["zone_high"]),
        "exit_15m_atr": float(signal["atr15"]),
        "exit_signal_5m_bar_start": int(signal["bar_start_ms"]),
        "exit_signal_15m_bar_start": int(signal["quarter_bar_t"]),
        "exit_mark_before_slippage": mark,
        "exit_rule_volume_threshold": VOLUME_RATIO_THRESHOLD,
        "exit_rule_dynamic_15m_pullback": True,
    }
    row.update(p.factors)
    self.trades.append(row)
    self.position = None
    self.last_close_ms = close_ms
    self.stats["closed"] += 1
    self.stats["pullback15_volume_loss_exit"] += 1


def _process_exit_with_pullback_volume_loss(self, bar):
    _ORIGINAL_PROCESS_EXIT(self, bar)
    if not self.position:
        return

    pos = self.position
    p = pos.pending
    close_ms = int(bar["t"]) + 60_000
    signal = _SIGNAL_BY_CLOSE_MS.get(close_ms)
    if not signal or not bool(signal["volume_ok"]):
        return

    if int(signal["bar_start_ms"]) < int(pos.fill_time):
        return

    pullback_ok = bool(signal["long_pullback"] if p.side == "做多" else signal["short_pullback"])
    if not pullback_ok:
        return

    mark = float(bar["c"])
    unrealized_gross = (mark - p.limit) * p.quantity_btc * research.side_dir(p.side)
    if unrealized_gross >= 0.0:
        return

    _close_pullback_volume_loss(self, bar, signal)


def _configure_and_patch():
    global _ORIGINAL_PROCESS_EXIT
    start, end = baseline._configure()
    baseline._verify_lock()
    _ORIGINAL_PROCESS_EXIT = research.Simulator.process_exit
    research.Simulator.process_exit = _process_exit_with_pullback_volume_loss
    research.VERSION = production.VERSION
    research.BUILD = "1620-outer-rsi30-70-macd-pullback15-volume-loss-exit"
    return start, end


def _verify_exit_patch():
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.THRESHOLD == 6.0
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0
    assert baseline.BE_ENABLED is False
    assert research.Simulator.process_exit is _process_exit_with_pullback_volume_loss
    assert _ORIGINAL_PROCESS_EXIT is not _process_exit_with_pullback_volume_loss
    assert VOLUME_RATIO_THRESHOLD == 1.2


def main():
    start, end = _configure_and_patch()
    _verify_exit_patch()
    print(
        "V162_PULLBACK15_VOLUME_LOSS_EXIT_360D_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} "
        "baseline=outer_only_rsi30_70_macd volume_score=+0.5 "
        "exit=pullback15_and_volume1.2x_and_loss no_time_window=ON "
        "signal_data=closed_5m_and_closed_15m sl=1H_ATR_x1 tp=2R be=OFF",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    _prime_dynamic_exit_map(data["5m"], data["15m"])
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    special = [r for r in sim.trades if str(r.get("reason")) == EXIT_REASON]
    special_net = sum(float(r.get("net_pnl") or 0.0) for r in special)
    metrics.update({
        "label": "V1.6.2 research — dynamic 15m pullback + volume losing exit — strict 360D",
        "release_base": "1.6.2",
        "research_variant": "outer_only_rsi30_70_macd_pullback15_volume_loss_exit",
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
        "dynamic_exit_enabled": True,
        "dynamic_exit_requires_15m_pullback_resonance": True,
        "dynamic_exit_requires_volume": True,
        "dynamic_exit_volume_ratio_threshold": VOLUME_RATIO_THRESHOLD,
        "dynamic_exit_volume_average_lookback": 20,
        "dynamic_exit_requires_mark_to_market_loss": True,
        "dynamic_exit_min_hold_min": 0,
        "dynamic_exit_max_hold_min": None,
        "dynamic_exit_signal_must_form_after_fill": True,
        "dynamic_exit_market_model": "1m close plus taker fee and slippage after fully closed 5m signal",
        "pullback15_volume_loss_exit_trades": len(special),
        "pullback15_volume_loss_exit_net_pnl": special_net,
        "pullback15_volume_loss_exit_avg_pnl": special_net / len(special) if special else 0.0,
        "pullback15_volume_loss_exit_avg_hold_min": sum(float(r.get("hold_min") or 0.0) for r in special) / len(special) if special else 0.0,
        "outer_only": True,
        "outer_rsi_min": 30.0,
        "outer_rsi_max": 70.0,
        "outer_macd_improving_required": True,
        "outer_4h_aligned_required": True,
        "outer_position_multiplier": 1.0,
        "be_enabled": False,
        "entry_order_type": "LIMIT",
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        "reference_run": REFERENCE_RUN,
        "reference_variant": "outer_only_rsi30_70_macd_volume_score_retained",
    })

    outdir = Path("backtest_output_v162_pullback15_volume_loss_exit_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline.base.prior._write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# V1.6.2 Research — Dynamic 15m Pullback + Volume Losing Exit — 360D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## A/B lock",
        f"- Reference Run: {REFERENCE_RUN}.",
        "- Baseline entry model unchanged: outer-only, RSI30-70, MACD improving, 4H aligned, Volume +0.5 retained.",
        "- Original 1H ATR SL / full 2R TP / No-BE unchanged.",
        "- Only change: after fill, a fully closed 5m candle must simultaneously show current 15m pullback resonance and volume >=1.2x previous-20 average; if position is losing, exit at market.",
        "- No 30m-4h window; monitoring continues until the original SL/TP closes the trade.",
        "- Resting SL/TP retains intrabar priority.",
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
        f"- Dynamic pullback+volume losing exits: {len(special)}; net {special_net:+.2f} U",
        "",
        "Historical simulation only. Dynamic exit uses only fully closed 5m and 15m information available at the decision timestamp.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V162_PULLBACK15_VOLUME_LOSS_EXIT_360D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Strict 360D A/B on the successful outer-only RSI/MACD model.

Baseline is Run 34952397559 / research/v162-outer-rsi-macd-360d-2000u.
Only change:
- after an entry has been open for >=30 minutes and <=4 hours,
- when a CLOSED 5m candle has volume >= 1.2x the PREVIOUS 20 closed 5m average,
- and the position is still in mark-to-market loss at that 5m close,
- exit immediately at market (modeled at the closing 1m price with taker fee + slippage).

Original resting SL/TP keep priority inside each 1m bar. Entry logic, Volume +0.5 score,
1H ATR SL, 2R full TP, 4H aligned, RSI30-70, MACD improving and No-BE are unchanged.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import run_v162_outer_rsi30_70_macd_360d_2000u as baseline

research = baseline.research
production = baseline.production

REFERENCE_RUN = 34952397559
EXIT_MIN_MS = 30 * 60_000
EXIT_MAX_MS = 4 * 60 * 60_000
VOLUME_RATIO_THRESHOLD = 1.2
EXIT_REASON = "VOLUME_LOSS_EXIT_30M_4H"

_ORIGINAL_PROCESS_EXIT = None
_VOLUME_BY_CLOSE_MS = {}


def _prime_volume_exit_map(five):
    """Map each 5m close to the exact existing volume-factor definition.

    The average excludes the current bar and uses the previous 20 bars, matching
    v152_model._volume_confirm(). The map is precomputed for speed but each key
    only becomes visible to the exit rule at that candle's own close timestamp.
    """
    global _VOLUME_BY_CLOSE_MS
    out = {}
    for idx, row in enumerate(five):
        if idx < 20:
            continue
        hist = [float(x["v"]) for x in five[idx - 20:idx]]
        avg = sum(hist) / len(hist) if hist else 0.0
        ratio = float(row["v"]) / avg if avg > 0 else 0.0
        close_ms = int(row["t"]) + 5 * 60_000
        out[close_ms] = (ratio >= VOLUME_RATIO_THRESHOLD, ratio)
    _VOLUME_BY_CLOSE_MS = out


def _close_volume_loss(self, bar, ratio):
    pos = self.position
    p = pos.pending
    close_ms = int(bar["t"]) + 60_000
    mark = float(bar["c"])
    direction = research.side_dir(p.side)

    # Market exit at the known 1m close, with the same taker+slippage convention
    # as the audited simulator's triggered exits.
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
        "exit_volume_ratio": float(ratio),
        "exit_mark_before_slippage": mark,
        "exit_rule_window_min": 30,
        "exit_rule_window_max_min": 240,
        "exit_rule_volume_threshold": VOLUME_RATIO_THRESHOLD,
    }
    row.update(p.factors)
    self.trades.append(row)
    self.position = None
    self.last_close_ms = close_ms
    self.stats["closed"] += 1
    self.stats["volume_loss_exit_30m_4h"] += 1


def _process_exit_with_volume_loss(self, bar):
    # Resting SL/TP has intrabar priority. The original method also updates MFE/MAE.
    _ORIGINAL_PROCESS_EXIT(self, bar)
    if not self.position:
        return

    pos = self.position
    p = pos.pending
    close_ms = int(bar["t"]) + 60_000
    age_ms = close_ms - int(pos.fill_time)
    if age_ms < EXIT_MIN_MS or age_ms > EXIT_MAX_MS:
        return

    volume_state = _VOLUME_BY_CLOSE_MS.get(close_ms)
    if not volume_state or not bool(volume_state[0]):
        return

    mark = float(bar["c"])
    # "亏损状态" is evaluated as mark-to-market price PnL before exit costs.
    unrealized_gross = (mark - p.limit) * p.quantity_btc * research.side_dir(p.side)
    if unrealized_gross >= 0:
        return

    _close_volume_loss(self, bar, volume_state[1])


def _configure_and_patch():
    global _ORIGINAL_PROCESS_EXIT
    start, end = baseline._configure()
    # Verify the baseline before adding the single experimental exit rule.
    baseline._verify_lock()
    _ORIGINAL_PROCESS_EXIT = research.Simulator.process_exit
    research.Simulator.process_exit = _process_exit_with_volume_loss
    research.VERSION = production.VERSION
    research.BUILD = "1620-outer-rsi30-70-macd-volume-loss-exit-30m-4h"
    return start, end


def _verify_exit_patch():
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.THRESHOLD == 6.0
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0
    assert baseline.BE_ENABLED is False
    assert research.Simulator.process_exit is _process_exit_with_volume_loss
    assert _ORIGINAL_PROCESS_EXIT is not _process_exit_with_volume_loss
    assert EXIT_MIN_MS == 1_800_000
    assert EXIT_MAX_MS == 14_400_000
    assert VOLUME_RATIO_THRESHOLD == 1.2


def main():
    start, end = _configure_and_patch()
    _verify_exit_patch()
    print(
        "V162_VOLUME_LOSS_EXIT_30M_4H_360D_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} "
        "baseline=outer_only_rsi30_70_macd volume_score=+0.5 "
        "exit_window=30..240min exit_volume=prev20avg_x1.2 exit_only_if_loss=ON "
        "sl=1H_ATR_x1 tp=2R be=OFF",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    _prime_volume_exit_map(data["5m"])
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    volume_exits = [r for r in sim.trades if str(r.get("reason")) == EXIT_REASON]
    volume_exit_net = sum(float(r.get("net_pnl") or 0.0) for r in volume_exits)
    metrics.update({
        "label": "V1.6.2 research — outer RSI30-70 MACD + 30m-4h losing-volume exit — strict 360D",
        "release_base": "1.6.2",
        "research_variant": "outer_only_rsi30_70_macd_volume_loss_exit_30m_4h",
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
        "exit_volume_rule_enabled": True,
        "exit_volume_min_hold_min": 30,
        "exit_volume_max_hold_min": 240,
        "exit_volume_ratio_threshold": VOLUME_RATIO_THRESHOLD,
        "exit_volume_average_lookback": 20,
        "exit_volume_average_excludes_current": True,
        "exit_requires_mark_to_market_loss": True,
        "exit_market_model": "1m close plus taker fee and slippage after closed 5m signal",
        "volume_loss_exit_trades": len(volume_exits),
        "volume_loss_exit_net_pnl": volume_exit_net,
        "volume_loss_exit_avg_pnl": volume_exit_net / len(volume_exits) if volume_exits else 0.0,
        "volume_loss_exit_avg_hold_min": sum(float(r.get("hold_min") or 0.0) for r in volume_exits) / len(volume_exits) if volume_exits else 0.0,
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

    outdir = Path("backtest_output_v162_volume_loss_exit_30m_4h_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline.base.prior._write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# V1.6.2 Research — Losing Volume Exit 30m-4h — 360D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## A/B lock",
        f"- Reference Run: {REFERENCE_RUN}.",
        "- Baseline entry model unchanged: outer-only, RSI30-70, MACD improving, 4H aligned, Volume +0.5 retained.",
        "- Original 1H ATR SL / full 2R TP / No-BE unchanged.",
        "- Only change: from 30 to 240 minutes after fill, a closed 5m volume >=1.2x previous-20 average exits immediately if mark PnL is negative.",
        "- Resting SL/TP retains intrabar priority; volume exit executes only if position remains open at 5m close.",
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
        f"- Volume-loss exits: {len(volume_exits)}; net {volume_exit_net:+.2f} U",
        "",
        "Historical simulation only. The discretionary exit is evaluated only after a 5m candle has fully closed.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V162_VOLUME_LOSS_EXIT_30M_4H_360D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

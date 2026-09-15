#!/usr/bin/env python3
"""Strict 360D A/B: full-hold adverse 15m structure break + 5m MACD deterioration.

Reference baseline: Run 34952397559 / research/v162-outer-rsi-macd-360d-2000u.
Only experimental position-management change:
- monitor for the ENTIRE holding period after fill (no 4-hour cap);
- require a FULLY CLOSED 15m candle formed after the fill;
- adverse structure is the nearest confirmed 15m horizontal zone from the
  previous closed-15m state, using the existing strategy._zones algorithm;
- LONG: 15m close breaks below nearest confirmed support by >=0.25 ATR15;
- SHORT: mirrored break above nearest confirmed resistance by >=0.25 ATR15;
- at the same 15m close, the last three CLOSED 5m MACD histogram values must
  make two consecutive changes against the held direction;
- if current mark-to-market R < 0: exit immediately with a marketable LIMIT proxy;
- if current mark-to-market R >= +1R: do not exit; move SL to entry (BE);
- if 0 <= current R < +1R: do nothing.

Original intrabar SL/TP has priority. Entry model, Volume +0.5 score, 4H alignment,
RSI30-70, outer-only, 1H ATR initial SL, full 2R TP and all other gates are unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import run_v162_structure_macd_4h_exit_be_360d_2000u as prior

baseline = prior.baseline
research = prior.research
production = prior.production

REFERENCE_RUN = 34952397559
STRUCTURE_BREAK_ATR = 0.25
EXIT_REASON = "STRUCTURE_MACD_LOSS_LIMIT_EXIT_FULLHOLD"
LIMIT_PROTECTION_BPS = 5.0

# Reuse the already-audited first-4H implementation, changing only the monitoring
# horizon and research labels. A very large cap is used so the prior processing
# function remains byte-for-byte identical apart from these module globals.
FULL_HOLD_MONITOR_MS = 10**18
prior.MONITOR_MAX_MS = FULL_HOLD_MONITOR_MS
prior.STRUCTURE_BREAK_ATR = STRUCTURE_BREAK_ATR
prior.EXIT_REASON = EXIT_REASON
prior.LIMIT_PROTECTION_BPS = LIMIT_PROTECTION_BPS


def _configure_and_patch():
    start, end = prior._configure_and_patch()
    research.BUILD = "1620-outer-rsi30-70-macd-structure-macd-fullhold-exit-be"
    return start, end


def _verify_exit_patch():
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.THRESHOLD == 6.0
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0
    assert baseline.BE_ENABLED is False
    assert research.Simulator.process_exit is prior._process_exit_with_structure_macd
    assert prior.STRUCTURE_BREAK_ATR == 0.25
    assert prior.MONITOR_MAX_MS == FULL_HOLD_MONITOR_MS
    assert prior.LIMIT_PROTECTION_BPS == 5.0
    assert prior.EXIT_REASON == EXIT_REASON
    assert prior._macd_adverse("做多", 3.0, 2.0, 1.0)
    assert not prior._macd_adverse("做多", 1.0, 2.0, 3.0)
    assert prior._macd_adverse("做空", 1.0, 2.0, 3.0)
    assert not prior._macd_adverse("做空", 3.0, 2.0, 1.0)


def main():
    start, end = _configure_and_patch()
    _verify_exit_patch()
    print(
        "V162_STRUCTURE_MACD_FULLHOLD_EXIT_BE_360D_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} "
        "baseline=outer_only_rsi30_70_macd volume_score=+0.5 "
        "monitor=entire_holding_period structure=previous_confirmed_15m_zone break=0.25ATR15 "
        "macd=two_consecutive_adverse_5m_changes loss=marketable_LIMIT_proxy "
        "profit_ge_1R=SL_to_BE original_sl=1H_ATR_x1 tp=2R",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    prior._prime_adverse_signal_map(data["5m"], data["15m"])
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
        "label": "V1.6.2 research — full-hold 15m structure break + 5m adverse MACD; loss LIMIT exit / >=1R BE — strict 360D",
        "release_base": "1.6.2",
        "research_variant": "outer_only_rsi30_70_macd_structure_break_macd_fullhold_loss_limit_be",
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
        "management_monitor_max_hold_min": None,
        "management_monitor_entire_holding_period": True,
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

    outdir = Path("backtest_output_v162_structure_macd_fullhold_exit_be_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline.base.prior._write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# V1.6.2 Research — Full-Hold Structure Break + Adverse MACD — 360D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## A/B lock",
        f"- Reference Run: {REFERENCE_RUN}.",
        "- Entry model unchanged: outer-only, RSI30-70, formal 5m MACD improving, 4H aligned, Volume +0.5 retained.",
        "- Initial SL remains 1x 1H ATR; TP remains full 2R; no baseline BE.",
        "- Experimental management monitors the entire holding period; there is no 4-hour cutoff.",
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
        "Historical simulation only. Immediate LIMIT exits use the same conservative marketable-limit proxy as the first-4H A/B because historical order-book sequencing is unavailable.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V162_STRUCTURE_MACD_FULLHOLD_EXIT_BE_360D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

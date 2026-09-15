#!/usr/bin/env python3
"""Strict 720D A/B versus Run 34979938543.

Only strategy change:
- remove the legacy +0.5 Volume score bonus;
- if the 5m signal candle Volume >= 1.2x the previous-20 closed-5m average,
  block the entry as a Hard Gate.

Everything else remains identical to the successful 720D Volume-retained baseline.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import run_v162_outer_rsi30_70_macd_720d_2000u as prior
import research_v162_outer_rsi_macd_volume_gate_model as gate

# Swap only the strategy overlay. The historical engine, execution model, window,
# risk profile and all other settings remain inherited from the audited 720D run.
prior.production = gate
prior.baseline.production = gate

research = prior.research
production = gate

DAYS = prior.DAYS
CAPITAL = prior.CAPITAL
BASE_POSITION_NOTIONAL = prior.BASE_POSITION_NOTIONAL
LEVERAGE = prior.LEVERAGE
RISK_USDT = prior.RISK_USDT
RISK_PCT = prior.RISK_PCT
DAILY_LOSS = prior.DAILY_LOSS
STOP_ATR = prior.STOP_ATR
REWARD_R = prior.REWARD_R
COOLDOWN_MS = prior.COOLDOWN_MS
BE_ENABLED = prior.BE_ENABLED
REFERENCE_RUN = 34979938543


def _configure():
    start, end = prior._configure()
    research.model = production
    research.VERSION = production.VERSION
    research.BUILD = "1620-outer-rsi30-70-macd-volume-hard-gate-720d"
    return start, end


def _verify_lock():
    assert DAYS == 720
    assert prior.FIXED_START.isoformat() == "2024-09-24T20:20:00+00:00"
    assert prior.FIXED_END.isoformat() == "2026-09-14T20:20:00+00:00"
    assert CAPITAL == 2000.0 and BASE_POSITION_NOTIONAL == 2000.0
    assert LEVERAGE == 5 and RISK_USDT == 20.0 and DAILY_LOSS == 60.0
    assert STOP_ATR == 1.0 and REWARD_R == 2.0 and BE_ENABLED is False
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.THRESHOLD == 6.0
    assert production.VOLUME_RATIO_THRESHOLD == 1.2

    # Prove the inherited scorer still contains the old +0.5, while this overlay
    # removes it and blocks the same signal. This makes the A/B change explicit.
    src = Path("v153_model.py").read_text(encoding="utf-8")
    assert '"volume5": 0.5 if opp.get("five_signal_volume_ok") else 0.0' in src
    row = {
        "total": 6.5, "raw": 6.5, "gate": True, "eligible": True,
        "signal_tier": 1, "position_multiplier": 1.0,
        "level": "test", "reason": "test",
        "items": [("5m信号K线成交量≥20均量1.2×", 0.5, 0.5)],
        "layers": {"volume5": 0.5},
        "confirmations": {"required": {}, "blockers": []},
        "opportunity": {"five_signal_volume_ok": True},
    }
    production._remove_volume_bonus_and_gate(row, row["opportunity"])
    assert row["total"] == 6.0
    assert row["layers"]["volume5"] == 0.0
    assert row["gate"] is False and row["eligible"] is False
    assert row["position_multiplier"] == 0.0
    assert row["confirmations"]["required"]["volume_below_1_2x_20"] is False


def _split_analysis(rows):
    rows = list(rows)
    mid_ms = int(prior.MIDPOINT.timestamp() * 1000)
    first = [r for r in rows if int(r.get("entry_time") or 0) < mid_ms]
    second = [r for r in rows if int(r.get("entry_time") or 0) >= mid_ms]
    longs = [r for r in rows if str(r.get("side") or "") == "做多"]
    shorts = [r for r in rows if str(r.get("side") or "") == "做空"]
    by_score = defaultdict(list)
    for r in rows:
        by_score[f"{float(r.get('score') or 0.0):.1f}"].append(r)
    return {
        "midpoint_utc": prior.MIDPOINT.isoformat(),
        "first_360d": prior._stats(first),
        "last_360d": prior._stats(second),
        "long": prior._stats(longs),
        "short": prior._stats(shorts),
        "by_score": {k: prior._stats(v) for k, v in sorted(by_score.items(), key=lambda kv: float(kv[0]))},
    }


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V162_VOLUME_HARD_GATE_720D_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} days={DAYS} capital={CAPITAL:.0f} "
        f"base_notional={BASE_POSITION_NOTIONAL:.0f} leverage={LEVERAGE} "
        "outer_only=ON rsi=30..70 macd=required 4h=aligned "
        "volume_score=0 volume_ge_1.2x20=HARD_GATE threshold=6 "
        "1x=ON sl=1H_ATR tp=2R be=OFF",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    analysis = _split_analysis(sim.trades)
    metrics.update({
        "label": "V1.6.2 research — outer RSI/MACD + Volume Hard Gate — strict 720D",
        "release_base": "1.6.2",
        "research_variant": "outer_only_rsi30_70_macd_volume_hard_gate_720d",
        "days": DAYS,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "capital": CAPITAL,
        "base_position_notional_usdt": BASE_POSITION_NOTIONAL,
        "leverage": LEVERAGE,
        "base_risk_usdt": RISK_USDT,
        "risk_pct": RISK_PCT,
        "daily_loss_usdt": DAILY_LOSS,
        "score_threshold": 6.0,
        "volume_score_enabled": False,
        "volume_score_points": 0.0,
        "volume_hard_gate_enabled": True,
        "volume_hard_gate_rule": "5m signal candle volume >= 1.2x previous-20 average blocks entry",
        "volume_ratio_threshold": 1.2,
        "middle_enabled": False,
        "outer_only": True,
        "outer_rsi_min": 30.0,
        "outer_rsi_max": 70.0,
        "outer_macd_improving_required": True,
        "outer_4h_aligned_required": True,
        "outer_position_multiplier": 1.0,
        "be_enabled": False,
        "path_c_enabled": False,
        "cooldown_minutes": 0,
        "loss_pause_enabled": False,
        "entry_order_type": "LIMIT",
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        "reference_run": REFERENCE_RUN,
        "reference_variant": "outer_only_rsi30_70_macd_volume_score_retained_720d",
        "split_analysis": analysis,
    })

    outdir = Path("backtest_output_v162_volume_hard_gate_720d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_720d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "split_analysis_720d.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    prior.baseline.base.prior._write_trade_csv(sim.trades, outdir / "trades_720d.csv")

    report = [
        "# V1.6.2 Research — Volume Hard Gate — 720D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## A/B lock",
        f"- Reference Run: {REFERENCE_RUN}.",
        "- Only strategy change: remove Volume +0.5 and block entries when signal-candle Volume >=1.2x previous-20 average.",
        "- Outer-only, RSI30-70, 5m MACD improving, 4H aligned, threshold 6.0 unchanged.",
        "- 1x LIMIT entry, 1H ATR x1 SL, full 2R TP, BE off unchanged.",
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
        f"- First 360D: {analysis['first_360d']['trades']} trades, net {analysis['first_360d']['net_pnl']:+.2f} U, PF {analysis['first_360d']['profit_factor']:.3f}",
        f"- Last 360D: {analysis['last_360d']['trades']} trades, net {analysis['last_360d']['net_pnl']:+.2f} U, PF {analysis['last_360d']['profit_factor']:.3f}",
        "",
        "Historical simulation only. Same audited historical execution engine as the reference 720D run.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V162_VOLUME_HARD_GATE_720D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

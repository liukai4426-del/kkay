#!/usr/bin/env python3
"""KAYTRADE V1.6.2 research: extend the successful 360D outer-only model to 720D.

Only intended change versus Run 34952397559:
- extend the fixed historical window backward from 360 days to 720 days.

Kept exactly:
- 5m BOLL outer bands only;
- long lower-band / short upper-band;
- both sides signal-time RSI 30..70;
- 5m MACD continuous improvement required;
- 4H aligned hard gate;
- score threshold >= 6.0;
- 5m volume expansion remains a +0.5 score component;
- all entries 1x LIMIT;
- 1H ATR x1 stop, 2R full TP, BE off;
- 2000U capital/profile and 5x leverage.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_v162_outer_rsi30_70_macd_360d_2000u as baseline

research = baseline.research
production = baseline.production

DAYS = 720
CAPITAL = baseline.CAPITAL
BASE_POSITION_NOTIONAL = baseline.BASE_POSITION_NOTIONAL
LEVERAGE = baseline.LEVERAGE
RISK_USDT = baseline.RISK_USDT
RISK_PCT = baseline.RISK_PCT
DAILY_LOSS = baseline.DAILY_LOSS
STOP_ATR = baseline.STOP_ATR
REWARD_R = baseline.REWARD_R
COOLDOWN_MS = baseline.COOLDOWN_MS
BE_ENABLED = baseline.BE_ENABLED
REFERENCE_RUN = 34952397559

# Preserve the exact end timestamp from the successful 360D reference and
# extend only the beginning backward by another 360 days.
FIXED_END = datetime(2026, 9, 14, 20, 20, tzinfo=timezone.utc)
FIXED_START = FIXED_END - timedelta(days=DAYS)
MIDPOINT = FIXED_START + timedelta(days=DAYS // 2)


def _configure():
    # Install the exact reference model/execution wiring first.
    baseline._configure()

    start, end = FIXED_START, FIXED_END
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms
    # This split is diagnostics only; it creates equal 360D halves for factor validation.
    research.SPLIT_MS = int(MIDPOINT.timestamp() * 1000)

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

    research.CAPITAL = CAPITAL
    research.LEVERAGE = LEVERAGE
    research.RISK_USDT = RISK_USDT
    research.RISK_PCT = RISK_PCT
    research.DAILY_LOSS = DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = BASE_POSITION_NOTIONAL
    research.STOP_ATR = STOP_ATR
    research.REWARD_R = REWARD_R
    research.COOLDOWN_MS = COOLDOWN_MS
    research.model = production
    research.Simulator.submit = baseline.base._submit_v162
    research.VERSION = production.VERSION
    research.BUILD = "1620-outer-rsi30-70-macd-720d-volume-retained"
    production._install_signal_patch()
    return start, end


def _verify_lock():
    baseline._verify_lock()
    assert DAYS == 720
    assert FIXED_END.isoformat() == "2026-09-14T20:20:00+00:00"
    assert FIXED_START.isoformat() == "2024-09-24T20:20:00+00:00"
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.THRESHOLD == 6.0
    assert BE_ENABLED is False
    # Explicitly prove the legacy V1.5.3 scorer still awards Volume +0.5.
    scoring_source = Path("v153_model.py").read_text(encoding="utf-8")
    assert '"volume5": 0.5 if opp.get("five_signal_volume_ok") else 0.0' in scoring_source
    assert 'components["volume5"]' in scoring_source


def _pf(rows):
    pos = sum(max(float(r.get("net_pnl") or 0.0), 0.0) for r in rows)
    neg = -sum(min(float(r.get("net_pnl") or 0.0), 0.0) for r in rows)
    if neg <= 0:
        return math.inf if pos > 0 else 0.0
    return pos / neg


def _stats(rows):
    rows = list(rows)
    n = len(rows)
    wins = sum(float(r.get("net_pnl") or 0.0) > 0 for r in rows)
    net = sum(float(r.get("net_pnl") or 0.0) for r in rows)
    gross = sum(float(r.get("gross_pnl") or 0.0) for r in rows)
    fees = sum(float(r.get("entry_fee") or 0.0) + float(r.get("exit_fee") or 0.0) for r in rows)
    funding = sum(float(r.get("funding_pnl") or 0.0) for r in rows)
    avg_score = sum(float(r.get("score") or 0.0) for r in rows) / n if n else 0.0
    avg_hold = sum(float(r.get("hold_min") or 0.0) for r in rows) / n if n else 0.0
    return {
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate_pct": wins / n * 100.0 if n else 0.0,
        "net_pnl": net,
        "gross_pnl": gross,
        "fees": fees,
        "funding_pnl": funding,
        "profit_factor": _pf(rows),
        "expectancy": net / n if n else 0.0,
        "avg_score": avg_score,
        "avg_hold_min": avg_hold,
    }


def _volume_analysis(rows, start, end):
    rows = list(rows)
    mid_ms = int((start + (end - start) / 2).timestamp() * 1000)
    vol = lambda r: float(r.get("volume5") or 0.0) > 0.0
    pull = lambda r: float(r.get("direction_pullback") or 0.0) > 0.0
    long_ = lambda r: str(r.get("side") or "") == "做多"
    short_ = lambda r: str(r.get("side") or "") == "做空"
    first = lambda r: int(r.get("entry_time") or 0) < mid_ms
    second = lambda r: int(r.get("entry_time") or 0) >= mid_ms

    groups = {
        "all": rows,
        "volume_on": [r for r in rows if vol(r)],
        "volume_off": [r for r in rows if not vol(r)],
        "volume_on_long": [r for r in rows if vol(r) and long_(r)],
        "volume_on_short": [r for r in rows if vol(r) and short_(r)],
        "volume_off_long": [r for r in rows if not vol(r) and long_(r)],
        "volume_off_short": [r for r in rows if not vol(r) and short_(r)],
        "volume_plus_15m_pullback": [r for r in rows if vol(r) and pull(r)],
        "15m_pullback_without_volume": [r for r in rows if pull(r) and not vol(r)],
        "volume_without_15m_pullback": [r for r in rows if vol(r) and not pull(r)],
        # With half-point score granularity, score==6 + volume0.5 means the
        # trade would have been 5.5 without Volume and therefore depended on it.
        "volume_required_to_reach_6": [r for r in rows if vol(r) and abs(float(r.get("score") or 0.0) - 6.0) < 1e-9],
        "volume_on_first_360d": [r for r in rows if vol(r) and first(r)],
        "volume_on_last_360d": [r for r in rows if vol(r) and second(r)],
        "volume_off_first_360d": [r for r in rows if not vol(r) and first(r)],
        "volume_off_last_360d": [r for r in rows if not vol(r) and second(r)],
    }

    by_score = defaultdict(list)
    for r in rows:
        if vol(r):
            by_score[f"{float(r.get('score') or 0.0):.1f}"] .append(r)

    return {
        "definition": "volume5=+0.5 when 5m signal candle volume >= 1.2x 20-bar average",
        "midpoint_utc": datetime.fromtimestamp(mid_ms / 1000.0, tz=timezone.utc).isoformat(),
        "groups": {k: _stats(v) for k, v in groups.items()},
        "volume_on_by_score": {k: _stats(v) for k, v in sorted(by_score.items(), key=lambda kv: float(kv[0]))},
    }


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V162_OUTER_RSI30_70_MACD_720D_VOLUME_RETAINED_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} days={DAYS} capital={CAPITAL:.0f} "
        f"base_notional={BASE_POSITION_NOTIONAL:.0f} leverage={LEVERAGE} "
        f"base_risk={RISK_USDT:.0f} daily_loss={DAILY_LOSS:.0f} "
        "middle=OFF outer_only=ON rsi=30..70 macd=required 4h=aligned "
        "volume_score=+0.5 threshold=6 1x=ON sl=1H_ATR tp=2R be=OFF",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    volume_analysis = _volume_analysis(sim.trades, start, end)
    metrics.update({
        "label": "V1.6.2 research — outer-only + RSI30-70 + MACD + Volume0.5 retained — strict 720D",
        "release_base": "1.6.2",
        "research_variant": "outer_only_rsi30_70_macd_volume_score_720d",
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
        "volume_score_enabled": True,
        "volume_score_points": 0.5,
        "volume_rule": "5m signal candle volume >= 1.2x 20-bar average",
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
        "boll_signal_wall_clock_expiry": False,
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        "reference_run": REFERENCE_RUN,
        "reference_variant": "same model / 360D / Volume +0.5 retained",
        "volume_analysis": volume_analysis,
    })

    outdir = Path("backtest_output_v162_outer_rsi30_70_macd_720d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_720d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "volume_analysis_720d.json").write_text(json.dumps(volume_analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline.base.prior._write_trade_csv(sim.trades, outdir / "trades_720d.csv")

    g = volume_analysis["groups"]
    report = [
        "# V1.6.2 Research — Outer Only + RSI 30-70 + MACD + Volume +0.5 — 720D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Research lock",
        f"- Exact strategy reference: Run {REFERENCE_RUN}; only the historical window is extended backward to 720 days.",
        "- Volume remains +0.5 when 5m signal-candle volume >= 1.2x its 20-bar average.",
        "- Middle disabled; outer only; RSI 30-70 both sides; 5m MACD improvement and 4H alignment mandatory.",
        "- 1x LIMIT; 1H ATR x1 SL; 2R full TP; BE off; threshold 6.0.",
        "",
        "## Overall result",
        f"- Trades: {metrics['trades']}",
        f"- Wins / losses: {metrics['wins']} / {metrics['losses']}",
        f"- Win rate: {metrics['win_rate_pct']:.2f}%",
        f"- Ending equity: {metrics['ending_equity']:.2f} U",
        f"- Net PnL: {metrics['net_pnl']:+.2f} U ({metrics['net_return_pct']:+.2f}%)",
        f"- Profit factor: {metrics['profit_factor']:.3f}",
        f"- Expectancy: {metrics['expectancy']:+.3f} U/trade",
        f"- Max DD: {metrics['max_drawdown_usdt']:.2f} U ({metrics['max_drawdown_pct']:.2f}%)",
        "",
        "## Volume factor diagnostics",
        f"- Volume ON: {g['volume_on']['trades']} trades, WR {g['volume_on']['win_rate_pct']:.2f}%, net {g['volume_on']['net_pnl']:+.2f}U, PF {g['volume_on']['profit_factor']:.3f}.",
        f"- Volume OFF: {g['volume_off']['trades']} trades, WR {g['volume_off']['win_rate_pct']:.2f}%, net {g['volume_off']['net_pnl']:+.2f}U, PF {g['volume_off']['profit_factor']:.3f}.",
        f"- Volume + 15m pullback: {g['volume_plus_15m_pullback']['trades']} trades, net {g['volume_plus_15m_pullback']['net_pnl']:+.2f}U, PF {g['volume_plus_15m_pullback']['profit_factor']:.3f}.",
        f"- 15m pullback without Volume: {g['15m_pullback_without_volume']['trades']} trades, net {g['15m_pullback_without_volume']['net_pnl']:+.2f}U, PF {g['15m_pullback_without_volume']['profit_factor']:.3f}.",
        f"- Volume required to reach score 6.0: {g['volume_required_to_reach_6']['trades']} trades, net {g['volume_required_to_reach_6']['net_pnl']:+.2f}U, PF {g['volume_required_to_reach_6']['profit_factor']:.3f}.",
        f"- Volume ON first 360D: {g['volume_on_first_360d']['trades']} trades, net {g['volume_on_first_360d']['net_pnl']:+.2f}U, PF {g['volume_on_first_360d']['profit_factor']:.3f}.",
        f"- Volume ON last 360D: {g['volume_on_last_360d']['trades']} trades, net {g['volume_on_last_360d']['net_pnl']:+.2f}U, PF {g['volume_on_last_360d']['profit_factor']:.3f}.",
        "",
        "Historical simulation only. LIMIT fills use the audited closed-1m proxy; historical tick/orderbook sequence is unavailable.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V162_OUTER_RSI30_70_MACD_720D_VOLUME_RETAINED_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

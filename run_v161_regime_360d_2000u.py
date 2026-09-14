#!/usr/bin/env python3
"""360-day validation of the revised regime-layer research rules, 2,000U profile."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import run_v160_365d_2000u as old
import research_v161_regime_model as production

# Reuse the already-audited 365D simulator, data/fill/cost layer and exact V1.6.0
# 1x-middle / 2x-outer order sizing. Only the model rules and window are changed.
old.production = production
old.DAYS = 360

research = old.research
DAYS = 360
CAPITAL = old.CAPITAL
BASE_POSITION_NOTIONAL = old.BASE_POSITION_NOTIONAL
LEVERAGE = old.LEVERAGE
RISK_USDT = old.RISK_USDT
RISK_PCT = old.RISK_PCT
DAILY_LOSS = old.DAILY_LOSS
STOP_ATR = old.STOP_ATR
REWARD_R = old.REWARD_R


def _configure():
    old.DAYS = DAYS
    old.production = production
    start, end = old._configure()
    research.VERSION = production.VERSION
    research.BUILD = "R161"
    return start, end


def _verify_lock():
    assert DAYS == 360
    assert CAPITAL == 2_000.0
    assert BASE_POSITION_NOTIONAL == 2_000.0
    assert LEVERAGE == 5
    assert RISK_USDT == 20.0
    assert RISK_PCT == 1.0
    assert DAILY_LOSS == 60.0
    assert STOP_ATR == 1.0 and REWARD_R == 2.0
    assert production.VERSION == "1.6.1-research-regime"
    assert production.THRESHOLD == 6.0
    assert production.SCORE_MAX == 11.0
    assert production.ENTRY_WINDOW_MS == 0
    assert production.TIME_WINDOW_ENABLED is False
    assert research.build1544.PATH_C_ENABLED is False
    assert research.COOLDOWN_MS == 0

    # Compatibility check used by the inherited submitter.
    assert production._macd_improving({"confirmations": {"middle_macd_improving_2bar": True}})
    assert not production._macd_improving({"confirmations": {"middle_macd_improving_2bar": False}})


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V161_REGIME_360D_2000U_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} capital={CAPITAL:.0f} "
        f"base_notional={BASE_POSITION_NOTIONAL:.0f} leverage={LEVERAGE} "
        f"base_risk={RISK_USDT:.0f} daily_loss={DAILY_LOSS:.0f}",
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
        "label": "KAYTRADE revised regime-layer research — 360D 2000U profile",
        "release": production.VERSION,
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
        "score_threshold": production.THRESHOLD,
        "score_max": production.SCORE_MAX,
        "hard_lock_4h_non_aligned": True,
        "score_4h_aligned": 2.0,
        "score_4h_boll_direction_zone": 2.0,
        "score_1h_direction_pullback": 1.0,
        "score_5m_macd_improving": 0.0,
        "middle_macd_required": True,
        "middle_macd_definition": "latest two closed 5m MACD histogram values improve in trade direction",
        "path_c_enabled": False,
        "cooldown_minutes": 0,
        "loss_pause_enabled": False,
        "entry_order_type": "LIMIT",
        "boll_signal_wall_clock_expiry": False,
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
    })

    outdir = Path("backtest_output_v161_regime_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    old._write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# KAYTRADE Regime-Layer Research — 360D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Revised rules",
        "- 4H aligned: mandatory hard gate and +2 score; neutral/opposite blocks entry.",
        "- 4H BOLL position: long middle-to-upper / short middle-to-lower = +2.",
        "- 1H correct-direction pullback-zone resonance = +1 (replaces 15m +2).",
        "- Middle MACD hard gate uses only the latest two closed 5m histogram values.",
        "- MACD improving is removed from the score.",
        "- Other V1.6.0 hard blocks, 6-point threshold and sizing are unchanged.",
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
        f"- Fees: {metrics['fees']:.2f} U",
        f"- Funding: {metrics['funding_pnl']:+.2f} U",
        "",
        "Historical LIMIT fills use the audited closed-1m proxy, not historical orderbook bid/ask.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print("V161_REGIME_360D_RESULT")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

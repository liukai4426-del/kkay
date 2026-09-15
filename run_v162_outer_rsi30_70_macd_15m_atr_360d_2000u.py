#!/usr/bin/env python3
"""Strict 360D A/B: same outer-only entry model, 15m ATR exits.

Reference is the immediately previous outer-only + RSI30-70 + MACD research
variant. The only intended change is the effective ATR timeframe for R:
- baseline: 1x 1H ATR SL, 2R TP;
- this experiment: 1x 15m ATR SL, 2R TP (= 2x 15m ATR).
All entry logic, sizing, 4H alignment, scoring and other hard gates are kept.
"""
from __future__ import annotations

import json
from pathlib import Path

import run_v162_outer_rsi30_70_macd_360d_2000u as baseline
import research_v162_outer_rsi_macd_15m_atr_model as production

research = baseline.research

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
REFERENCE_RUN = 34952338301


def _configure():
    # The baseline runner resolves its production global at runtime.
    baseline.production = production
    start, end = baseline._configure()
    research.model = production
    research.VERSION = production.VERSION
    research.BUILD = "1620-outer-rsi30-70-macd-15m-atr-research"
    production._install_signal_patch()
    return start, end


def _verify_lock():
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.THRESHOLD == 6.0
    assert production.ENTRY_WINDOW_MS == 0
    assert production.TIME_WINDOW_ENABLED is False
    assert research.build1544.PATH_C_ENABLED is False
    assert research.COOLDOWN_MS == 0
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0
    assert research.CAPITAL == CAPITAL
    assert research.FIRST_SIGNAL_NOTIONAL == BASE_POSITION_NOTIONAL
    assert research.RISK_USDT == RISK_USDT and research.DAILY_LOSS == DAILY_LOSS
    assert BE_ENABLED is False

    # Verify the patch changes the exact field consumed by both v153 scoring
    # and the audited submit path, while preserving original 1H ATR metadata.
    sample = {
        "atr1h": 500.0,
        "atr15": 200.0,
        "trigger_detail": {},
    }
    orig = production._ORIGINAL_NEW_OPPORTUNITY
    try:
        production._ORIGINAL_NEW_OPPORTUNITY = lambda *a, **k: dict(sample)
        opp = production._new_opportunity_15m(None, None, None, None, "做多", None)
        assert opp["atr1h_original"] == 500.0
        assert opp["atr15"] == 200.0
        assert opp["atr1h"] == 200.0
        assert opp["effective_stop_atr_timeframe"] == "15m"
    finally:
        production._ORIGINAL_NEW_OPPORTUNITY = orig


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V162_OUTER_RSI30_70_MACD_15M_ATR_360D_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} capital={CAPITAL:.0f} "
        f"base_notional={BASE_POSITION_NOTIONAL:.0f} leverage={LEVERAGE} "
        f"base_risk={RISK_USDT:.0f} daily_loss={DAILY_LOSS:.0f} "
        "middle=OFF rsi=30..70 macd=required 4h=aligned stop=1x15mATR tp=2x15mATR be=OFF",
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
        "label": "V1.6.2 research — outer only + RSI30-70 + MACD + 15m ATR exits — strict 360D",
        "release_base": "1.6.2",
        "research_variant": "outer_only_rsi30_70_macd_15m_atr",
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
        "stop_atr_timeframe": "15m",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        "take_profit_atr_timeframe": "15m",
        "take_profit_atr_multiplier": 2.0,
        "reference_run": REFERENCE_RUN,
        "reference_variant": "outer_only_rsi30_70_macd_1h_atr",
    })

    outdir = Path("backtest_output_v162_outer_rsi30_70_macd_15m_atr_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline.base.prior._write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# V1.6.2 Research — Outer Only + RSI30-70 + MACD + 15m ATR — 360D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Strict A/B lock",
        f"- Reference outer-only 1H-ATR research Run: {REFERENCE_RUN}.",
        "- Entry model unchanged: lower-band long / upper-band short only, RSI 30-70, 5m MACD improving, 4H aligned.",
        "- Risk basis changed only: 1H ATR -> 15m ATR.",
        "- SL = 1x 15m ATR; TP = 2R = 2x 15m ATR.",
        "- R-based front-space, cost and structural stop gates use the same 15m ATR distance.",
        "- 1x LIMIT sizing; BE remains off.",
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

    print("V162_OUTER_RSI30_70_MACD_15M_ATR_360D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

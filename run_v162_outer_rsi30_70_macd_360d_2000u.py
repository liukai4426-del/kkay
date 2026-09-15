#!/usr/bin/env python3
"""Strict 360D research A/B on top of V1.6.2.

Only research changes:
- middle_rsi disabled;
- lower-band long / upper-band short only;
- both sides require signal-time 5m RSI in [30, 70];
- both sides require formal 5m macd_improving at execution.

Everything else stays on the V1.6.2 360D comparison lock: same fixed window,
2000U capital/profile, 5x leverage, 20U base risk, 60U daily-loss budget,
all entries 1x LIMIT, 4H aligned hard gate, 1H ATR stop, 2R TP, BE off.
"""
from __future__ import annotations

import json
from pathlib import Path

import run_v162_360d_2000u as base
import research_v162_outer_rsi_macd_model as production

research = base.research

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
REFERENCE_RUN = 34945333315


def _configure():
    # Swap the V1.6.2 runner's production-model reference before configuration;
    # its submit path resolves this global at runtime, so sizing/fills stay exact.
    base.production = production
    start, end = base._configure()
    research.model = production
    research.Simulator.submit = base._submit_v162
    research.VERSION = production.VERSION
    research.BUILD = "1620-outer-rsi30-70-macd-research"
    research.CAPITAL = CAPITAL
    research.LEVERAGE = LEVERAGE
    research.RISK_USDT = RISK_USDT
    research.RISK_PCT = RISK_PCT
    research.DAILY_LOSS = DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = BASE_POSITION_NOTIONAL
    research.STOP_ATR = STOP_ATR
    research.REWARD_R = REWARD_R
    research.COOLDOWN_MS = COOLDOWN_MS
    production._install_signal_patch()
    return start, end


def _sample_row(path, state="aligned", macd=1.0, rsi=50.0):
    return {
        "total": 6.5,
        "gate": True,
        "eligible": True,
        "position_multiplier": 1.0,
        "layers": {"macd_improving": macd},
        "confirmations": {
            "required": {},
            "4H_trend_state": state,
            "rsi5": rsi,
            "blockers": [],
        },
        "opportunity": {"signal_path": path},
        "reason": "ok",
        "level": "ok",
    }


def _verify_lock():
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
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
    assert research.Simulator.process_exit is base.prior._ORIGINAL_PROCESS_EXIT
    assert research.Simulator.process_pending is base.prior._ORIGINAL_PROCESS_PENDING

    middle = _sample_row(production.MIDDLE_PATH)
    production._apply_outer_research_rules(middle, production.MIDDLE_PATH)
    assert middle["eligible"] is False

    good_outer = _sample_row("lower_band", macd=1.0, rsi=50.0)
    production._apply_outer_research_rules(good_outer, "lower_band")
    assert good_outer["eligible"] is True

    low_rsi = _sample_row("lower_band", macd=1.0, rsi=29.9)
    production._apply_outer_research_rules(low_rsi, "lower_band")
    assert low_rsi["eligible"] is False

    high_rsi = _sample_row("upper_band", macd=1.0, rsi=70.1)
    production._apply_outer_research_rules(high_rsi, "upper_band")
    assert high_rsi["eligible"] is False

    no_macd = _sample_row("upper_band", macd=0.0, rsi=50.0)
    production._apply_outer_research_rules(no_macd, "upper_band")
    assert no_macd["eligible"] is False


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V162_OUTER_RSI30_70_MACD_360D_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} capital={CAPITAL:.0f} "
        f"base_notional={BASE_POSITION_NOTIONAL:.0f} leverage={LEVERAGE} "
        f"base_risk={RISK_USDT:.0f} daily_loss={DAILY_LOSS:.0f} "
        "middle=OFF outer_only=ON rsi=30..70 macd=required 4h=aligned 1x=ON be=OFF",
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
        "label": "V1.6.2 research — outer-only + RSI30-70 + 5m MACD improving — strict 360D",
        "release_base": "1.6.2",
        "research_variant": "outer_only_rsi30_70_macd",
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
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        "reference_run": REFERENCE_RUN,
        "reference_variant": "V1.6.2 all-BOLL 4H aligned / all 1x / BE off",
    })

    outdir = Path("backtest_output_v162_outer_rsi30_70_macd_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    base.prior._write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# V1.6.2 Research — Outer Only + RSI 30-70 + MACD — 360D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Research lock",
        f"- Reference production backtest Run: {REFERENCE_RUN}.",
        "- Middle BOLL path disabled completely.",
        "- Long: 5m lower-band touch + RSI 30-70 + formal 5m MACD improving.",
        "- Short: 5m upper-band touch + RSI 30-70 + formal 5m MACD improving.",
        "- 4H aligned remains mandatory; inherited structural/cost gates unchanged.",
        "- All initial entries remain 1x LIMIT; 1H ATR SL; 2R full TP; BE off.",
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

    print("V162_OUTER_RSI30_70_MACD_360D_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

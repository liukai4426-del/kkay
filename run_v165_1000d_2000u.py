#!/usr/bin/env python3
"""KAYTRADE V1.6.5 strict 1000-day BTC-USDT-SWAP backtest, 2,000U profile.

This runner reuses the audited historical simulator/data layer while routing
entry evaluation and execution checks through the exact V1.6.5 production
model. It mirrors the current production semantics:
- latest CLOSED candles only;
- 5m BOLL outer-band signal package valid until the next closed 5m candle;
- lower-band long / upper-band short only;
- BOLL outer signal = 2.5 points; Volume does not score;
- 5m signal Volume >= 1.20x previous-20 average blocks opening;
- signal RSI 30..70, formal 5m MACD improvement, 4H aligned;
- score >= 6.0, fixed 1x LIMIT entry;
- 1x 1H ATR stop, full-position 2R TP, BE off.

Historical LIMIT fills use the project's audited closed-1m proxy; historical
order-book queue position is unavailable.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_v160_365d_2000u as prior
import v165_model as production

research = prior.research

DAYS = 1000
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
FIXED_END = datetime(2026, 9, 14, 20, 20, tzinfo=timezone.utc)
FIXED_START = FIXED_END - timedelta(days=DAYS)

# Capture the audited original exit path before any version-specific configure
# function can install a historical BE overlay.
_ORIGINAL_PROCESS_PENDING = research.Simulator.process_pending
_ORIGINAL_PROCESS_EXIT = research.Simulator.process_exit
_ORIGINAL_FINISH = research.Simulator.finish


def _pf(rows):
    positive = sum(max(float(r.get("net_pnl") or 0.0), 0.0) for r in rows)
    negative = -sum(min(float(r.get("net_pnl") or 0.0), 0.0) for r in rows)
    if negative <= 0:
        return math.inf if positive > 0 else 0.0
    return positive / negative


def _stats(rows):
    rows = list(rows)
    n = len(rows)
    wins = sum(float(r.get("net_pnl") or 0.0) > 0 for r in rows)
    net = sum(float(r.get("net_pnl") or 0.0) for r in rows)
    gross = sum(float(r.get("gross_pnl") or 0.0) for r in rows)
    fees = sum(float(r.get("entry_fee") or 0.0) + float(r.get("exit_fee") or 0.0) for r in rows)
    funding = sum(float(r.get("funding_pnl") or 0.0) for r in rows)
    avg_hold = sum(float(r.get("hold_min") or 0.0) for r in rows) / n if n else 0.0
    avg_score = sum(float(r.get("score") or 0.0) for r in rows) / n if n else 0.0
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
        "avg_hold_min": avg_hold,
        "avg_score": avg_score,
    }


def _streak_analysis(rows):
    ordered = sorted(rows, key=lambda r: int(r.get("entry_time") or 0))
    max_loss = max_win = cur_loss = cur_win = 0
    loss_runs = Counter()
    win_runs = Counter()
    for row in ordered:
        pnl = float(row.get("net_pnl") or 0.0)
        if pnl < 0:
            if cur_win:
                win_runs[cur_win] += 1
                cur_win = 0
            cur_loss += 1
            max_loss = max(max_loss, cur_loss)
        elif pnl > 0:
            if cur_loss:
                loss_runs[cur_loss] += 1
                cur_loss = 0
            cur_win += 1
            max_win = max(max_win, cur_win)
        else:
            if cur_loss:
                loss_runs[cur_loss] += 1
                cur_loss = 0
            if cur_win:
                win_runs[cur_win] += 1
                cur_win = 0
    if cur_loss:
        loss_runs[cur_loss] += 1
    if cur_win:
        win_runs[cur_win] += 1
    return {
        "max_consecutive_losses": max_loss,
        "max_consecutive_wins": max_win,
        "loss_streak_distribution": {str(k): v for k, v in sorted(loss_runs.items())},
        "win_streak_distribution": {str(k): v for k, v in sorted(win_runs.items())},
    }


def _analysis(rows):
    rows = list(rows)
    start_ms = int(FIXED_START.timestamp() * 1000)
    segment_ms = int(timedelta(days=250).total_seconds() * 1000)
    by_segment = {}
    for idx in range(4):
        lo = start_ms + idx * segment_ms
        hi = start_ms + (idx + 1) * segment_ms
        selected = [r for r in rows if lo <= int(r.get("entry_time") or 0) < hi]
        by_segment[f"days_{idx * 250 + 1}_{(idx + 1) * 250}"] = _stats(selected)

    by_year = defaultdict(list)
    by_score = defaultdict(list)
    for r in rows:
        ts = int(r.get("entry_time") or 0) / 1000.0
        if ts > 0:
            by_year[str(datetime.fromtimestamp(ts, tz=timezone.utc).year)].append(r)
        by_score[f"{float(r.get('score') or 0.0):.1f}"].append(r)

    return {
        "long": _stats([r for r in rows if str(r.get("side") or "") == "做多"]),
        "short": _stats([r for r in rows if str(r.get("side") or "") == "做空"]),
        "by_250d_segment": by_segment,
        "by_calendar_year_utc": {k: _stats(v) for k, v in sorted(by_year.items())},
        "by_score": {k: _stats(v) for k, v in sorted(by_score.items(), key=lambda kv: float(kv[0]))},
        "streaks": _streak_analysis(rows),
    }


def _submit_v165(self, result, now_ms, mark):
    """Mirror current V1.6.5 first-entry execution on audited historical data."""
    opp = result.get("opportunity")
    if not isinstance(opp, dict):
        return
    side = str(opp.get("side") or "")
    score = (result.get("scores") or {}).get(side) or {}
    if side not in ("做多", "做空"):
        return
    if not score.get("eligible") or float(score.get("total") or 0.0) < production.THRESHOLD:
        return

    self.stats["eligible_states"] += 1
    if not research.variant_accept(self.variant, score, opp):
        self.stats["variant_filter_blocks"] += 1
        return
    if not self.can_submit(now_ms):
        return

    path = str(opp.get("signal_path") or opp.get("path") or "")
    if path not in production.OUTER_PATHS:
        self.stats["non_outer_blocks"] += 1
        return

    # Exact V1.6.5 lifecycle: old 5m package dies at the next 5m close.
    opened, _age_ms, _remaining_ms, _end_ms = production._signal_window(opp, now_ms)
    if not opened:
        self.stats["signal_cycle_expiry_blocks"] += 1
        return

    confirmations = score.get("confirmations") or {}
    required = confirmations.get("required") or {}
    if required and not all(bool(v) for v in required.values()):
        self.stats["required_gate_blocks"] += 1
        return
    if str(confirmations.get("4H_trend_state") or "") != "aligned":
        self.stats["four_hour_blocks"] += 1
        return
    rsi = production._finite(confirmations.get("rsi5"))
    if rsi is None or not (production.RSI_MIN <= rsi <= production.RSI_MAX):
        self.stats["rsi_blocks"] += 1
        return
    if not production._macd_improving(score):
        self.stats["macd_blocks"] += 1
        return

    ratio = production._finite(opp.get("five_signal_volume_ratio"))
    if ratio is None or ratio >= production.VOLUME_HARD_GATE:
        self.stats["volume_hard_gate_blocks"] += 1
        return

    entry = float(mark)
    stop_distance = float(opp["atr1h"]) * research.STOP_ATR
    if not math.isfinite(stop_distance) or stop_distance <= 0:
        self.stats["sizing_skips"] += 1
        return
    direction = research.side_dir(side)
    stop = entry - direction * stop_distance
    target = entry + direction * stop_distance * research.REWARD_R

    eq = self.equity(mark)
    risk_capital = min(research.CAPITAL, eq)
    base_risk = min(research.RISK_USDT, risk_capital * research.RISK_PCT / 100.0)
    risk_budget = min(base_risk, self.daily_remaining(now_ms, mark))
    if risk_budget <= 0:
        self.stats["sizing_skips"] += 1
        return

    maker = research.MAKER_BPS / 10000.0
    taker = research.TAKER_BPS / 10000.0
    slip = research.SLIPPAGE_BPS / 10000.0
    risk_entry_fee = max(maker, taker)
    per_btc = stop_distance + entry * risk_entry_fee + stop * (taker + slip)
    notional_cap = min(
        research.FIRST_SIGNAL_NOTIONAL,
        risk_capital * research.LEVERAGE,
        max(eq, 0.0) * 0.9 * research.LEVERAGE,
    )
    btc = research.round_contract_btc(min(risk_budget / per_btc, notional_cap / entry), self.meta)
    if btc <= 0:
        self.stats["sizing_skips"] += 1
        return

    expected_cost_per_btc = entry * maker + target * taker
    expected_fee_multiple = (
        2.0 * stop_distance / expected_cost_per_btc
        if expected_cost_per_btc > 0 else math.inf
    )
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
        "position_multiplier": 1.0,
        "base_position_notional_cap": research.FIRST_SIGNAL_NOTIONAL,
        "requested_notional_cap": research.FIRST_SIGNAL_NOTIONAL,
        "effective_notional_cap": notional_cap,
        "signal_volume_ratio": ratio,
        "signal_window_ms": production.ENTRY_WINDOW_MS,
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
        trigger_combination=dict(confirmations.get("trigger") or {}),
        front_r=float(diag.get("front_r", math.inf)),
        cost_r=float(diag.get("cost_r", math.inf)),
    )
    self.stats["limit_submitted"] += 1
    self.stats["v165_1x_submitted"] += 1


def _configure():
    start, end = FIXED_START, FIXED_END
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

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

    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms
    research.SPLIT_MS = int((start + (end - start) / 2).timestamp() * 1000)
    research.CAPITAL = CAPITAL
    research.LEVERAGE = LEVERAGE
    research.RISK_USDT = RISK_USDT
    research.RISK_PCT = RISK_PCT
    research.DAILY_LOSS = DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = BASE_POSITION_NOTIONAL
    research.STOP_ATR = STOP_ATR
    research.REWARD_R = REWARD_R
    research.COOLDOWN_MS = COOLDOWN_MS
    research.VERSION = "1.6.5"
    research.BUILD = "1650-1000d"

    production._patch_v164_base()
    research.model = production
    research.Simulator.submit = _submit_v165
    # Explicitly preserve V1.6.5 No-BE exits for the entire 1000D replay.
    research.Simulator.process_pending = _ORIGINAL_PROCESS_PENDING
    research.Simulator.process_exit = _ORIGINAL_PROCESS_EXIT
    research.Simulator.finish = _ORIGINAL_FINISH
    if hasattr(research, "build1544"):
        research.build1544.PATH_C_ENABLED = False
    return start, end


def _verify_lock():
    assert DAYS == 1000
    assert FIXED_START.isoformat() == "2023-12-19T20:20:00+00:00"
    assert FIXED_END.isoformat() == "2026-09-14T20:20:00+00:00"
    assert production.VERSION == "1.6.5"
    assert production.THRESHOLD == 6.0
    assert production.ENTRY_WINDOW_MS == 300_000 and production.TIME_WINDOW_ENABLED is True
    assert production.VOLUME_HARD_GATE == 1.20
    assert production.BOLL_OUTER_SCORE == 2.50
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert CAPITAL == 2_000.0 and BASE_POSITION_NOTIONAL == 2_000.0
    assert LEVERAGE == 5 and RISK_USDT == 20.0 and DAILY_LOSS == 60.0
    assert STOP_ATR == 1.0 and REWARD_R == 2.0 and BE_ENABLED is False
    assert COOLDOWN_MS == 0
    assert research.Simulator.process_pending is _ORIGINAL_PROCESS_PENDING
    assert research.Simulator.process_exit is _ORIGINAL_PROCESS_EXIT
    assert research.Simulator.finish is _ORIGINAL_FINISH

    probe = {"signal_close_ms": 1_000_000}
    assert production._signal_window(probe, 1_299_999)[0] is True
    assert production._signal_window(probe, 1_300_000)[0] is False


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V165_1000D_2000U_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} days={DAYS} capital={CAPITAL:.0f} "
        f"base_notional={BASE_POSITION_NOTIONAL:.0f} leverage={LEVERAGE} base_risk={RISK_USDT:.0f} "
        "outer_only=ON boll=2.5 signal_cycle=5m rsi=30..70 macd=required "
        "volume_score=0 volume_ge_1.2x20=HARD_GATE 4h=aligned threshold=6 "
        "position=1x limit=ON sl=1H_ATR tp=2R be=OFF",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    analysis = _analysis(sim.trades)
    metrics.update({
        "label": "KAYTRADE V1.6.5 latest production model — strict 1000D / 2000U",
        "release": "1.6.5",
        "release_build": 1650,
        "days": DAYS,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "capital": CAPITAL,
        "base_position_notional_usdt": BASE_POSITION_NOTIONAL,
        "leverage": LEVERAGE,
        "base_risk_usdt": RISK_USDT,
        "risk_pct": RISK_PCT,
        "daily_loss_usdt": DAILY_LOSS,
        "score_threshold": production.THRESHOLD,
        "outer_only": True,
        "middle_enabled": False,
        "boll_outer_score": production.BOLL_OUTER_SCORE,
        "signal_lifecycle": "latest closed 5m package valid until next 5m close",
        "signal_window_ms": production.ENTRY_WINDOW_MS,
        "volume_score_enabled": False,
        "volume_hard_gate_enabled": True,
        "volume_ratio_threshold": production.VOLUME_HARD_GATE,
        "outer_rsi_min": production.RSI_MIN,
        "outer_rsi_max": production.RSI_MAX,
        "outer_macd_improving_required": True,
        "outer_4h_aligned_required": True,
        "position_multiplier": 1.0,
        "entry_order_type": "LIMIT",
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": STOP_ATR,
        "reward_r": REWARD_R,
        "be_enabled": False,
        "path_c_enabled": False,
        "cooldown_minutes": 0,
        "loss_pause_enabled": False,
        "analysis": analysis,
    })

    outdir = Path("backtest_output_v165_1000d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_1000d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "analysis_1000d.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    prior._write_trade_csv(sim.trades, outdir / "trades_1000d.csv")

    report = [
        "# KAYTRADE V1.6.5 — 1000D / 2000U Backtest",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Strategy lock",
        "- Exact V1.6.5 production model; latest closed candles only.",
        "- 5m BOLL outer only; signal package valid until the next closed 5m candle; BOLL=2.5 points.",
        "- Signal RSI 30-70; formal 5m MACD improving; 4H aligned mandatory.",
        "- Volume does not score; signal Volume >=1.20x previous-20 5m average blocks opening.",
        "- Score >=6.0; fixed 1x LIMIT; 1x 1H ATR SL; full-position 2R TP; BE off.",
        "- Capital 2,000U; 5x leverage; base risk 20U; daily loss budget 60U.",
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
        f"- Max consecutive losses: {analysis['streaks']['max_consecutive_losses']}",
        "",
        "Historical simulation only. LIMIT fills use the audited closed-1m proxy; historical order-book queue position is unavailable.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V165_1000D_2000U_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

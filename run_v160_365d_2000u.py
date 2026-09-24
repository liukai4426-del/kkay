#!/usr/bin/env python3
"""KAYTRADE V1.6.0 rolling 365-day BTC-USDT-SWAP backtest, 2,000U profile.

Research only. Uses the audited Build1544 simulator/data layer while routing all
entry evaluation and pre-submit checks through the current V1.6.0 production
model. V1.6.0 path-dependent sizing is reproduced explicitly:
- middle_rsi: 1x base position;
- lower_band / upper_band: one single 2x base LIMIT entry.

2,000U profile semantics follow the project's earlier 2,000U research runs:
- strategy capital/equity: 2,000U;
- base position notional cap: 2,000U;
- leverage: 5x;
- base risk: 1% = 20U;
- daily loss budget: 3% = 60U;
- outer-band entry doubles both risk budget and notional cap for that one order,
  matching v160_update_patch.py.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_v1544_180d_fixed as compat

research = compat.research
import v160_model as production

DAYS = 365
CAPITAL = 2_000.0
BASE_POSITION_NOTIONAL = 2_000.0
LEVERAGE = 5
RISK_USDT = 20.0
RISK_PCT = 1.0
DAILY_LOSS = 60.0
STOP_ATR = 1.0
REWARD_R = 2.0
COOLDOWN_MS = 0


def _write_trade_csv(rows, path: Path):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for row in rows:
            out = dict(row)
            for key, value in list(out.items()):
                if isinstance(value, (dict, list, tuple)):
                    out[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            w.writerow(out)


def _submit_v160(self, result, now_ms, mark):
    """Mirror V1.6.0 live pre-submit sizing on the audited historical simulator."""
    opp = result.get("opportunity")
    if not isinstance(opp, dict):
        return
    side = str(opp.get("side") or "")
    score = (result.get("scores") or {}).get(side) or {}
    if not score.get("eligible") or float(score.get("total") or 0.0) < production.THRESHOLD:
        return

    self.stats["eligible_states"] += 1
    if not research.variant_accept(self.variant, score, opp):
        self.stats["variant_filter_blocks"] += 1
        return
    if not self.can_submit(now_ms):
        return

    path = str(opp.get("signal_path") or "")
    multiplier = 2.0 if path in production.OUTER_PATHS else 1.0

    # Defensive repeat of the production middle MACD hard gate. The model should
    # already make this row ineligible, but live V1.6.0 also rechecks pre-submit.
    if path == production.MIDDLE_PATH and not production._macd_improving(score):
        self.stats["middle_macd_presubmit_blocks"] += 1
        return

    entry = float(mark)
    stop_distance = float(opp["atr1h"]) * research.STOP_ATR
    direction = research.side_dir(side)
    stop = entry - direction * stop_distance
    target = entry + direction * stop_distance * research.REWARD_R

    eq = self.equity(mark)
    risk_capital = min(research.CAPITAL, eq)
    base_risk = min(research.RISK_USDT, risk_capital * research.RISK_PCT / 100.0)
    risk_budget = min(base_risk * multiplier, self.daily_remaining(now_ms, mark))
    if risk_budget <= 0:
        self.stats["sizing_skips"] += 1
        return

    maker = research.MAKER_BPS / 10000.0
    taker = research.TAKER_BPS / 10000.0
    slip = research.SLIPPAGE_BPS / 10000.0
    risk_entry_fee = max(maker, taker)
    per_btc = stop_distance + entry * risk_entry_fee + stop * (taker + slip)

    # V1.6.0 outer-band entry doubles the configured fixed notional cap in the
    # same single order. It is still bounded by strategy leverage/equity limits.
    requested_notional_cap = research.FIRST_SIGNAL_NOTIONAL * multiplier
    notional_cap = min(
        requested_notional_cap,
        risk_capital * research.LEVERAGE,
        max(eq, 0.0) * 0.9 * research.LEVERAGE,
    )
    btc = research.round_contract_btc(
        min(risk_budget / per_btc, notional_cap / entry), self.meta
    )
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

    worst_roundtrip_cost = btc * (
        entry * max(maker, taker) + target * (taker + slip)
    )
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
        "position_multiplier": multiplier,
        "base_position_notional_cap": research.FIRST_SIGNAL_NOTIONAL,
        "requested_notional_cap": requested_notional_cap,
        "effective_notional_cap": notional_cap,
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
        trigger_combination=dict((score.get("confirmations") or {}).get("trigger") or {}),
        front_r=float(diag.get("front_r", math.inf)),
        cost_r=float(diag.get("cost_r", math.inf)),
    )
    self.stats["limit_submitted"] += 1
    if multiplier == 2.0:
        self.stats["outer_2x_submitted"] += 1
    else:
        self.stats["middle_1x_submitted"] += 1


def _configure():
    # Freeze the run to the latest completed-minute boundary when the job starts.
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms
    research.SPLIT_MS = int((end - timedelta(days=60)).timestamp() * 1000)
    research.CAPITAL = CAPITAL
    research.LEVERAGE = LEVERAGE
    research.RISK_USDT = RISK_USDT
    research.RISK_PCT = RISK_PCT
    research.DAILY_LOSS = DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = BASE_POSITION_NOTIONAL
    research.STOP_ATR = STOP_ATR
    research.REWARD_R = REWARD_R
    research.COOLDOWN_MS = COOLDOWN_MS
    research.VERSION = "1.6.0"
    research.BUILD = "1600"

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

    # Route the audited simulator through the exact latest production model.
    research.model = production
    research.Simulator.submit = _submit_v160

    return start, end


def _verify_lock():
    assert production.VERSION == "1.6.0"
    assert production.THRESHOLD == 6.0
    assert production.ENTRY_WINDOW_MS == 0
    assert production.TIME_WINDOW_ENABLED is False
    assert research.build1544.PATH_C_ENABLED is False
    assert research.COOLDOWN_MS == 0
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0
    assert research.CAPITAL == 2_000.0
    assert research.FIRST_SIGNAL_NOTIONAL == 2_000.0
    assert research.RISK_USDT == 20.0 and research.DAILY_LOSS == 60.0

    middle_blocked = {
        "total": 6.5,
        "gate": True,
        "eligible": True,
        "position_multiplier": 1.0,
        "layers": {"macd_improving": 0.0},
        "confirmations": {"required": {}},
        "reason": "ok",
        "level": "ok",
    }
    production._apply_row_rules(middle_blocked, "middle_rsi")
    assert middle_blocked["eligible"] is False
    assert middle_blocked["position_multiplier"] == 0.0

    outer = {
        "total": 6.5,
        "gate": True,
        "eligible": True,
        "position_multiplier": 1.0,
        "layers": {"macd_improving": 0.0},
        "confirmations": {"required": {}},
        "reason": "ok",
        "level": "ok",
    }
    production._apply_row_rules(outer, "upper_band")
    assert outer["eligible"] is True
    assert outer["position_multiplier"] == 2.0
    assert "middle_macd_improving" not in outer["confirmations"]["required"]


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V160_365D_2000U_CONFIG "
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
        "label": "KAYTRADE V1.6.0 latest production model — 365D 2000U profile",
        "release": "1.6.0",
        "release_build": 1600,
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
        "score_threshold": 6.0,
        "middle_macd_required": True,
        "middle_macd_definition": "existing formal 5m macd_improving",
        "path_c_enabled": False,
        "cooldown_minutes": 0,
        "loss_pause_enabled": False,
        "entry_order_type": "LIMIT",
        "boll_signal_wall_clock_expiry": False,
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
    })

    outdir = Path("backtest_output_v160_365d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_365d.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_trade_csv(sim.trades, outdir / "trades_365d.csv")

    report = [
        "# KAYTRADE V1.6.0 — 365D / 2000U Backtest",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Profile",
        "- Strategy capital/equity: 2,000 U.",
        "- Base position notional cap: 2,000 U.",
        "- Middle path: 1x base position; existing 5m macd_improving is mandatory.",
        "- Lower/upper outer bands: one 2x base LIMIT entry (4,000 U requested cap).",
        "- 5x leverage; 1% base risk = 20 U; outer risk = 40 U before daily cap.",
        "- Daily loss budget: 60 U (3%).",
        "- Score >= 6; Path C OFF; cooldown OFF; loss-pause OFF.",
        "- Stop = 1x 1H ATR; full-position TP = 2R.",
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
        "Historical simulation only. Historical LIMIT fills use the audited closed-1m proxy, not historical orderbook bid/ask.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V160_365D_2000U_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

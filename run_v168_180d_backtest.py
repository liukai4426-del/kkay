#!/usr/bin/env python3
"""KAYTRADE V1.6.8 Build1680 production-faithful 180D BTC backtest.

This replay keeps the current V1.6.8 model active and reuses the project's
audited historical simulator / LIMIT-fill proxy. Strategy lock:
- latest CLOSED 15m BOLL outer only; no 5m BOLL path;
- 15m signal lifecycle (900000 ms, until the next 15m close);
- BOLL outer score = 2.0;
- 4H aligned = +1 and mandatory Hard Gate;
- 1H EMA9/26 trend = +1 scoring-only;
- 5m MACD explicit-adverse-only Hard Gate, improvement keeps +1;
- 5m KDJ does not score;
- 5m RSI 30..70 and Volume <1.20x hard gates;
- threshold >=6, fixed 1x LIMIT, 1H ATR x1 SL, full 2R TP, No-BE.

Historical LIMIT fills use the audited closed-1m proxy; order-book queue
position is unavailable historically.
"""
from __future__ import annotations

import csv
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import v168_update_patch as v168
import run_v165_1000d_2000u as v165runner
from research_backtest_data_cache import load_market

production = v168.model
research = v165runner.research

DAYS = 180
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
FIXED_END = v165runner.FIXED_END
FIXED_START = FIXED_END - timedelta(days=DAYS)
TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H")
OUT = Path("backtest_output_v168_180d")


def _finite(value, default=0.0):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _pf(rows):
    gp = sum(max(_finite(r.get("net_pnl")), 0.0) for r in rows)
    gl = -sum(min(_finite(r.get("net_pnl")), 0.0) for r in rows)
    return gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0)


def _stats(rows):
    rows = list(rows)
    n = len(rows)
    wins = sum(_finite(r.get("net_pnl")) > 0 for r in rows)
    net = sum(_finite(r.get("net_pnl")) for r in rows)
    gross = sum(_finite(r.get("gross_pnl")) for r in rows)
    fees = sum(_finite(r.get("entry_fee")) + _finite(r.get("exit_fee")) for r in rows)
    funding = sum(_finite(r.get("funding_pnl")) for r in rows)
    return {
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate_pct": 100.0 * wins / n if n else 0.0,
        "net_pnl": net,
        "gross_pnl": gross,
        "fees": fees,
        "funding_pnl": funding,
        "profit_factor": _pf(rows),
        "expectancy": net / n if n else 0.0,
        "avg_hold_min": sum(_finite(r.get("hold_min")) for r in rows) / n if n else 0.0,
        "avg_score": sum(_finite(r.get("score")) for r in rows) / n if n else 0.0,
    }


def _streaks(rows):
    ordered = sorted(rows, key=lambda r: int(r.get("entry_time") or 0))
    max_loss = max_win = cur_loss = cur_win = 0
    for row in ordered:
        pnl = _finite(row.get("net_pnl"))
        if pnl < 0:
            cur_loss += 1
            cur_win = 0
            max_loss = max(max_loss, cur_loss)
        elif pnl > 0:
            cur_win += 1
            cur_loss = 0
            max_win = max(max_win, cur_win)
        else:
            cur_loss = cur_win = 0
    return {"max_consecutive_losses": max_loss, "max_consecutive_wins": max_win}


def _analysis(rows):
    rows = list(rows)
    by_side = defaultdict(list)
    by_score = defaultdict(list)
    by_month = defaultdict(list)
    by_ema = defaultdict(list)
    by_h4 = defaultdict(list)
    entry_delay = defaultdict(list)
    for row in rows:
        by_side[str(row.get("side") or "unknown")].append(row)
        by_score[f"{_finite(row.get('score')):.1f}"].append(row)
        ts = int(row.get("entry_time") or 0) / 1000.0
        if ts > 0:
            by_month[datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")].append(row)
        factors = row.get("factors") or {}
        by_ema["aligned" if bool(factors.get("ema9_26_1h_ok")) else "not_aligned"].append(row)
        by_h4[str(factors.get("4h_trend_state_actual") or "unknown")].append(row)
        delay_min = _finite(factors.get("signal_age_min"), -1.0)
        if delay_min >= 0:
            bucket = "0-5m" if delay_min < 5 else "5-10m" if delay_min < 10 else "10-15m"
            entry_delay[bucket].append(row)
    return {
        "by_side": {k: _stats(v) for k, v in by_side.items()},
        "by_score": {k: _stats(v) for k, v in sorted(by_score.items(), key=lambda kv: float(kv[0]))},
        "by_month_utc": {k: _stats(v) for k, v in sorted(by_month.items())},
        "by_1h_ema9_26": {k: _stats(v) for k, v in by_ema.items()},
        "by_4h_state": {k: _stats(v) for k, v in by_h4.items()},
        "by_signal_age": {k: _stats(v) for k, v in entry_delay.items()},
        "streaks": _streaks(rows),
    }


def _write_csv(rows, path):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            item = dict(row)
            for key, value in list(item.items()):
                if isinstance(value, (dict, list, tuple)):
                    item[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            writer.writerow(item)


def _submit_v168(self, result, now_ms, mark):
    """Use the audited V1.6.5 submit proxy with the live V1.6.8 model module."""
    v168._pin_v168_runtime()
    # v165runner.production and v168.model are the same v165_model module object,
    # mutated by the V1.6.8 overlay. The legacy submit function therefore calls
    # the current 15m window, current adverse-only MACD gate, and V1.6.8 checks.
    assert v165runner.production is production
    before = self.pending
    v165runner._submit_v165(self, result, now_ms, mark)
    if self.pending is None or self.pending is before:
        return
    opp = result.get("opportunity") or {}
    side = str(opp.get("side") or "")
    row = (result.get("scores") or {}).get(side) or {}
    conf = row.get("confirmations") or {}
    factors = self.pending.factors
    factors["strategy_version"] = "1.6.8"
    factors["build"] = "1680"
    factors["boll_timeframe"] = "15m"
    factors["signal_window_ms"] = production.ENTRY_WINDOW_MS
    factors["boll_outer_score"] = production.BOLL_OUTER_SCORE
    factors["4h_trend_state_actual"] = str(conf.get("4H_trend_state") or "")
    factors["4h_aligned_hard_gate"] = str(conf.get("4H_trend_state") or "") == "aligned"
    factors["ema9_26_1h_ok"] = bool(conf.get("1H_ema9_26_trend_ok"))
    factors["ema9_1h"] = conf.get("1H_ema9")
    factors["ema26_1h"] = conf.get("1H_ema26")
    factors["macd_explicit_adverse"] = bool(conf.get("5m_macd_adverse"))
    try:
        signal_close = int(opp.get("signal_close_ms") or 0)
        factors["signal_age_min"] = max(0.0, (int(now_ms) - signal_close) / 60000.0) if signal_close else None
    except (TypeError, ValueError):
        factors["signal_age_min"] = None


def configure():
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
    research.VERSION = "1.6.8"
    research.BUILD = "1680-180d"

    v168._pin_v168_runtime()
    research.model = production
    research.Simulator.submit = _submit_v168
    research.Simulator.process_pending = v165runner._ORIGINAL_PROCESS_PENDING
    research.Simulator.process_exit = v165runner._ORIGINAL_PROCESS_EXIT
    research.Simulator.finish = v165runner._ORIGINAL_FINISH
    if hasattr(research, "build1544"):
        research.build1544.PATH_C_ENABLED = False
    return start, end


def verify_lock():
    v168._pin_v168_runtime()
    assert DAYS == 180
    assert (FIXED_END - FIXED_START).days == 180
    assert v168.VERSION == "1.6.8" and v168.BUILD == "1680"
    assert production.VERSION == "1.6.8" and production.BUILD == "1680"
    assert production.THRESHOLD == 6.0
    assert production.ENTRY_WINDOW_MS == 900_000
    assert production.BOLL_OUTER_SCORE == 2.0
    assert production.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert production.FIVE_MINUTE_BOLL_ENABLED is False
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.VOLUME_HARD_GATE == 1.20
    assert v168.EMA_FAST == 9 and v168.EMA_SLOW == 26
    assert v168._ema_trend_ok("做多", 101.0, 100.0)
    assert v168._ema_trend_ok("做空", 99.0, 100.0)
    assert not v168._ema_trend_ok("做空", 101.0, 100.0)
    assert CAPITAL == 2000.0 and LEVERAGE == 5 and RISK_USDT == 20.0
    assert STOP_ATR == 1.0 and REWARD_R == 2.0 and BE_ENABLED is False
    probe = {"signal_close_ms": 1_000_000}
    assert production._signal_window(probe, 1_899_999)[0] is True
    assert production._signal_window(probe, 1_900_000)[0] is False
    assert research.Simulator.process_exit is v165runner._ORIGINAL_PROCESS_EXIT


def main():
    started = time.perf_counter()
    start, end = configure()
    verify_lock()
    OUT.mkdir(parents=True, exist_ok=True)
    print(
        "V168_180D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "boll=15m_outer_only", "boll_score=2.0", "signal_lifecycle=15m",
        "4h=aligned_required_plus1", "ema1h=EMA9_26_plus1",
        "macd=5m_explicit_adverse_only_plus1_if_improving", "kdj_score=0",
        "rsi=5m_30..70", "volume_lt_1.20x=HARD_GATE", "threshold=6",
        "position=1x", "entry=LIMIT", "sl=1H_ATR_x1", "tp=2R", "be=OFF",
        flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = load_market(research.base, TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim_started = time.perf_counter()
    sim, raw_metrics = research.run(data, ts, meta, funding, variant="baseline")
    sim_elapsed = time.perf_counter() - sim_started
    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    analysis = _analysis(rows)
    elapsed = time.perf_counter() - started

    payload = {
        "research": "KAYTRADE V1.6.8 Build1680 production-faithful 180D baseline",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": CAPITAL,
        "strategy_lock": {
            "version": "1.6.8",
            "build": "1680",
            "boll_timeframe": "15m latest CLOSED outer only",
            "five_minute_boll_enabled": False,
            "boll_signal_lifetime_ms": production.ENTRY_WINDOW_MS,
            "boll_signal_lifetime": "until next closed 15m candle",
            "boll_outer_score": production.BOLL_OUTER_SCORE,
            "4h": "aligned mandatory Hard Gate and +1 score; neutral/opposite blocked",
            "1h_ema": "EMA9>EMA26 long / EMA9<EMA26 short = +1; scoring only",
            "5m_macd": "explicit adverse/opposite blocks; improvement +1 retained",
            "5m_kdj_score": 0.0,
            "5m_rsi": "30-70 Hard Gate",
            "5m_volume": "<1.20x prior-20 average Hard Gate; no score",
            "score_threshold": production.THRESHOLD,
            "position": "fixed 1x",
            "entry": "LIMIT, audited closed-1m historical fill proxy",
            "stop": "1H ATR x1",
            "tp": "2R full position",
            "break_even": False,
            "early_exit": False,
        },
        "metrics": {k: metrics.get(k) for k in (
            "trades", "wins", "losses", "win_rate_pct", "ending_equity", "net_pnl",
            "net_return_pct", "profit_factor", "expectancy", "max_drawdown_usdt",
            "max_drawdown_pct", "fees", "funding_pnl"
        )},
        "analysis": analysis,
        "simulator_stats": dict(sim.stats),
        "top_blockers": Counter(sim.blockers).most_common(30),
        "cache": cache_manifest,
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": elapsed},
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m proxy; order-book queue position is unavailable.",
            "This run replays the exact committed V1.6.8 Build1680 model state on the research branch."
        ],
    }

    _write_csv(rows, OUT / "trades_v168_180d.csv")
    (OUT / "result_v168_180d.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "metrics_raw_v168_180d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    m = payload["metrics"]
    report = [
        "# KAYTRADE V1.6.8 Build1680 — 180D Backtest",
        "",
        f"Window: {start.isoformat()} → {end.isoformat()}",
        "",
        "## Strategy lock",
        "- 15m BOLL outer only, 2.0 points, valid until next 15m close.",
        "- 4H aligned is mandatory and +1; 1H EMA9/26 aligned trend is +1 scoring-only.",
        "- 5m MACD explicit-adverse-only gate; improvement +1; KDJ no score.",
        "- 5m RSI 30–70; Volume <1.20x prior-20 average; threshold >=6.",
        "- Fixed 1x LIMIT, 1H ATR x1 SL, full 2R TP, No-BE, no Early Exit.",
        "",
        "## Result",
        f"- Trades: {m.get('trades')}",
        f"- Wins / losses: {m.get('wins')} / {m.get('losses')}",
        f"- Win rate: {_finite(m.get('win_rate_pct')):.2f}%",
        f"- Net PnL: {_finite(m.get('net_pnl')):+.2f} U ({_finite(m.get('net_return_pct')):+.2f}%)",
        f"- Profit factor: {_finite(m.get('profit_factor')):.3f}",
        f"- Expectancy: {_finite(m.get('expectancy')):+.3f} U/trade",
        f"- Max DD: {_finite(m.get('max_drawdown_usdt')):.2f} U ({_finite(m.get('max_drawdown_pct')):.2f}%)",
        f"- Fees: {_finite(m.get('fees')):.2f} U; Funding: {_finite(m.get('funding_pnl')):+.2f} U",
        "",
        "Historical simulation only."
    ]
    (OUT / "REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print("V168_180D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"V168_180D_TIMING total={elapsed:.2f}s market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    main()

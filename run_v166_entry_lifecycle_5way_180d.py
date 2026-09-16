#!/usr/bin/env python3
"""KAYTRADE V1.6.6 research: 5-way entry-lifecycle A/B/C/D/E over 180D.

Only one variable changes versus exact V1.6.5 baseline: the lifetime of a valid
5m BOLL outer-band opportunity. Everything else stays locked:
- outer only, BOLL 2.5 points
- RSI 30..70
- formal 5m MACD improvement required
- 4H aligned required
- Volume score off; current Volume hard-gate semantics unchanged
- score >= 6.0
- fixed 1x LIMIT entry
- 1H ATR x1 stop, full 2R TP, BE off
- no Early Exit

Variants:
- 5m      : current V1.6.5 lifecycle
- 15m     : 15-minute rolling opportunity lifetime
- 20m     : 20-minute rolling opportunity lifetime
- 30m     : 30-minute rolling opportunity lifetime
- noexpiry: effectively no wall-clock expiry within the research horizon

Historical LIMIT fills use the existing audited closed-1m proxy.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import timedelta
from pathlib import Path

import run_v165_1000d_2000u as base

research = base.research
production = base.production
DAYS = 180
NOEXPIRY_MS = 10 * 365 * 24 * 60 * 60_000
VARIANTS = (
    ("5m", 5 * 60_000),
    ("15m", 15 * 60_000),
    ("20m", 20 * 60_000),
    ("30m", 30 * 60_000),
    ("noexpiry", NOEXPIRY_MS),
)
_ORIGINAL_PROCESS_PENDING = base._ORIGINAL_PROCESS_PENDING
_ORIGINAL_PROCESS_EXIT = base._ORIGINAL_PROCESS_EXIT
_ORIGINAL_FINISH = base._ORIGINAL_FINISH


def _configure():
    end = base.FIXED_END
    start = end - timedelta(days=DAYS)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    for name, value in {
        "START": start,
        "END": end,
        "START_MS": start_ms,
        "END_MS": end_ms,
        "CAPITAL": base.CAPITAL,
        "LEVERAGE": base.LEVERAGE,
        "RISK_USDT": base.RISK_USDT,
        "RISK_PCT": base.RISK_PCT,
        "DAILY_LOSS": base.DAILY_LOSS,
        "COOLDOWN_MINUTES": 0,
        "STOP_ATR": base.STOP_ATR,
        "REWARD_R": base.REWARD_R,
    }.items():
        setattr(research.base, name, value)
    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms
    research.SPLIT_MS = int((start + (end - start) / 2).timestamp() * 1000)
    research.CAPITAL = base.CAPITAL
    research.LEVERAGE = base.LEVERAGE
    research.RISK_USDT = base.RISK_USDT
    research.RISK_PCT = base.RISK_PCT
    research.DAILY_LOSS = base.DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = base.BASE_POSITION_NOTIONAL
    research.STOP_ATR = base.STOP_ATR
    research.REWARD_R = base.REWARD_R
    research.COOLDOWN_MS = base.COOLDOWN_MS
    research.VERSION = "1.6.6-research"
    research.BUILD = "1660-entry-lifecycle-5way-180d"
    research.model = production
    research.Simulator.submit = base._submit_v165
    research.Simulator.process_pending = _ORIGINAL_PROCESS_PENDING
    research.Simulator.process_exit = _ORIGINAL_PROCESS_EXIT
    research.Simulator.finish = _ORIGINAL_FINISH
    if hasattr(research, "build1544"):
        research.build1544.PATH_C_ENABLED = False
    return start, end


def _set_window(window_ms: int):
    production.ENTRY_WINDOW_MS = int(window_ms)
    production.TIME_WINDOW_ENABLED = True
    production._patch_v164_base()


def _verify_lock(start, end):
    assert (end - start).days == DAYS
    assert end.isoformat() == "2026-09-14T20:20:00+00:00"
    assert production.VERSION == "1.6.5"
    assert production.THRESHOLD == 6.0
    assert production.BOLL_OUTER_SCORE == 2.5
    assert production.VOLUME_HARD_GATE == 1.2
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert base.CAPITAL == 2000.0 and base.BASE_POSITION_NOTIONAL == 2000.0
    assert base.LEVERAGE == 5 and base.RISK_USDT == 20.0
    assert base.STOP_ATR == 1.0 and base.REWARD_R == 2.0 and base.BE_ENABLED is False
    assert base.COOLDOWN_MS == 0
    assert research.Simulator.process_exit is _ORIGINAL_PROCESS_EXIT


def _write_rows(rows, path):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _pf(rows):
    gp = sum(max(float(r.get("net_pnl") or 0.0), 0.0) for r in rows)
    gl = -sum(min(float(r.get("net_pnl") or 0.0), 0.0) for r in rows)
    if gl <= 0:
        return math.inf if gp > 0 else 0.0
    return gp / gl


def _subset_stats(rows):
    rows = list(rows)
    n = len(rows)
    wins = sum(float(r.get("net_pnl") or 0.0) > 0 for r in rows)
    pnl = sum(float(r.get("net_pnl") or 0.0) for r in rows)
    return {
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate_pct": wins / n * 100.0 if n else 0.0,
        "net_pnl": pnl,
        "profit_factor": _pf(rows),
        "expectancy": pnl / n if n else 0.0,
        "long_trades": sum(str(r.get("side") or "") == "做多" for r in rows),
        "short_trades": sum(str(r.get("side") or "") == "做空" for r in rows),
    }


def _age_stats(rows):
    ages = sorted(float(r.get("signal_age_min") or 0.0) for r in rows)
    if not ages:
        return {"mean_min": 0.0, "median_min": 0.0, "p90_min": 0.0, "gt5_count": 0, "gt10_count": 0, "gt15_count": 0}
    n = len(ages)
    def pct(p):
        idx = min(n - 1, max(0, math.ceil(p * n) - 1))
        return ages[idx]
    return {
        "mean_min": sum(ages) / n,
        "median_min": pct(0.50),
        "p90_min": pct(0.90),
        "gt5_count": sum(x > 5.0 for x in ages),
        "gt10_count": sum(x > 10.0 for x in ages),
        "gt15_count": sum(x > 15.0 for x in ages),
    }


def _metric_summary(m):
    return {k: m[k] for k in (
        "trades", "wins", "losses", "win_rate_pct", "net_pnl", "net_return_pct",
        "profit_factor", "expectancy", "max_drawdown_usdt", "max_drawdown_pct",
        "fees", "funding_pnl", "avg_hold_min", "median_hold_min",
    ) if k in m}


def main():
    start, end = _configure()
    _verify_lock(start, end)
    print(
        f"V166_ENTRY_LIFECYCLE_5WAY_180D start={start.isoformat()} end={end.isoformat()} "
        "variants=5m,15m,20m,30m,noexpiry; all non-lifecycle strategy rules locked",
        flush=True,
    )

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    out = Path("backtest_output_v166_entry_lifecycle_5way_180d")
    out.mkdir(parents=True, exist_ok=True)
    sims = {}
    metrics = {}
    summaries = {}

    for name, window_ms in VARIANTS:
        _set_window(window_ms)
        research.Simulator.process_pending = _ORIGINAL_PROCESS_PENDING
        research.Simulator.process_exit = _ORIGINAL_PROCESS_EXIT
        research.Simulator.finish = _ORIGINAL_FINISH
        sim, m = research.run(data, ts, meta, funding, variant="baseline")
        m = dict(m)
        rows = list(sim.trades)
        m.update({
            "label": f"V1.6.6 research entry lifecycle {name} — 180D",
            "release_base": "1.6.5",
            "research_version": "1.6.6-entry-lifecycle-5way",
            "days": DAYS,
            "start_utc": start.isoformat(),
            "end_utc": end.isoformat(),
            "capital": base.CAPITAL,
            "leverage": base.LEVERAGE,
            "base_risk_usdt": base.RISK_USDT,
            "score_threshold": production.THRESHOLD,
            "outer_only": True,
            "boll_outer_score": production.BOLL_OUTER_SCORE,
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
            "stop_atr_multiplier": 1.0,
            "reward_r": 2.0,
            "be_enabled": False,
            "early_exit_enabled": False,
            "entry_lifecycle_variant": name,
            "entry_window_ms": int(window_ms),
            "entry_window_minutes": None if name == "noexpiry" else int(window_ms // 60_000),
            "no_wall_clock_expiry": name == "noexpiry",
            "signal_age_stats": _age_stats(rows),
        })
        sims[name] = rows
        metrics[name] = m
        summaries[name] = _metric_summary(m) | {"signal_age_stats": m["signal_age_stats"]}
        (out / f"metrics_{name}_180d.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        base.prior._write_trade_csv(rows, out / f"trades_{name}_180d.csv")
        print("VARIANT_RESULT", name, json.dumps(summaries[name], ensure_ascii=False), flush=True)

    baseline_rows = sims["5m"]
    baseline_ids = {str(r.get("opportunity_id") or "") for r in baseline_rows if r.get("opportunity_id")}
    incremental = {}
    for name, _window_ms in VARIANTS:
        rows = sims[name]
        ids = {str(r.get("opportunity_id") or "") for r in rows if r.get("opportunity_id")}
        added = [r for r in rows if str(r.get("opportunity_id") or "") not in baseline_ids]
        lost = [r for r in baseline_rows if str(r.get("opportunity_id") or "") not in ids]
        incremental[name] = {
            "added_vs_5m": _subset_stats(added),
            "lost_from_5m": _subset_stats(lost),
            "shared_opportunity_ids": len(ids & baseline_ids),
            "variant_unique_ids": len(ids - baseline_ids),
            "baseline_unique_ids_missing": len(baseline_ids - ids),
        }
        _write_rows(added, out / f"added_vs_5m_{name}_180d.csv")
        _write_rows(lost, out / f"lost_vs_5m_{name}_180d.csv")

    comparison = {
        "research": "V1.6.6 entry-lifecycle 5-way 180D",
        "window": {"start_utc": start.isoformat(), "end_utc": end.isoformat(), "days": DAYS},
        "locked_strategy": {
            "release_base": "1.6.5",
            "outer_only": True,
            "boll_outer_score": 2.5,
            "rsi": "30..70",
            "macd_improving_required": True,
            "4h_aligned_required": True,
            "volume_score": 0.0,
            "volume_hard_gate_threshold": 1.2,
            "score_threshold": 6.0,
            "position_multiplier": 1.0,
            "stop": "1H ATR x1",
            "target": "2R full position",
            "be": False,
            "early_exit": False,
        },
        "variants": summaries,
        "incremental_vs_5m": incremental,
    }
    (out / "comparison_180d.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# V1.6.6 Research — Entry Lifecycle 5-Way — 180D",
        "",
        f"Window: `{start.isoformat()} -> {end.isoformat()}`",
        "",
        "Only entry-opportunity lifetime changes. All V1.6.5 technical gates/risk/exits remain locked. Early Exit is OFF.",
        "",
        "| Variant | Trades | WR | Net PnL | PF | Expectancy | Max DD | Added vs 5m | Added PF | Added PnL |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, _ in VARIANTS:
        s = summaries[name]
        a = incremental[name]["added_vs_5m"]
        lines.append(
            f"| {name} | {s.get('trades',0)} | {s.get('win_rate_pct',0):.2f}% | {s.get('net_pnl',0):+.2f}U | "
            f"{s.get('profit_factor',0):.3f} | {s.get('expectancy',0):+.3f} | {s.get('max_drawdown_usdt',0):.2f}U | "
            f"{a['trades']} | {a['profit_factor']:.3f} | {a['net_pnl']:+.2f}U |"
        )
    lines += [
        "",
        "Primary decision rule: prefer lifecycle extensions whose added-vs-5m orders remain independently profitable (PF > 1 and positive expectancy), rather than choosing only by total PnL.",
        "",
        "Historical simulation only. LIMIT fills use the audited closed-1m proxy.",
    ]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    print("V166_ENTRY_LIFECYCLE_5WAY_COMPARISON")
    print(json.dumps(comparison, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", out.resolve(), flush=True)


if __name__ == "__main__":
    main()

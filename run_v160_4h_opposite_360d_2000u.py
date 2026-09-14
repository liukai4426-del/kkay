#!/usr/bin/env python3
"""360D / 2000U validation: V1.6.0 baseline + 4H opposite hard lock only.

The window is frozen to the prior 360D research window so A/B comparison uses
identical dates. Market data is cached on disk and all timeframes are fetched in
parallel on a cache miss to reduce wall-clock time.
"""
from __future__ import annotations

import gzip
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_v160_365d_2000u as old
import research_v160_4h_opposite_lock_model as production

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
FIXED_END = datetime(2026, 9, 14, 16, 27, tzinfo=timezone.utc)
FIXED_START = FIXED_END - timedelta(days=DAYS)
CACHE_DIR = Path(os.environ.get("KAYTRADE_MARKET_CACHE_DIR", ".cache/kaytrade-360d-20260914-1627"))


def _configure():
    old.DAYS = DAYS
    old.production = production
    # Install the audited V1.6.0 submitter/model plumbing first.
    old._configure()

    start, end = FIXED_START, FIXED_END
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms
    research.SPLIT_MS = int((end - timedelta(days=60)).timestamp() * 1000)
    research.VERSION = production.VERSION
    research.BUILD = "R160-4HLOCK"
    research.model = production

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
    assert production.VERSION == "1.6.0-research-4h-opposite-lock"
    assert production.THRESHOLD == 6.0
    assert production.SCORE_MAX == 10.0
    assert production.ENTRY_WINDOW_MS == 0
    assert production.TIME_WINDOW_ENABLED is False
    assert research.build1544.PATH_C_ENABLED is False
    assert research.COOLDOWN_MS == 0
    # Original V1.6.0 middle MACD rule is retained.
    assert production._macd_improving({"layers": {"macd_improving": 1.0}})
    assert not production._macd_improving({"layers": {"macd_improving": 0.0}})


def _cache_file(name):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{name}.json.gz"


def _load_cache(name, start, end):
    path = _cache_file(name)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("start") != start.isoformat() or payload.get("end") != end.isoformat():
            return None
        data = payload.get("data")
        if not data:
            return None
        print(f"CACHE HIT {name}: {len(data):,} rows", flush=True)
        return data
    except Exception as exc:
        print(f"CACHE MISS {name}: {exc}", flush=True)
        return None


def _save_cache(name, start, end, data):
    path = _cache_file(name)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as f:
        json.dump({"start": start.isoformat(), "end": end.isoformat(), "data": data}, f, separators=(",", ":"))
    tmp.replace(path)
    print(f"CACHE SAVE {name}: {len(data):,} rows", flush=True)


def _fetch_market(start, end):
    names = ("1m", "5m", "15m", "1H", "4H")
    data = {}
    missing = []
    for tf in names:
        hit = _load_cache(tf, start, end)
        if hit is None:
            missing.append(tf)
        else:
            data[tf] = hit

    funding = _load_cache("funding", start, end)
    meta = research.base.fetch_instrument()

    # On a cache miss, fetch independent OKX datasets concurrently. 1m remains
    # the longest task, so this removes the serial 5m/15m/1H/4H tail.
    if missing or funding is None:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(research.base.fetch_candles, tf): ("tf", tf) for tf in missing}
            if funding is None:
                futures[pool.submit(research.base.fetch_funding)] = ("funding", "funding")
            for future in as_completed(futures):
                kind, name = futures[future]
                value = future.result()
                if kind == "tf":
                    data[name] = value
                else:
                    funding = value
                _save_cache(name, start, end, value)

    return meta, data, funding


def main():
    start, end = _configure()
    _verify_lock()
    print(
        "V160_4H_OPPOSITE_LOCK_360D_CONFIG "
        f"start={start.isoformat()} end={end.isoformat()} capital={CAPITAL:.0f} "
        f"base_notional={BASE_POSITION_NOTIONAL:.0f} leverage={LEVERAGE} "
        f"base_risk={RISK_USDT:.0f} daily_loss={DAILY_LOSS:.0f}",
        flush=True,
    )

    meta, data, funding = _fetch_market(start, end)
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    metrics = dict(metrics)
    metrics.update({
        "label": "KAYTRADE V1.6.0 + 4H opposite Hard Lock — 360D 2000U",
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
        "hard_lock_4h_opposite": True,
        "score_4h_aligned": 1.0,
        "score_4h_neutral": 0.0,
        "score_4h_opposite": 0.0,
        "score_5m_macd_improving": 1.0,
        "middle_macd_required": True,
        "middle_macd_definition": "original V1.6.0: latest three closed 5m MACD histogram values improve in trade direction",
        "path_c_enabled": False,
        "cooldown_minutes": 0,
        "loss_pause_enabled": False,
        "entry_order_type": "LIMIT",
        "boll_signal_wall_clock_expiry": False,
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
    })

    outdir = Path("backtest_output_v160_4h_opposite_360d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_360d.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    old._write_trade_csv(sim.trades, outdir / "trades_360d.csv")

    report = [
        "# KAYTRADE V1.6.0 + 4H Opposite Hard Lock — 360D / 2000U",
        "",
        f"Window: {start.isoformat()} -> {end.isoformat()}",
        "",
        "## Only change vs V1.6.0",
        "- 4H aligned: +1 (unchanged).",
        "- 4H neutral: 0 (unchanged).",
        "- 4H opposite: no -1 score; Hard Lock blocks entry.",
        "- All other V1.6.0 score items, hard blocks, 3-bar middle MACD rule and sizing are unchanged.",
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

    print("V160_4H_OPPOSITE_LOCK_360D_RESULT")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

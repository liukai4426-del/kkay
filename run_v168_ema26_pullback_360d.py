#!/usr/bin/env python3
"""KAYTRADE V1.6.8 R2, 360D, replace 1H EMA9/26 trend score with EMA26 pullback score.

Only strategy delta from V1.6.8 R2:
- remove 1H EMA9/26 aligned +1 score;
- add +1 when price pulls back/rebounds into the 1H EMA26 dynamic support/resistance zone.

Pullback rule:
- tolerance = 0.25 * ATR15 (Wilder ATR14 on closed 15m candles);
- long: previous closed 15m close is above EMA26, latest closed 15m candle touches EMA26 +/- tolerance,
  and latest close is not below EMA26 - tolerance;
- short: previous closed 15m close is below EMA26, latest closed 15m candle touches EMA26 +/- tolerance,
  and latest close is not above EMA26 + tolerance.

Everything else remains exact V1.6.8 R2: latest CLOSED 15m BOLL outer only, 15m lifecycle,
4H aligned Hard Gate +1, 5m MACD explicit adverse block / improving +1, RSI 30-70 hard gate,
Volume >=1.20x hard gate, threshold 6.0, fixed 1x LIMIT, 1H ATR x1 SL, 2R full TP, No-BE, No-PEE.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import run_v168_180d_backtest_r2 as base

DAYS = 360
TOL_ATR15 = 0.25
VERSION = "1.6.8-EMA26-PULLBACK-research"
BUILD = "1680-ema26-pb-360d"
OUT = Path("backtest_output_v168_ema26_pullback_360d")


def _atr15(rows, period=14):
    if not rows or len(rows) < period + 1:
        return None
    tr = []
    for a, b in zip(rows, rows[1:]):
        prev_close = float(a["c"])
        high = float(b["h"])
        low = float(b["l"])
        tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(tr) < period:
        return None
    value = sum(tr[:period]) / period
    for x in tr[period:]:
        value = (value * (period - 1) + x) / period
    return float(value) if math.isfinite(value) and value > 0 else None


def _pullback_state(side, quarter, e26, atr15):
    if e26 is None or atr15 is None or not quarter or len(quarter) < 2:
        return False, None, None
    prev_close = float(quarter[-2]["c"])
    bar = quarter[-1]
    low = float(bar["l"])
    high = float(bar["h"])
    close = float(bar["c"])
    tol = TOL_ATR15 * atr15
    zone_low = e26 - tol
    zone_high = e26 + tol
    touched = low <= zone_high and high >= zone_low
    if side == "做多":
        ok = bool(prev_close > e26 and touched and close >= zone_low)
    elif side == "做空":
        ok = bool(prev_close < e26 and touched and close <= zone_high)
    else:
        ok = False
    return ok, tol, close


def _decorate(result, opp, hour, quarter, four, now_ms):
    # First preserve the exact V1.6.8 R2 hard gates / MACD / BOLL behavior.
    result = base._decorate_v168(result, opp, hour, four, now_ms)
    if not isinstance(result, dict):
        return result

    scores = result.get("scores") or {}
    e9, e26 = base._ema9_26(hour)
    atr15 = _atr15(quarter)

    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue
        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
        layers.pop("ema9_26_1h", None)
        pb_ok, tol, qclose = _pullback_state(side, quarter, e26, atr15)
        layers["ema26_1h_pullback"] = 1.0 if pb_ok else 0.0
        row["layers"] = layers

        conf = row.setdefault("confirmations", {})
        conf["1H_ema9"] = e9
        conf["1H_ema26"] = e26
        conf["1H_ema9_26_score_enabled"] = False
        conf["1H_ema26_pullback_ok"] = pb_ok
        conf["1H_ema26_pullback_score"] = 1.0 if pb_ok else 0.0
        conf["1H_ema26_pullback_tolerance_atr15"] = TOL_ATR15
        conf["15m_atr14"] = atr15
        conf["ema26_pullback_tolerance_abs"] = tol
        conf["15m_close"] = qclose

        raw = 0.0
        for value in layers.values():
            try:
                n = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(n):
                raw += n
        total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
        row["raw"] = raw
        row["total"] = total
        row["required"] = base.THRESHOLD
        row["eligible"] = bool(row.get("gate") and total >= base.THRESHOLD)
        row["position_multiplier"] = 1.0 if row["eligible"] else 0.0

    qualified = [
        side for side, row in scores.items()
        if isinstance(row, dict) and row.get("eligible")
    ]
    selected = "观望"
    if len(qualified) == 1:
        selected = qualified[0]
    elif len(qualified) == 2:
        a, b = qualified
        av = float(scores[a].get("total") or 0.0)
        bv = float(scores[b].get("total") or 0.0)
        if av != bv:
            selected = a if av > bv else b
    result["side"] = selected
    result.update({
        "strategy_version": VERSION,
        "build": BUILD,
        "1h_ema9_26_score_enabled": False,
        "1h_ema26_pullback_score_enabled": True,
        "ema26_pullback_tolerance_atr15": TOL_ATR15,
    })
    return result


class HistoricalV168EMA26PullbackModel(base.HistoricalV168Model):
    VERSION = VERSION
    BUILD = BUILD

    @staticmethod
    def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                 maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                 now_ms=None, allow_new=True):
        result, opp, transition = base.proven.Model.evaluate(
            hour, quarter, five, one, four,
            opportunity=opportunity,
            stop_atr=stop_atr,
            maker_bps=maker_bps,
            taker_bps=taker_bps,
            slippage_bps=slippage_bps,
            now_ms=now_ms,
            allow_new=allow_new,
        )
        effective_now = int(now_ms if now_ms is not None else (int(one[-1]["t"]) + 60_000 if one else 0))
        return _decorate(result, opp, hour, quarter, four, effective_now), opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        ok, diag, blockers = base.HistoricalV168Model.execution_checks(plan, opportunity, score)
        diag = dict(diag or {})
        diag.update({
            "strategy_version": VERSION,
            "build": BUILD,
            "1h_ema9_26_score_enabled": False,
            "1h_ema26_pullback_score_enabled": True,
            "ema26_pullback_tolerance_atr15": TOL_ATR15,
        })
        return ok, diag, blockers


def _submit(self, result, now_ms, mark):
    old_model = base.v165runner.production
    base.v165runner.production = HistoricalV168EMA26PullbackModel
    try:
        before = self.pending
        base.v165runner._submit_v165(self, result, now_ms, mark)
        if self.pending is not None and self.pending is not before:
            side = str((result.get("opportunity") or {}).get("side") or "")
            row = (result.get("scores") or {}).get(side) or {}
            conf = row.get("confirmations") or {}
            self.pending.factors.update({
                "strategy_version": VERSION,
                "build": BUILD,
                "boll_timeframe": "15m",
                "signal_window_ms": base.BOLL_WINDOW_MS,
                "boll_outer_score": base.BOLL_OUTER_SCORE,
                "4h_trend_state_actual": str(conf.get("4H_trend_state") or ""),
                "ema9_26_1h_score_enabled": False,
                "ema26_1h": conf.get("1H_ema26"),
                "ema26_1h_pullback_ok": bool(conf.get("1H_ema26_pullback_ok")),
                "ema26_pullback_tolerance_atr15": TOL_ATR15,
                "atr15": conf.get("15m_atr14"),
                "macd_explicit_adverse": bool(conf.get("5m_macd_adverse")),
                "r2_proven_signal_path": True,
            })
    finally:
        base.v165runner.production = old_model


def configure():
    base.DAYS = DAYS
    base.FIXED_START = base.FIXED_END - timedelta(days=DAYS)
    base.OUT = OUT
    start, end = base.configure()
    base.research.VERSION = VERSION
    base.research.BUILD = BUILD
    base.research.model = HistoricalV168EMA26PullbackModel
    base.research.Simulator.submit = _submit
    base.research.Simulator.process_pending = base.proven._ORIG_PENDING
    base.research.Simulator.process_exit = base.proven._ORIG_EXIT
    base.research.Simulator.finish = base.proven._ORIG_FINISH
    return start, end


def verify_lock(start, end):
    assert DAYS == 360
    assert (end - start).days == 360
    assert base.BOLL_WINDOW_MS == 900_000
    assert HistoricalV168EMA26PullbackModel.ENTRY_WINDOW_MS == 900_000
    assert HistoricalV168EMA26PullbackModel.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert HistoricalV168EMA26PullbackModel.FIVE_MINUTE_BOLL_ENABLED is False
    assert base.BOLL_OUTER_SCORE == 2.0
    assert base.THRESHOLD == 6.0
    assert TOL_ATR15 == 0.25


def main():
    started = time.perf_counter()
    start, end = configure()
    verify_lock(start, end)
    OUT.mkdir(parents=True, exist_ok=True)

    print(
        "V168_EMA26_PULLBACK_360D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "baseline=V1.6.8_R2", "boll=15m_latest_closed_outer_only",
        "signal_lifecycle=15m_until_next_closed_15m", "boll_score=2",
        "4h=aligned_required_plus1",
        "ema1h_trend_score=REMOVED",
        "ema26_pullback=PLUS1", "ema26_zone=0.25x_ATR15",
        "macd=5m_explicit_adverse_only_plus1_if_improving",
        "threshold=6", "sl=1H_ATR_x1", "tp=2R", "be=OFF", "pee=OFF",
        flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = base.load_market(base.research.base, base.TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    base.research.cache_patch.prime_one_minute(data["1m"], base.research.MODEL_WINDOW)

    sim_started = time.perf_counter()
    sim, raw_metrics = base.research.run(data, ts, meta, funding, variant="baseline")
    sim_elapsed = time.perf_counter() - sim_started

    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    metrics["days"] = DAYS
    metrics["strategy_version"] = VERSION
    metrics["build"] = BUILD
    analysis = base._analysis(rows)
    pullback_count = sum(1 for r in rows if bool(r.get("ema26_1h_pullback_ok")))
    pullback_side = Counter(str(r.get("side") or "") for r in rows if bool(r.get("ema26_1h_pullback_ok")))
    elapsed = time.perf_counter() - started

    payload = {
        "research": "KAYTRADE V1.6.8 R2 - 1H EMA26 pullback score 360D",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": base.CAPITAL,
        "strategy_lock": {
            "baseline": "V1.6.8 Build1680 R2",
            "boll_timeframe": "15m latest CLOSED outer only",
            "boll_signal_lifetime_ms": base.BOLL_WINDOW_MS,
            "boll_outer_score": base.BOLL_OUTER_SCORE,
            "4h": "aligned mandatory Hard Gate and +1 score",
            "1h_ema9_26_trend_score": 0.0,
            "1h_ema26_pullback_score": 1.0,
            "1h_ema26_pullback_definition": "previous closed 15m on trend side; latest closed 15m touches EMA26 +/- 0.25 ATR15 and does not close beyond opposite edge",
            "ema26_pullback_tolerance_atr15": TOL_ATR15,
            "5m_macd": "explicit adverse blocks; improving +1",
            "5m_rsi": "30-70 Hard Gate",
            "5m_volume": ">=1.20x prior-20 average Hard Gate; no score",
            "score_threshold": base.THRESHOLD,
            "position": "fixed 1x",
            "entry": "LIMIT, audited closed-1m historical fill proxy",
            "stop": "1H ATR x1",
            "tp": "2R full position",
            "break_even": False,
            "pee": False,
            "adx": False,
        },
        "metrics": metrics,
        "analysis": analysis,
        "ema26_pullback": {
            "filled_trade_count_with_plus1": pullback_count,
            "by_side": dict(pullback_side),
        },
        "simulator_stats": dict(sim.stats),
        "top_blockers": sim.blockers.most_common(30),
        "transitions": dict(sim.transitions),
        "cache": cache_manifest,
        "correction": {
            "proven_v168_r2_signal_path_retained": True,
            "boll_15m_refresh_semantics_preserved": True,
            "only_strategy_delta": "replace 1H EMA9/26 aligned +1 with 1H EMA26 pullback/resistance +1",
            "production_strategy_variables_changed": False,
        },
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": elapsed},
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m proxy; order-book queue position is unavailable.",
            "Research-only variant; production V1.6.8 files are unchanged.",
        ],
    }

    (OUT / "result_v168_ema26_pullback_360d.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base._write_csv(rows, OUT / "trades_v168_ema26_pullback_360d.csv")
    (OUT / "README.txt").write_text(
        "V1.6.8 R2 360D. Only score delta: remove 1H EMA9/26 trend +1; add 1H EMA26 pullback/resistance +1 within +/-0.25 ATR15. No PEE/ADX/BE.\n",
        encoding="utf-8",
    )
    print("V168_EMA26_PULLBACK_360D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"V168_EMA26_PULLBACK_360D_TIMING total={elapsed:.2f}s market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    main()

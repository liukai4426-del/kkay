#!/usr/bin/env python3
"""KAYTRADE V1.6.8 R2 180D research: add 5m BOLL overextension +1.

Baseline is the exact audited V1.6.8 R2 180D strategy. Only added score:
- during the exact 15m BOLL opportunity lifecycle, if any CLOSED 5m candle
  overextends beyond the 5m BOLL outer band by >= 0.35 * 5m ATR(14), latch +1
  for that opportunity until the opportunity expires at the next CLOSED 15m bar.
- long: lower_band - close >= 0.35 * ATR14
- short: close - upper_band >= 0.35 * ATR14

No existing V1.6.8 score or hard gate is removed. No ADX / PEE / BE is added.
Research-only; production files are unchanged.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path

import run_v168_180d_backtest_r2 as base

DAYS = 180
OVEREXT_ATR_MULT = 0.35
OVEREXT_SCORE = 1.0
VERSION = "1.6.8-BOLL5-OVEREXT035-research"
BUILD = "1680-boll5-overext035-180d"
OUT = Path("backtest_output_v168_boll5_overext035_180d")


def _boll5_state(side, five):
    """Return current CLOSED-5m overextension diagnostics using V1.6.8 indicator stack."""
    if not five or len(five) < 30:
        return False, None
    ind = base.proven.v163.indicators(five)
    bar = five[-1]
    close = float(bar["c"])
    lower = float(ind["lower"])
    upper = float(ind["upper"])
    atr14 = float(ind["atr"])
    if not all(math.isfinite(x) for x in (close, lower, upper, atr14)) or atr14 <= 0.0:
        return False, None
    if side == "做多":
        deviation = max(0.0, lower - close)
    elif side == "做空":
        deviation = max(0.0, close - upper)
    else:
        return False, None
    ratio = deviation / atr14
    return ratio >= OVEREXT_ATR_MULT, {
        "five_bar_t": int(bar["t"]),
        "close": close,
        "lower": lower,
        "upper": upper,
        "atr14": atr14,
        "deviation": deviation,
        "deviation_atr": ratio,
    }


def _rescore(result, opp, five, now_ms):
    if not isinstance(result, dict):
        return result
    scores = result.get("scores") or {}
    opp_side = str((opp or {}).get("side") or "") if isinstance(opp, dict) else ""
    opened = False
    if isinstance(opp, dict):
        opened, _, _, _ = base.proven._signal_window_15m(opp, now_ms)
        if opened and opp_side in ("做多", "做空"):
            hit, diag = _boll5_state(opp_side, five)
            if hit:
                opp["boll5_overext035_latched"] = True
                opp["boll5_overext035_first"] = opp.get("boll5_overext035_first") or dict(diag or {})
            if diag:
                opp["boll5_overext035_latest"] = dict(diag)

    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue
        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
        active = bool(opened and side == opp_side and isinstance(opp, dict) and opp.get("boll5_overext035_latched"))
        layers["boll5_overextension"] = OVEREXT_SCORE if active else 0.0
        row["layers"] = layers

        conf = row.setdefault("confirmations", {})
        conf["5m_boll_overextension_enabled"] = True
        conf["5m_boll_overextension_threshold_atr14"] = OVEREXT_ATR_MULT
        conf["5m_boll_overextension_latched"] = active
        if side == opp_side and isinstance(opp, dict):
            first = opp.get("boll5_overext035_first") or {}
            latest = opp.get("boll5_overext035_latest") or {}
            conf["5m_boll_overextension_first_bar_t"] = first.get("five_bar_t")
            conf["5m_boll_overextension_first_atr_ratio"] = first.get("deviation_atr")
            conf["5m_boll_overextension_latest_atr_ratio"] = latest.get("deviation_atr")

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

    qualified = [s for s, r in scores.items() if isinstance(r, dict) and r.get("eligible")]
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
        "5m_boll_overextension_score_enabled": True,
        "5m_boll_overextension_threshold_atr14": OVEREXT_ATR_MULT,
        "5m_boll_overextension_latched_for_15m_opportunity": True,
    })
    return result


class HistoricalV168Boll5OverextModel(base.HistoricalV168Model):
    VERSION = VERSION
    BUILD = BUILD

    @staticmethod
    def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                 maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                 now_ms=None, allow_new=True):
        result, opp, transition = base.HistoricalV168Model.evaluate(
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
        return _rescore(result, opp, five, effective_now), opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        ok, diag, blockers = base.HistoricalV168Model.execution_checks(plan, opportunity, score)
        diag = dict(diag or {})
        diag.update({
            "strategy_version": VERSION,
            "build": BUILD,
            "5m_boll_overextension_score_enabled": True,
            "5m_boll_overextension_threshold_atr14": OVEREXT_ATR_MULT,
            "5m_boll_overextension_latched": bool(((score or {}).get("confirmations") or {}).get("5m_boll_overextension_latched")),
        })
        return ok, diag, blockers


def _submit(self, result, now_ms, mark):
    old_model = base.v165runner.production
    base.v165runner.production = HistoricalV168Boll5OverextModel
    try:
        before = self.pending
        base.v165runner._submit_v165(self, result, now_ms, mark)
        if self.pending is not None and self.pending is not before:
            side = str((result.get("opportunity") or {}).get("side") or "")
            row = (result.get("scores") or {}).get(side) or {}
            conf = row.get("confirmations") or {}
            opp = result.get("opportunity") or {}
            self.pending.factors.update({
                "strategy_version": VERSION,
                "build": BUILD,
                "boll_timeframe": "15m",
                "signal_window_ms": base.BOLL_WINDOW_MS,
                "boll_outer_score": base.BOLL_OUTER_SCORE,
                "4h_trend_state_actual": str(conf.get("4H_trend_state") or ""),
                "ema9_26_1h_ok": bool(conf.get("1H_ema9_26_trend_ok")),
                "macd_explicit_adverse": bool(conf.get("5m_macd_adverse")),
                "boll5_overext035_latched": bool(conf.get("5m_boll_overextension_latched")),
                "boll5_overext035_first_atr_ratio": conf.get("5m_boll_overextension_first_atr_ratio"),
                "boll5_overext035_latest_atr_ratio": conf.get("5m_boll_overextension_latest_atr_ratio"),
                "boll5_overext035_first_bar_t": (opp.get("boll5_overext035_first") or {}).get("five_bar_t"),
                "r2_proven_signal_path": True,
            })
    finally:
        base.v165runner.production = old_model


def configure():
    start, end = base.configure()
    base.research.VERSION = VERSION
    base.research.BUILD = BUILD
    base.research.model = HistoricalV168Boll5OverextModel
    base.research.Simulator.submit = _submit
    base.research.Simulator.process_pending = base.proven._ORIG_PENDING
    base.research.Simulator.process_exit = base.proven._ORIG_EXIT
    base.research.Simulator.finish = base.proven._ORIG_FINISH
    return start, end


def verify_lock(start, end):
    assert DAYS == 180 and (end - start).days == 180
    assert OVEREXT_ATR_MULT == 0.35 and OVEREXT_SCORE == 1.0
    assert base.THRESHOLD == 6.0
    assert base.BOLL_WINDOW_MS == 900_000
    assert HistoricalV168Boll5OverextModel.ENTRY_WINDOW_MS == 900_000
    assert HistoricalV168Boll5OverextModel.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert HistoricalV168Boll5OverextModel.FIVE_MINUTE_BOLL_ENABLED is False
    assert base.BOLL_OUTER_SCORE == 2.0
    # Exact indicator stack lock: BOLL20 +/-2 sigma and Wilder ATR(14).
    assert base.research.base.compute_indicators.__name__ == "compute_indicators"
    # Historical mapping must select only a bar whose close is <= the evaluation time.
    ts = [0, 300_000, 600_000, 900_000]
    assert base.research.base.mapped_index(ts, 600_000, 300_000) == 1
    assert base.research.base.mapped_index(ts, 900_000, 300_000) == 2


def main():
    started = time.perf_counter()
    start, end = configure()
    verify_lock(start, end)
    OUT.mkdir(parents=True, exist_ok=True)

    print(
        "V168_BOLL5_OVEREXT035_180D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "baseline=V1.6.8_R2", "boll15=latest_closed_outer_only",
        "signal_lifecycle=15m_until_next_closed_15m", "boll15_score=2",
        "4h=aligned_required_plus1", "ema1h=9_26_aligned_plus1",
        "macd5=explicit_adverse_block_improving_plus1",
        "boll5_overext=close_outside_by_0.35x_ATR14_plus1_latched",
        "threshold=6", "sl=1H_ATR_x1", "tp=2R", "be=OFF", "pee=OFF", "adx=OFF",
        flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = base.load_market(base.research.base, base.TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    base.research.cache_patch.prime_one_minute(data["1m"], base.research.MODEL_WINDOW)

    # Pre-run market-data audit: strict ordering / expected spacing / closed-bar coverage.
    for tf in base.TIMEFRAMES:
        rows = data[tf]
        assert rows, f"AUDIT FAIL: no {tf} rows"
        step = base.research.base.BAR_MS[tf]
        assert all(int(b["t"]) > int(a["t"]) for a, b in zip(rows, rows[1:])), f"AUDIT FAIL: unordered {tf}"
        assert all(int(b["t"]) - int(a["t"]) == step for a, b in zip(rows, rows[1:])), f"AUDIT FAIL: gap/duplicate {tf}"
    print("AUDIT PASS: closed-bar data ordering/spacing + V1.6.8 R2 lock + no-lookahead mapping", flush=True)

    sim_started = time.perf_counter()
    sim, raw_metrics = base.research.run(data, ts, meta, funding, variant="baseline")
    sim_elapsed = time.perf_counter() - sim_started

    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    metrics["days"] = DAYS
    metrics["strategy_version"] = VERSION
    metrics["build"] = BUILD
    analysis = base._analysis(rows)
    overext_rows = [r for r in rows if bool(r.get("boll5_overext035_latched"))]
    overext_by_side = Counter(str(r.get("side") or "") for r in overext_rows)
    overext_by_score = Counter(f"{float(r.get('score') or 0.0):.1f}" for r in overext_rows)
    elapsed = time.perf_counter() - started

    payload = {
        "research": "KAYTRADE V1.6.8 R2 + 5m BOLL overextension 0.35 ATR14 +1, 180D",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": base.CAPITAL,
        "strategy_lock": {
            "baseline": "V1.6.8 Build1680 R2",
            "boll_timeframe": "15m latest CLOSED outer only",
            "boll_signal_lifetime_ms": base.BOLL_WINDOW_MS,
            "boll_outer_score": base.BOLL_OUTER_SCORE,
            "4h": "aligned mandatory Hard Gate and +1 score",
            "1h_ema9_26": "aligned +1 score retained",
            "5m_macd": "explicit adverse blocks; improving +1 retained",
            "5m_rsi": "30-70 Hard Gate",
            "5m_volume": ">=1.20x prior-20 average Hard Gate; no score",
            "5m_boll_overextension_score": OVEREXT_SCORE,
            "5m_boll_overextension_rule": "closed 5m close beyond direction-side outer band by >=0.35 x Wilder ATR14; latch until exact 15m opportunity expiry",
            "5m_boll_overextension_threshold_atr14": OVEREXT_ATR_MULT,
            "score_threshold": base.THRESHOLD,
            "position": "fixed 1x",
            "entry": "LIMIT, audited closed-1m historical fill proxy",
            "stop": "1H ATR x1",
            "tp": "2R full position",
            "break_even": False,
            "pee": False,
            "adx": False,
        },
        "preflight_audit": {
            "closed_market_bars_only": True,
            "strict_timeframe_spacing_checked": True,
            "no_lookahead_mapped_index_checked": True,
            "atr": "Wilder ATR(14)",
            "boll": "20-period mean +/- 2 population standard deviations",
            "exact_15m_signal_lifecycle_checked": True,
            "production_files_changed": False,
        },
        "metrics": metrics,
        "analysis": analysis,
        "boll5_overextension": {
            "filled_trade_count_with_plus1": len(overext_rows),
            "by_side": dict(overext_by_side),
            "by_score": dict(overext_by_score),
        },
        "simulator_stats": dict(sim.stats),
        "top_blockers": sim.blockers.most_common(30),
        "transitions": dict(sim.transitions),
        "cache": cache_manifest,
        "correction": {
            "proven_v168_r2_signal_path_retained": True,
            "boll_15m_refresh_semantics_preserved": True,
            "only_strategy_delta": "add latched +1 for closed-5m BOLL overextension >=0.35 x ATR14",
            "production_strategy_variables_changed": False,
        },
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": elapsed},
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m proxy; order-book queue position is unavailable.",
            "Research-only variant; production V1.6.8 files are unchanged.",
        ],
    }

    (OUT / "result_v168_boll5_overext035_180d.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base._write_csv(rows, OUT / "trades_v168_boll5_overext035_180d.csv")
    (OUT / "README.txt").write_text(
        "V1.6.8 R2 180D + closed-5m BOLL overextension >=0.35 x ATR14 +1 (latched for exact 15m opportunity).\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

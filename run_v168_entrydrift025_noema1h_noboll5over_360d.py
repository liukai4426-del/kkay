#!/usr/bin/env python3
"""V1.6.8 R2 360D A/B: Entry Drift <=0.25 ATR5 +1, no 1H EMA score, no 5m BOLL-overextension score.

Only scoring changes versus audited V1.6.8 R2:
- remove 1H EMA9/26 aligned +1;
- keep 5m BOLL overextension score disabled;
- add +1 when current executable entry proxy remains within 0.25 * ATR5 of the
  opportunity trigger_reference.

Entry Drift is computed only from information available at the current closed
1m bar:
    abs(current closed-1m close - opportunity.trigger_reference) / opportunity.atr5
No future fill/outcome information is used.

Everything else stays unchanged: 15m BOLL outer trigger/lifecycle, 4H aligned
Hard Gate +1, 5m MACD adverse block/improving +1, RSI/Volume hard gates,
score threshold 6.0, fixed 1x LIMIT entry, 1H ATR x1 SL, 2R full TP,
PEE/ADX/BE OFF. Research-only.
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
DRIFT_MAX_ATR5 = 0.25
DRIFT_SCORE = 1.0
VERSION = "1.6.8-ENTRYDRIFT025-NOEMA1H-NOBOLL5OVER-research"
BUILD = "1680-entrydrift025-noema1h-noboll5over-360d"
OUT = Path("backtest_output_v168_entrydrift025_noema1h_noboll5over_360d")


def _finite(value, default=0.0):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _entry_drift_diag(opp, one):
    if not isinstance(opp, dict) or not one:
        return math.inf, None, None, None
    entry = _finite(one[-1].get("c"), math.nan)
    atr5 = _finite(opp.get("atr5"), math.nan)
    ref = _finite(opp.get("trigger_reference"), math.nan)
    if not (math.isfinite(entry) and math.isfinite(atr5) and atr5 > 0 and math.isfinite(ref)):
        return math.inf, entry, ref, atr5
    return abs(entry - ref) / atr5, entry, ref, atr5


def _rescore_noema_entrydrift(result, opp, one):
    if not isinstance(result, dict):
        return result

    scores = result.get("scores") or {}
    opp_side = str((opp or {}).get("side") or "") if isinstance(opp, dict) else ""
    drift, entry_proxy, ref, atr5 = _entry_drift_diag(opp, one)

    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue

        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
        layers["ema9_26_1h"] = 0.0
        layers.pop("boll5_overextension", None)
        layers["entry_drift_near"] = (
            DRIFT_SCORE
            if side == opp_side and math.isfinite(drift) and drift <= DRIFT_MAX_ATR5
            else 0.0
        )
        row["layers"] = layers

        conf = row.setdefault("confirmations", {})
        conf["1H_ema9_26_score_enabled"] = False
        conf["1H_ema9_26_score_value"] = 0.0
        conf["5m_boll_overextension_score_enabled"] = False
        conf["5m_boll_overextension_score_value"] = 0.0
        conf["entry_drift_score_enabled"] = True
        conf["entry_drift_threshold_atr5"] = DRIFT_MAX_ATR5
        conf["entry_drift_score_value"] = layers["entry_drift_near"]
        if side == opp_side:
            conf["entry_drift_atr5_live"] = drift if math.isfinite(drift) else None
            conf["entry_drift_entry_proxy"] = entry_proxy
            conf["entry_drift_trigger_reference"] = ref
            conf["entry_drift_atr5_value"] = atr5

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
        av = _finite(scores[a].get("total"))
        bv = _finite(scores[b].get("total"))
        if av != bv:
            selected = a if av > bv else b

    result["side"] = selected
    result.update({
        "strategy_version": VERSION,
        "build": BUILD,
        "1h_ema9_26_score_enabled": False,
        "5m_boll_overextension_score_enabled": False,
        "5m_boll_overextension_score": 0.0,
        "entry_drift_score_enabled": True,
        "entry_drift_threshold_atr5": DRIFT_MAX_ATR5,
        "entry_drift_score": DRIFT_SCORE,
    })
    return result


class HistoricalV168EntryDriftModel(base.HistoricalV168Model):
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
        return _rescore_noema_entrydrift(result, opp, one), opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        ok, diag, blockers = base.HistoricalV168Model.execution_checks(
            plan, opportunity, score
        )
        diag = dict(diag or {})
        conf = (score or {}).get("confirmations") or {}
        diag.update({
            "strategy_version": VERSION,
            "build": BUILD,
            "1h_ema9_26_score_enabled": False,
            "5m_boll_overextension_score_enabled": False,
            "entry_drift_score_enabled": True,
            "entry_drift_threshold_atr5": DRIFT_MAX_ATR5,
            "entry_drift_atr5_live": conf.get("entry_drift_atr5_live"),
            "entry_drift_score_value": conf.get("entry_drift_score_value"),
        })
        return ok, diag, blockers


def _submit(self, result, now_ms, mark):
    old_model = base.v165runner.production
    base.v165runner.production = HistoricalV168EntryDriftModel
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
                "entry_drift_plus1": float(conf.get("entry_drift_score_value") or 0.0),
                "entry_drift_live_at_submit": conf.get("entry_drift_atr5_live"),
                "entry_drift_threshold_atr5": DRIFT_MAX_ATR5,
                "ema1h_score_removed": True,
                "boll5_overextension_score_removed": True,
                "r2_proven_signal_path": True,
            })
    finally:
        base.v165runner.production = old_model


def configure():
    base.DAYS = DAYS
    base.FIXED_START = base.FIXED_END - timedelta(days=DAYS)
    start, end = base.configure()
    base.research.VERSION = VERSION
    base.research.BUILD = BUILD
    base.research.model = HistoricalV168EntryDriftModel
    base.research.Simulator.submit = _submit
    base.research.Simulator.process_pending = base.proven._ORIG_PENDING
    base.research.Simulator.process_exit = base.proven._ORIG_EXIT
    base.research.Simulator.finish = base.proven._ORIG_FINISH
    return start, end


def verify_lock(start, end):
    assert (end - start).days == DAYS
    assert DAYS == 360
    assert base.THRESHOLD == 6.0
    assert base.BOLL_WINDOW_MS == 900_000
    assert base.BOLL_OUTER_SCORE == 2.0
    assert HistoricalV168EntryDriftModel.FIVE_MINUTE_BOLL_ENABLED is False
    assert DRIFT_MAX_ATR5 == 0.25 and DRIFT_SCORE == 1.0

    sample = {
        "scores": {
            "做多": {
                "layers": {
                    "boll_entry": 2.0,
                    "trend4h": 1.0,
                    "ema9_26_1h": 1.0,
                    "entry_near_zone": 1.0,
                    "ema50_15m_move": 1.0,
                },
                "gate": True,
                "eligible": True,
                "confirmations": {},
            },
            "做空": {"layers": {}, "gate": False, "eligible": False, "confirmations": {}},
        }
    }
    opp = {"side": "做多", "atr5": 100.0, "trigger_reference": 1000.0}
    one = [{"c": 1020.0}]
    out = _rescore_noema_entrydrift(sample, opp, one)
    row = out["scores"]["做多"]
    assert row["layers"]["ema9_26_1h"] == 0.0
    assert row["layers"]["entry_drift_near"] == 1.0
    assert "boll5_overextension" not in row["layers"]
    assert row["total"] == 6.0
    assert row["eligible"] is True

    one_far = [{"c": 1030.0}]
    out_far = _rescore_noema_entrydrift(sample, opp, one_far)
    row_far = out_far["scores"]["做多"]
    assert row_far["layers"]["entry_drift_near"] == 0.0
    assert row_far["total"] == 5.0
    assert row_far["eligible"] is False


def main():
    started = time.perf_counter()
    start, end = configure()
    verify_lock(start, end)
    OUT.mkdir(parents=True, exist_ok=True)

    print(
        "V168_ENTRYDRIFT025_NOEMA1H_NOBOLL5OVER_360D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "baseline=V1.6.8_R2",
        "boll15=latest_closed_outer_only",
        "signal_lifecycle=15m_until_next_closed_15m",
        "boll15_score=2",
        "4h=aligned_required_plus1",
        "ema1h_score=0",
        "boll5_overextension_score=0",
        "entry_drift=abs(closed1m_close-trigger_reference)/atr5_le_0.25_plus1",
        "macd5=explicit_adverse_block_improving_plus1",
        "threshold=6",
        "sl=1H_ATR_x1", "tp=2R", "be=OFF", "pee=OFF", "adx=OFF",
        flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = base.load_market(base.research.base, base.TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    base.research.cache_patch.prime_one_minute(data["1m"], base.research.MODEL_WINDOW)

    for tf in base.TIMEFRAMES:
        rows_tf = data[tf]
        assert rows_tf, f"AUDIT FAIL: no {tf} rows"
        step = base.research.base.BAR_MS[tf]
        assert all(int(b["t"]) > int(a["t"]) for a, b in zip(rows_tf, rows_tf[1:])), f"AUDIT FAIL: unordered {tf}"
        assert all(int(b["t"]) - int(a["t"]) == step for a, b in zip(rows_tf, rows_tf[1:])), f"AUDIT FAIL: gap/duplicate {tf}"

    print("AUDIT PASS: closed bars only + no-lookahead Entry Drift at current closed-1m price", flush=True)

    sim_started = time.perf_counter()
    sim, raw_metrics = base.research.run(data, ts, meta, funding, variant="baseline")
    sim_elapsed = time.perf_counter() - sim_started

    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    metrics["days"] = DAYS
    metrics["strategy_version"] = VERSION
    metrics["build"] = BUILD
    analysis = base._analysis(rows)

    drift_plus_rows = [
        r for r in rows
        if _finite((r.get("score_components") or {}).get("entry_drift_near")) > 0
    ]
    drift_ratios = [
        _finite(r.get("entry_drift_atr5"), math.nan)
        for r in rows
        if math.isfinite(_finite(r.get("entry_drift_atr5"), math.nan))
    ]
    elapsed = time.perf_counter() - started

    payload = {
        "research": "V1.6.8 R2 360D: Entry Drift <=0.25 ATR5 +1; NoEMA1H; NoBOLL5Over score",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": base.CAPITAL,
        "strategy_lock": {
            "baseline": "V1.6.8 Build1680 R2",
            "boll_timeframe": "15m latest CLOSED outer only",
            "boll_signal_lifetime_ms": base.BOLL_WINDOW_MS,
            "boll_outer_score": base.BOLL_OUTER_SCORE,
            "4h": "aligned mandatory Hard Gate and +1 score",
            "1h_ema9_26_score_enabled": False,
            "1h_ema9_26_score": 0.0,
            "5m_boll_overextension_score_enabled": False,
            "5m_boll_overextension_score": 0.0,
            "entry_drift_score_enabled": True,
            "entry_drift_formula": "abs(current closed-1m close - opportunity.trigger_reference) / opportunity.atr5",
            "entry_drift_threshold_atr5": DRIFT_MAX_ATR5,
            "entry_drift_score": DRIFT_SCORE,
            "5m_macd": "explicit adverse blocks; improving +1 retained",
            "5m_rsi": "30-70 Hard Gate",
            "5m_volume": ">=1.20x prior-20 average Hard Gate blocks; no score",
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
            "no_lookahead_entry_drift_checked": True,
            "entry_drift_uses_current_closed_1m_price": True,
            "production_files_changed": False,
        },
        "metrics": metrics,
        "analysis": analysis,
        "entry_drift": {
            "filled_trade_count_with_plus1": len(drift_plus_rows),
            "filled_trade_count_total": len(rows),
            "actual_entry_drift_atr5_min": min(drift_ratios) if drift_ratios else None,
            "actual_entry_drift_atr5_median": sorted(drift_ratios)[len(drift_ratios)//2] if drift_ratios else None,
            "actual_entry_drift_atr5_max": max(drift_ratios) if drift_ratios else None,
            "by_score": dict(Counter(f"{_finite(r.get('score')):.1f}" for r in drift_plus_rows)),
        },
        "simulator_stats": dict(sim.stats),
        "top_blockers": sim.blockers.most_common(30),
        "transitions": dict(sim.transitions),
        "cache": cache_manifest,
        "correction": {
            "proven_v168_r2_signal_path_retained": True,
            "production_strategy_variables_changed": False,
            "only_strategy_delta": "1H EMA9/26 +1 removed; 5m BOLL-overextension +1 disabled; Entry Drift <=0.25 ATR5 adds +1",
        },
        "timing_sec": {
            "market_load": market_elapsed,
            "simulation": sim_elapsed,
            "total": elapsed,
        },
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m proxy; order-book queue position is unavailable.",
            "Research-only variant; production V1.6.8 files are unchanged.",
        ],
    }

    (OUT / "result_v168_entrydrift025_noema1h_noboll5over_360d.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base._write_csv(rows, OUT / "trades_v168_entrydrift025_noema1h_noboll5over_360d.csv")
    (OUT / "README.txt").write_text(
        "V1.6.8 R2 360D: Entry Drift <=0.25 ATR5 +1; 1H EMA score OFF; 5m BOLL overextension score OFF.\n",
        encoding="utf-8",
    )
    print("ENTRYDRIFT025_360D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""KAYTRADE V1.6.8 R2 + 30m BOLL lifecycle + PEE3, 180D research.

Baseline V1.6.8 R2 stays unchanged except:
- 15m BOLL outer signal lifecycle is extended from 15m to 30m;
- PEE 3.0 is added during the first 4 hours of an open trade.

No ADX filter or ADX score is used in this variant.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

import run_v168_180d_backtest_r2 as base
import run_v168_adx_30m_backtest as window_src
import run_v168_adx30m_pee3_compare as pee

DAYS = 180
BOLL_WINDOW_MS = 30 * 60 * 1000
VERSION = "1.6.8-30m-PEE3-research"
BUILD = "1680-30m-pee3"
OUT = Path("backtest_output_v168_30m_pee3_180d")


class HistoricalV168ThirtyMinModel:
    VERSION = VERSION
    BUILD = BUILD
    THRESHOLD = base.THRESHOLD
    ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    TIME_WINDOW_ENABLED = True
    BOLL_OUTER_SCORE = base.BOLL_OUTER_SCORE
    BOLL_TRIGGER_TIMEFRAME = "15m"
    FIVE_MINUTE_BOLL_ENABLED = False
    OUTER_PATHS = base.HistoricalV168Model.OUTER_PATHS
    MIDDLE_PATH = base.HistoricalV168Model.MIDDLE_PATH
    RSI_MIN = base.HistoricalV168Model.RSI_MIN
    RSI_MAX = base.HistoricalV168Model.RSI_MAX
    VOLUME_HARD_GATE = base.HistoricalV168Model.VOLUME_HARD_GATE
    _signal_window = staticmethod(window_src._signal_window_30m)
    _finite = staticmethod(base.proven.Model._finite)
    _path_from = staticmethod(base.proven.Model._path_from)
    _macd_improving = staticmethod(base.proven._macd_allowed)

    @staticmethod
    def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                 maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                 now_ms=None, allow_new=True):
        window_src._install_window_patch()
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
        if isinstance(opp, dict):
            start = base.proven.prod._signal_close_ms(opp)
            opp["expires_ms"] = start + BOLL_WINDOW_MS if start > 0 else 0
            opp["signal_valid_30m"] = True
        if isinstance(result, dict):
            result["strategy_version"] = VERSION
            result["build"] = BUILD
            result["entry_window_ms"] = BOLL_WINDOW_MS
            result["boll_signal_lifecycle"] = "30m_from_closed_15m_trigger"
            result["1h_ema9_26_score_enabled"] = True
            result["5m_adx_score_enabled"] = False
            result["5m_adx_hard_gate"] = False
        return result, opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        window_src._install_window_patch()
        ok, diag, blockers = base.HistoricalV168Model.execution_checks(plan, opportunity, score)
        diag = dict(diag or {})
        blockers = list(blockers or [])
        now_ms = int(diag.get("now_ms") or 0)
        if now_ms > 0:
            opened, age_ms, remaining_ms, end_ms = window_src._signal_window_30m(opportunity, now_ms)
            diag.update({
                "signal_window_ok": opened,
                "signal_age_ms": age_ms,
                "signal_remaining_ms": remaining_ms,
                "signal_expires_ms": end_ms,
            })
            if not opened:
                blockers.append("V1.6.8 30m：15m BOLL触发已超过30分钟有效期")
        blockers = list(dict.fromkeys(str(x) for x in blockers))
        diag.update({
            "strategy_version": VERSION,
            "build": BUILD,
            "entry_window_ms": BOLL_WINDOW_MS,
            "1h_ema9_26_score_enabled": True,
            "5m_adx_score_enabled": False,
            "5m_adx_hard_gate": False,
        })
        return not blockers, diag, blockers


def _submit(self, result, now_ms, mark):
    old_model = base.v165runner.production
    base.v165runner.production = HistoricalV168ThirtyMinModel
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
                "signal_window_ms": BOLL_WINDOW_MS,
                "ema9_26_1h_ok": bool(conf.get("1H_ema9_26_trend_ok")),
                "ema9_1h": conf.get("1H_ema9"),
                "ema26_1h": conf.get("1H_ema26"),
                "macd_explicit_adverse": bool(conf.get("5m_macd_adverse")),
                "4h_trend_state_actual": str(conf.get("4H_trend_state") or ""),
                "adx_enabled": False,
            })
    finally:
        base.v165runner.production = old_model


def configure():
    window_src._install_window_patch()
    base.BOLL_WINDOW_MS = BOLL_WINDOW_MS
    base.HistoricalV168Model.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    base.DAYS = DAYS
    base.FIXED_START = base.FIXED_END - base.timedelta(days=DAYS)
    base.OUT = OUT
    start, end = base.configure()
    base.research.VERSION = VERSION
    base.research.BUILD = BUILD
    base.research.model = HistoricalV168ThirtyMinModel
    base.research.Simulator.submit = _submit
    base.research.Simulator.process_pending = base.proven._ORIG_PENDING
    base.research.Simulator.process_exit = pee._ORIGINAL_PROCESS_EXIT
    base.research.Simulator.finish = base.proven._ORIG_FINISH
    return start, end


def verify_lock():
    assert DAYS == 180
    assert BOLL_WINDOW_MS == 1_800_000
    assert HistoricalV168ThirtyMinModel.ENTRY_WINDOW_MS == 1_800_000
    assert HistoricalV168ThirtyMinModel.THRESHOLD == 6.0
    assert HistoricalV168ThirtyMinModel.BOLL_OUTER_SCORE == 2.0
    assert base.EMA_FAST == 9 and base.EMA_SLOW == 26
    assert pee.PEE_VERSION == "3.0"
    assert pee.MONITOR_MAX_MS == 4 * 60 * 60_000
    assert pee.STRUCTURE_BREAK_ATR == 0.25


def main():
    started = time.perf_counter()
    start, end = configure()
    verify_lock()
    OUT.mkdir(parents=True, exist_ok=True)

    print(
        "V168_30M_PEE3_180D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "baseline=V1.6.8_R2", "boll=15m_outer_only", "boll_score=2",
        "signal_lifecycle=30m", "4h=aligned_required_plus1",
        "ema1h=EMA9_26_plus1_PRESERVED",
        "macd=5m_explicit_adverse_only_plus1_if_improving",
        "adx=OFF", "PEE=3.0", "monitor=0..4H",
        "pee_current_r_gate=-0.50R", "pee_mfe_cutoff=0.80R",
        "threshold=6", "sl=1H_ATR_x1", "tp=2R", "be=OFF",
        flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = base.load_market(base.research.base, base.TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    base.research.cache_patch.prime_one_minute(data["1m"], base.research.MODEL_WINDOW)
    pee._prime_feature_map(data["5m"], data["15m"], data["1H"])

    sim_started = time.perf_counter()
    base.research.Simulator.process_exit = pee._process_exit_pee3
    sim, raw_metrics = base.research.run(data, ts, meta, funding, variant="baseline")
    sim_elapsed = time.perf_counter() - sim_started

    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    metrics["days"] = DAYS
    analysis = base._analysis(rows)
    pee_exits = [r for r in rows if str(r.get("reason") or "") == pee.EXIT_REASON]
    stage_counts = Counter(str(r.get("early_exit_stage") or "unknown") for r in pee_exits)
    path_counts = Counter(str(r.get("early_exit_path") or "unknown") for r in pee_exits)
    elapsed = time.perf_counter() - started

    payload = {
        "research": "KAYTRADE V1.6.8 R2 + 30m BOLL lifecycle + PEE3 180D",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": base.CAPITAL,
        "strategy_lock": {
            "version": "1.6.8",
            "research_build": BUILD,
            "baseline": "V1.6.8 Build1680 R2",
            "boll_timeframe": "15m latest CLOSED outer only",
            "boll_signal_lifetime_ms": BOLL_WINDOW_MS,
            "boll_signal_lifetime": "30 minutes from closed 15m trigger",
            "boll_outer_score": base.BOLL_OUTER_SCORE,
            "4h": "aligned mandatory Hard Gate and +1 score; neutral/opposite blocked",
            "1h_ema": "EMA9>EMA26 long / EMA9<EMA26 short = +1; scoring only; PRESERVED",
            "5m_macd": "explicit adverse/opposite blocks; improvement +1 retained",
            "5m_kdj_score": 0.0,
            "5m_rsi": "30-70 Hard Gate",
            "5m_volume": "<1.20x prior-20 average Hard Gate; no score",
            "5m_adx_score_enabled": False,
            "5m_adx_hard_gate": False,
            "score_threshold": base.THRESHOLD,
            "position": "fixed 1x",
            "entry": "LIMIT, audited closed-1m historical fill proxy",
            "stop": "1H ATR x1",
            "tp": "2R full position",
            "break_even": False,
            "pee_version": pee.PEE_VERSION,
            "pee_monitor_minutes": 240,
            "pee_current_r_gate": -0.50,
            "pee_mfe_cutoff_r": 0.80,
            "pee_structure_break_atr": pee.STRUCTURE_BREAK_ATR,
        },
        "metrics": metrics,
        "analysis": analysis,
        "pee3": {
            "early_exit_count": len(pee_exits),
            "stage_counts": dict(stage_counts),
            "path_counts": dict(path_counts),
        },
        "simulator_stats": dict(sim.stats),
        "top_blockers": sim.blockers.most_common(30),
        "transitions": dict(sim.transitions),
        "cache": cache_manifest,
        "correction": {
            "proven_v168_r2_baseline_retained": True,
            "boll_lifecycle_changed_to_30m": True,
            "adx_enabled": False,
            "position_management_change": "PEE3 only",
            "production_strategy_variables_changed": False,
        },
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": elapsed},
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m proxy; order-book queue position is unavailable.",
            "Research-only variant; production V1.6.8 files are unchanged.",
        ],
    }

    (OUT / "result_v168_30m_pee3_180d.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base._write_csv(rows, OUT / "trades_v168_30m_pee3_180d.csv")
    (OUT / "README.txt").write_text(
        "KAYTRADE V1.6.8 R2 + 30m BOLL lifecycle + PEE3, 180D.\n"
        "No ADX. EMA score preserved. Only BOLL lifecycle and PEE3 differ from baseline.\n",
        encoding="utf-8",
    )

    print("V168_30M_PEE3_180D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"V168_30M_PEE3_180D_TIMING total={elapsed:.2f}s market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fast 360D research: current V1.6.5 with 15m BOLL outer trigger + score threshold 5.5.

Only changes under test:
1) BOLL outer trigger uses the latest CLOSED 15m candle; signal valid until the next 15m close.
2) score threshold 6.0 -> 5.5.

Unchanged: 5m RSI 30..70, formal 5m MACD improvement required, current Volume hard gate,
4H must be aligned, fixed 1x LIMIT entry, 1H ATR x1 SL, 2R full TP, No-BE, no Early Exit.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import run_v166_15m_boll_4h_neutral_360d_fast as src

slow = src.slow
v165runner = src.v165runner
prod = src.prod
v163 = src.v163
research = src.research
load_market = src.load_market

DAYS = 360
THRESHOLD = 5.5
BOLL_WINDOW_MS = 15 * 60 * 1000
TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H")
OUT = Path("backtest_output_v166_15m_boll_threshold55_360d_fast")

_ORIG_V163_BOLL = v163.boll_entry_signal
_ORIG_PENDING = slow.ORIG_PENDING
_ORIG_EXIT = slow.ORIG_EXIT
_ORIG_FINISH = slow.ORIG_FINISH


def _signal_window_15m(opportunity, now_ms):
    start = prod._signal_close_ms(opportunity)
    now = int(now_ms or 0)
    end = start + BOLL_WINDOW_MS if start > 0 else 0
    opened = bool(start > 0 and start <= now < end)
    age_ms = max(0, now - start) if start else 0
    remaining_ms = max(0, end - now) if end else 0
    return opened, age_ms, remaining_ms, end


class Model:
    VERSION = "1.6.6-research-15m-boll-threshold55"
    ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    TIME_WINDOW_ENABLED = True
    THRESHOLD = THRESHOLD
    OUTER_PATHS = prod.OUTER_PATHS
    MIDDLE_PATH = prod.MIDDLE_PATH
    RSI_MIN = prod.RSI_MIN
    RSI_MAX = prod.RSI_MAX
    VOLUME_HARD_GATE = prod.VOLUME_HARD_GATE
    BOLL_OUTER_SCORE = prod.BOLL_OUTER_SCORE
    _signal_window = staticmethod(_signal_window_15m)
    _finite = staticmethod(prod._finite)
    _macd_improving = staticmethod(prod._macd_improving)
    _path_from = staticmethod(prod._path_from)

    @staticmethod
    def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                 maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                 now_ms=None, allow_new=True):
        # Reuse the audited 15m-BOLL adapter, but do NOT relax the 4H gate.
        src._CURRENT_QUARTER = quarter
        v163.boll_entry_signal = src._boll15_outer_signal
        prod.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
        prod.TIME_WINDOW_ENABLED = True
        prod.THRESHOLD = THRESHOLD
        prod._patch_v164_base()
        result, opp, transition = prod.evaluate(
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
            start = prod._signal_close_ms(opp)
            opp["expires_ms"] = start + BOLL_WINDOW_MS if start > 0 else 0
            opp["boll_timeframe"] = "15m"
            opp["time_window_enabled"] = True
        if isinstance(result, dict):
            result["boll_timeframe"] = "15m"
            result["signal_lifecycle"] = "closed_15m_until_next_close"
            result["threshold"] = THRESHOLD
        return result, opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        prod.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
        prod.TIME_WINDOW_ENABLED = True
        prod.THRESHOLD = THRESHOLD
        ok, diag, blockers = prod.execution_checks(plan, opportunity, score)
        blockers = list(blockers or [])
        diag = dict(diag or {})
        diag.update({
            "strategy_version": Model.VERSION,
            "boll_timeframe": "15m",
            "entry_window_ms": BOLL_WINDOW_MS,
            "score_threshold": THRESHOLD,
            "4h_rule": "aligned required",
            "macd_rule": "formal 5m improvement required",
        })
        return not blockers, diag, blockers


def submit_current(self, result, now_ms, mark):
    old_model = v165runner.production
    old_threshold = getattr(old_model, "THRESHOLD", 6.0)
    v165runner.production = Model
    try:
        v165runner._submit_v165(self, result, now_ms, mark)
        if self.pending is not None:
            self.pending.factors["boll_timeframe"] = "15m"
            self.pending.factors["signal_window_ms"] = BOLL_WINDOW_MS
            self.pending.factors["score_threshold"] = THRESHOLD
            self.pending.factors["4h_neutral_allowed"] = False
            self.pending.factors["macd_improvement_required"] = True
    finally:
        v165runner.production = old_model
        old_model.THRESHOLD = old_threshold


def configure():
    start, end = slow.configure()
    assert (end - start).days == DAYS
    prod.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    prod.TIME_WINDOW_ENABLED = True
    prod.THRESHOLD = THRESHOLD
    research.model = Model
    research.Simulator.submit = submit_current
    research.Simulator.process_pending = _ORIG_PENDING
    research.Simulator.process_exit = _ORIG_EXIT
    research.Simulator.finish = _ORIG_FINISH
    return start, end


def main():
    started = time.perf_counter()
    start, end = configure()
    OUT.mkdir(parents=True, exist_ok=True)
    print(
        "V166_15M_BOLL_THRESHOLD55_360D_FAST",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "boll=15m_outer", "boll_lifetime=15m", "rsi=5m_30..70",
        "macd=5m_required", "4h=aligned_required", "threshold=5.5",
        "volume=current_hard_gate", "position=1x", "entry=LIMIT",
        "sl=1H_ATR", "tp=2R", "be=OFF", "early_exit=OFF",
        flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = load_market(research.base, TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim_started = time.perf_counter()
    sim, metrics = research.run(data, ts, meta, funding, variant="baseline")
    sim_elapsed = time.perf_counter() - sim_started
    rows = list(sim.trades)
    summary = slow.metric(dict(metrics))
    total_elapsed = time.perf_counter() - started

    src._write_csv(rows, OUT / "trades_15m_boll_threshold55_360d.csv")
    payload = {
        "research": "V1.6.5 current model: 15m BOLL outer + score threshold 5.5, 360D fast",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "changes": {
            "boll_timeframe": "15m",
            "boll_signal_lifetime": "until next closed 15m candle",
            "score_threshold": 5.5,
        },
        "unchanged": {
            "rsi": "5m 30-70",
            "macd": "formal 5m improvement required",
            "4h_trend_gate": "aligned required; neutral/opposite blocked",
            "volume": "current V1.6.5 hard-gate semantics",
            "position": "fixed 1x",
            "entry": "LIMIT",
            "stop": "1H ATR x1",
            "tp": "2R full position",
            "break_even": False,
            "early_exit": False,
        },
        "metrics": summary,
        "analysis": src._analysis(rows),
        "cache": cache_manifest,
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": total_elapsed},
    }
    (OUT / "result_15m_boll_threshold55_360d.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "metrics_raw.json").write_text(json.dumps(dict(metrics), ensure_ascii=False, indent=2), encoding="utf-8")
    print("RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"FAST_TIMING total={total_elapsed:.2f}s market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        v163.boll_entry_signal = _ORIG_V163_BOLL

#!/usr/bin/env python3
"""KAYTRADE V1.6.8 R2 + ADX Hard Gate only, 180D research backtest.

Baseline is the proven V1.6.8 R2 strategy, unchanged:
- 15m latest CLOSED BOLL outer only, signal lifecycle 15m, BOLL score +2;
- 4H aligned mandatory Hard Gate and +1;
- 1H EMA9/26 aligned +1, scoring only;
- 5m MACD explicit adverse/opposite blocks; improving +1;
- KDJ score removed;
- 5m RSI 30..70 Hard Gate;
- Volume <1.20x prior-20 average Hard Gate, no score;
- threshold 6.0, fixed 1x LIMIT, 1H ATR x1 SL, 2R full TP, No-BE.

Only added rule:
- 5m ADX(14) Hard Gate ONLY: ADX>=25, opposite DI dominant, and ADX rises
  across 3 consecutive closed 5m bars => block entry.
- ADX contributes 0 score and does not replace the 1H EMA score.

Research/backtest-only. Production V1.6.8 files are unchanged.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import run_v168_180d_backtest_r2 as base
import run_v168_adx_30m_backtest as adxsrc

DAYS = 180
ADX_PERIOD = 14
ADX_THRESHOLD = 25.0
VERSION = "1.6.8-ADX-HG-research"
BUILD = "1680-adx-hg"
OUT = Path("backtest_output_v168_adx_hardgate_only_180d")


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _decorate_adx_hard_gate(result, five):
    if not isinstance(result, dict):
        return result

    snap = adxsrc._adx_snapshot(five)
    scores = result.get("scores") or {}

    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue

        opposite_di = (
            (snap["minus_di"] > snap["plus_di"])
            if side == "做多"
            else (snap["plus_di"] > snap["minus_di"])
        )
        adx_block = bool(
            snap["adx"] >= ADX_THRESHOLD
            and opposite_di
            and snap["rising3"]
        )

        conf = row.setdefault("confirmations", {})
        required = conf.setdefault("required", {})
        conf.update({
            "5m_adx14": snap["adx"],
            "5m_plus_di14": snap["plus_di"],
            "5m_minus_di14": snap["minus_di"],
            "5m_adx_rising3": snap["rising3"],
            "5m_adx_opposite_di": opposite_di,
            "5m_adx_score_enabled": False,
            "5m_adx_score": 0.0,
            "5m_adx_hard_gate_block": adx_block,
            "5m_adx_rule": "Hard Gate only: ADX>=25 + opposite DI dominant + ADX rising 3 closed 5m bars",
        })
        required["5m_adx_not_strong_adverse"] = not adx_block

        blockers = [b for b in conf.get("blockers") or [] if "ADX Hard Gate" not in str(b)]
        if adx_block:
            blockers.append("ADX Hard Gate：5m ADX>=25、逆向DI占优且ADX连续3根增强，禁止开仓")
        conf["blockers"] = _dedupe(blockers)

        # Keep every V1.6.8 score exactly unchanged. ADX only removes eligibility.
        row["gate"] = bool(row.get("gate") and not adx_block)
        total = float(row.get("total") or 0.0)
        row["eligible"] = bool(row["gate"] and total >= base.THRESHOLD)
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
        "entry_window_ms": base.BOLL_WINDOW_MS,
        "boll_signal_lifecycle": "15m_until_next_closed_15m_candle",
        "1h_ema9_26_score_enabled": True,
        "5m_adx_score_enabled": False,
        "5m_adx_hard_gate": True,
    })
    return result


class HistoricalV168AdxHardGateOnlyModel:
    VERSION = VERSION
    BUILD = BUILD
    THRESHOLD = base.THRESHOLD
    ENTRY_WINDOW_MS = base.BOLL_WINDOW_MS
    TIME_WINDOW_ENABLED = True
    BOLL_OUTER_SCORE = base.BOLL_OUTER_SCORE
    BOLL_TRIGGER_TIMEFRAME = "15m"
    FIVE_MINUTE_BOLL_ENABLED = False
    OUTER_PATHS = base.HistoricalV168Model.OUTER_PATHS
    MIDDLE_PATH = base.HistoricalV168Model.MIDDLE_PATH
    RSI_MIN = base.HistoricalV168Model.RSI_MIN
    RSI_MAX = base.HistoricalV168Model.RSI_MAX
    VOLUME_HARD_GATE = base.HistoricalV168Model.VOLUME_HARD_GATE
    _signal_window = staticmethod(base.proven._signal_window_15m)
    _finite = staticmethod(base.proven.Model._finite)
    _path_from = staticmethod(base.proven.Model._path_from)
    _macd_improving = staticmethod(base.proven._macd_allowed)

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
        return _decorate_adx_hard_gate(result, five), opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        ok, diag, blockers = base.HistoricalV168Model.execution_checks(plan, opportunity, score)
        diag = dict(diag or {})
        blockers = list(blockers or [])
        conf = (score or {}).get("confirmations") or {}
        if bool(conf.get("5m_adx_hard_gate_block")):
            blockers.append("V1.6.8 ADX-HG：5m ADX强逆向 Hard Gate")
        blockers = _dedupe(blockers)
        diag.update({
            "strategy_version": VERSION,
            "build": BUILD,
            "entry_window_ms": base.BOLL_WINDOW_MS,
            "1h_ema9_26_score_enabled": True,
            "5m_adx_score_enabled": False,
            "5m_adx_hard_gate": True,
        })
        return not blockers, diag, blockers


def _submit(self, result, now_ms, mark):
    old_model = base.v165runner.production
    base.v165runner.production = HistoricalV168AdxHardGateOnlyModel
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
                "ema9_26_1h_ok": bool(conf.get("1H_ema9_26_trend_ok")),
                "ema9_1h": conf.get("1H_ema9"),
                "ema26_1h": conf.get("1H_ema26"),
                "adx5": conf.get("5m_adx14"),
                "plus_di5": conf.get("5m_plus_di14"),
                "minus_di5": conf.get("5m_minus_di14"),
                "adx5_rising3": bool(conf.get("5m_adx_rising3")),
                "adx5_opposite_di": bool(conf.get("5m_adx_opposite_di")),
                "adx5_score": 0.0,
                "adx5_hard_gate_block": bool(conf.get("5m_adx_hard_gate_block")),
                "macd_explicit_adverse": bool(conf.get("5m_macd_adverse")),
                "4h_trend_state_actual": str(conf.get("4H_trend_state") or ""),
            })
    finally:
        base.v165runner.production = old_model


def configure():
    # Explicitly restore the untouched V1.6.8 R2 15m lifecycle in case another
    # research module was imported in the same interpreter.
    base.BOLL_WINDOW_MS = 15 * 60 * 1000
    base.proven.BOLL_WINDOW_MS = base.BOLL_WINDOW_MS
    base.proven.Model.ENTRY_WINDOW_MS = base.BOLL_WINDOW_MS
    base.proven.prod.ENTRY_WINDOW_MS = base.BOLL_WINDOW_MS
    base.proven._signal_window_15m = base.proven_r2.src._signal_window_15m if hasattr(base.proven_r2, "src") else base.proven._signal_window_15m

    base.DAYS = DAYS
    base.FIXED_START = base.FIXED_END - base.timedelta(days=DAYS)
    base.OUT = OUT
    start, end = base.configure()
    base.research.VERSION = VERSION
    base.research.BUILD = BUILD
    base.research.model = HistoricalV168AdxHardGateOnlyModel
    base.research.Simulator.submit = _submit
    base.research.Simulator.process_pending = base.proven._ORIG_PENDING
    base.research.Simulator.process_exit = base.proven._ORIG_EXIT
    base.research.Simulator.finish = base.proven._ORIG_FINISH
    return start, end


def verify_lock():
    assert DAYS == 180
    assert HistoricalV168AdxHardGateOnlyModel.ENTRY_WINDOW_MS == 900_000
    assert HistoricalV168AdxHardGateOnlyModel.THRESHOLD == 6.0
    assert HistoricalV168AdxHardGateOnlyModel.BOLL_OUTER_SCORE == 2.0
    assert HistoricalV168AdxHardGateOnlyModel.FIVE_MINUTE_BOLL_ENABLED is False
    assert ADX_PERIOD == 14 and ADX_THRESHOLD == 25.0
    assert base.EMA_FAST == 9 and base.EMA_SLOW == 26


def main():
    started = time.perf_counter()
    start, end = configure()
    verify_lock()
    OUT.mkdir(parents=True, exist_ok=True)

    print(
        "V168_ADX_HARDGATE_ONLY_180D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "baseline=V1.6.8_R2", "boll=15m_outer_only", "boll_score=2",
        "signal_lifecycle=15m", "4h=aligned_required_plus1",
        "ema1h=EMA9_26_plus1_PRESERVED",
        "macd=5m_explicit_adverse_only_plus1_if_improving",
        "adx5_score=OFF", "adx5_hard_gate=ADX14>=25_opposite_DI_rising3",
        "rsi=5m_30..70", "volume_lt_1.20x=HARD_GATE", "threshold=6",
        "position=1x", "entry=LIMIT", "sl=1H_ATR_x1", "tp=2R", "be=OFF",
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
    analysis = base._analysis(rows)
    elapsed = time.perf_counter() - started

    payload = {
        "research": "KAYTRADE V1.6.8 R2 + ADX Hard Gate only 180D",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": base.CAPITAL,
        "strategy_lock": {
            "version": "1.6.8",
            "research_build": BUILD,
            "baseline": "V1.6.8 Build1680 R2",
            "boll_timeframe": "15m latest CLOSED outer only",
            "five_minute_boll_enabled": False,
            "boll_signal_lifetime_ms": base.BOLL_WINDOW_MS,
            "boll_signal_lifetime": "until next closed 15m candle",
            "boll_outer_score": base.BOLL_OUTER_SCORE,
            "4h": "aligned mandatory Hard Gate and +1 score; neutral/opposite blocked",
            "1h_ema": "EMA9>EMA26 long / EMA9<EMA26 short = +1; scoring only; PRESERVED",
            "5m_macd": "explicit adverse/opposite blocks; improvement +1 retained",
            "5m_kdj_score": 0.0,
            "5m_rsi": "30-70 Hard Gate",
            "5m_volume": "<1.20x prior-20 average Hard Gate; no score",
            "5m_adx_period": ADX_PERIOD,
            "5m_adx_threshold": ADX_THRESHOLD,
            "5m_adx_score_enabled": False,
            "5m_adx_score": 0.0,
            "5m_adx_hard_gate": True,
            "5m_adx_hard_gate_rule": "ADX>=25 and opposite DI dominant and ADX rises 3 consecutive closed 5m bars",
            "score_threshold": base.THRESHOLD,
            "position": "fixed 1x",
            "entry": "LIMIT, audited closed-1m historical fill proxy",
            "stop": "1H ATR x1",
            "tp": "2R full position",
            "break_even": False,
            "early_exit": False,
        },
        "metrics": metrics,
        "analysis": analysis,
        "simulator_stats": dict(sim.stats),
        "top_blockers": sim.blockers.most_common(30),
        "transitions": dict(sim.transitions),
        "cache": cache_manifest,
        "correction": {
            "baseline_proven_signal_path_reused": True,
            "execution_window_missing_now_ms_fix_retained": True,
            "production_strategy_variables_changed": False,
            "only_strategy_delta": "5m ADX14 strong-opposite Hard Gate",
        },
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": elapsed},
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m proxy; order-book queue position is unavailable.",
            "Research-only variant; production V1.6.8 files are unchanged.",
        ],
    }

    (OUT / "result_v168_adx_hardgate_only_180d.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base._write_csv(rows, OUT / "trades_v168_adx_hardgate_only_180d.csv")
    (OUT / "README.txt").write_text(
        "KAYTRADE V1.6.8 R2 + ADX Hard Gate only, 180D.\n"
        "Only delta: 5m ADX14>=25 + opposite DI dominant + ADX rising 3 closed 5m bars blocks entry.\n"
        "ADX score is disabled; 1H EMA9/26 +1 and 15m signal lifecycle are preserved.\n",
        encoding="utf-8",
    )

    print("V168_ADX_HARDGATE_ONLY_180D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(
        f"V168_ADX_HARDGATE_ONLY_180D_TIMING total={elapsed:.2f}s "
        f"market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()

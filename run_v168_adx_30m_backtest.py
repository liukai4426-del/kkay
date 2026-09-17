#!/usr/bin/env python3
"""KAYTRADE V1.6.8 ADX30m research backtest.

Changes versus V1.6.8 R2 baseline:
- remove 1H EMA9/EMA26 +1 score (direction framework remains untouched);
- add 5m ADX(14) +DI/-DI confirmation: ADX>=25 and DI aligned = +1;
- add 5m ADX hard gate: ADX>=25, opposite DI dominant, ADX rising 3 closed 5m bars => block;
- extend the 15m BOLL outer opportunity lifetime from 15m to 30m;
- all other V1.6.8 R2 rules stay unchanged.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import timedelta
from pathlib import Path

import run_v168_180d_backtest_r2 as base

ADX_PERIOD = 14
ADX_THRESHOLD = 25.0
ADX_SCORE = 1.0
BOLL_WINDOW_MS = 30 * 60 * 1000
THRESHOLD = 6.0
CAPITAL = 2000.0
BUILD = "1680-adx30m"
VERSION = "1.6.8-ADX30M-research"
_ADX_CACHE = {}


def _finite(v, default=0.0):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _patch_30m_window():
    base.BOLL_WINDOW_MS = BOLL_WINDOW_MS
    base.proven.BOLL_WINDOW_MS = BOLL_WINDOW_MS
    base.proven.Model.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    base.proven.prod.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    base.proven.prod.TIME_WINDOW_ENABLED = True


def _signal_window_30m(opportunity, now_ms):
    start = base.proven.prod._signal_close_ms(opportunity)
    now = int(now_ms or 0)
    end = start + BOLL_WINDOW_MS if start > 0 else 0
    opened = bool(start > 0 and start <= now < end)
    remaining_ms = max(0, end - now) if end else 0
    age_ms = max(0, now - start) if start else 0
    return opened, age_ms, remaining_ms, end


def _install_window_patch():
    _patch_30m_window()
    base.proven._signal_window_15m = _signal_window_30m
    base.proven.Model._signal_window = staticmethod(_signal_window_30m)


def _adx_series(five, period=ADX_PERIOD):
    n = len(five)
    if n < period * 2 + 2:
        return []
    tr = [0.0] * n
    pdm = [0.0] * n
    mdm = [0.0] * n
    for i in range(1, n):
        h = float(five[i]["h"]); l = float(five[i]["l"])
        ph = float(five[i-1]["h"]); pl = float(five[i-1]["l"]); pc = float(five[i-1]["c"])
        up = h - ph
        down = pl - l
        pdm[i] = up if up > down and up > 0 else 0.0
        mdm[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    sm_tr = sum(tr[1:period+1])
    sm_p = sum(pdm[1:period+1])
    sm_m = sum(mdm[1:period+1])
    dx = []
    points = []
    for i in range(period, n):
        if i > period:
            sm_tr = sm_tr - sm_tr / period + tr[i]
            sm_p = sm_p - sm_p / period + pdm[i]
            sm_m = sm_m - sm_m / period + mdm[i]
        plus = 100.0 * sm_p / sm_tr if sm_tr > 0 else 0.0
        minus = 100.0 * sm_m / sm_tr if sm_tr > 0 else 0.0
        denom = plus + minus
        cur_dx = 100.0 * abs(plus - minus) / denom if denom > 0 else 0.0
        dx.append((i, cur_dx, plus, minus))

    if len(dx) < period:
        return []
    adx = sum(x[1] for x in dx[:period]) / period
    first_i, _, plus, minus = dx[period-1]
    points.append((first_i, adx, plus, minus))
    for i, cur_dx, plus, minus in dx[period:]:
        adx = ((adx * (period - 1)) + cur_dx) / period
        points.append((i, adx, plus, minus))
    return points


def _adx_snapshot(five):
    if not five:
        return {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0, "rising3": False}
    key = int(five[-1]["t"])
    cached = _ADX_CACHE.get(key)
    if cached is not None:
        return cached
    pts = _adx_series(five)
    if not pts:
        snap = {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0, "rising3": False}
    else:
        _, adx, plus, minus = pts[-1]
        rising = len(pts) >= 3 and pts[-1][1] > pts[-2][1] > pts[-3][1]
        snap = {"adx": float(adx), "plus_di": float(plus), "minus_di": float(minus), "rising3": bool(rising)}
    _ADX_CACHE[key] = snap
    return snap


def _decorate_adx(result, opp, five):
    if not isinstance(result, dict):
        return result
    snap = _adx_snapshot(five)
    scores = result.get("scores") or {}

    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue
        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
        layers.pop("ema9_26_1h", None)

        aligned_di = (snap["plus_di"] > snap["minus_di"]) if side == "做多" else (snap["minus_di"] > snap["plus_di"])
        opposite_di = (snap["minus_di"] > snap["plus_di"]) if side == "做多" else (snap["plus_di"] > snap["minus_di"])
        adx_score = ADX_SCORE if snap["adx"] >= ADX_THRESHOLD and aligned_di else 0.0
        adx_block = bool(snap["adx"] >= ADX_THRESHOLD and opposite_di and snap["rising3"])
        layers["adx5_confirmation"] = adx_score
        row["layers"] = layers

        conf = row.setdefault("confirmations", {})
        required = conf.setdefault("required", {})
        conf.update({
            "1H_ema9_26_score_enabled": False,
            "5m_adx14": snap["adx"],
            "5m_plus_di14": snap["plus_di"],
            "5m_minus_di14": snap["minus_di"],
            "5m_adx_rising3": snap["rising3"],
            "5m_adx_direction_aligned": aligned_di,
            "5m_adx_score": adx_score,
            "5m_adx_hard_gate_block": adx_block,
            "5m_adx_rule": "ADX>=25 + DI aligned = +1; ADX>=25 + opposite DI + 3-bar rising ADX = Hard Gate",
        })
        required["5m_adx_not_strong_adverse"] = not adx_block
        blockers = [b for b in conf.get("blockers") or [] if "ADX" not in str(b)]
        if adx_block:
            blockers.append("ADX Hard Gate：5m ADX>=25、逆向DI占优且ADX连续3根增强，禁止开仓")
        conf["blockers"] = _dedupe(blockers)

        numeric = []
        for value in layers.values():
            try:
                n = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(n):
                numeric.append(n)
        raw = sum(numeric)
        total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
        row["raw"] = raw
        row["total"] = total
        row["required"] = THRESHOLD
        row["gate"] = bool(row.get("gate") and not adx_block)
        row["eligible"] = bool(row["gate"] and total >= THRESHOLD)
        row["position_multiplier"] = 1.0 if row["eligible"] else 0.0

    qualified = [s for s, r in scores.items() if isinstance(r, dict) and r.get("eligible")]
    selected = "观望"
    if len(qualified) == 1:
        selected = qualified[0]
    elif len(qualified) == 2:
        a, b = qualified
        av = float(scores[a].get("total") or 0.0); bv = float(scores[b].get("total") or 0.0)
        if av != bv:
            selected = a if av > bv else b
    result["side"] = selected
    result.update({
        "strategy_version": VERSION,
        "build": BUILD,
        "entry_window_ms": BOLL_WINDOW_MS,
        "boll_signal_lifecycle": "30m_from_closed_15m_trigger",
        "1h_ema9_26_score_enabled": False,
        "5m_adx_score_enabled": True,
        "5m_adx_score": ADX_SCORE,
        "5m_adx_hard_gate": True,
    })
    return result


class HistoricalV168Adx30mModel:
    VERSION = VERSION
    BUILD = BUILD
    THRESHOLD = THRESHOLD
    ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    TIME_WINDOW_ENABLED = True
    BOLL_OUTER_SCORE = 2.0
    BOLL_TRIGGER_TIMEFRAME = "15m"
    FIVE_MINUTE_BOLL_ENABLED = False
    OUTER_PATHS = base.proven.Model.OUTER_PATHS
    MIDDLE_PATH = base.proven.Model.MIDDLE_PATH
    RSI_MIN = base.proven.Model.RSI_MIN
    RSI_MAX = base.proven.Model.RSI_MAX
    VOLUME_HARD_GATE = base.proven.Model.VOLUME_HARD_GATE
    _signal_window = staticmethod(_signal_window_30m)
    _finite = staticmethod(base.proven.Model._finite)
    _path_from = staticmethod(base.proven.Model._path_from)
    _macd_improving = staticmethod(base.proven._macd_allowed)

    @staticmethod
    def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                 maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                 now_ms=None, allow_new=True):
        _install_window_patch()
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
        if isinstance(opp, dict):
            start = base.proven.prod._signal_close_ms(opp)
            opp["expires_ms"] = start + BOLL_WINDOW_MS if start > 0 else 0
            opp["signal_valid_30m"] = True
        effective_now = int(now_ms if now_ms is not None else (int(one[-1]["t"]) + 60_000 if one else 0))
        result = base._decorate_v168(result, opp, hour, four, effective_now)
        return _decorate_adx(result, opp, five), opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        _install_window_patch()
        ok, diag, blockers = base.proven_r2.corrected_execution_checks(plan, opportunity, score)
        diag = dict(diag or {})
        blockers = list(blockers or [])
        conf = (score or {}).get("confirmations") or {}
        if str(conf.get("4H_trend_state") or "") != "aligned":
            blockers.append("V1.6.8 ADX30m：4H非同向 Hard Gate")
        if bool(conf.get("5m_adx_hard_gate_block")):
            blockers.append("V1.6.8 ADX30m：5m ADX强逆向 Hard Gate")

        # R2-compatible lifecycle handling: do not coerce a missing execution
        # timestamp to zero. The proven R2 check above already enforces the
        # production lifecycle. Re-check the research 30m window only when a
        # real diagnostic timestamp is available.
        now_ms = int(diag.get("now_ms") or 0)
        opened = diag.get("signal_window_ok")
        age_ms = diag.get("signal_age_ms")
        remaining_ms = diag.get("signal_remaining_ms")
        end_ms = diag.get("signal_expires_ms")
        if now_ms > 0:
            opened, age_ms, remaining_ms, end_ms = _signal_window_30m(opportunity, now_ms)
            if not opened:
                blockers.append("V1.6.8 ADX30m：15m BOLL触发已超过30分钟有效期")

        blockers = _dedupe(blockers)
        diag.update({
            "strategy_version": VERSION,
            "build": BUILD,
            "entry_window_ms": BOLL_WINDOW_MS,
            "signal_window_ok": opened,
            "signal_age_ms": age_ms,
            "signal_remaining_ms": remaining_ms,
            "signal_expires_ms": end_ms,
            "adx30m_execution_window_missing_now_ms_fix": True,
            "1h_ema9_26_score_enabled": False,
            "5m_adx_score_enabled": True,
            "5m_adx_hard_gate": True,
        })
        return not blockers, diag, blockers


def _submit(self, result, now_ms, mark):
    old_model = base.v165runner.production
    base.v165runner.production = HistoricalV168Adx30mModel
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
                "ema9_26_1h_scored": False,
                "adx5": conf.get("5m_adx14"),
                "plus_di5": conf.get("5m_plus_di14"),
                "minus_di5": conf.get("5m_minus_di14"),
                "adx5_rising3": bool(conf.get("5m_adx_rising3")),
                "adx5_score": conf.get("5m_adx_score"),
                "adx5_hard_gate_block": bool(conf.get("5m_adx_hard_gate_block")),
            })
    finally:
        base.v165runner.production = old_model


def configure(days):
    days = int(days)
    _install_window_patch()
    base.DAYS = days
    base.FIXED_START = base.FIXED_END - timedelta(days=days)
    base.OUT = Path(f"backtest_output_v168_adx30m_{days}d")
    start, end = base.configure()
    base.research.VERSION = VERSION
    base.research.BUILD = BUILD
    base.research.model = HistoricalV168Adx30mModel
    base.research.Simulator.submit = _submit
    base.research.Simulator.process_pending = base.proven._ORIG_PENDING
    base.research.Simulator.process_exit = base.proven._ORIG_EXIT
    base.research.Simulator.finish = base.proven._ORIG_FINISH
    return start, end


def verify_lock(days):
    assert int(days) in (180, 360, 720)
    assert HistoricalV168Adx30mModel.ENTRY_WINDOW_MS == 1_800_000
    assert HistoricalV168Adx30mModel.THRESHOLD == 6.0
    assert HistoricalV168Adx30mModel.BOLL_OUTER_SCORE == 2.0
    assert HistoricalV168Adx30mModel.FIVE_MINUTE_BOLL_ENABLED is False
    assert ADX_PERIOD == 14 and ADX_THRESHOLD == 25.0 and ADX_SCORE == 1.0


def main():
    days = int(os.environ.get("BACKTEST_DAYS", "360"))
    started = time.perf_counter()
    start, end = configure(days)
    verify_lock(days)
    out = Path(f"backtest_output_v168_adx30m_{days}d")
    out.mkdir(parents=True, exist_ok=True)
    print(
        "V168_ADX30M_CONFIG",
        f"days={days}", f"start={start.isoformat()}", f"end={end.isoformat()}",
        "boll=15m_outer_only", "boll_score=2", "signal_lifecycle=30m",
        "ema9_26_1h_score=REMOVED", "adx5=ADX14_DI_plus1", "adx_threshold=25",
        "adx_hard_gate=opposite_DI_and_ADX_rising3", "threshold=6",
        "other_rules=V1.6.8_R2_unchanged", flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = base.load_market(base.research.base, base.TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    base.research.cache_patch.prime_one_minute(data["1m"], base.research.MODEL_WINDOW)

    sim_started = time.perf_counter()
    # The legacy research simulator only recognizes its built-in variant labels.
    # This runner already installs the ADX30m model above, so use baseline here
    # to avoid the legacy variant_accept("adx30m") ValueError.
    sim, raw_metrics = base.research.run(data, ts, meta, funding, variant="baseline")
    sim_elapsed = time.perf_counter() - sim_started
    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    metrics["days"] = days
    analysis = base._analysis(rows)
    elapsed = time.perf_counter() - started

    payload = {
        "research": f"KAYTRADE V1.6.8 ADX30m {days}D research",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "capital": CAPITAL,
        "strategy_lock": {
            "version": VERSION,
            "build": BUILD,
            "boll_timeframe": "15m latest CLOSED outer only",
            "five_minute_boll_enabled": False,
            "boll_signal_lifetime_ms": BOLL_WINDOW_MS,
            "boll_signal_lifetime": "30 minutes from closed 15m trigger",
            "boll_outer_score": 2.0,
            "4h": "aligned mandatory Hard Gate and +1",
            "1h_15m_direction": "unchanged V1.6.8 direction filter",
            "1h_ema9_26_score_enabled": False,
            "5m_adx_period": ADX_PERIOD,
            "5m_adx_threshold": ADX_THRESHOLD,
            "5m_adx_score_enabled": True,
            "5m_adx_score": ADX_SCORE,
            "5m_adx_score_rule": "ADX>=25 and DI aligned with trade direction = +1",
            "5m_adx_hard_gate": True,
            "5m_adx_hard_gate_rule": "ADX>=25 and opposite DI dominant and ADX rises 3 consecutive closed 5m bars",
            "5m_macd": "V1.6.8 unchanged: explicit adverse blocks; improving +1",
            "5m_rsi": "30-70 Hard Gate",
            "5m_volume": "V1.6.8 unchanged Hard Gate; no score",
            "score_threshold": THRESHOLD,
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
            "revision": "ADX30m-R2",
            "execution_window_missing_now_ms_fix": True,
            "legacy_variant_gate_fix": True,
            "strategy_variables_changed": False,
        },
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": elapsed},
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m proxy; order-book queue position is unavailable.",
            "Research-only variant; production V1.6.8 files are unchanged.",
        ],
    }
    result_path = out / f"result_v168_adx30m_{days}d.json"
    csv_path = out / f"trades_v168_adx30m_{days}d.csv"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    base._write_csv(rows, csv_path)
    (out / "README.txt").write_text(
        f"KAYTRADE V1.6.8 ADX30m {days}D research backtest.\n"
        "Changes: remove 1H EMA9/26 +1; 5m ADX14 aligned DI +1; strong opposite ADX Hard Gate; 15m BOLL signal valid 30m.\n"
        "R2 plumbing fix: missing execution now_ms is not coerced to zero; legacy simulator uses baseline label while ADX30m model stays installed.\n",
        encoding="utf-8",
    )
    print("V168_ADX30M_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"V168_ADX30M_TIMING total={elapsed:.2f}s market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    main()

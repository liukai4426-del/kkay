#!/usr/bin/env python3
"""KAYTRADE research: current V1.6.5 with 15m BOLL and restored 15m middle path.

Only research changes:
1) BOLL signal source moves from closed 5m to closed 15m candles;
2) restore the mirrored BOLL middle pullback path on 15m.

Current production gates remain locked for BOTH outer and restored middle paths:
5m RSI 30..70, formal 5m MACD improvement, 4H aligned, current Volume hard gate,
score >= 6.0, fixed 1x LIMIT entry, 1H ATR x1 stop, 2R full TP, No-BE,
no Early Exit. A closed 15m BOLL package is valid until the next 15m close.
"""
from __future__ import annotations

import csv
import json
import math
import time
from collections import defaultdict
from pathlib import Path

import run_v166_entry_expansion_4way_360d as slow
import run_v165_1000d_2000u as v165runner
import v165_model as prod
import v163_model as v163
from research_backtest_data_cache import load_market

research = slow.research
DAYS = 360
OUT = Path("backtest_output_v166_15m_boll_middle_360d_fast")
TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H")
BOLL_WINDOW_MS = 15 * 60 * 1000
THRESHOLD = 6.0
_CURRENT_QUARTER = None
_REAL_MIDDLE_PATH = prod.MIDDLE_PATH
_SENTINEL_DISABLED_PATH = "__v166_middle_disable_sentinel__"
_ORIG_V163_BOLL = v163.boll_entry_signal
_ORIG_V163_MIDDLE_PATH = v163.MIDDLE_PATH
_ORIG_PENDING = slow.ORIG_PENDING
_ORIG_EXIT = slow.ORIG_EXIT
_ORIG_FINISH = slow.ORIG_FINISH


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _signal_window_15m(opportunity, now_ms):
    start = prod._signal_close_ms(opportunity)
    now = int(now_ms or 0)
    end = start + BOLL_WINDOW_MS if start > 0 else 0
    opened = bool(start > 0 and start <= now < end)
    remaining_ms = max(0, end - now) if end else 0
    age_ms = max(0, now - start) if start else 0
    return opened, age_ms, remaining_ms, end


def _middle_touch_15m(bar, middle, atr15):
    tol = max(0.0, float(atr15)) * 0.10
    return float(bar["l"]) <= float(middle) + tol and float(bar["h"]) >= float(middle) - tol


def _boll15_middle_outer_signal(five, side):
    """15m BOLL middle/outer trigger; current 5m RSI and 5m drift ATR stay intact."""
    quarter = _CURRENT_QUARTER
    if not quarter or len(quarter) < 2 or len(five) < 2:
        return None
    q = v163.indicators(quarter)
    qprev = v163.indicators(quarter[:-1])
    f = v163.indicators(five)
    bar, prev_bar = quarter[-1], quarter[-2]
    middle = float(q["middle"])
    upper = float(q["upper"])
    lower = float(q["lower"])
    prev_middle = float(qprev["middle"])
    prev_close = float(prev_bar["c"])
    atr15 = float(q["atr"])
    atr5 = float(f["atr"])
    rsi5 = float(f["rsi"])
    if not (prod.RSI_MIN <= rsi5 <= prod.RSI_MAX):
        return None
    touch_middle = _middle_touch_15m(bar, middle, atr15)
    if side == "做多":
        middle_ok = prev_close > prev_middle and touch_middle
        outer_ok = float(bar["l"]) <= lower
        if middle_ok:
            path, reference = _REAL_MIDDLE_PATH, middle
        elif outer_ok:
            path, reference = "lower_band", lower
        else:
            return None
    elif side == "做空":
        middle_ok = prev_close < prev_middle and touch_middle
        outer_ok = float(bar["h"]) >= upper
        if middle_ok:
            path, reference = _REAL_MIDDLE_PATH, middle
        elif outer_ok:
            path, reference = "upper_band", upper
        else:
            return None
    else:
        return None
    bar_t = int(bar["t"])
    return {
        "path": path,
        "bar_t": bar_t,
        "signal_close_ms": bar_t + BOLL_WINDOW_MS,
        "reference": float(reference),
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "rsi5": rsi5,
        "atr5": atr5,
        "atr15_signal": atr15,
        "bar_low": float(bar["l"]),
        "bar_high": float(bar["h"]),
        "boll_timeframe": "15m",
    }


def _strip_outer_only_middle_blockers(blockers):
    out = []
    for b in blockers or []:
        s = str(b)
        if _REAL_MIDDLE_PATH and any(x in s for x in (
            "BOLL中轨路径已关闭", "仅允许上下外轨", "仅允许5m BOLL上下外轨",
            "仅允许5m BOLL外轨", "中轨已关闭",
        )):
            continue
        out.append(s)
    return _dedupe(out)


def _apply_current_middle_gates(result, opportunity):
    if not isinstance(result, dict) or not isinstance(opportunity, dict):
        return result
    if str(opportunity.get("signal_path") or "") != _REAL_MIDDLE_PATH:
        result["boll_timeframe"] = "15m"
        result["signal_lifecycle"] = "closed_15m_until_next_close"
        return result
    side = str(opportunity.get("side") or "")
    row = (result.get("scores") or {}).get(side)
    if not isinstance(row, dict):
        return result
    conf = row.setdefault("confirmations", {})
    req = conf.setdefault("required", {})
    blockers = _strip_outer_only_middle_blockers(conf.get("blockers") or [])
    rsi = prod._finite(conf.get("rsi5"))
    rsi_ok = bool(rsi is not None and prod.RSI_MIN <= rsi <= prod.RSI_MAX)
    macd_ok = bool(prod._macd_improving(row))
    state = str(conf.get("4H_trend_state") or "")
    four_ok = state == "aligned"
    ratio = prod._finite(opportunity.get("five_signal_volume_ratio"))
    volume_ok = bool(ratio is not None and ratio < prod.VOLUME_HARD_GATE)
    req["restored_middle_rsi_30_70"] = rsi_ok
    req["restored_middle_macd_improving"] = macd_ok
    req["restored_middle_4h_aligned"] = four_ok
    req["restored_middle_volume_below_1_2x"] = volume_ok
    conf["restored_middle_rule"] = "15m middle pullback; current V1.6.5 hard gates retained"
    conf["volume_ratio_5m"] = ratio
    if not rsi_ok:
        blockers.append("15m中轨恢复：5m RSI必须处于30-70")
    if not macd_ok:
        blockers.append("15m中轨恢复：5m MACD必须连续向交易方向改善")
    if not four_ok:
        blockers.append("15m中轨恢复：4H必须与交易方向同向")
    if not volume_ok:
        blockers.append("15m中轨恢复：Volume Hard Gate未通过")
    conf["blockers"] = _dedupe(blockers)
    required_ok = all(bool(v) for v in req.values()) if req else True
    row["gate"] = bool(required_ok and not conf["blockers"])
    row["eligible"] = bool(row["gate"] and float(row.get("total") or 0.0) >= THRESHOLD)
    row["position_multiplier"] = 1.0 if row["eligible"] else 0.0
    row["level"] = "15m BOLL中轨 · 当前Hard Gate · 1×" if row["eligible"] else row.get("level")
    scores = result.get("scores") or {}
    qualified = [s for s, r in scores.items() if isinstance(r, dict) and r.get("eligible")]
    if len(qualified) == 1:
        result["side"] = qualified[0]
    elif len(qualified) == 2:
        a, b = qualified
        sa = float(scores[a].get("total") or 0.0)
        sb = float(scores[b].get("total") or 0.0)
        result["side"] = a if sa > sb else b if sb > sa else "观望"
    else:
        result["side"] = "观望"
    result["boll_timeframe"] = "15m"
    result["signal_lifecycle"] = "closed_15m_until_next_close"
    return result


class Model:
    VERSION = "1.6.6-research-15m-boll-middle"
    ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    TIME_WINDOW_ENABLED = True
    THRESHOLD = THRESHOLD
    # Submit must accept the restored middle path as a valid BOLL path.
    OUTER_PATHS = tuple(prod.OUTER_PATHS) + (_REAL_MIDDLE_PATH,)
    MIDDLE_PATH = _REAL_MIDDLE_PATH
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
        global _CURRENT_QUARTER
        _CURRENT_QUARTER = quarter
        # Keep v163's historical 'middle disabled' guard from deleting the restored path,
        # while leaving v162/v161 middle 4H+MACD rules intact underneath it.
        v163.MIDDLE_PATH = _SENTINEL_DISABLED_PATH
        v163.boll_entry_signal = _boll15_middle_outer_signal
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
        return _apply_current_middle_gates(result, opp), opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        v163.MIDDLE_PATH = _SENTINEL_DISABLED_PATH
        prod.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
        prod.TIME_WINDOW_ENABLED = True
        ok, diag, blockers = prod.execution_checks(plan, opportunity, score)
        path = str((opportunity or {}).get("signal_path") or "")
        blockers = list(blockers or [])
        if path == _REAL_MIDDLE_PATH:
            blockers = _strip_outer_only_middle_blockers(blockers)
            conf = (score or {}).get("confirmations") or {}
            rsi = prod._finite(conf.get("rsi5"))
            if rsi is None or not (prod.RSI_MIN <= rsi <= prod.RSI_MAX):
                blockers.append("15m中轨执行检查：5m RSI不在30-70")
            if not prod._macd_improving(score or {}):
                blockers.append("15m中轨执行检查：5m MACD未连续改善")
            if str(conf.get("4H_trend_state") or "") != "aligned":
                blockers.append("15m中轨执行检查：4H未同向")
            ratio = prod._finite((opportunity or {}).get("five_signal_volume_ratio"))
            if ratio is None or ratio >= prod.VOLUME_HARD_GATE:
                blockers.append("15m中轨执行检查：Volume Hard Gate未通过")
        now_ms = int((diag or {}).get("now_ms") or 0)
        opened, age_ms, remaining_ms, end_ms = _signal_window_15m(opportunity, now_ms)
        if not opened:
            blockers.append("15m BOLL信号已到下一根15m收盘，禁止使用旧信号")
        diag = dict(diag or {})
        diag.update({
            "strategy_version": Model.VERSION,
            "boll_timeframe": "15m",
            "middle_restored": True,
            "entry_window_ms": BOLL_WINDOW_MS,
            "signal_window_ok": opened,
            "signal_age_ms": age_ms,
            "signal_remaining_ms": remaining_ms,
            "signal_expires_ms": end_ms,
        })
        blockers = _dedupe(blockers)
        return not blockers, diag, blockers


def submit_current(self, result, now_ms, mark):
    old_model = v165runner.production
    old_thr = getattr(old_model, "THRESHOLD", THRESHOLD)
    v165runner.production = Model
    try:
        v165runner._submit_v165(self, result, now_ms, mark)
        if self.pending is not None:
            self.pending.factors["boll_timeframe"] = "15m"
            self.pending.factors["middle_restored"] = True
            self.pending.factors["signal_window_ms"] = BOLL_WINDOW_MS
    finally:
        v165runner.production = old_model
        old_model.THRESHOLD = old_thr


def _pf(rows):
    gp = sum(max(float(r.get("net_pnl") or 0.0), 0.0) for r in rows)
    gl = -sum(min(float(r.get("net_pnl") or 0.0), 0.0) for r in rows)
    return gp / gl if gl else (math.inf if gp else 0.0)


def _stats(rows):
    rows = list(rows)
    n = len(rows)
    w = sum(float(r.get("net_pnl") or 0.0) > 0 for r in rows)
    pnl = sum(float(r.get("net_pnl") or 0.0) for r in rows)
    return {"trades": n, "wins": w, "losses": n-w,
            "win_rate_pct": 100.0*w/n if n else 0.0,
            "net_pnl": pnl, "profit_factor": _pf(rows),
            "expectancy": pnl/n if n else 0.0}


def _analysis(rows):
    by_path, by_side, by_score = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in rows:
        factors = r.get("factors") or {}
        path = str(factors.get("signal_path") or factors.get("boll_path") or r.get("signal_path") or "unknown")
        by_path[path].append(r)
        by_side[str(r.get("side") or "unknown")].append(r)
        by_score[f"{float(r.get('score') or 0.0):.1f}"].append(r)
    return {
        "by_path": {k: _stats(v) for k, v in by_path.items()},
        "by_side": {k: _stats(v) for k, v in by_side.items()},
        "by_score": {k: _stats(v) for k, v in sorted(by_score.items(), key=lambda kv: float(kv[0]))},
    }


def _write_csv(rows, path):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys, flat = [], []
    for row in rows:
        item = dict(row)
        for k, v in list(item.items()):
            if isinstance(v, (dict, list, tuple)):
                item[k] = json.dumps(v, ensure_ascii=False)
        flat.append(item)
        for k in item:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader(); w.writerows(flat)


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
    print("V166_15M_BOLL_MIDDLE_360D_FAST", f"start={start.isoformat()}", f"end={end.isoformat()}",
          "boll=15m_middle+outer", "lifetime=15m", "rsi=5m_30..70", "macd=5m_required",
          "4h=aligned_required", "threshold=6.0", "volume=current_hard_gate", "sl=1H_ATR", "tp=2R", "be=OFF", flush=True)
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
    _write_csv(rows, OUT / "trades_15m_boll_middle_360d.csv")
    payload = {
        "research": "V1.6.5 current model: 15m BOLL middle+outer restored, 360D fast",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "changes": {"boll_timeframe": "15m", "middle_path": "restored", "boll_signal_lifetime": "until next closed 15m candle"},
        "unchanged": {"rsi": "5m 30-70", "macd": "formal 5m improvement required", "4h": "aligned required",
                      "volume": "current V1.6.5 hard-gate semantics", "score_threshold": 6.0, "position": "fixed 1x",
                      "entry": "LIMIT", "stop": "1H ATR x1", "tp": "2R full position", "break_even": False, "early_exit": False},
        "metrics": summary,
        "analysis": _analysis(rows),
        "cache": cache_manifest,
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": total_elapsed},
    }
    (OUT / "result_15m_boll_middle_360d.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "metrics_raw.json").write_text(json.dumps(dict(metrics), ensure_ascii=False, indent=2), encoding="utf-8")
    print("RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"FAST_TIMING total={total_elapsed:.2f}s market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        v163.boll_entry_signal = _ORIG_V163_BOLL
        v163.MIDDLE_PATH = _ORIG_V163_MIDDLE_PATH

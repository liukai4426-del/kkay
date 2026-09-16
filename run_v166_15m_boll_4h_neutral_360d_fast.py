#!/usr/bin/env python3
"""KAYTRADE research: current V1.6.5 semantics with two isolated changes.

Changes under test:
1) BOLL outer trigger uses the latest CLOSED 15m candle instead of 5m.
   The 15m BOLL signal remains valid until the next 15m candle closes.
2) 4H trend neutral is allowed; only explicit 4H trend opposite is blocked.

Everything else stays on current V1.6.5 semantics: 5m RSI 30..70, formal 5m
MACD improvement, current Volume hard gate, score >= 6.0, fixed 1x LIMIT entry,
1H ATR x1 stop, 2R full TP, No-BE, no Early Exit.

The runner uses the persistent market cache introduced by Backtest Speed V1.
"""
from __future__ import annotations

import copy
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
OUT = Path("backtest_output_v166_15m_boll_4h_neutral_360d_fast")
TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H")
BOLL_WINDOW_MS = 15 * 60 * 1000
THRESHOLD = 6.0
_CURRENT_QUARTER = None

_ORIG_V163_BOLL = v163.boll_entry_signal
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


def _alignment_blocker(text):
    s = str(text or "")
    low = s.lower()
    return (
        "4h同向" in low
        or "4h必须与交易方向同向" in low
        or "4h中性/逆向" in low
        or "4h必须4h同向" in low
    )


def _signal_window_15m(opportunity, now_ms):
    start = prod._signal_close_ms(opportunity)
    now = int(now_ms or 0)
    end = start + BOLL_WINDOW_MS if start > 0 else 0
    opened = bool(start > 0 and start <= now < end)
    remaining_ms = max(0, end - now) if end else 0
    age_ms = max(0, now - start) if start else 0
    return opened, age_ms, remaining_ms, end


def _boll15_outer_signal(five, side):
    """15m BOLL trigger while preserving current 5m RSI/ATR context."""
    quarter = _CURRENT_QUARTER
    if not quarter or len(quarter) < 2 or len(five) < 2:
        return None
    q = v163.indicators(quarter)
    f = v163.indicators(five)
    bar = quarter[-1]
    upper = float(q["upper"])
    lower = float(q["lower"])
    middle = float(q["middle"])
    rsi5 = float(f["rsi"])
    atr5 = float(f["atr"])
    if not (prod.RSI_MIN <= rsi5 <= prod.RSI_MAX):
        return None
    if side == "做多":
        if float(bar["l"]) > lower:
            return None
        path, reference = "lower_band", lower
    elif side == "做空":
        if float(bar["h"]) < upper:
            return None
        path, reference = "upper_band", upper
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
        "bar_low": float(bar["l"]),
        "bar_high": float(bar["h"]),
        "boll_timeframe": "15m",
    }


def _relax_4h_neutral(result, opportunity):
    if not isinstance(result, dict):
        return result
    scores = result.get("scores") or {}
    for row in scores.values():
        if not isinstance(row, dict):
            continue
        path = prod._path_from(row, result=result, opportunity=opportunity)
        if path not in prod.OUTER_PATHS:
            continue
        conf = row.setdefault("confirmations", {})
        state = str(conf.get("4H_trend_state") or "")
        if state != "neutral":
            continue
        required = conf.setdefault("required", {})
        for key in list(required):
            if "4h" in str(key).lower() and "adverse" not in str(key).lower():
                required[key] = True
        blockers = [b for b in conf.get("blockers") or [] if not _alignment_blocker(b)]
        conf["blockers"] = _dedupe(blockers)
        conf["4H_trend_rule_research"] = "aligned or neutral allowed; explicit opposite blocked"
        required_ok = all(bool(v) for v in required.values()) if required else True
        row["gate"] = bool(required_ok and not blockers)
        row["eligible"] = bool(row["gate"] and float(row.get("total") or 0.0) >= THRESHOLD)
        row["position_multiplier"] = 1.0 if row["eligible"] else 0.0
        if row["eligible"]:
            row["level"] = "15m BOLL外轨 · 4H中性允许 · 1×"
    qualified = [s for s, r in scores.items() if isinstance(r, dict) and r.get("eligible")]
    if len(qualified) == 1:
        result["side"] = qualified[0]
    elif len(qualified) == 2:
        a, b = qualified
        sa = float(scores[a].get("total") or 0.0)
        sb = float(scores[b].get("total") or 0.0)
        result["side"] = a if sa > sb else b if sb > sa else "观望"
    elif result.get("side") not in qualified:
        result["side"] = "观望"
    result["boll_timeframe"] = "15m"
    result["signal_lifecycle"] = "closed_15m_until_next_close"
    return result


class Model:
    VERSION = "1.6.6-research-15m-boll-4h-neutral"
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
        global _CURRENT_QUARTER
        _CURRENT_QUARTER = quarter
        # The V1.6.5 patch re-pins signal_core.boll_entry_signal from v163 each call.
        # Point v163's source at the 15m-BOLL adapter before entering production code.
        v163.boll_entry_signal = _boll15_outer_signal
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
        result = _relax_4h_neutral(result, opp)
        return result, opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        prod.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
        prod.TIME_WINDOW_ENABLED = True
        ok, diag, blockers = prod.execution_checks(plan, opportunity, score)
        blockers = list(blockers or [])
        conf = (score or {}).get("confirmations") or {}
        state = str(conf.get("4H_trend_state") or "")
        if state == "neutral":
            blockers = [b for b in blockers if not _alignment_blocker(b)]
        opened, age_ms, remaining_ms, end_ms = _signal_window_15m(opportunity, int((diag or {}).get("now_ms") or 0))
        diag = dict(diag or {})
        diag.update({
            "strategy_version": Model.VERSION,
            "boll_timeframe": "15m",
            "entry_window_ms": BOLL_WINDOW_MS,
            "signal_window_ok": opened,
            "signal_age_ms": age_ms,
            "signal_remaining_ms": remaining_ms,
            "signal_expires_ms": end_ms,
            "4h_neutral_allowed": True,
            "4h_explicit_opposite_blocked": True,
        })
        if not opened:
            blockers.append("15m BOLL信号已到下一根15m收盘，禁止使用旧信号开仓")
        return not _dedupe(blockers), diag, _dedupe(blockers)


def _prepare_submit_result(result):
    """Adapt only the legacy aligned-only submit guard; keep actual state in factors."""
    cloned = copy.deepcopy(result)
    side = str(((cloned.get("opportunity") or {}).get("side")) or "")
    row = (cloned.get("scores") or {}).get(side)
    actual = ""
    if isinstance(row, dict):
        conf = row.setdefault("confirmations", {})
        actual = str(conf.get("4H_trend_state") or "")
        if actual == "neutral":
            conf["4H_trend_state"] = "aligned"
            req = conf.setdefault("required", {})
            for key in list(req):
                if "4h" in str(key).lower() and "adverse" not in str(key).lower():
                    req[key] = True
            conf["blockers"] = [b for b in conf.get("blockers") or [] if not _alignment_blocker(b)]
    return cloned, actual


def submit_current(self, result, now_ms, mark):
    adapted, actual_state = _prepare_submit_result(result)
    old_model = v165runner.production
    old_threshold = getattr(old_model, "THRESHOLD", THRESHOLD)
    v165runner.production = Model
    try:
        v165runner._submit_v165(self, adapted, now_ms, mark)
        if self.pending is not None:
            self.pending.factors["boll_timeframe"] = "15m"
            self.pending.factors["signal_window_ms"] = BOLL_WINDOW_MS
            self.pending.factors["4h_trend_state_actual"] = actual_state
            self.pending.factors["4h_neutral_allowed"] = True
    finally:
        v165runner.production = old_model
        old_model.THRESHOLD = old_threshold


def _pf(rows):
    gp = sum(max(float(r.get("net_pnl") or 0.0), 0.0) for r in rows)
    gl = -sum(min(float(r.get("net_pnl") or 0.0), 0.0) for r in rows)
    return gp / gl if gl else (math.inf if gp else 0.0)


def _stats(rows):
    rows = list(rows)
    n = len(rows)
    w = sum(float(r.get("net_pnl") or 0.0) > 0 for r in rows)
    pnl = sum(float(r.get("net_pnl") or 0.0) for r in rows)
    return {
        "trades": n,
        "wins": w,
        "losses": n - w,
        "win_rate_pct": 100.0 * w / n if n else 0.0,
        "net_pnl": pnl,
        "profit_factor": _pf(rows),
        "expectancy": pnl / n if n else 0.0,
    }


def _analysis(rows):
    side = defaultdict(list)
    score = defaultdict(list)
    h4 = defaultdict(list)
    for r in rows:
        side[str(r.get("side") or "unknown")].append(r)
        score[f"{float(r.get('score') or 0.0):.1f}"].append(r)
        factors = r.get("factors") or {}
        h4[str(factors.get("4h_trend_state_actual") or "unknown")].append(r)
    return {
        "by_side": {k: _stats(v) for k, v in side.items()},
        "by_score": {k: _stats(v) for k, v in sorted(score.items(), key=lambda kv: float(kv[0]))},
        "by_4h_state": {k: _stats(v) for k, v in h4.items()},
    }


def _write_csv(rows, path):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    flat = []
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
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(flat)


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
        "V166_15M_BOLL_4H_NEUTRAL_360D_FAST",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "boll=15m_outer", "boll_lifetime=15m", "rsi=5m_30..70",
        "macd=5m_required", "4h=aligned_or_neutral", "threshold=6.0",
        "volume=current_hard_gate", "sl=1H_ATR", "tp=2R", "be=OFF",
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
    analysis = _analysis(rows)
    total_elapsed = time.perf_counter() - started

    _write_csv(rows, OUT / "trades_15m_boll_4h_neutral_360d.csv")
    payload = {
        "research": "V1.6.5 current model: 15m BOLL outer + 4H neutral allowed, 360D fast",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "changes": {
            "boll_timeframe": "15m",
            "boll_signal_lifetime": "until next closed 15m candle",
            "4h_trend_gate": "aligned or neutral allowed; explicit opposite blocked",
        },
        "unchanged": {
            "rsi": "5m 30-70",
            "macd": "formal 5m improvement required",
            "volume": "current V1.6.5 hard-gate semantics",
            "score_threshold": 6.0,
            "position": "fixed 1x",
            "entry": "LIMIT",
            "stop": "1H ATR x1",
            "tp": "2R full position",
            "break_even": False,
            "early_exit": False,
        },
        "metrics": summary,
        "analysis": analysis,
        "cache": cache_manifest,
        "timing_sec": {
            "market_load": market_elapsed,
            "simulation": sim_elapsed,
            "total": total_elapsed,
        },
    }
    (OUT / "result_15m_boll_4h_neutral_360d.json").write_text(
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

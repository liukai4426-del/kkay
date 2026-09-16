#!/usr/bin/env python3
"""KAYTRADE research: current V1.6.5 semantics with two isolated changes.

Changes under test:
1) BOLL outer trigger uses latest CLOSED 15m candle instead of 5m; signal is
   valid until the next 15m candle closes.
2) 5m MACD is no longer required to formally improve. It blocks opening only
   when the existing model explicitly marks 5m MACD as adverse/opposite.

Everything else remains current V1.6.5: 5m RSI 30..70, 4H must be aligned,
current Volume hard gate, score >= 6.0, fixed 1x LIMIT entry, 1H ATR x1 stop,
2R full TP, No-BE, no Early Exit.
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
OUT = Path("backtest_output_v166_15m_boll_macd_adverse_only_360d_fast")
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


def _signal_window_15m(opportunity, now_ms):
    start = prod._signal_close_ms(opportunity)
    now = int(now_ms or 0)
    end = start + BOLL_WINDOW_MS if start > 0 else 0
    opened = bool(start > 0 and start <= now < end)
    remaining_ms = max(0, end - now) if end else 0
    age_ms = max(0, now - start) if start else 0
    return opened, age_ms, remaining_ms, end


def _boll15_outer_signal(five, side):
    """Use 15m BOLL outer touch while preserving current 5m RSI/ATR context."""
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


def _macd_adverse(score):
    conf = (score or {}).get("confirmations") or {}
    if "5m_macd_adverse" in conf:
        return bool(conf.get("5m_macd_adverse"))
    # Defensive fallbacks for future naming changes; do not interpret mere
    # non-improvement as adverse.
    for key in ("macd5_adverse", "macd_adverse"):
        if key in conf:
            return bool(conf.get(key))
    for key in ("macd5_state", "5m_macd_state", "macd_state"):
        state = str(conf.get(key) or "").lower()
        if state:
            return any(x in state for x in ("opposite", "adverse", "逆向", "恶化"))
    return False


def _macd_allowed(score):
    return not _macd_adverse(score)


def _relax_macd_gate(result, opportunity):
    if not isinstance(result, dict):
        return result
    scores = result.get("scores") or {}
    for side, row in scores.items():
        if not isinstance(row, dict):
            continue
        path = prod._path_from(row, result=result, opportunity=opportunity)
        if path not in prod.OUTER_PATHS:
            continue
        conf = row.setdefault("confirmations", {})
        req = conf.setdefault("required", {})
        adverse = _macd_adverse(row)
        conf["macd_research_rule"] = "5m MACD may be neutral/non-improving; only explicit adverse blocks"
        conf["macd_explicit_adverse"] = adverse
        if not adverse:
            for key in list(req):
                if "macd" in str(key).lower():
                    req[key] = True
            blockers = [b for b in conf.get("blockers") or [] if "macd" not in str(b).lower()]
            conf["blockers"] = _dedupe(blockers)
            req_ok = all(bool(v) for v in req.values()) if req else True
            row["gate"] = bool(req_ok and not blockers)
            row["eligible"] = bool(row["gate"] and float(row.get("total") or 0.0) >= THRESHOLD)
            row["position_multiplier"] = 1.0 if row["eligible"] else 0.0
            if row["eligible"]:
                row["level"] = "15m BOLL外轨 · MACD非逆向 · 1×"
        else:
            blockers = list(conf.get("blockers") or [])
            if not any("MACD" in str(b) or "macd" in str(b) for b in blockers):
                blockers.append("研究规则：5m MACD明确逆向恶化，禁止开仓")
            conf["blockers"] = _dedupe(blockers)
            row["gate"] = False
            row["eligible"] = False
            row["position_multiplier"] = 0.0

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
    result["macd_gate"] = "explicit_adverse_only"
    return result


class Model:
    VERSION = "1.6.6-research-15m-boll-macd-adverse-only"
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
    _macd_improving = staticmethod(_macd_allowed)
    _path_from = staticmethod(prod._path_from)

    @staticmethod
    def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                 maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                 now_ms=None, allow_new=True):
        global _CURRENT_QUARTER
        _CURRENT_QUARTER = quarter
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
        return _relax_macd_gate(result, opp), opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        prod.ENTRY_WINDOW_MS = BOLL_WINDOW_MS
        prod.TIME_WINDOW_ENABLED = True
        ok, diag, blockers = prod.execution_checks(plan, opportunity, score)
        blockers = list(blockers or [])
        if not _macd_adverse(score):
            blockers = [b for b in blockers if "macd" not in str(b).lower()]
        elif not any("macd" in str(b).lower() for b in blockers):
            blockers.append("研究规则：5m MACD明确逆向恶化，禁止开仓")
        diag = dict(diag or {})
        opened, age_ms, remaining_ms, end_ms = _signal_window_15m(
            opportunity, int(diag.get("now_ms") or 0)
        )
        diag.update({
            "strategy_version": Model.VERSION,
            "boll_timeframe": "15m",
            "entry_window_ms": BOLL_WINDOW_MS,
            "signal_window_ok": opened,
            "signal_age_ms": age_ms,
            "signal_remaining_ms": remaining_ms,
            "signal_expires_ms": end_ms,
            "macd_gate": "explicit_adverse_only",
            "macd_explicit_adverse": _macd_adverse(score),
            "4h_gate": "aligned_required",
        })
        # Remove production's stale 5m lifecycle wording if it appears, then
        # enforce the actual 15m lifecycle once.
        blockers = [b for b in blockers if not ("5m外轨信号" in str(b) and "下一根5m" in str(b))]
        if not opened:
            blockers.append("15m BOLL信号已到下一根15m收盘，禁止使用旧信号开仓")
        blockers = _dedupe(blockers)
        return not blockers, diag, blockers


def submit_current(self, result, now_ms, mark):
    old_model = v165runner.production
    old_threshold = getattr(old_model, "THRESHOLD", THRESHOLD)
    v165runner.production = Model
    try:
        v165runner._submit_v165(self, result, now_ms, mark)
        if self.pending is not None:
            self.pending.factors["boll_timeframe"] = "15m"
            self.pending.factors["signal_window_ms"] = BOLL_WINDOW_MS
            self.pending.factors["macd_gate"] = "explicit_adverse_only"
            row = (result.get("scores") or {}).get(str((result.get("opportunity") or {}).get("side") or "")) or {}
            self.pending.factors["macd_explicit_adverse"] = _macd_adverse(row)
            self.pending.factors["4h_trend_state_actual"] = str(((row.get("confirmations") or {}).get("4H_trend_state")) or "")
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
    for row in rows:
        side[str(row.get("side") or "unknown")].append(row)
        score[f"{float(row.get('score') or 0.0):.1f}"].append(row)
        factors = row.get("factors") or {}
        h4[str(factors.get("4h_trend_state_actual") or "unknown")].append(row)
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
        for key, value in list(item.items()):
            if isinstance(value, (dict, list, tuple)):
                item[key] = json.dumps(value, ensure_ascii=False)
        flat.append(item)
        for key in item:
            if key not in keys:
                keys.append(key)
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
        "V166_15M_BOLL_MACD_ADVERSE_ONLY_360D_FAST",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "boll=15m_outer", "boll_lifetime=15m", "rsi=5m_30..70",
        "macd=5m_explicit_adverse_only", "4h=aligned_required", "threshold=6.0",
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

    _write_csv(rows, OUT / "trades_15m_boll_macd_adverse_only_360d.csv")
    payload = {
        "research": "V1.6.5 current model: 15m BOLL outer + MACD adverse-only gate, 360D fast",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "changes": {
            "boll_timeframe": "15m",
            "boll_signal_lifetime": "until next closed 15m candle",
            "macd_gate": "5m explicit adverse/opposite only; neutral/non-improving allowed",
        },
        "unchanged": {
            "rsi": "5m 30-70",
            "4h": "aligned required; neutral/opposite blocked",
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
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": total_elapsed},
    }
    (OUT / "result_15m_boll_macd_adverse_only_360d.json").write_text(
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

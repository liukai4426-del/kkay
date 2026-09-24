#!/usr/bin/env python3
"""KAYTRADE V1.6.8 R2 180D research.

Entry/scoring baseline:
- exact audited V1.6.8 R2 180D;
- add latched +1 when any CLOSED 5m candle overextends beyond its 5m BOLL outer band
  by >=0.35 * Wilder ATR(14), valid only inside the exact current 15m BOLL opportunity lifecycle.

Exit overlay:
- Precision Early Exit 3.0 during first 4h only;
- once historical MFE reaches >= +0.60R, PEE3 is permanently disabled for that trade;
- normal V1.6.8 SL/TP processing always runs before PEE3;
- no ADX and no break-even overlay.

Research-only; production files are unchanged.
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import time
from collections import Counter
from pathlib import Path

import run_v168_boll5_overext035_180d as entry
import strategy

base = entry.base
research = base.research
DAYS = 180
OUT = Path("backtest_output_v168_boll5_overext035_pee3_mfe06_180d")
MONITOR_MAX_MS = 4 * 60 * 60_000
MFE_CUTOFF_R = 0.60
STRUCTURE_BREAK_ATR = 0.25
LIMIT_PROTECTION_BPS = 5.0
SOFT_CAP = 3.0
PEE_VERSION = "3.0-mfe06"
EXIT_REASON = "PEE3_EARLY_EXIT_4H_MFE06"
_ORIGINAL_PROCESS_EXIT = base.proven._ORIG_EXIT
_FEATURE_BY_CLOSE_MS = {}


def _safe_ratio(a, b):
    return float(a) / float(b) if float(b) > 0.0 else 0.0


def _prior_mean(rows, i, n=20):
    if i < n:
        return 0.0
    vals = [float(rows[j]["v"]) for j in range(i - n, i)]
    return sum(vals) / len(vals) if vals else 0.0


def _macd_adverse(side, a, b, c):
    return (c < b < a) if side == "做多" else (c > b > a)


def _structure_break(quarter, q_ind, qi):
    empty = {"long": False, "short": False, "support": None, "resistance": None,
             "long_break_atr": 0.0, "short_break_atr": 0.0}
    if qi < 1:
        return empty
    atr15 = float(q_ind[qi].get("atr") or 0.0)
    atr_prev = float(q_ind[qi - 1].get("atr") or 0.0)
    if atr15 <= 0.0 or atr_prev <= 0.0:
        return empty
    prev_rows = quarter[max(0, qi - 220):qi]
    if len(prev_rows) < 20:
        return empty
    zones = strategy._zones(prev_rows, atr_prev, 160)
    prev_close = float(prev_rows[-1]["c"])
    support = strategy._nearest(zones, prev_close, "support")
    resistance = strategy._nearest(zones, prev_close, "resistance")
    current_close = float(quarter[qi]["c"])
    support_px = float(support["price"]) if support else None
    resistance_px = float(resistance["price"]) if resistance else None
    long_break_atr = ((support_px - current_close) / atr15) if support_px is not None else 0.0
    short_break_atr = ((current_close - resistance_px) / atr15) if resistance_px is not None else 0.0
    return {
        "long": support_px is not None and long_break_atr >= STRUCTURE_BREAK_ATR,
        "short": resistance_px is not None and short_break_atr >= STRUCTURE_BREAK_ATR,
        "support": support_px, "resistance": resistance_px,
        "long_break_atr": float(long_break_atr), "short_break_atr": float(short_break_atr),
    }


def _prime_feature_map(five, quarter, hour):
    global _FEATURE_BY_CLOSE_MS
    f_ind = research.base.compute_indicators(five)
    q_ind = research.base.compute_indicators(quarter)
    h_ind = research.base.compute_indicators(hour)
    q_close = [int(r["t"]) + 15 * 60_000 for r in quarter]
    h_close = [int(r["t"]) + 60 * 60_000 for r in hour]
    out = {}
    for i in range(20, len(five)):
        close_ms = int(five[i]["t"]) + 5 * 60_000
        qi = bisect.bisect_right(q_close, close_ms) - 1
        hi = bisect.bisect_right(h_close, close_ms) - 1
        if qi < 2 or hi < 1 or i < 2:
            continue
        f, pf, q, h = f_ind[i], f_ind[i - 1], q_ind[qi], h_ind[hi]
        px5 = float(five[i]["c"])
        atr5 = float(f.get("atr") or 0.0)
        atr5_pct = _safe_ratio(atr5 * 100.0, px5)
        atr5_pct_1 = _safe_ratio(float(pf.get("atr") or 0.0) * 100.0, float(five[i - 1]["c"]))
        atr5_pct_2 = _safe_ratio(float(f_ind[i - 2].get("atr") or 0.0) * 100.0, float(five[i - 2]["c"]))
        vol5_ratio = _safe_ratio(float(five[i]["v"]), _prior_mean(five, i, 20))
        vol15_ratio = _safe_ratio(float(quarter[qi]["v"]), _prior_mean(quarter, qi, 20))
        prev_close5 = float(five[i - 1]["c"])
        lower = float(f.get("lower") or math.nan); prev_lower = float(pf.get("lower") or math.nan)
        upper = float(f.get("upper") or math.nan); prev_upper = float(pf.get("upper") or math.nan)
        long_boll_persistent = all(math.isfinite(x) for x in (lower, prev_lower)) and px5 < lower and prev_close5 < prev_lower
        short_boll_persistent = all(math.isfinite(x) for x in (upper, prev_upper)) and px5 > upper and prev_close5 > prev_upper
        close15 = float(quarter[qi]["c"])
        ema20_15 = float(q.get("ema20") or close15)
        out[close_ms] = {
            "five_bar_start_ms": int(five[i]["t"]),
            "prev_five_bar_start_ms": int(five[i - 1]["t"]),
            "quarter_bar_start_ms": int(quarter[qi]["t"]),
            "hour_bar_start_ms": int(hour[hi]["t"]),
            "open5": float(five[i]["o"]), "close5": px5,
            "open15": float(quarter[qi]["o"]), "close15": close15,
            "rsi5": float(f.get("rsi") or math.nan), "rsi15": float(q.get("rsi") or math.nan),
            "macd5_a": float(f_ind[i - 2].get("hist") or 0.0),
            "macd5_b": float(pf.get("hist") or 0.0), "macd5_c": float(f.get("hist") or 0.0),
            "macd15_a": float(q_ind[qi - 2].get("hist") or 0.0),
            "macd15_b": float(q_ind[qi - 1].get("hist") or 0.0), "macd15_c": float(q.get("hist") or 0.0),
            "atr5_pct": atr5_pct, "atr5_expanding": bool(atr5_pct > atr5_pct_1 > atr5_pct_2),
            "vol5_ratio": vol5_ratio, "vol15_ratio": vol15_ratio,
            "structure": _structure_break(quarter, q_ind, qi),
            "long_boll_persistent": bool(long_boll_persistent), "short_boll_persistent": bool(short_boll_persistent),
            "trend15_long_bad": bool(q.get("down")) and close15 < ema20_15,
            "trend15_short_bad": bool(q.get("up")) and close15 > ema20_15,
            "hour_up": bool(h.get("up")), "hour_down": bool(h.get("down")),
        }
    _FEATURE_BY_CLOSE_MS = out


def _soft_and_hard(side, feat, pos, hold_ms, current_r, mfe_r):
    fill = int(pos.fill_time)
    post5 = int(feat["five_bar_start_ms"]) >= fill
    post5_pair = int(feat["prev_five_bar_start_ms"]) >= fill
    post15 = int(feat["quarter_bar_start_ms"]) >= fill
    post1h = int(feat["hour_bar_start_ms"]) >= fill
    if side == "做多":
        struct15 = post15 and bool(feat["structure"]["long"])
        boll5 = post5_pair and bool(feat["long_boll_persistent"])
        trend15 = post15 and bool(feat["trend15_long_bad"])
        hour_bad = post1h and bool(feat["hour_down"])
        rsi5_bad = post5 and math.isfinite(feat["rsi5"]) and feat["rsi5"] < 40.0
        rsi15_bad = post15 and math.isfinite(feat["rsi15"]) and feat["rsi15"] < 40.0
        adverse_vol5 = post5 and feat["close5"] < feat["open5"] and feat["vol5_ratio"] >= 1.20
        adverse_vol15 = post15 and feat["close15"] < feat["open15"] and feat["vol15_ratio"] >= 1.20
    else:
        struct15 = post15 and bool(feat["structure"]["short"])
        boll5 = post5_pair and bool(feat["short_boll_persistent"])
        trend15 = post15 and bool(feat["trend15_short_bad"])
        hour_bad = post1h and bool(feat["hour_up"])
        rsi5_bad = post5 and math.isfinite(feat["rsi5"]) and feat["rsi5"] > 60.0
        rsi15_bad = post15 and math.isfinite(feat["rsi15"]) and feat["rsi15"] > 60.0
        adverse_vol5 = post5 and feat["close5"] > feat["open5"] and feat["vol5_ratio"] >= 1.20
        adverse_vol15 = post15 and feat["close15"] > feat["open15"] and feat["vol15_ratio"] >= 1.20
    hard = {
        "structure_break_15m": bool(struct15),
        "boll5_persistent_failure": bool(boll5),
        "trend_reversal_15m": bool(trend15),
        "opposite_closed_1h": bool(hour_bad and hold_ms >= 120 * 60_000),
    }
    hard_count = sum(int(v) for v in hard.values())
    macd5 = post5 and _macd_adverse(side, feat["macd5_a"], feat["macd5_b"], feat["macd5_c"])
    macd15 = post15 and _macd_adverse(side, feat["macd15_a"], feat["macd15_b"], feat["macd15_c"])
    hold_min = hold_ms / 60_000.0
    progress = 0.0
    if mfe_r < 0.25:
        if hold_min >= 120: progress = 0.50
        elif hold_min >= 60: progress = 0.25
    components = {
        "macd15": 0.75 if macd15 else 0.0, "macd5": 0.50 if macd5 else 0.0,
        "rsi15": 0.50 if rsi15_bad else 0.0, "rsi5": 0.25 if rsi5_bad else 0.0,
        "adverse_volume": 0.50 if (adverse_vol5 or adverse_vol15) else 0.0,
        "atr_expansion": 0.25 if (current_r < 0 and post5 and feat["atr5_pct"] >= 0.22 and feat["atr5_expanding"]) else 0.0,
        "failure_to_progress": progress, "current_loss": 0.0,
        "closed_1h_aux_before_2h": bool(hour_bad and hold_ms < 120 * 60_000),
    }
    keys = ("macd15", "macd5", "rsi15", "rsi5", "adverse_volume", "atr_expansion", "failure_to_progress")
    soft_score = min(SOFT_CAP, sum(float(components[k]) for k in keys))
    return hard_count, hard, soft_score, components


def _decision(hold_ms, current_r, mfe_r, hard_count, hard, soft_score, components):
    hold_min = hold_ms / 60_000.0
    if hold_min < 0 or hold_min > 240 or current_r > -0.50 or mfe_r >= MFE_CUTOFF_R:
        return None
    if hold_min < 60:
        return {"stage": "0-1H", "path": "legacy", "hard_min": 1, "soft_min": 1.50,
                "allow": hard_count >= 1 and soft_score >= 1.50}
    if hold_min < 120:
        if current_r <= -0.80:
            return {"stage": "1-2H_deep", "path": "legacy_deep", "hard_min": 1, "soft_min": 1.00,
                    "allow": hard_count >= 1 and soft_score >= 1.00}
        return {"stage": "1-2H_mid", "path": "legacy_mid", "hard_min": 2, "soft_min": 1.50,
                "allow": hard_count >= 2 and soft_score >= 1.50}
    if hold_min < 180:
        if mfe_r >= 0.50:
            return None
        return {"stage": "2-3H", "path": "legacy", "hard_min": 1, "soft_min": 1.25,
                "allow": hard_count >= 1 and soft_score >= 1.25}
    fresh_confirm = bool(float(components.get("macd15") or 0.0) > 0.0 or
                         float(components.get("adverse_volume") or 0.0) > 0.0 or
                         bool(hard.get("boll5_persistent_failure")))
    adverse_volume = float(components.get("adverse_volume") or 0.0) > 0.0
    strong_single = bool(hard.get("structure_break_15m") or hard.get("opposite_closed_1h"))
    trend_only = bool(hard_count == 1 and hard.get("trend_reversal_15m") and
                      not hard.get("structure_break_15m") and not hard.get("boll5_persistent_failure") and
                      not hard.get("opposite_closed_1h"))
    boll_only = bool(hard_count == 1 and hard.get("boll5_persistent_failure") and
                     not hard.get("structure_break_15m") and not hard.get("trend_reversal_15m") and
                     not hard.get("opposite_closed_1h"))
    if hard_count >= 2:
        return {"stage": "3-4H", "path": "A_multi_hard", "hard_min": 2, "soft_min": 1.00,
                "allow": soft_score >= 1.00, "fresh_confirm": fresh_confirm}
    if hard_count == 1 and strong_single:
        return {"stage": "3-4H", "path": "B_single_strong_hard", "hard_min": 1, "soft_min": 1.25,
                "allow": soft_score >= 1.25 and fresh_confirm, "fresh_confirm": fresh_confirm}
    if trend_only:
        if adverse_volume:
            return {"stage": "3-4H", "path": "C_trend_only_with_volume", "hard_min": 1, "soft_min": 1.50,
                    "allow": soft_score >= 1.50, "fresh_confirm": True}
        return {"stage": "3-4H", "path": "C_trend_only_no_volume_deep", "hard_min": 1, "soft_min": 2.00,
                "allow": current_r <= -0.80 and soft_score >= 2.00, "fresh_confirm": False,
                "deep_loss_required": -0.80}
    if boll_only:
        return {"stage": "3-4H", "path": "D_boll_only_blocked", "hard_min": 2, "soft_min": 99.0,
                "allow": False, "fresh_confirm": False}
    return {"stage": "3-4H", "path": "no_qualified_hard_path", "hard_min": 2, "soft_min": 99.0,
            "allow": False, "fresh_confirm": fresh_confirm}


def _close_pee3(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req):
    pos, p = self.position, self.position.pending
    close_ms = int(bar["t"]) + 60_000
    mark = float(bar["c"])
    direction = research.side_dir(p.side)
    bps = (LIMIT_PROTECTION_BPS + self.stress_extra_bps) / 10000.0
    exit_px = mark * (1.0 - bps if p.side == "做多" else 1.0 + bps)
    gross = (exit_px - p.limit) * p.quantity_btc * direction
    exit_fee = exit_px * p.quantity_btc * research.TAKER_BPS / 10000.0
    net = gross - pos.entry_fee - exit_fee + pos.funding_pnl
    self.cash += net
    risk = abs(float(p.limit) - float(p.stop)); risk_money = risk * p.quantity_btc
    row = {
        "variant": self.variant, "side": p.side, "score": p.score, "boll_path": p.factors["boll_path"],
        "entry": p.limit, "stop": p.stop, "target": p.target, "exit": exit_px, "reason": EXIT_REASON,
        "quantity_btc": p.quantity_btc, "net_pnl": net, "gross_pnl": gross,
        "entry_fee": pos.entry_fee, "exit_fee": exit_fee, "funding_pnl": pos.funding_pnl,
        "realized_r": net / risk_money if risk_money > 0 else 0.0,
        "mfe_r": float(mfe_r), "mae_r": pos.mae / risk if risk > 0 else 0.0,
        "front_r": p.front_r, "cost_r": p.cost_r,
        "entry_time": pos.fill_time, "exit_time": close_ms, "hold_min": (close_ms - pos.fill_time) / 60_000.0,
        "opportunity_id": p.opportunity_id, "score_components": dict(p.score_components),
        "trigger_combination": dict(p.trigger_combination), "exit_order_type": "MARKETABLE_LIMIT_PROXY",
        "exit_limit_protection_bps": LIMIT_PROTECTION_BPS, "pee_version": PEE_VERSION,
        "pee_mfe_cutoff_r": MFE_CUTOFF_R, "early_exit_stage": req["stage"], "early_exit_path": req["path"],
        "early_exit_current_r": float(current_r), "early_exit_mfe_r": float(mfe_r),
        "early_exit_hard_count": int(hard_count), "early_exit_hard": dict(hard),
        "early_exit_soft_score": float(soft_score), "early_exit_soft_components": dict(components),
    }
    row.update(p.factors)
    self.trades.append(row); self.position = None; self.last_close_ms = close_ms
    self.stats["closed"] += 1; self.stats["pee3_closed"] += 1


def _process_exit_pee3(self, bar):
    if not self.position:
        return
    n_before = len(self.trades)
    _ORIGINAL_PROCESS_EXIT(self, bar)  # normal SL/TP has priority
    if len(self.trades) > n_before or not self.position:
        return
    close_ms = int(bar["t"]) + 60_000
    feat = _FEATURE_BY_CLOSE_MS.get(close_ms)
    if not feat:
        return
    pos, p = self.position, self.position.pending
    hold_ms = close_ms - int(pos.fill_time)
    if hold_ms < 0 or hold_ms > MONITOR_MAX_MS or int(feat["five_bar_start_ms"]) < int(pos.fill_time):
        return
    risk = abs(float(p.limit) - float(p.stop))
    if risk <= 0.0:
        return
    current_r = ((float(bar["c"]) - float(p.limit)) * research.side_dir(p.side)) / risk
    mfe_r = float(pos.mfe) / risk
    if current_r > -0.50 or mfe_r >= MFE_CUTOFF_R:
        return
    hard_count, hard, soft_score, components = _soft_and_hard(p.side, feat, pos, hold_ms, current_r, mfe_r)
    req = _decision(hold_ms, current_r, mfe_r, hard_count, hard, soft_score, components)
    if req is None:
        return
    self.stats["pee3_evaluated"] += 1; self.stats[f"pee3_eval_{req['stage']}"] += 1
    self.stats[f"pee3_path_{req['path']}"] += 1
    if not req.get("allow"):
        self.stats["pee3_blocked"] += 1; return
    _close_pee3(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req)


def _write_csv(rows, path):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8"); return
    keys = []
    for row in rows:
        for key in row:
            if key not in keys: keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader()
        for row in rows:
            item = dict(row)
            for k, v in list(item.items()):
                if isinstance(v, (dict, list, tuple)): item[k] = json.dumps(v, ensure_ascii=False)
            w.writerow(item)


def configure():
    start, end = entry.configure()
    research.Simulator.process_exit = _process_exit_pee3
    return start, end


def verify_lock(start, end):
    entry.verify_lock(start, end)
    assert DAYS == 180 and (end - start).days == 180
    assert entry.OVEREXT_ATR_MULT == 0.35 and entry.OVEREXT_SCORE == 1.0
    assert MFE_CUTOFF_R == 0.60 and MONITOR_MAX_MS == 14_400_000
    assert base.BOLL_WINDOW_MS == 900_000 and base.THRESHOLD == 6.0
    assert research.Simulator.process_exit is _process_exit_pee3
    assert _ORIGINAL_PROCESS_EXIT is base.proven._ORIG_EXIT
    # PEE3 must be disabled at/above +0.60R and after 4h.
    assert _decision(60_000, -0.8, 0.60, 2, {}, 2.0, {}) is None
    assert _decision(241 * 60_000, -0.8, 0.0, 2, {}, 2.0, {}) is None


def main():
    started = time.perf_counter(); start, end = configure(); verify_lock(start, end)
    OUT.mkdir(parents=True, exist_ok=True)
    print("V168_BOLL5_OVEREXT035_PEE3_MFE06_180D_CONFIG",
          f"start={start.isoformat()}", f"end={end.isoformat()}",
          "baseline=V1.6.8_R2", "boll5_overext=0.35x_ATR14_plus1_latched",
          "pee=3.0_first4h", "pee_mfe_cutoff=0.60R", "normal_exit_priority=true",
          "adx=OFF", "be=OFF", flush=True)

    meta, data, funding, cache_manifest = base.load_market(research.base, base.TIMEFRAMES)
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)
    for tf in base.TIMEFRAMES:
        rows = data[tf]; step = research.base.BAR_MS[tf]
        assert rows and all(int(b["t"]) - int(a["t"]) == step for a, b in zip(rows, rows[1:])), f"AUDIT FAIL {tf}"
    _prime_feature_map(data["5m"], data["15m"], data["1H"])
    print("AUDIT PASS: closed-bar spacing + exact 15m lifecycle + BOLL5/ATR14 score + PEE3 4H/MFE0.60 + normal exit priority", flush=True)

    sim, raw_metrics = research.run(data, ts, meta, funding, variant="boll5_overext035_pee3_mfe06")
    rows = list(sim.trades); metrics = dict(raw_metrics)
    pee_rows = [r for r in rows if str(r.get("reason") or "") == EXIT_REASON]
    payload = {
        "research": "V1.6.8 R2 + 5m BOLL overextension 0.35 ATR14 +1 + PEE3 MFE cutoff 0.60R, 180D",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "strategy_lock": {
            "baseline": "V1.6.8 Build1680 R2",
            "boll_signal_lifetime_ms": base.BOLL_WINDOW_MS,
            "5m_boll_overextension_score": 1.0,
            "5m_boll_overextension_threshold_atr14": 0.35,
            "pee_version": PEE_VERSION,
            "pee_monitor_max_hours": 4,
            "pee_mfe_cutoff_r": MFE_CUTOFF_R,
            "normal_sl_tp_priority": True,
            "adx": False, "break_even": False,
        },
        "preflight_audit": {
            "closed_market_bars_only": True, "strict_timeframe_spacing_checked": True,
            "exact_15m_signal_lifecycle_checked": True, "normal_exit_priority_checked": True,
            "pee_first4h_checked": True, "pee_mfe_cutoff_060_checked": True,
        },
        "metrics": metrics,
        "analysis": base._analysis(rows),
        "pee3": {
            "early_exit_count": len(pee_rows),
            "by_stage": dict(Counter(str(r.get("early_exit_stage") or "unknown") for r in pee_rows)),
            "by_path": dict(Counter(str(r.get("early_exit_path") or "unknown") for r in pee_rows)),
            "net_pnl": sum(float(r.get("net_pnl") or 0.0) for r in pee_rows),
        },
        "simulator_stats": dict(sim.stats), "cache": cache_manifest,
        "correction": {"production_strategy_variables_changed": False,
                       "only_deltas": ["5m BOLL overextension >=0.35 ATR14 +1 latched", "PEE3 first4h with permanent MFE>=0.60R cutoff"]},
        "timing_sec": {"total": time.perf_counter() - started},
    }
    (OUT / "result_v168_boll5_overext035_pee3_mfe06_180d.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(rows, OUT / "trades_v168_boll5_overext035_pee3_mfe06_180d.csv")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

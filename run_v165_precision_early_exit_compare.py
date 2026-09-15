#!/usr/bin/env python3
"""KAYTRADE V1.6.5 Precision Early Exit A/B research.

A = exact V1.6.5 baseline (No Early Exit)
B = exact V1.6.5 entry/risk model + Precision Early Exit during first 4h only.

Precision Early Exit:
- Current R is a risk-zone gate, never a score component.
- MFE >= +0.80R disables Early Exit (profit-retracement territory).
- Hard confirmation is mandatory; soft evidence never exits alone.
- Closed 5m/15m/1H candles only; 1H becomes Hard only after 2h and only
  when the closed 1H candle is fully post-fill.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from collections import Counter
from datetime import timedelta
from pathlib import Path

import run_v165_1000d_2000u as base
import strategy

research = base.research
production = base.production
MONITOR_MAX_MS = 4 * 60 * 60_000
STRUCTURE_BREAK_ATR = 0.25
LIMIT_PROTECTION_BPS = 5.0
EXIT_REASON = "PRECISION_EARLY_EXIT_4H"
SOFT_CAP = 3.0
_ORIGINAL_PROCESS_PENDING = base._ORIGINAL_PROCESS_PENDING
_ORIGINAL_PROCESS_EXIT = base._ORIGINAL_PROCESS_EXIT
_ORIGINAL_FINISH = base._ORIGINAL_FINISH
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


def _configure(days):
    end = base.FIXED_END
    start = end - timedelta(days=days)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    for name, value in {
        "START": start, "END": end, "START_MS": start_ms, "END_MS": end_ms,
        "CAPITAL": base.CAPITAL, "LEVERAGE": base.LEVERAGE,
        "RISK_USDT": base.RISK_USDT, "RISK_PCT": base.RISK_PCT,
        "DAILY_LOSS": base.DAILY_LOSS, "COOLDOWN_MINUTES": 0,
        "STOP_ATR": base.STOP_ATR, "REWARD_R": base.REWARD_R,
    }.items():
        setattr(research.base, name, value)
    research.START, research.END = start, end
    research.START_MS, research.END_MS = start_ms, end_ms
    research.SPLIT_MS = int((start + (end - start) / 2).timestamp() * 1000)
    research.CAPITAL, research.LEVERAGE = base.CAPITAL, base.LEVERAGE
    research.RISK_USDT, research.RISK_PCT = base.RISK_USDT, base.RISK_PCT
    research.DAILY_LOSS = base.DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = base.BASE_POSITION_NOTIONAL
    research.STOP_ATR, research.REWARD_R = base.STOP_ATR, base.REWARD_R
    research.COOLDOWN_MS = base.COOLDOWN_MS
    research.VERSION, research.BUILD = "1.6.5", f"1650-precision-ee-{days}d"
    production._patch_v164_base()
    research.model = production
    research.Simulator.submit = base._submit_v165
    research.Simulator.process_pending = _ORIGINAL_PROCESS_PENDING
    research.Simulator.process_exit = _ORIGINAL_PROCESS_EXIT
    research.Simulator.finish = _ORIGINAL_FINISH
    if hasattr(research, "build1544"):
        research.build1544.PATH_C_ENABLED = False
    return start, end


def _verify_lock(days, start, end):
    assert days in (180, 1000) and (end - start).days == days
    assert end.isoformat() == "2026-09-14T20:20:00+00:00"
    assert production.VERSION == "1.6.5" and production.THRESHOLD == 6.0
    assert production.ENTRY_WINDOW_MS == 300_000
    assert production.VOLUME_HARD_GATE == 1.20 and production.BOLL_OUTER_SCORE == 2.50
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert base.CAPITAL == 2_000.0 and base.BASE_POSITION_NOTIONAL == 2_000.0
    assert base.LEVERAGE == 5 and base.RISK_USDT == 20.0
    assert base.STOP_ATR == 1.0 and base.REWARD_R == 2.0 and base.BE_ENABLED is False


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
        if hold_min >= 180: progress = 0.75
        elif hold_min >= 120: progress = 0.50
        elif hold_min >= 60: progress = 0.25
    components = {
        "macd15": 0.75 if macd15 else 0.0,
        "macd5": 0.50 if macd5 else 0.0,
        "rsi15": 0.50 if rsi15_bad else 0.0,
        "rsi5": 0.25 if rsi5_bad else 0.0,
        "adverse_volume": 0.50 if (adverse_vol5 or adverse_vol15) else 0.0,
        "atr_expansion": 0.25 if (current_r < 0 and post5 and feat["atr5_pct"] >= 0.22 and feat["atr5_expanding"]) else 0.0,
        "failure_to_progress": progress,
        "current_loss": 0.0,
        "closed_1h_aux_before_2h": bool(hour_bad and hold_ms < 120 * 60_000),
    }
    keys = ("macd15", "macd5", "rsi15", "rsi5", "adverse_volume", "atr_expansion", "failure_to_progress")
    soft_score = min(SOFT_CAP, sum(float(components[k]) for k in keys))
    return hard_count, hard, soft_score, components


def _requirements(hold_ms, current_r, mfe_r):
    hold_min = hold_ms / 60_000.0
    if hold_min < 0 or hold_min > 240 or current_r > -0.50 or mfe_r >= 0.80:
        return None
    if hold_min < 60:
        return {"stage": "0-1H", "hard_min": 1, "soft_min": 1.50}
    if hold_min < 120:
        if current_r <= -0.80:
            return {"stage": "1-2H_deep", "hard_min": 1, "soft_min": 1.00}
        return {"stage": "1-2H_mid", "hard_min": 2, "soft_min": 1.50}
    if hold_min < 180:
        if mfe_r >= 0.50:
            return None
        return {"stage": "2-3H", "hard_min": 1, "soft_min": 1.25}
    return {"stage": "3-4H", "hard_min": 1, "soft_min": 1.00}


def _close_precision(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req):
    pos, p = self.position, self.position.pending
    close_ms = int(bar["t"]) + 60_000
    mark = float(bar["c"]); direction = research.side_dir(p.side)
    bps = (LIMIT_PROTECTION_BPS + self.stress_extra_bps) / 10000.0
    exit_px = mark * (1.0 - bps if p.side == "做多" else 1.0 + bps)
    gross = (exit_px - p.limit) * p.quantity_btc * direction
    exit_fee = exit_px * p.quantity_btc * research.TAKER_BPS / 10000.0
    net = gross - pos.entry_fee - exit_fee + pos.funding_pnl
    self.cash += net
    risk = abs(float(p.limit) - float(p.stop)); risk_money = risk * p.quantity_btc
    row = {
        "variant": self.variant, "side": p.side, "score": p.score,
        "boll_path": p.factors["boll_path"], "entry": p.limit, "stop": p.stop, "target": p.target,
        "exit": exit_px, "reason": EXIT_REASON, "quantity_btc": p.quantity_btc,
        "net_pnl": net, "gross_pnl": gross, "entry_fee": pos.entry_fee,
        "exit_fee": exit_fee, "funding_pnl": pos.funding_pnl,
        "realized_r": net / risk_money if risk_money > 0 else 0.0,
        "mfe_r": float(mfe_r), "mae_r": pos.mae / risk if risk > 0 else 0.0,
        "front_r": p.front_r, "cost_r": p.cost_r,
        "entry_time": pos.fill_time, "exit_time": close_ms,
        "hold_min": (close_ms - pos.fill_time) / 60_000.0,
        "opportunity_id": p.opportunity_id, "score_components": dict(p.score_components),
        "trigger_combination": dict(p.trigger_combination),
        "exit_order_type": "MARKETABLE_LIMIT_PROXY", "exit_limit_protection_bps": LIMIT_PROTECTION_BPS,
        "early_exit_stage": req["stage"], "early_exit_current_r": float(current_r),
        "early_exit_mfe_r": float(mfe_r), "early_exit_hard_count": int(hard_count),
        "early_exit_hard_required": int(req["hard_min"]), "early_exit_hard": dict(hard),
        "early_exit_soft_score": float(soft_score), "early_exit_soft_required": float(req["soft_min"]),
        "early_exit_soft_components": dict(components), "early_exit_atr5_pct": float(feat["atr5_pct"]),
        "early_exit_vol5_ratio": float(feat["vol5_ratio"]), "early_exit_vol15_ratio": float(feat["vol15_ratio"]),
        "early_exit_rsi5": float(feat["rsi5"]), "early_exit_rsi15": float(feat["rsi15"]),
    }
    row.update(p.factors)
    self.trades.append(row); self.position = None; self.last_close_ms = close_ms
    self.stats["closed"] += 1; self.stats["precision_early_exit_closed"] += 1


def _process_exit_precision(self, bar):
    if not self.position:
        return
    n_before = len(self.trades)
    _ORIGINAL_PROCESS_EXIT(self, bar)
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
    req = _requirements(hold_ms, current_r, mfe_r)
    if req is None:
        return
    hard_count, hard, soft_score, components = _soft_and_hard(p.side, feat, pos, hold_ms, current_r, mfe_r)
    self.stats["precision_early_exit_evaluated"] += 1
    if hard_count < req["hard_min"]:
        self.stats["precision_early_exit_hard_block"] += 1; return
    if soft_score < req["soft_min"]:
        self.stats["precision_early_exit_soft_block"] += 1; return
    _close_precision(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req)


def _write_rows(rows, path):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8"); return
    keys = []
    for row in rows:
        for key in row:
            if key not in keys: keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)


def _compare(base_sim, base_metrics, ee_sim, ee_metrics):
    base_by_id = {str(r.get("opportunity_id")): r for r in base_sim.trades if r.get("opportunity_id")}
    exits = [r for r in ee_sim.trades if str(r.get("reason")) == EXIT_REASON]
    matched, correct, false_kills, unmatched = [], [], [], []
    stage_counts, hard_combo = Counter(), Counter()
    for r in exits:
        stage_counts[str(r.get("early_exit_stage") or "unknown")] += 1
        hard = r.get("early_exit_hard") or {}
        hard_combo["+".join(sorted(k for k, v in hard.items() if v)) or "none"] += 1
        b = base_by_id.get(str(r.get("opportunity_id") or ""))
        if not b:
            unmatched.append(r); continue
        delta = float(r.get("net_pnl") or 0.0) - float(b.get("net_pnl") or 0.0)
        item = {
            "opportunity_id": r.get("opportunity_id"), "side": r.get("side"), "stage": r.get("early_exit_stage"),
            "entry_time": r.get("entry_time"), "early_exit_time": r.get("exit_time"), "early_hold_min": r.get("hold_min"),
            "early_current_r": r.get("early_exit_current_r"), "early_mfe_r": r.get("early_exit_mfe_r"),
            "hard_count": r.get("early_exit_hard_count"), "hard": r.get("early_exit_hard"),
            "soft_score": r.get("early_exit_soft_score"), "early_net_pnl": r.get("net_pnl"),
            "baseline_reason": b.get("reason"), "baseline_hold_min": b.get("hold_min"),
            "baseline_net_pnl": b.get("net_pnl"), "baseline_realized_r": b.get("realized_r"), "delta_net_pnl": delta,
        }
        matched.append(item)
        if float(b.get("net_pnl") or 0.0) < 0.0: correct.append(item)
        elif float(b.get("net_pnl") or 0.0) > 0.0: false_kills.append(item)
    precision = len(correct) / len(matched) * 100.0 if matched else 0.0
    false_rate = len(false_kills) / len(matched) * 100.0 if matched else 0.0
    saved = sum(float(x["delta_net_pnl"]) for x in correct)
    lost = -sum(float(x["delta_net_pnl"]) for x in false_kills if float(x["delta_net_pnl"]) < 0)
    matched_delta = sum(float(x["delta_net_pnl"]) for x in matched)
    b, e = base_metrics, ee_metrics
    return {
        "baseline": {k: b[k] for k in ("trades","wins","losses","win_rate_pct","net_pnl","net_return_pct","profit_factor","expectancy","max_drawdown_usdt","max_drawdown_pct")},
        "precision_early_exit": ({k: e[k] for k in ("trades","wins","losses","win_rate_pct","net_pnl","net_return_pct","profit_factor","expectancy","max_drawdown_usdt","max_drawdown_pct")} | {"early_exit_count": len(exits), "stage_counts": dict(stage_counts), "hard_combinations": dict(hard_combo)}),
        "delta_precision_minus_baseline": {
            "trades": e["trades"]-b["trades"], "wins": e["wins"]-b["wins"], "losses": e["losses"]-b["losses"],
            "win_rate_pct_points": e["win_rate_pct"]-b["win_rate_pct"], "net_pnl": e["net_pnl"]-b["net_pnl"],
            "net_return_pct_points": e["net_return_pct"]-b["net_return_pct"], "profit_factor": e["profit_factor"]-b["profit_factor"],
            "expectancy": e["expectancy"]-b["expectancy"], "max_drawdown_usdt": e["max_drawdown_usdt"]-b["max_drawdown_usdt"],
            "max_drawdown_pct_points": e["max_drawdown_pct"]-b["max_drawdown_pct"],
        },
        "matched_exit_quality": {
            "matched_early_exits": len(matched), "correct_cuts_baseline_loss": len(correct),
            "false_kills_baseline_win": len(false_kills), "unmatched_after_path_divergence": len(unmatched),
            "precision_pct": precision, "false_kill_rate_pct": false_rate,
            "saved_pnl_from_correct_cuts": saved, "lost_pnl_from_false_kills": lost, "matched_net_delta": matched_delta,
        },
        "matched_details": matched, "false_kill_details": false_kills,
    }


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, choices=(180,1000), required=True)
    days = ap.parse_args().days
    start, end = _configure(days); _verify_lock(days, start, end)
    print(f"V165_PRECISION_EE_CONFIG days={days} start={start.isoformat()} end={end.isoformat()} exact_V1.6.5 PrecisionEE=0..4H", flush=True)
    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m","5m","15m","1H","4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)
    _prime_feature_map(data["5m"], data["15m"], data["1H"])

    research.Simulator.process_exit = _ORIGINAL_PROCESS_EXIT
    base_sim, base_metrics = research.run(data, ts, meta, funding, variant="baseline"); base_metrics = dict(base_metrics)
    research.Simulator.process_exit = _process_exit_precision
    ee_sim, ee_metrics = research.run(data, ts, meta, funding, variant="baseline"); ee_metrics = dict(ee_metrics)
    comparison = _compare(base_sim, base_metrics, ee_sim, ee_metrics)
    common = {
        "release":"1.6.5","release_build":1650,"days":days,"start_utc":start.isoformat(),"end_utc":end.isoformat(),
        "capital":base.CAPITAL,"leverage":base.LEVERAGE,"base_risk_usdt":base.RISK_USDT,"score_threshold":production.THRESHOLD,
        "boll_outer_score":production.BOLL_OUTER_SCORE,"volume_score_enabled":False,"volume_hard_gate_enabled":True,
        "volume_ratio_threshold":production.VOLUME_HARD_GATE,"outer_only":True,"outer_rsi_min":production.RSI_MIN,
        "outer_rsi_max":production.RSI_MAX,"outer_macd_improving_required":True,"outer_4h_aligned_required":True,
        "position_multiplier":1.0,"entry_order_type":"LIMIT","stop_atr_timeframe":"1H","stop_atr_multiplier":1.0,
        "reward_r":2.0,"be_enabled":False,"early_exit_monitor_minutes":240,"early_exit_eval_timeframe":"closed_5m",
        "early_exit_current_loss_scores":False,"early_exit_mfe_profit_state_cutoff_r":0.80,"early_exit_soft_score_cap":SOFT_CAP,
    }
    base_metrics.update(common | {"label":f"V1.6.5 No Early Exit — {days}D"})
    ee_metrics.update(common | {"label":f"V1.6.5 Precision Early Exit — {days}D"})
    out = Path(f"backtest_output_v165_precision_ee_{days}d_2000u"); out.mkdir(parents=True, exist_ok=True)
    (out/f"metrics_baseline_{days}d.json").write_text(json.dumps(base_metrics,ensure_ascii=False,indent=2),encoding="utf-8")
    (out/f"metrics_precision_{days}d.json").write_text(json.dumps(ee_metrics,ensure_ascii=False,indent=2),encoding="utf-8")
    (out/f"comparison_{days}d.json").write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding="utf-8")
    base.prior._write_trade_csv(base_sim.trades,out/f"trades_baseline_{days}d.csv")
    base.prior._write_trade_csv(ee_sim.trades,out/f"trades_precision_{days}d.csv")
    _write_rows(comparison["matched_details"],out/f"matched_early_exits_{days}d.csv")
    _write_rows(comparison["false_kill_details"],out/f"false_kills_{days}d.csv")
    b,e,d,q = comparison["baseline"],comparison["precision_early_exit"],comparison["delta_precision_minus_baseline"],comparison["matched_exit_quality"]
    report = f"""# KAYTRADE V1.6.5 Precision Early Exit — {days}D / 2000U

Window: `{start.isoformat()} -> {end.isoformat()}`

Baseline: trades {b['trades']}, WR {b['win_rate_pct']:.2f}%, PnL {b['net_pnl']:+.2f}U, PF {b['profit_factor']:.4f}, DD {b['max_drawdown_usdt']:.2f}U/{b['max_drawdown_pct']:.2f}%.

Precision EE: trades {e['trades']}, WR {e['win_rate_pct']:.2f}%, PnL {e['net_pnl']:+.2f}U, PF {e['profit_factor']:.4f}, DD {e['max_drawdown_usdt']:.2f}U/{e['max_drawdown_pct']:.2f}%, exits {e['early_exit_count']}.

Delta PnL {d['net_pnl']:+.2f}U; PF {d['profit_factor']:+.4f}; WR {d['win_rate_pct_points']:+.2f}pp; DD {d['max_drawdown_usdt']:+.2f}U.

Matched EE: {q['matched_early_exits']}; correct {q['correct_cuts_baseline_loss']}; false kills {q['false_kills_baseline_win']}; precision {q['precision_pct']:.2f}%; false-kill {q['false_kill_rate_pct']:.2f}%; correct-cut saving {q['saved_pnl_from_correct_cuts']:+.2f}U; false-kill loss {q['lost_pnl_from_false_kills']:.2f}U; matched delta {q['matched_net_delta']:+.2f}U.

Historical simulation only. LIMIT fills use the audited closed-1m proxy.
"""
    (out/"REPORT.md").write_text(report,encoding="utf-8")
    print("V165_PRECISION_EE_COMPARISON"); print(json.dumps(comparison,ensure_ascii=False,indent=2),flush=True)
    print("OUTPUT_DIR",out.resolve(),flush=True)


if __name__ == "__main__":
    main()

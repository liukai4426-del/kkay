#!/usr/bin/env python3
"""KAYTRADE V1.6.8 ADX30m + Precision Early Exit 3.0 A/B research.

A = exact V1.6.8 ADX30m baseline (no early exit).
B = exact same entry/risk model + PEE 3.0 during first 4h only.

PEE 3.0 lock:
- Current R is only a risk-zone gate; never contributes score.
- MFE >= +0.80R permanently disables PEE for that trade.
- 0-3H keeps the prior Precision EE thresholds unchanged.
- 3-4H is reworked around Hard quality to reduce false kills:
  * >=2 Hard + Soft>=1.00 => exit.
  * exactly one strong Hard (15m structure break or opposite closed 1H)
    requires Soft>=1.25 plus fresh adverse confirmation.
  * exactly one 15m trend-reversal Hard requires adverse volume + Soft>=1.50;
    without adverse volume it can exit only when CurrentR<=-0.80 and Soft>=2.00.
  * a lone 5m BOLL persistent-failure Hard cannot exit by itself in 3-4H.
- Failure-to-progress is capped at +0.50 during 3-4H and never counts as
  fresh confirmation.
- Closed 5m/15m/1H candles only; opposite 1H becomes Hard only after 2H and
  only when the closed 1H candle is fully post-fill.
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import os
import time
from collections import Counter
from pathlib import Path

import run_v168_adx_30m_backtest as entry
import strategy

base = entry.base
research = base.research
MONITOR_MAX_MS = 4 * 60 * 60_000
STRUCTURE_BREAK_ATR = 0.25
LIMIT_PROTECTION_BPS = 5.0
EXIT_REASON = "PEE3_EARLY_EXIT_4H"
SOFT_CAP = 3.0
PEE_VERSION = "3.0"
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
    empty = {
        "long": False, "short": False, "support": None, "resistance": None,
        "long_break_atr": 0.0, "short_break_atr": 0.0,
    }
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
        "support": support_px,
        "resistance": resistance_px,
        "long_break_atr": float(long_break_atr),
        "short_break_atr": float(short_break_atr),
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
        lower = float(f.get("lower") or math.nan)
        prev_lower = float(pf.get("lower") or math.nan)
        upper = float(f.get("upper") or math.nan)
        prev_upper = float(pf.get("upper") or math.nan)
        long_boll_persistent = (
            all(math.isfinite(x) for x in (lower, prev_lower))
            and px5 < lower and prev_close5 < prev_lower
        )
        short_boll_persistent = (
            all(math.isfinite(x) for x in (upper, prev_upper))
            and px5 > upper and prev_close5 > prev_upper
        )
        close15 = float(quarter[qi]["c"])
        ema20_15 = float(q.get("ema20") or close15)
        out[close_ms] = {
            "five_bar_start_ms": int(five[i]["t"]),
            "prev_five_bar_start_ms": int(five[i - 1]["t"]),
            "quarter_bar_start_ms": int(quarter[qi]["t"]),
            "hour_bar_start_ms": int(hour[hi]["t"]),
            "open5": float(five[i]["o"]),
            "close5": px5,
            "open15": float(quarter[qi]["o"]),
            "close15": close15,
            "rsi5": float(f.get("rsi") or math.nan),
            "rsi15": float(q.get("rsi") or math.nan),
            "macd5_a": float(f_ind[i - 2].get("hist") or 0.0),
            "macd5_b": float(pf.get("hist") or 0.0),
            "macd5_c": float(f.get("hist") or 0.0),
            "macd15_a": float(q_ind[qi - 2].get("hist") or 0.0),
            "macd15_b": float(q_ind[qi - 1].get("hist") or 0.0),
            "macd15_c": float(q.get("hist") or 0.0),
            "atr5_pct": atr5_pct,
            "atr5_expanding": bool(atr5_pct > atr5_pct_1 > atr5_pct_2),
            "vol5_ratio": vol5_ratio,
            "vol15_ratio": vol15_ratio,
            "structure": _structure_break(quarter, q_ind, qi),
            "long_boll_persistent": bool(long_boll_persistent),
            "short_boll_persistent": bool(short_boll_persistent),
            "trend15_long_bad": bool(q.get("down")) and close15 < ema20_15,
            "trend15_short_bad": bool(q.get("up")) and close15 > ema20_15,
            "hour_up": bool(h.get("up")),
            "hour_down": bool(h.get("down")),
        }
    _FEATURE_BY_CLOSE_MS = out


def configure(days):
    start, end = entry.configure(days)
    research.Simulator.process_exit = _ORIGINAL_PROCESS_EXIT
    return start, end


def verify_lock(days):
    entry.verify_lock(days)
    assert int(days) in (360, 720)
    assert entry.HistoricalV168Adx30mModel.ENTRY_WINDOW_MS == 1_800_000
    assert entry.HistoricalV168Adx30mModel.THRESHOLD == 6.0
    assert entry.ADX_PERIOD == 14 and entry.ADX_THRESHOLD == 25.0 and entry.ADX_SCORE == 1.0
    assert MONITOR_MAX_MS == 14_400_000 and STRUCTURE_BREAK_ATR == 0.25
    assert PEE_VERSION == "3.0"


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
        if hold_min >= 180:
            progress = 0.50  # PEE3: cap 3-4H time evidence at +0.50
        elif hold_min >= 120:
            progress = 0.50
        elif hold_min >= 60:
            progress = 0.25

    components = {
        "macd15": 0.75 if macd15 else 0.0,
        "macd5": 0.50 if macd5 else 0.0,
        "rsi15": 0.50 if rsi15_bad else 0.0,
        "rsi5": 0.25 if rsi5_bad else 0.0,
        "adverse_volume": 0.50 if (adverse_vol5 or adverse_vol15) else 0.0,
        "atr_expansion": 0.25 if (
            current_r < 0 and post5 and feat["atr5_pct"] >= 0.22 and feat["atr5_expanding"]
        ) else 0.0,
        "failure_to_progress": progress,
        "current_loss": 0.0,
        "closed_1h_aux_before_2h": bool(hour_bad and hold_ms < 120 * 60_000),
    }
    keys = (
        "macd15", "macd5", "rsi15", "rsi5",
        "adverse_volume", "atr_expansion", "failure_to_progress",
    )
    soft_score = min(SOFT_CAP, sum(float(components[k]) for k in keys))
    return hard_count, hard, soft_score, components


def _decision(hold_ms, current_r, mfe_r, hard_count, hard, soft_score, components):
    hold_min = hold_ms / 60_000.0
    if hold_min < 0 or hold_min > 240 or current_r > -0.50 or mfe_r >= 0.80:
        return None

    if hold_min < 60:
        return {
            "stage": "0-1H", "path": "legacy",
            "hard_min": 1, "soft_min": 1.50,
            "allow": hard_count >= 1 and soft_score >= 1.50,
        }

    if hold_min < 120:
        if current_r <= -0.80:
            return {
                "stage": "1-2H_deep", "path": "legacy_deep",
                "hard_min": 1, "soft_min": 1.00,
                "allow": hard_count >= 1 and soft_score >= 1.00,
            }
        return {
            "stage": "1-2H_mid", "path": "legacy_mid",
            "hard_min": 2, "soft_min": 1.50,
            "allow": hard_count >= 2 and soft_score >= 1.50,
        }

    if hold_min < 180:
        if mfe_r >= 0.50:
            return None
        return {
            "stage": "2-3H", "path": "legacy",
            "hard_min": 1, "soft_min": 1.25,
            "allow": hard_count >= 1 and soft_score >= 1.25,
        }

    # PEE 3.0 rework for 3-4H.
    fresh_confirm = bool(
        float(components.get("macd15") or 0.0) > 0.0
        or float(components.get("adverse_volume") or 0.0) > 0.0
        or bool(hard.get("boll5_persistent_failure"))
    )
    adverse_volume = float(components.get("adverse_volume") or 0.0) > 0.0
    strong_single = bool(hard.get("structure_break_15m") or hard.get("opposite_closed_1h"))
    trend_only = bool(
        hard_count == 1 and hard.get("trend_reversal_15m")
        and not hard.get("structure_break_15m")
        and not hard.get("boll5_persistent_failure")
        and not hard.get("opposite_closed_1h")
    )
    boll_only = bool(
        hard_count == 1 and hard.get("boll5_persistent_failure")
        and not hard.get("structure_break_15m")
        and not hard.get("trend_reversal_15m")
        and not hard.get("opposite_closed_1h")
    )

    if hard_count >= 2:
        return {
            "stage": "3-4H", "path": "A_multi_hard",
            "hard_min": 2, "soft_min": 1.00,
            "allow": soft_score >= 1.00,
            "fresh_confirm": fresh_confirm,
        }

    if hard_count == 1 and strong_single:
        return {
            "stage": "3-4H", "path": "B_single_strong_hard",
            "hard_min": 1, "soft_min": 1.25,
            "allow": soft_score >= 1.25 and fresh_confirm,
            "fresh_confirm": fresh_confirm,
        }

    if trend_only:
        if adverse_volume:
            return {
                "stage": "3-4H", "path": "C_trend_only_with_volume",
                "hard_min": 1, "soft_min": 1.50,
                "allow": soft_score >= 1.50,
                "fresh_confirm": True,
            }
        return {
            "stage": "3-4H", "path": "C_trend_only_no_volume_deep",
            "hard_min": 1, "soft_min": 2.00,
            "allow": current_r <= -0.80 and soft_score >= 2.00,
            "fresh_confirm": False,
            "deep_loss_required": -0.80,
        }

    if boll_only:
        return {
            "stage": "3-4H", "path": "D_boll_only_blocked",
            "hard_min": 2, "soft_min": 99.0,
            "allow": False,
            "fresh_confirm": False,
        }

    return {
        "stage": "3-4H", "path": "no_qualified_hard_path",
        "hard_min": 2, "soft_min": 99.0,
        "allow": False,
        "fresh_confirm": fresh_confirm,
    }


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
    risk = abs(float(p.limit) - float(p.stop))
    risk_money = risk * p.quantity_btc
    row = {
        "variant": self.variant,
        "side": p.side,
        "score": p.score,
        "boll_path": p.factors["boll_path"],
        "entry": p.limit,
        "stop": p.stop,
        "target": p.target,
        "exit": exit_px,
        "reason": EXIT_REASON,
        "quantity_btc": p.quantity_btc,
        "net_pnl": net,
        "gross_pnl": gross,
        "entry_fee": pos.entry_fee,
        "exit_fee": exit_fee,
        "funding_pnl": pos.funding_pnl,
        "realized_r": net / risk_money if risk_money > 0 else 0.0,
        "mfe_r": float(mfe_r),
        "mae_r": pos.mae / risk if risk > 0 else 0.0,
        "front_r": p.front_r,
        "cost_r": p.cost_r,
        "entry_time": pos.fill_time,
        "exit_time": close_ms,
        "hold_min": (close_ms - pos.fill_time) / 60_000.0,
        "opportunity_id": p.opportunity_id,
        "score_components": dict(p.score_components),
        "trigger_combination": dict(p.trigger_combination),
        "exit_order_type": "MARKETABLE_LIMIT_PROXY",
        "exit_limit_protection_bps": LIMIT_PROTECTION_BPS,
        "pee_version": PEE_VERSION,
        "early_exit_stage": req["stage"],
        "early_exit_path": req["path"],
        "early_exit_current_r": float(current_r),
        "early_exit_mfe_r": float(mfe_r),
        "early_exit_hard_count": int(hard_count),
        "early_exit_hard_required": int(req.get("hard_min", 0)),
        "early_exit_hard": dict(hard),
        "early_exit_soft_score": float(soft_score),
        "early_exit_soft_required": float(req.get("soft_min", 0.0)),
        "early_exit_soft_components": dict(components),
        "early_exit_atr5_pct": float(feat["atr5_pct"]),
        "early_exit_vol5_ratio": float(feat["vol5_ratio"]),
        "early_exit_vol15_ratio": float(feat["vol15_ratio"]),
        "early_exit_rsi5": float(feat["rsi5"]),
        "early_exit_rsi15": float(feat["rsi15"]),
    }
    row.update(p.factors)
    self.trades.append(row)
    self.position = None
    self.last_close_ms = close_ms
    self.stats["closed"] += 1
    self.stats["pee3_closed"] += 1


def _process_exit_pee3(self, bar):
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
    if current_r > -0.50 or mfe_r >= 0.80:
        return

    hard_count, hard, soft_score, components = _soft_and_hard(
        p.side, feat, pos, hold_ms, current_r, mfe_r
    )
    req = _decision(hold_ms, current_r, mfe_r, hard_count, hard, soft_score, components)
    if req is None:
        return

    self.stats["pee3_evaluated"] += 1
    self.stats[f"pee3_eval_{req['stage']}"] += 1
    self.stats[f"pee3_path_{req['path']}"] += 1
    if not req.get("allow"):
        self.stats["pee3_blocked"] += 1
        return
    _close_pee3(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req)


def _write_rows(rows, path):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _metric_slice(m):
    keys = (
        "trades", "wins", "losses", "win_rate_pct", "net_pnl", "net_return_pct",
        "profit_factor", "expectancy", "max_drawdown_usdt", "max_drawdown_pct",
    )
    return {k: m.get(k) for k in keys}


def _compare(base_sim, base_metrics, pee_sim, pee_metrics):
    base_by_id = {
        str(r.get("opportunity_id")): r
        for r in base_sim.trades if r.get("opportunity_id")
    }
    exits = [r for r in pee_sim.trades if str(r.get("reason")) == EXIT_REASON]
    matched, correct, false_kills, unmatched = [], [], [], []
    stage_counts, hard_combo, path_counts = Counter(), Counter(), Counter()

    for r in exits:
        stage_counts[str(r.get("early_exit_stage") or "unknown")] += 1
        path_counts[str(r.get("early_exit_path") or "unknown")] += 1
        hard = r.get("early_exit_hard") or {}
        hard_combo["+".join(sorted(k for k, v in hard.items() if v)) or "none"] += 1
        b = base_by_id.get(str(r.get("opportunity_id") or ""))
        if not b:
            unmatched.append(r)
            continue
        delta = float(r.get("net_pnl") or 0.0) - float(b.get("net_pnl") or 0.0)
        item = {
            "opportunity_id": r.get("opportunity_id"),
            "side": r.get("side"),
            "score": r.get("score"),
            "stage": r.get("early_exit_stage"),
            "path": r.get("early_exit_path"),
            "entry_time": r.get("entry_time"),
            "early_exit_time": r.get("exit_time"),
            "early_hold_min": r.get("hold_min"),
            "early_current_r": r.get("early_exit_current_r"),
            "early_mfe_r": r.get("early_exit_mfe_r"),
            "hard_count": r.get("early_exit_hard_count"),
            "hard": r.get("early_exit_hard"),
            "soft_score": r.get("early_exit_soft_score"),
            "soft_components": r.get("early_exit_soft_components"),
            "early_net_pnl": r.get("net_pnl"),
            "baseline_reason": b.get("reason"),
            "baseline_hold_min": b.get("hold_min"),
            "baseline_net_pnl": b.get("net_pnl"),
            "baseline_realized_r": b.get("realized_r"),
            "delta_net_pnl": delta,
        }
        matched.append(item)
        if float(b.get("net_pnl") or 0.0) < 0.0:
            correct.append(item)
        elif float(b.get("net_pnl") or 0.0) > 0.0:
            false_kills.append(item)

    precision = len(correct) / len(matched) * 100.0 if matched else 0.0
    false_rate = len(false_kills) / len(matched) * 100.0 if matched else 0.0
    saved = sum(float(x["delta_net_pnl"]) for x in correct)
    lost = -sum(float(x["delta_net_pnl"]) for x in false_kills if float(x["delta_net_pnl"]) < 0)
    matched_delta = sum(float(x["delta_net_pnl"]) for x in matched)

    return {
        "baseline": _metric_slice(base_metrics),
        "pee3": _metric_slice(pee_metrics) | {
            "early_exit_count": len(exits),
            "stage_counts": dict(stage_counts),
            "path_counts": dict(path_counts),
            "hard_combinations": dict(hard_combo),
        },
        "delta_pee3_minus_baseline": {
            "trades": (pee_metrics.get("trades") or 0) - (base_metrics.get("trades") or 0),
            "wins": (pee_metrics.get("wins") or 0) - (base_metrics.get("wins") or 0),
            "losses": (pee_metrics.get("losses") or 0) - (base_metrics.get("losses") or 0),
            "win_rate_pct_points": (pee_metrics.get("win_rate_pct") or 0.0) - (base_metrics.get("win_rate_pct") or 0.0),
            "net_pnl": (pee_metrics.get("net_pnl") or 0.0) - (base_metrics.get("net_pnl") or 0.0),
            "net_return_pct_points": (pee_metrics.get("net_return_pct") or 0.0) - (base_metrics.get("net_return_pct") or 0.0),
            "profit_factor": (pee_metrics.get("profit_factor") or 0.0) - (base_metrics.get("profit_factor") or 0.0),
            "expectancy": (pee_metrics.get("expectancy") or 0.0) - (base_metrics.get("expectancy") or 0.0),
            "max_drawdown_usdt": (pee_metrics.get("max_drawdown_usdt") or 0.0) - (base_metrics.get("max_drawdown_usdt") or 0.0),
            "max_drawdown_pct_points": (pee_metrics.get("max_drawdown_pct") or 0.0) - (base_metrics.get("max_drawdown_pct") or 0.0),
        },
        "matched_exit_quality": {
            "matched_early_exits": len(matched),
            "correct_cuts_baseline_loss": len(correct),
            "false_kills_baseline_win": len(false_kills),
            "unmatched_after_path_divergence": len(unmatched),
            "precision_pct": precision,
            "false_kill_rate_pct": false_rate,
            "saved_pnl_from_correct_cuts": saved,
            "lost_pnl_from_false_kills": lost,
            "matched_net_delta": matched_delta,
        },
        "matched_details": matched,
        "false_kill_details": false_kills,
    }


def main():
    days = int(os.environ.get("BACKTEST_DAYS", "360"))
    started = time.perf_counter()
    start, end = configure(days)
    verify_lock(days)
    out = Path(f"backtest_output_v168_adx30m_pee3_{days}d")
    out.mkdir(parents=True, exist_ok=True)

    print(
        "V168_ADX30M_PEE3_CONFIG",
        f"days={days}", f"start={start.isoformat()}", f"end={end.isoformat()}",
        "entry=exact_V1.6.8_ADX30m", "PEE=3.0", "monitor=0..4H",
        "mfe_cutoff=0.80R", "3-4H=hard_quality_rework", flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = base.load_market(research.base, base.TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)
    _prime_feature_map(data["5m"], data["15m"], data["1H"])

    baseline_started = time.perf_counter()
    research.Simulator.process_exit = _ORIGINAL_PROCESS_EXIT
    baseline_sim, baseline_metrics = research.run(
        data, ts, meta, funding, variant="adx30m_baseline"
    )
    baseline_metrics = dict(baseline_metrics)
    baseline_elapsed = time.perf_counter() - baseline_started

    pee_started = time.perf_counter()
    research.Simulator.process_exit = _process_exit_pee3
    pee_sim, pee_metrics = research.run(
        data, ts, meta, funding, variant="adx30m_pee3"
    )
    pee_metrics = dict(pee_metrics)
    pee_elapsed = time.perf_counter() - pee_started

    comparison = _compare(baseline_sim, baseline_metrics, pee_sim, pee_metrics)
    common = {
        "release": "1.6.8-ADX30M-research",
        "days": days,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "capital": entry.CAPITAL,
        "score_threshold": entry.THRESHOLD,
        "boll_signal_lifetime_ms": entry.BOLL_WINDOW_MS,
        "boll_outer_score": 2.0,
        "1h_ema9_26_score_enabled": False,
        "5m_adx_period": entry.ADX_PERIOD,
        "5m_adx_threshold": entry.ADX_THRESHOLD,
        "5m_adx_score": entry.ADX_SCORE,
        "5m_adx_hard_gate": True,
        "position_multiplier": 1.0,
        "entry_order_type": "LIMIT",
        "stop_atr_timeframe": "1H",
        "stop_atr_multiplier": 1.0,
        "reward_r": 2.0,
        "be_enabled": False,
        "pee_version": PEE_VERSION,
        "pee_monitor_minutes": 240,
        "pee_mfe_cutoff_r": 0.80,
        "pee_current_r_gate": -0.50,
        "pee_3_4h_failure_to_progress_cap": 0.50,
    }
    baseline_metrics.update(common | {"label": f"V1.6.8 ADX30m No PEE — {days}D"})
    pee_metrics.update(common | {"label": f"V1.6.8 ADX30m + PEE 3.0 — {days}D"})

    (out / f"metrics_baseline_{days}d.json").write_text(
        json.dumps(baseline_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / f"metrics_pee3_{days}d.json").write_text(
        json.dumps(pee_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / f"comparison_{days}d.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base._write_csv(list(baseline_sim.trades), out / f"trades_baseline_{days}d.csv")
    base._write_csv(list(pee_sim.trades), out / f"trades_pee3_{days}d.csv")
    _write_rows(comparison["matched_details"], out / f"matched_pee3_exits_{days}d.csv")
    _write_rows(comparison["false_kill_details"], out / f"false_kills_{days}d.csv")

    b = comparison["baseline"]
    p = comparison["pee3"]
    d = comparison["delta_pee3_minus_baseline"]
    q = comparison["matched_exit_quality"]
    report = f"""# KAYTRADE V1.6.8 ADX30m + PEE 3.0 — {days}D / 2000U

Window: `{start.isoformat()} -> {end.isoformat()}`

Baseline: trades {b['trades']}, WR {float(b['win_rate_pct'] or 0):.2f}%, PnL {float(b['net_pnl'] or 0):+.2f}U, PF {float(b['profit_factor'] or 0):.4f}, DD {float(b['max_drawdown_usdt'] or 0):.2f}U/{float(b['max_drawdown_pct'] or 0):.2f}%.

PEE 3.0: trades {p['trades']}, WR {float(p['win_rate_pct'] or 0):.2f}%, PnL {float(p['net_pnl'] or 0):+.2f}U, PF {float(p['profit_factor'] or 0):.4f}, DD {float(p['max_drawdown_usdt'] or 0):.2f}U/{float(p['max_drawdown_pct'] or 0):.2f}%, PEE exits {p['early_exit_count']}.

Delta: PnL {float(d['net_pnl'] or 0):+.2f}U; PF {float(d['profit_factor'] or 0):+.4f}; WR {float(d['win_rate_pct_points'] or 0):+.2f}pp; DD {float(d['max_drawdown_usdt'] or 0):+.2f}U.

Matched PEE exits: {q['matched_early_exits']}; correct {q['correct_cuts_baseline_loss']}; false kills {q['false_kills_baseline_win']}; precision {q['precision_pct']:.2f}%; false-kill {q['false_kill_rate_pct']:.2f}%; correct-cut saving {q['saved_pnl_from_correct_cuts']:+.2f}U; false-kill loss {q['lost_pnl_from_false_kills']:.2f}U; matched delta {q['matched_net_delta']:+.2f}U.

PEE 3.0 3-4H rules: >=2 Hard + Soft>=1.0; single strong Hard needs Soft>=1.25 + fresh adverse confirmation; trend-reversal-only needs adverse volume + Soft>=1.5, otherwise CurrentR<=-0.8 + Soft>=2.0; lone BOLL persistent failure does not exit by itself.

Historical simulation only. LIMIT fills use the audited closed-1m proxy.
"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")

    payload = {
        "days": days,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "strategy": "V1.6.8 ADX30m",
        "pee_version": PEE_VERSION,
        "comparison": comparison,
        "baseline_stats": dict(baseline_sim.stats),
        "pee3_stats": dict(pee_sim.stats),
        "cache": cache_manifest,
        "timing_sec": {
            "market_load": market_elapsed,
            "baseline": baseline_elapsed,
            "pee3": pee_elapsed,
            "total": time.perf_counter() - started,
        },
    }
    (out / f"result_v168_adx30m_pee3_{days}d.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("V168_ADX30M_PEE3_COMPARISON")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", out.resolve(), flush=True)


if __name__ == "__main__":
    main()

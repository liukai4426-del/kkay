#!/usr/bin/env python3
"""V1.6.8 ADX30m A/B/C research with Dynamic Profit-PEE3.

A = V1.6.8 ADX30m baseline, no PEE.
B = V1.6.8 ADX30m + locked PEE 3.0.
C = B plus Dynamic Profit-PEE3:
    - once MFE >= +1.00R, Profit-PEE is permanently armed for that trade;
    - it does not act while current R > 0 (above cost line);
    - from cost line (current R <= 0), evaluate every closed 5m bar;
    - thresholds relax as loss deepens;
    - once armed, profit-mode monitoring has no 4H expiry and remains active
      until TP2R, Profit-PEE exit, or the original stop.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path

import run_v168_adx30m_pee3_compare as pee

research = pee.research
base = pee.base
ORIG_EXIT = pee._ORIGINAL_PROCESS_EXIT
PROFIT_EXIT_REASON = "PEE3_PROFIT_DYNAMIC_AFTER_1R"
PROFIT_ARM_R = 1.00
COST_LINE_R = 0.00


def _fresh_confirm(hard, components):
    return bool(
        float(components.get("macd15") or 0.0) > 0.0
        or float(components.get("adverse_volume") or 0.0) > 0.0
        or bool(hard.get("boll5_persistent_failure"))
    )


def _strong_single(hard_count, hard):
    return bool(
        hard_count == 1
        and (hard.get("structure_break_15m") or hard.get("opposite_closed_1h"))
    )


def _trend_only(hard_count, hard):
    return bool(
        hard_count == 1
        and hard.get("trend_reversal_15m")
        and not hard.get("structure_break_15m")
        and not hard.get("boll5_persistent_failure")
        and not hard.get("opposite_closed_1h")
    )


def _profit_decision(current_r, hard_count, hard, soft_score, components):
    """Dynamic loss-depth gate for an already +1R-proven trade."""
    if current_r > COST_LINE_R:
        return None

    fresh = _fresh_confirm(hard, components)
    strong_one = _strong_single(hard_count, hard)
    trend_one = _trend_only(hard_count, hard)
    adverse_volume = float(components.get("adverse_volume") or 0.0) > 0.0

    if current_r > -0.20:
        allow = (
            (hard_count >= 2 and soft_score >= 1.50)
            or (strong_one and soft_score >= 2.00 and fresh)
        )
        return {
            "stage": "profit_0_to_-0.20R",
            "path": "profit_very_strict",
            "hard_min": 2,
            "soft_min": 1.50,
            "allow": bool(allow),
            "fresh_confirm": fresh,
        }

    if current_r > -0.50:
        allow = (
            (hard_count >= 2 and soft_score >= 1.25)
            or (strong_one and soft_score >= 1.50 and fresh)
        )
        return {
            "stage": "profit_-0.20_to_-0.50R",
            "path": "profit_strict",
            "hard_min": 1,
            "soft_min": 1.25,
            "allow": bool(allow),
            "fresh_confirm": fresh,
        }

    if current_r > -0.80:
        # At medium loss a single Hard is enough, except a lone 15m trend
        # reversal still needs adverse-volume confirmation.
        allow = hard_count >= 1 and soft_score >= 1.25
        if trend_one and not adverse_volume:
            allow = False
        return {
            "stage": "profit_-0.50_to_-0.80R",
            "path": "profit_medium",
            "hard_min": 1,
            "soft_min": 1.25,
            "allow": bool(allow),
            "fresh_confirm": fresh,
            "trend_only_volume_required": True,
        }

    # <= -0.80R: close to the original -1R stop, prioritize damage control.
    return {
        "stage": "profit_le_-0.80R",
        "path": "profit_relaxed",
        "hard_min": 1,
        "soft_min": 0.75,
        "allow": bool(hard_count >= 1 and soft_score >= 0.75),
        "fresh_confirm": fresh,
    }


def _close_profit(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req):
    pos, p = self.position, self.position.pending
    close_ms = int(bar["t"]) + 60_000
    mark = float(bar["c"])
    direction = research.side_dir(p.side)
    bps = (pee.LIMIT_PROTECTION_BPS + self.stress_extra_bps) / 10000.0
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
        "reason": PROFIT_EXIT_REASON,
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
        "exit_limit_protection_bps": pee.LIMIT_PROTECTION_BPS,
        "pee_version": pee.PEE_VERSION,
        "profit_pee_armed": True,
        "profit_pee_arm_mfe_r": PROFIT_ARM_R,
        "profit_pee_start_current_r": COST_LINE_R,
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
    self.stats["profit_pee3_closed"] += 1


def _process_exit_c(self, bar):
    if not self.position:
        return

    n_before = len(self.trades)
    ORIG_EXIT(self, bar)
    if len(self.trades) > n_before or not self.position:
        return

    close_ms = int(bar["t"]) + 60_000
    feat = pee._FEATURE_BY_CLOSE_MS.get(close_ms)
    if not feat:
        return

    pos, p = self.position, self.position.pending
    hold_ms = close_ms - int(pos.fill_time)
    if hold_ms < 0 or int(feat["five_bar_start_ms"]) < int(pos.fill_time):
        return

    risk = abs(float(p.limit) - float(p.stop))
    if risk <= 0.0:
        return
    current_r = ((float(bar["c"]) - float(p.limit)) * research.side_dir(p.side)) / risk
    mfe_r = float(pos.mfe) / risk

    # C uses ordinary PEE3 before the trade proves +0.8R, exactly like B.
    # Between +0.8R and +1.0R, ordinary PEE3 is disabled exactly like B.
    # Once +1.0R MFE is reached, profit mode is permanently armed.
    if mfe_r < PROFIT_ARM_R:
        if hold_ms > pee.MONITOR_MAX_MS or current_r > -0.50 or mfe_r >= 0.80:
            return
        hard_count, hard, soft_score, components = pee._soft_and_hard(
            p.side, feat, pos, hold_ms, current_r, mfe_r
        )
        req = pee._decision(hold_ms, current_r, mfe_r, hard_count, hard, soft_score, components)
        if req is None:
            return
        self.stats["c_regular_pee3_evaluated"] += 1
        if not req.get("allow"):
            self.stats["c_regular_pee3_blocked"] += 1
            return
        pee._close_pee3(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req)
        return

    self.stats["profit_pee3_armed_bar"] += 1
    if current_r > COST_LINE_R:
        self.stats["profit_pee3_above_cost_block"] += 1
        return

    # Profit mode has no 4H expiry. Reuse the same market evidence, but apply
    # a loss-depth-dependent decision threshold.
    hard_count, hard, soft_score, components = pee._soft_and_hard(
        p.side, feat, pos, hold_ms, current_r, mfe_r
    )
    req = _profit_decision(current_r, hard_count, hard, soft_score, components)
    if req is None:
        return
    self.stats["profit_pee3_evaluated"] += 1
    self.stats[f"profit_pee3_eval_{req['stage']}"] += 1
    if not req.get("allow"):
        self.stats["profit_pee3_blocked"] += 1
        return
    _close_profit(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req)


def _compare_c_to_a(a_sim, c_sim):
    a_by_id = {
        str(r.get("opportunity_id")): r
        for r in a_sim.trades if r.get("opportunity_id")
    }
    profit_exits = [r for r in c_sim.trades if str(r.get("reason")) == PROFIT_EXIT_REASON]
    matched, saved, lost = [], 0.0, 0.0
    stages = Counter()
    for r in profit_exits:
        stages[str(r.get("early_exit_stage") or "unknown")] += 1
        a = a_by_id.get(str(r.get("opportunity_id") or ""))
        if not a:
            continue
        delta = float(r.get("net_pnl") or 0.0) - float(a.get("net_pnl") or 0.0)
        item = {
            "opportunity_id": r.get("opportunity_id"),
            "side": r.get("side"),
            "stage": r.get("early_exit_stage"),
            "current_r": r.get("early_exit_current_r"),
            "mfe_r": r.get("early_exit_mfe_r"),
            "hard_count": r.get("early_exit_hard_count"),
            "hard": r.get("early_exit_hard"),
            "soft_score": r.get("early_exit_soft_score"),
            "profit_exit_pnl": r.get("net_pnl"),
            "a_reason": a.get("reason"),
            "a_net_pnl": a.get("net_pnl"),
            "delta_net_pnl": delta,
        }
        matched.append(item)
        if delta > 0:
            saved += delta
        elif delta < 0:
            lost += -delta
    return {
        "profit_exit_count": len(profit_exits),
        "matched_profit_exits": len(matched),
        "stage_counts": dict(stages),
        "positive_delta_sum": saved,
        "negative_delta_sum": lost,
        "matched_net_delta": sum(float(x["delta_net_pnl"]) for x in matched),
        "details": matched,
    }


def _delta(x, y):
    keys = ("trades", "wins", "losses", "win_rate_pct", "net_pnl", "net_return_pct", "profit_factor", "expectancy", "max_drawdown_usdt", "max_drawdown_pct")
    return {k: (float(y.get(k) or 0.0) - float(x.get(k) or 0.0)) for k in keys}


def verify_lock(days):
    pee.verify_lock(days)
    assert int(days) in (360, 720)
    assert PROFIT_ARM_R == 1.0 and COST_LINE_R == 0.0
    # Exact dynamic thresholds locked for C.
    t0 = _profit_decision(-0.10, 2, {}, 1.50, {})
    t1 = _profit_decision(-0.30, 2, {}, 1.25, {})
    t2 = _profit_decision(-0.60, 1, {"structure_break_15m": True}, 1.25, {})
    t3 = _profit_decision(-0.85, 1, {"trend_reversal_15m": True}, 0.75, {})
    assert t0["allow"] and t1["allow"] and t2["allow"] and t3["allow"]


def main():
    days = int(os.environ.get("BACKTEST_DAYS", "360"))
    started = time.perf_counter()
    start, end = pee.configure(days)
    verify_lock(days)
    out = Path(f"backtest_output_v168_adx30m_pee3_profit1r_abc_{days}d")
    out.mkdir(parents=True, exist_ok=True)

    print(
        "V168_ADX30M_PEE3_PROFIT1R_ABC_CONFIG",
        f"days={days}", f"start={start.isoformat()}", f"end={end.isoformat()}",
        "A=no_PEE", "B=PEE3", "C=PEE3_plus_profit_dynamic",
        "profit_arm=MFE>=1.0R", "profit_eval=currentR<=0R",
        "bands=0/-0.2/-0.5/-0.8R", flush=True,
    )

    meta, data, funding, cache_manifest = base.load_market(research.base, base.TIMEFRAMES)
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)
    pee._prime_feature_map(data["5m"], data["15m"], data["1H"])

    research.Simulator.process_exit = ORIG_EXIT
    a_sim, a_metrics = research.run(data, ts, meta, funding, variant="A_adx30m")
    a_metrics = dict(a_metrics)

    research.Simulator.process_exit = pee._process_exit_pee3
    b_sim, b_metrics = research.run(data, ts, meta, funding, variant="B_pee3")
    b_metrics = dict(b_metrics)

    research.Simulator.process_exit = _process_exit_c
    c_sim, c_metrics = research.run(data, ts, meta, funding, variant="C_profit_pee3_dynamic")
    c_metrics = dict(c_metrics)

    ab = pee._compare(a_sim, a_metrics, b_sim, b_metrics)
    c_profit = _compare_c_to_a(a_sim, c_sim)
    result = {
        "days": days,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "strategy": "V1.6.8 ADX30m",
        "pee_version": "3.0",
        "groups": {
            "A": pee._metric_slice(a_metrics),
            "B": pee._metric_slice(b_metrics),
            "C": pee._metric_slice(c_metrics),
        },
        "delta_B_minus_A": _delta(a_metrics, b_metrics),
        "delta_C_minus_A": _delta(a_metrics, c_metrics),
        "delta_C_minus_B": _delta(b_metrics, c_metrics),
        "AB_pee3_quality": ab.get("matched_exit_quality"),
        "C_profit_pee_quality": c_profit,
        "C_rules": {
            "profit_arm_mfe_r": 1.0,
            "start_eval_current_r": 0.0,
            "monitor_expiry_after_arm": None,
            "0_to_-0.20R": "Hard>=2+Soft>=1.50 OR single strong Hard+Soft>=2.00+fresh",
            "-0.20_to_-0.50R": "Hard>=2+Soft>=1.25 OR single strong Hard+Soft>=1.50+fresh",
            "-0.50_to_-0.80R": "Hard>=1+Soft>=1.25; trend-only also requires adverse volume",
            "le_-0.80R": "Hard>=1+Soft>=0.75",
        },
        "A_stats": dict(a_sim.stats),
        "B_stats": dict(b_sim.stats),
        "C_stats": dict(c_sim.stats),
        "cache": cache_manifest,
        "elapsed_sec": time.perf_counter() - started,
    }

    for label, sim in (("A", a_sim), ("B", b_sim), ("C", c_sim)):
        base._write_csv(list(sim.trades), out / f"trades_{label}_{days}d.csv")
    pee._write_rows(c_profit["details"], out / f"profit_pee_matched_{days}d.csv")
    (out / f"result_abc_{days}d.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "REPORT.md").write_text(
        "# V1.6.8 ADX30m + PEE3 Dynamic Profit-PEE A/B/C\n\n"
        f"Window: {days}D\n\n"
        f"A PnL: {float(a_metrics.get('net_pnl') or 0):+.2f}U\n\n"
        f"B PnL: {float(b_metrics.get('net_pnl') or 0):+.2f}U\n\n"
        f"C PnL: {float(c_metrics.get('net_pnl') or 0):+.2f}U\n\n"
        "C: MFE>=1R arms Profit-PEE permanently; evaluation begins at cost line and thresholds relax with deeper loss.\n",
        encoding="utf-8",
    )
    print("V168_ADX30M_PEE3_PROFIT1R_ABC_RESULT")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", out.resolve(), flush=True)


if __name__ == "__main__":
    main()

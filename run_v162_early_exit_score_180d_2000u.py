#!/usr/bin/env python3
"""V1.6.2 strict 180D A/B: baseline vs first-4H Early Exit Score.

Baseline is the completed outer-only V1.6.2 research model:
- 5m BOLL outer bands only;
- signal-time RSI 30..70;
- 5m MACD improvement required;
- 4H aligned hard gate;
- Volume expansion remains +0.5 in ENTRY score;
- 1x LIMIT entry, 1H ATR x1 initial SL, 2R full TP, BE off.

Experimental change B only:
- Early Exit Score is active only from fill through 240 minutes; after 240m it is disabled.
- Evaluation happens only on fully CLOSED 5m bars and never uses future candles.
- Dynamic exit threshold by current unrealized R:
    -0.30R .. -0.50R -> score >= 4.0
    -0.50R .. -0.80R -> score >= 3.0
    <= -0.80R          -> score >= 2.0
  Original -1R SL always has intrabar priority.
- Score combines 5m detection, 15m confirmation, 1H regime and failure-to-progress.
"""
from __future__ import annotations

import bisect
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_v162_outer_rsi30_70_macd_360d_2000u as baseline
import strategy

research = baseline.research
production = baseline.production

DAYS = 180
CAPITAL = baseline.CAPITAL
BASE_POSITION_NOTIONAL = baseline.BASE_POSITION_NOTIONAL
LEVERAGE = baseline.LEVERAGE
RISK_USDT = baseline.RISK_USDT
RISK_PCT = baseline.RISK_PCT
DAILY_LOSS = baseline.DAILY_LOSS
STOP_ATR = baseline.STOP_ATR
REWARD_R = baseline.REWARD_R
COOLDOWN_MS = baseline.COOLDOWN_MS
BE_ENABLED = baseline.BE_ENABLED

FIXED_END = datetime(2026, 9, 14, 20, 20, tzinfo=timezone.utc)
FIXED_START = FIXED_END - timedelta(days=DAYS)
MONITOR_MAX_MS = 4 * 60 * 60_000
MIN_PROGRESS_HOLD_MS = 30 * 60_000
LIMIT_PROTECTION_BPS = 5.0
STRUCTURE_BREAK_ATR = 0.25
EXIT_REASON = "EARLY_EXIT_SCORE_4H"

_ORIGINAL_PROCESS_EXIT = None
_FEATURE_BY_CLOSE_MS = {}


def _configure_180d():
    baseline._configure()
    baseline._verify_lock()

    start, end = FIXED_START, FIXED_END
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms

    for name, value in {
        "START": start,
        "END": end,
        "START_MS": start_ms,
        "END_MS": end_ms,
        "CAPITAL": CAPITAL,
        "LEVERAGE": LEVERAGE,
        "RISK_USDT": RISK_USDT,
        "RISK_PCT": RISK_PCT,
        "DAILY_LOSS": DAILY_LOSS,
        "COOLDOWN_MINUTES": 0,
        "STOP_ATR": STOP_ATR,
        "REWARD_R": REWARD_R,
    }.items():
        setattr(research.base, name, value)

    research.CAPITAL = CAPITAL
    research.LEVERAGE = LEVERAGE
    research.RISK_USDT = RISK_USDT
    research.RISK_PCT = RISK_PCT
    research.DAILY_LOSS = DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = BASE_POSITION_NOTIONAL
    research.STOP_ATR = STOP_ATR
    research.REWARD_R = REWARD_R
    research.COOLDOWN_MS = COOLDOWN_MS
    research.model = production
    research.Simulator.submit = baseline.base._submit_v162
    research.VERSION = production.VERSION
    research.BUILD = "1620-outer-rsi30-70-macd-early-exit-score-180d"
    production._install_signal_patch()
    return start, end


def _verify_lock():
    assert DAYS == 180
    assert FIXED_START.isoformat() == "2026-03-18T20:20:00+00:00"
    assert FIXED_END.isoformat() == "2026-09-14T20:20:00+00:00"
    assert production.RSI_MIN == 30.0 and production.RSI_MAX == 70.0
    assert production.THRESHOLD == 6.0
    assert BE_ENABLED is False
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0
    assert MONITOR_MAX_MS == 4 * 60 * 60_000


def _macd_adverse(side, a, b, c):
    if side == "做多":
        return c < b < a
    return c > b > a


def _prior_mean(rows, i, n=20):
    if i < n:
        return 0.0
    vals = [float(rows[j]["v"]) for j in range(i - n, i)]
    return sum(vals) / len(vals) if vals else 0.0


def _safe_ratio(a, b):
    return float(a) / float(b) if float(b) > 0.0 else 0.0


def _structure_break(quarter, q_ind, qi):
    if qi < 1:
        return {"long": False, "short": False, "support": None, "resistance": None,
                "long_break_atr": 0.0, "short_break_atr": 0.0}
    atr15 = float(q_ind[qi].get("atr") or 0.0)
    atr_prev = float(q_ind[qi - 1].get("atr") or 0.0)
    if atr15 <= 0.0 or atr_prev <= 0.0:
        return {"long": False, "short": False, "support": None, "resistance": None,
                "long_break_atr": 0.0, "short_break_atr": 0.0}
    prev_rows = quarter[max(0, qi - 220):qi]
    if len(prev_rows) < 20:
        return {"long": False, "short": False, "support": None, "resistance": None,
                "long_break_atr": 0.0, "short_break_atr": 0.0}
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
        if qi < 0 or hi < 0:
            continue

        f = f_ind[i]
        atr5 = float(f.get("atr") or 0.0)
        px5 = float(five[i]["c"])
        atr5_pct = _safe_ratio(atr5 * 100.0, px5)
        atr5_pct_1 = _safe_ratio(float(f_ind[i - 1].get("atr") or 0.0) * 100.0, float(five[i - 1]["c"]))
        atr5_pct_2 = _safe_ratio(float(f_ind[i - 2].get("atr") or 0.0) * 100.0, float(five[i - 2]["c"]))
        atr5_expanding = atr5_pct > atr5_pct_1 > atr5_pct_2
        vol5_ratio = _safe_ratio(float(five[i]["v"]), _prior_mean(five, i, 20))

        q = q_ind[qi]
        vol15_ratio = _safe_ratio(float(quarter[qi]["v"]), _prior_mean(quarter, qi, 20))
        structure = _structure_break(quarter, q_ind, qi)

        out[close_ms] = {
            "five_bar_start_ms": int(five[i]["t"]),
            "quarter_bar_start_ms": int(quarter[qi]["t"]),
            "hour_bar_start_ms": int(hour[hi]["t"]),
            "close5": px5,
            "rsi5": float(f.get("rsi") or math.nan),
            "boll_lower5": float(f.get("lower") or math.nan),
            "boll_upper5": float(f.get("upper") or math.nan),
            "macd5_a": float(f_ind[i - 2].get("hist") or 0.0),
            "macd5_b": float(f_ind[i - 1].get("hist") or 0.0),
            "macd5_c": float(f.get("hist") or 0.0),
            "atr5_pct": atr5_pct,
            "atr5_expanding": bool(atr5_expanding),
            "vol5_ratio": vol5_ratio,
            "rsi15": float(q.get("rsi") or math.nan),
            "macd15_a": float(q_ind[qi - 2].get("hist") or 0.0) if qi >= 2 else 0.0,
            "macd15_b": float(q_ind[qi - 1].get("hist") or 0.0) if qi >= 1 else 0.0,
            "macd15_c": float(q.get("hist") or 0.0),
            "vol15_ratio": vol15_ratio,
            "structure": structure,
            "hour_up": bool(h_ind[hi].get("up")),
            "hour_down": bool(h_ind[hi].get("down")),
        }
    _FEATURE_BY_CLOSE_MS = out


def _original_risk_distance(p):
    return abs(float(p.limit) - float(p.stop))


def _dynamic_threshold(current_r):
    if current_r <= -0.80:
        return 2.0
    if current_r <= -0.50:
        return 3.0
    if current_r <= -0.30:
        return 4.0
    return None


def _score_early_exit(side, feat, pos, risk, hold_ms, current_r):
    post_fill_5m = int(feat["five_bar_start_ms"]) >= int(pos.fill_time)
    post_fill_15m = int(feat["quarter_bar_start_ms"]) >= int(pos.fill_time)
    components = {}

    mfe_r = float(pos.mfe) / risk if risk > 0 else 0.0
    components["failure_to_progress"] = 1.0 if hold_ms >= MIN_PROGRESS_HOLD_MS and mfe_r < 0.25 else 0.0
    components["current_loss"] = 0.5 if current_r <= -0.30 else 0.0

    macd5 = post_fill_5m and _macd_adverse(side, feat["macd5_a"], feat["macd5_b"], feat["macd5_c"])
    macd15 = post_fill_15m and _macd_adverse(side, feat["macd15_a"], feat["macd15_b"], feat["macd15_c"])
    if side == "做多":
        rsi5_bad = post_fill_5m and math.isfinite(feat["rsi5"]) and feat["rsi5"] < 40.0
        rsi15_bad = post_fill_15m and math.isfinite(feat["rsi15"]) and feat["rsi15"] < 40.0
        boll_bad = post_fill_5m and math.isfinite(feat["boll_lower5"]) and feat["close5"] < feat["boll_lower5"]
        structure_bad = post_fill_15m and bool(feat["structure"]["long"])
        hour_bad = bool(feat["hour_down"])
    else:
        rsi5_bad = post_fill_5m and math.isfinite(feat["rsi5"]) and feat["rsi5"] > 60.0
        rsi15_bad = post_fill_15m and math.isfinite(feat["rsi15"]) and feat["rsi15"] > 60.0
        boll_bad = post_fill_5m and math.isfinite(feat["boll_upper5"]) and feat["close5"] > feat["boll_upper5"]
        structure_bad = post_fill_15m and bool(feat["structure"]["short"])
        hour_bad = bool(feat["hour_up"])

    components["macd5"] = 1.0 if macd5 else 0.0
    components["macd15"] = 1.0 if macd15 else 0.0
    components["rsi5"] = 0.5 if rsi5_bad else 0.0
    components["rsi15"] = 0.5 if rsi15_bad else 0.0
    components["momentum_capped"] = min(2.0, components["macd5"] + components["macd15"] + components["rsi5"] + components["rsi15"])
    components["boll_outer_failure"] = 1.0 if boll_bad else 0.0

    atr_points = 0.0
    if current_r < 0 and post_fill_5m and feat["atr5_pct"] >= 0.22 and feat["atr5_expanding"]:
        atr_points += 0.5
    if current_r < 0 and post_fill_5m and feat["atr5_pct"] >= 0.30:
        atr_points += 0.5
    volume_points = 0.0
    if post_fill_5m and feat["vol5_ratio"] >= 1.20:
        volume_points += 0.5
    if post_fill_15m and feat["vol15_ratio"] >= 1.20:
        volume_points += 0.5
    components["atr_state"] = atr_points
    components["volume_state"] = volume_points
    components["market_state_capped"] = min(1.5, atr_points + volume_points)
    components["structure_break_15m"] = 1.5 if structure_bad else 0.0
    components["opposite_1h_regime"] = 1.5 if hour_bad else 0.0

    total = (components["failure_to_progress"] + components["current_loss"] +
             components["momentum_capped"] + components["boll_outer_failure"] +
             components["market_state_capped"] + components["structure_break_15m"] +
             components["opposite_1h_regime"])
    return float(total), components, float(mfe_r)


def _close_early_exit(self, bar, feat, current_r, score, threshold, components, mfe_r):
    pos = self.position
    p = pos.pending
    close_ms = int(bar["t"]) + 60_000
    mark = float(bar["c"])
    direction = research.side_dir(p.side)
    bps = (LIMIT_PROTECTION_BPS + self.stress_extra_bps) / 10000.0
    limit_px = mark * (1.0 - bps if p.side == "做多" else 1.0 + bps)
    gross = (limit_px - p.limit) * p.quantity_btc * direction
    exit_fee = limit_px * p.quantity_btc * research.TAKER_BPS / 10000.0
    net = gross - pos.entry_fee - exit_fee + pos.funding_pnl
    self.cash += net

    risk = _original_risk_distance(p)
    risk_money = risk * p.quantity_btc
    structure = feat["structure"]
    row = {
        "variant": self.variant, "side": p.side, "score": p.score,
        "boll_path": p.factors["boll_path"], "entry": p.limit, "stop": p.stop,
        "target": p.target, "exit": limit_px, "reason": EXIT_REASON,
        "quantity_btc": p.quantity_btc, "net_pnl": net, "gross_pnl": gross,
        "entry_fee": pos.entry_fee, "exit_fee": exit_fee, "funding_pnl": pos.funding_pnl,
        "realized_r": net / risk_money if risk_money > 0 else 0.0,
        "mfe_r": float(mfe_r), "mae_r": pos.mae / risk if risk > 0 else 0.0,
        "front_r": p.front_r, "cost_r": p.cost_r,
        "entry_time": pos.fill_time, "exit_time": close_ms,
        "hold_min": (close_ms - pos.fill_time) / 60_000.0,
        "opportunity_id": p.opportunity_id,
        "score_components": dict(p.score_components),
        "trigger_combination": dict(p.trigger_combination),
        "exit_order_type": "MARKETABLE_LIMIT_PROXY",
        "exit_limit_protection_bps": LIMIT_PROTECTION_BPS,
        "early_exit_score": score, "early_exit_threshold": threshold,
        "early_exit_current_r": current_r, "early_exit_components": dict(components),
        "early_exit_atr5_pct": float(feat["atr5_pct"]),
        "early_exit_vol5_ratio": float(feat["vol5_ratio"]),
        "early_exit_vol15_ratio": float(feat["vol15_ratio"]),
        "early_exit_rsi5": float(feat["rsi5"]), "early_exit_rsi15": float(feat["rsi15"]),
        "early_exit_structure_support": structure.get("support"),
        "early_exit_structure_resistance": structure.get("resistance"),
    }
    row.update(p.factors)
    self.trades.append(row)
    self.position = None
    self.last_close_ms = close_ms
    self.stats["closed"] += 1
    self.stats["early_exit_score_closed"] += 1


def _process_exit_with_early_score(self, bar):
    if not self.position:
        return
    n_before = len(self.trades)
    _ORIGINAL_PROCESS_EXIT(self, bar)
    if len(self.trades) > n_before or not self.position:
        return

    pos = self.position
    p = pos.pending
    close_ms = int(bar["t"]) + 60_000
    feat = _FEATURE_BY_CLOSE_MS.get(close_ms)
    if not feat:
        return

    hold_ms = close_ms - int(pos.fill_time)
    if hold_ms < 0 or hold_ms > MONITOR_MAX_MS:
        return
    if int(feat["five_bar_start_ms"]) < int(pos.fill_time):
        return

    risk = _original_risk_distance(p)
    if risk <= 0.0:
        return
    mark = float(bar["c"])
    current_r = ((mark - float(p.limit)) * research.side_dir(p.side)) / risk
    threshold = _dynamic_threshold(current_r)
    if threshold is None:
        return

    score, components, mfe_r = _score_early_exit(p.side, feat, pos, risk, hold_ms, current_r)
    self.stats["early_exit_score_evaluated"] += 1
    if score < threshold:
        return
    _close_early_exit(self, bar, feat, current_r, score, threshold, components, mfe_r)


def _compare(baseline_sim, baseline_metrics, early_sim, early_metrics):
    base_by_id = {str(r.get("opportunity_id")): r for r in baseline_sim.trades if r.get("opportunity_id")}
    early_exits = [r for r in early_sim.trades if str(r.get("reason")) == EXIT_REASON]
    matched, false_kills = [], []
    saved_vs_baseline = 0.0
    for r in early_exits:
        b = base_by_id.get(str(r.get("opportunity_id")))
        if not b:
            continue
        delta = float(r.get("net_pnl") or 0.0) - float(b.get("net_pnl") or 0.0)
        saved_vs_baseline += delta
        item = {
            "opportunity_id": r.get("opportunity_id"), "side": r.get("side"),
            "entry_time": r.get("entry_time"), "early_hold_min": r.get("hold_min"),
            "early_score": r.get("early_exit_score"), "early_threshold": r.get("early_exit_threshold"),
            "early_current_r": r.get("early_exit_current_r"), "early_net_pnl": r.get("net_pnl"),
            "baseline_reason": b.get("reason"), "baseline_hold_min": b.get("hold_min"),
            "baseline_net_pnl": b.get("net_pnl"), "delta_net_pnl": delta,
        }
        matched.append(item)
        if float(b.get("net_pnl") or 0.0) > 0.0:
            false_kills.append(item)

    base_fast_losses = [r for r in baseline_sim.trades if float(r.get("hold_min") or 0.0) <= 240.0 and float(r.get("net_pnl") or 0.0) < 0.0]
    early_fast_losses = [r for r in early_sim.trades if float(r.get("hold_min") or 0.0) <= 240.0 and float(r.get("net_pnl") or 0.0) < 0.0]
    threshold_counts = Counter(str(r.get("early_exit_threshold")) for r in early_exits)

    def avg(rows, key):
        return sum(float(r.get(key) or 0.0) for r in rows) / len(rows) if rows else 0.0

    return {
        "baseline": {
            "trades": baseline_metrics["trades"], "wins": baseline_metrics["wins"], "losses": baseline_metrics["losses"],
            "win_rate_pct": baseline_metrics["win_rate_pct"], "net_pnl": baseline_metrics["net_pnl"],
            "net_return_pct": baseline_metrics["net_return_pct"], "profit_factor": baseline_metrics["profit_factor"],
            "max_drawdown_usdt": baseline_metrics["max_drawdown_usdt"], "max_drawdown_pct": baseline_metrics["max_drawdown_pct"],
            "expectancy": baseline_metrics["expectancy"], "fast_0_4h_losses": len(base_fast_losses),
            "fast_0_4h_loss_pnl": sum(float(r.get("net_pnl") or 0.0) for r in base_fast_losses),
        },
        "early_exit": {
            "trades": early_metrics["trades"], "wins": early_metrics["wins"], "losses": early_metrics["losses"],
            "win_rate_pct": early_metrics["win_rate_pct"], "net_pnl": early_metrics["net_pnl"],
            "net_return_pct": early_metrics["net_return_pct"], "profit_factor": early_metrics["profit_factor"],
            "max_drawdown_usdt": early_metrics["max_drawdown_usdt"], "max_drawdown_pct": early_metrics["max_drawdown_pct"],
            "expectancy": early_metrics["expectancy"], "fast_0_4h_losses": len(early_fast_losses),
            "fast_0_4h_loss_pnl": sum(float(r.get("net_pnl") or 0.0) for r in early_fast_losses),
            "early_exit_count": len(early_exits), "early_exit_avg_r": avg(early_exits, "realized_r"),
            "early_exit_avg_hold_min": avg(early_exits, "hold_min"), "threshold_counts": dict(threshold_counts),
        },
        "delta": {
            "net_pnl": early_metrics["net_pnl"] - baseline_metrics["net_pnl"],
            "net_return_pct_points": early_metrics["net_return_pct"] - baseline_metrics["net_return_pct"],
            "profit_factor": early_metrics["profit_factor"] - baseline_metrics["profit_factor"],
            "max_drawdown_usdt": early_metrics["max_drawdown_usdt"] - baseline_metrics["max_drawdown_usdt"],
            "max_drawdown_pct_points": early_metrics["max_drawdown_pct"] - baseline_metrics["max_drawdown_pct"],
            "expectancy": early_metrics["expectancy"] - baseline_metrics["expectancy"],
            "fast_0_4h_loss_pnl": sum(float(r.get("net_pnl") or 0.0) for r in early_fast_losses) - sum(float(r.get("net_pnl") or 0.0) for r in base_fast_losses),
        },
        "matched_early_exits": len(matched), "matched_saved_vs_baseline_pnl": saved_vs_baseline,
        "false_kill_count": len(false_kills),
        "false_kill_rate_pct": (len(false_kills) / len(matched) * 100.0) if matched else 0.0,
        "false_kills": false_kills, "matched_details": matched,
    }


def main():
    global _ORIGINAL_PROCESS_EXIT
    start, end = _configure_180d()
    _verify_lock()
    _ORIGINAL_PROCESS_EXIT = research.Simulator.process_exit

    print("V162_EARLY_EXIT_SCORE_180D_AB_CONFIG "
          f"start={start.isoformat()} end={end.isoformat()} "
          "A=baseline B=early_exit_score monitor=0..240m "
          "thresholds=-0.3R:4,-0.5R:3,-0.8R:2 original_-1R_priority=ON", flush=True)

    meta = research.base.fetch_instrument()
    data = {tf: research.base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = research.base.fetch_funding()
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)
    _prime_feature_map(data["5m"], data["15m"], data["1H"])

    research.Simulator.process_exit = _ORIGINAL_PROCESS_EXIT
    baseline_sim, baseline_metrics = research.run(data, ts, meta, funding, variant="baseline")
    baseline_metrics = dict(baseline_metrics)

    research.Simulator.process_exit = _process_exit_with_early_score
    early_sim, early_metrics = research.run(data, ts, meta, funding, variant="baseline")
    early_metrics = dict(early_metrics)
    comparison = _compare(baseline_sim, baseline_metrics, early_sim, early_metrics)

    common = {
        "release_base": "1.6.2", "days": DAYS, "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "capital": CAPITAL, "base_position_notional_usdt": BASE_POSITION_NOTIONAL, "leverage": LEVERAGE,
        "base_risk_usdt": RISK_USDT, "score_threshold": 6.0, "middle_enabled": False, "outer_only": True,
        "outer_rsi_min": 30.0, "outer_rsi_max": 70.0, "outer_macd_improving_required": True,
        "outer_4h_aligned_required": True, "volume_score_enabled": True, "volume_score_points": 0.5,
        "be_enabled": False, "stop_atr_timeframe": "1H", "stop_atr_multiplier": 1.0, "reward_r": 2.0,
    }
    baseline_metrics.update(common | {"label": "A — V1.6.2 baseline 180D", "research_variant": "baseline_180d"})
    early_metrics.update(common | {
        "label": "B — V1.6.2 + Early Exit Score first 4H 180D", "research_variant": "early_exit_score_4h_180d",
        "early_exit_enabled": True, "early_exit_monitor_minutes": 240,
        "early_exit_dynamic_thresholds": {"-0.30R": 4.0, "-0.50R": 3.0, "-0.80R": 2.0},
        "early_exit_eval_timeframe": "closed_5m", "early_exit_15m_confirmation": True,
        "early_exit_1h_regime": True,
    })

    outdir = Path("backtest_output_v162_early_exit_score_180d_2000u")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metrics_baseline_180d.json").write_text(json.dumps(baseline_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "metrics_early_exit_180d.json").write_text(json.dumps(early_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "comparison_180d.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline.base.prior._write_trade_csv(baseline_sim.trades, outdir / "trades_baseline_180d.csv")
    baseline.base.prior._write_trade_csv(early_sim.trades, outdir / "trades_early_exit_180d.csv")

    a, b, d = comparison["baseline"], comparison["early_exit"], comparison["delta"]
    report = [
        "# V1.6.2 Early Exit Score — 180D A/B / 2000U", "",
        f"Window: {start.isoformat()} -> {end.isoformat()}", "",
        "## Rule lock",
        "- Early Exit active only during first 0-240 minutes after fill; >240m is fully disabled.",
        "- Closed-5m evaluation only; 15m confirmation and 1H regime use only fully closed candles.",
        "- Dynamic threshold: <=-0.30R requires 4 points; <=-0.50R requires 3; <=-0.80R requires 2.",
        "- Original intrabar -1R 1H-ATR stop and +2R TP keep priority.",
        "- Momentum bucket (MACD+RSI) capped at 2.0; market-state bucket (ATR+Volume) capped at 1.5.",
        "- 15m adverse structure break = +1.5; opposite 1H regime = +1.5; failure-to-progress after 30m = +1.", "",
        "## A — baseline",
        f"- Trades {a['trades']}; WR {a['win_rate_pct']:.2f}%; net {a['net_pnl']:+.2f}U ({a['net_return_pct']:+.2f}%); PF {a['profit_factor']:.3f}; MaxDD {a['max_drawdown_pct']:.2f}%.",
        f"- 0-4H losing trades: {a['fast_0_4h_losses']}; combined PnL {a['fast_0_4h_loss_pnl']:+.2f}U.", "",
        "## B — Early Exit Score",
        f"- Trades {b['trades']}; WR {b['win_rate_pct']:.2f}%; net {b['net_pnl']:+.2f}U ({b['net_return_pct']:+.2f}%); PF {b['profit_factor']:.3f}; MaxDD {b['max_drawdown_pct']:.2f}%.",
        f"- Early exits: {b['early_exit_count']}; avg realized R {b['early_exit_avg_r']:+.3f}; avg hold {b['early_exit_avg_hold_min']:.1f}m.",
        f"- 0-4H losing trades: {b['fast_0_4h_losses']}; combined PnL {b['fast_0_4h_loss_pnl']:+.2f}U.", "",
        "## Delta B - A",
        f"- Net PnL {d['net_pnl']:+.2f}U; return {d['net_return_pct_points']:+.2f}pp; PF {d['profit_factor']:+.3f}.",
        f"- MaxDD {d['max_drawdown_pct_points']:+.2f}pp; expectancy {d['expectancy']:+.3f}U/trade.",
        f"- 0-4H losing PnL improvement {d['fast_0_4h_loss_pnl']:+.2f}U.",
        f"- Matched early exits: {comparison['matched_early_exits']}; matched PnL delta {comparison['matched_saved_vs_baseline_pnl']:+.2f}U.",
        f"- False-kill count: {comparison['false_kill_count']} ({comparison['false_kill_rate_pct']:.2f}% of matched early exits).", "",
        "Historical simulation only. LIMIT/marketable-LIMIT fills use closed-bar proxies; historical tick/orderbook sequence is unavailable.",
    ]
    (outdir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print("EARLY_EXIT_180D_COMPARISON")
    print(json.dumps(comparison, ensure_ascii=False, indent=2), flush=True)
    print("OUTPUT_DIR", outdir.resolve(), flush=True)


if __name__ == "__main__":
    main()

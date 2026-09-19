#!/usr/bin/env python3
"""KAYTRADE research: 4H+1H trend / direct 15m BOLL pullback, 180D.

ENTRY (no score system):
- 4H and 1H must independently show the same full trend:
  long = close>EMA200, EMA20>EMA50 and indicator up=True;
  short = close<EMA200, EMA20<EMA50 and indicator down=True.
- latest CLOSED 15m candle touches direction-side BOLL outer:
  long low<=lower; short high>=upper.
- front strong structure space must be strictly >1.50R.
- estimated execution cost must be strictly <0.30R.
- no RSI / MACD / volume / entry-drift / BOLL-width / score threshold gate.
- fixed 1x historical LIMIT-at-mark entry proxy.

EXIT:
- SL = 1.0 x closed 1H ATR.
- TP = full position at 2R.
- No BE.
- production PEE4 tiered Boolean logic, first 4h, permanently disabled after MFE>=+0.60R.
- actual PEE4 exit => global Lock1H for both sides.

Research only. Production V1.7 files are not modified.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from core import indicators
import v152_model as structure_model
import run_v168_180d_backtest_r2 as base180
import run_v168_boll5_overext035_pee3_mfe06_180d as pee_src
import run_v168_boll5_overext010_pee4_365d as pee4
from research_backtest_data_cache import load_market

research = base180.research

DAYS = 180
CAPITAL = 2_000.0
BASE_POSITION_NOTIONAL = 2_000.0
LEVERAGE = 5
RISK_USDT = 20.0
RISK_PCT = 1.0
DAILY_LOSS = 60.0
STOP_ATR = 1.0
REWARD_R = 2.0
ORDER_TTL_MS = 60_000
FRONT_MIN_R = 1.50
COST_MAX_R = 0.30
LOCK_MS = 60 * 60_000
FIXED_END = base180.FIXED_END
FIXED_START = FIXED_END - timedelta(days=DAYS)
TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H")
OUT = Path("backtest_output_v170_trend_pullback_180d")
RESULT_NAME = "result_v170_trend_pullback_180d.json"
TRADES_NAME = "trades_v170_trend_pullback_180d.csv"
PEE_EXIT_REASON = "PEE4_EARLY_EXIT_TIERED"

_ORIG_PEE_CLOSE = pee_src._close_pee3


def _finite(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _trend_state(rows):
    """Independent full trend for a single timeframe; no 15m direction dependency."""
    ind = indicators(rows)
    close = float(rows[-1]["c"])
    if close > float(ind["ema200"]) and float(ind["ema20"]) > float(ind["ema50"]) and bool(ind["up"]):
        return "做多"
    if close < float(ind["ema200"]) and float(ind["ema20"]) < float(ind["ema50"]) and bool(ind["down"]):
        return "做空"
    return "观望"


def _joint_direction(hour, four):
    h1 = _trend_state(hour)
    h4 = _trend_state(four)
    return h1 if h1 == h4 and h1 in ("做多", "做空") else "观望", h1, h4


def _boll_touch(quarter, side):
    ind = indicators(quarter)
    bar = quarter[-1]
    lower = float(ind["lower"])
    upper = float(ind["upper"])
    if side == "做多":
        hit = float(bar["l"]) <= lower
        ref = lower
        path = "15m_lower"
    else:
        hit = float(bar["h"]) >= upper
        ref = upper
        path = "15m_upper"
    if not hit:
        return None
    return {
        "bar_t": int(bar["t"]),
        "signal_close_ms": int(bar["t"]) + 15 * 60_000,
        "lower": lower,
        "middle": float(ind["middle"]),
        "upper": upper,
        "reference": ref,
        "path": path,
        "bar_o": float(bar["o"]),
        "bar_h": float(bar["h"]),
        "bar_l": float(bar["l"]),
        "bar_c": float(bar["c"]),
    }


def _opportunity_id(side, bar_t, ref):
    raw = f"trend-pb|{side}|{int(bar_t)}|{float(ref):.8f}".encode()
    return "tp-" + hashlib.sha1(raw).hexdigest()[:16]


def _make_opportunity(hour, quarter, five, one, four, side, trigger):
    h1 = indicators(hour)
    f5 = indicators(five)
    entry_ref = float(one[-1]["c"])
    front = structure_model._front_structure(hour, quarter, side, entry_ref)
    return {
        "id": _opportunity_id(side, trigger["bar_t"], trigger["reference"]),
        "side": side,
        "signal_path": trigger["path"],
        "boll_timeframe": "15m",
        "signal_bar_t": int(trigger["bar_t"]),
        "signal_close_ms": int(trigger["signal_close_ms"]),
        "created_ms": int(trigger["signal_close_ms"]),
        "expires_ms": int(trigger["signal_close_ms"]) + 15 * 60_000,
        "trigger_reference": float(trigger["reference"]),
        "trigger_detail": {
            "boll_lower": float(trigger["lower"]),
            "boll_middle": float(trigger["middle"]),
            "boll_upper": float(trigger["upper"]),
            "bar_o": trigger["bar_o"], "bar_h": trigger["bar_h"],
            "bar_l": trigger["bar_l"], "bar_c": trigger["bar_c"],
        },
        "atr1h": float(h1["atr"]),
        "atr15": float(indicators(quarter)["atr"]),
        "atr5": float(f5["atr"]),
        "front_structure": front,
        "trend_1h": _trend_state(hour),
        "trend_4h": _trend_state(four),
        "consumed": False,
    }


class TrendPullbackModel:
    VERSION = "V1.7-TrendPullback-Research"
    BUILD = "TP180D"
    THRESHOLD = 0.0
    ENTRY_WINDOW_MS = 15 * 60_000
    BOLL_TRIGGER_TIMEFRAME = "15m"
    FIVE_MINUTE_BOLL_ENABLED = False

    @staticmethod
    def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                 maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                 now_ms=None, allow_new=True):
        now_ms = int(now_ms if now_ms is not None else int(one[-1]["t"]) + 60_000)
        direction, h1_state, h4_state = _joint_direction(hour, four)
        opp = dict(opportunity) if isinstance(opportunity, dict) else None
        transition = None

        if opp:
            if int(now_ms) >= int(opp.get("expires_ms") or 0):
                transition = ("invalidated", "15m外轨机会到下一根15m收盘失效")
                opp = None
            elif direction != str(opp.get("side") or ""):
                transition = ("invalidated", "4H/1H大趋势不再同向")
                opp = None

        trigger = _boll_touch(quarter, direction) if direction in ("做多", "做空") else None
        if allow_new and isinstance(trigger, dict):
            old_t = int((opp or {}).get("signal_bar_t") or -1)
            if opp is None or int(trigger["bar_t"]) > old_t:
                opp = _make_opportunity(hour, quarter, five, one, four, direction, trigger)
                transition = ("created", str(opp["id"]))

        scores = {
            "做多": {"total": 0.0, "gate": False, "eligible": False, "confirmations": {}, "layers": {}},
            "做空": {"total": 0.0, "gate": False, "eligible": False, "confirmations": {}, "layers": {}},
        }
        if isinstance(opp, dict):
            side = str(opp["side"])
            scores[side] = {
                "total": 0.0,
                "raw": 0.0,
                "gate": True,
                "eligible": True,
                "required": 0.0,
                "position_multiplier": 1.0,
                "layers": {},
                "confirmations": {
                    "trend_1h": h1_state,
                    "trend_4h": h4_state,
                    "trigger": dict(opp.get("trigger_detail") or {}),
                    "required": {
                        "4h_1h_same_full_trend": direction == side,
                        "closed_15m_boll_outer_touch": True,
                    },
                    "blockers": [],
                },
                "opportunity": dict(opp),
            }

        return {
            "side": str(opp.get("side")) if isinstance(opp, dict) else "观望",
            "direction": direction,
            "scores": scores,
            "opportunity": dict(opp) if isinstance(opp, dict) else None,
            "opportunity_id": str((opp or {}).get("id") or ""),
            "strategy_version": TrendPullbackModel.VERSION,
            "trend_1h": h1_state,
            "trend_4h": h4_state,
        }, opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        # Entry execution is intentionally implemented in _submit_trend below.
        return True, {}, []


def _submit_trend(self, result, now_ms, mark):
    opp = result.get("opportunity")
    if not isinstance(opp, dict):
        return
    side = str(opp.get("side") or "")
    if side not in ("做多", "做空"):
        return

    if int(now_ms) < int(getattr(self, "_trend_lock_until_ms", 0) or 0):
        self.stats["pee4_lock1h_blocked_submit"] += 1
        return

    signal_bar_t = int(opp.get("signal_bar_t") or -1)
    if signal_bar_t <= int(getattr(self, "_trend_last_submitted_signal_bar_t", -1)):
        self.stats["same_15m_signal_repeat_block"] += 1
        return
    if not self.can_submit(now_ms):
        return

    entry = float(mark)
    stop_distance = float(opp.get("atr1h") or 0.0) * STOP_ATR
    if stop_distance <= 0:
        self.stats["invalid_atr1h"] += 1
        return

    # Recompute front structure from the immutable signal snapshot stored in opportunity.
    front_r, front_status = structure_model._front_r(opp, entry, stop_distance)
    if not (math.isinf(front_r) or float(front_r) > FRONT_MIN_R):
        self.stats["front_r_le_150_block"] += 1
        self.blockers["Front R <= 1.50R"] += 1
        return

    cost_r = structure_model.estimated_cost_r(
        entry, stop_distance, side,
        maker_bps=research.MAKER_BPS,
        taker_bps=research.TAKER_BPS,
        slippage_bps=research.SLIPPAGE_BPS,
    )
    if not float(cost_r) < COST_MAX_R:
        self.stats["cost_r_ge_030_block"] += 1
        self.blockers["Cost R >= 0.30R"] += 1
        return

    direction = research.side_dir(side)
    stop = entry - direction * stop_distance
    target = entry + direction * stop_distance * REWARD_R

    eq = self.equity(mark)
    risk_capital = min(CAPITAL, eq)
    base_risk = min(RISK_USDT, risk_capital * RISK_PCT / 100.0)
    risk_budget = min(base_risk, self.daily_remaining(now_ms, mark))
    if risk_budget <= 0:
        self.stats["sizing_skips"] += 1
        return

    maker = research.MAKER_BPS / 10000.0
    taker = research.TAKER_BPS / 10000.0
    slip = research.SLIPPAGE_BPS / 10000.0
    per_btc = stop_distance + entry * max(maker, taker) + stop * (taker + slip)
    notional_cap = min(
        BASE_POSITION_NOTIONAL,
        risk_capital * LEVERAGE,
        max(eq, 0.0) * 0.9 * LEVERAGE,
    )
    btc = research.round_contract_btc(min(risk_budget / per_btc, notional_cap / entry), self.meta)
    if btc <= 0:
        self.stats["sizing_skips"] += 1
        return

    dt = research.local_dt(now_ms)
    factors = {
        "boll_path": str(opp.get("signal_path") or ""),
        "boll_timeframe": "15m",
        "trend_1h": str(opp.get("trend_1h") or ""),
        "trend_4h": str(opp.get("trend_4h") or ""),
        "4h_trend_state_actual": "aligned",
        "front_structure_timeframe": str((opp.get("front_structure") or {}).get("timeframe") or ""),
        "front_structure_price": (opp.get("front_structure") or {}).get("price"),
        "front_space_status": "unknown" if front_status == "unknown" else "ok",
        "front_r": float(front_r),
        "cost_r": float(cost_r),
        "signal_age_min": max(0.0, (int(now_ms) - int(opp.get("signal_close_ms") or now_ms)) / 60_000.0),
        "atr1h_pct": 100.0 * stop_distance / max(entry, 1e-12),
        "local_hour_cn": int(dt.hour),
        "weekday_cn": dt.strftime("%a"),
        "month_cn": dt.strftime("%Y-%m"),
        "trigger_reference": float(opp.get("trigger_reference") or entry),
        "boll_lower_15m": (opp.get("trigger_detail") or {}).get("boll_lower"),
        "boll_middle_15m": (opp.get("trigger_detail") or {}).get("boll_middle"),
        "boll_upper_15m": (opp.get("trigger_detail") or {}).get("boll_upper"),
        "entry_rule": "4H+1H full trend + closed15m outer touch + Front>1.50R + Cost<0.30R",
        "position_multiplier": 1.0,
    }

    self.pending = research.PendingEntry(
        side=side,
        limit=entry,
        stop=stop,
        target=target,
        quantity_btc=btc,
        score=0.0,
        opportunity_id=str(opp["id"]),
        signal_bar_t=signal_bar_t,
        signal_close_ms=int(opp.get("signal_close_ms") or now_ms),
        submitted_ms=int(now_ms),
        expires_ms=int(now_ms) + ORDER_TTL_MS,
        factors=factors,
        score_components={},
        trigger_combination=dict(opp.get("trigger_detail") or {}),
        front_r=float(front_r),
        cost_r=float(cost_r),
    )
    self._trend_last_submitted_signal_bar_t = signal_bar_t
    self.stats["trend_pullback_limit_submitted"] += 1
    self.stats["limit_submitted"] += 1


def _close_pee4_with_lock(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req):
    before = len(self.trades)
    _ORIG_PEE_CLOSE(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req)
    if len(self.trades) > before:
        close_ms = int(bar["t"]) + 60_000
        self._trend_lock_until_ms = max(int(getattr(self, "_trend_lock_until_ms", 0) or 0), close_ms + LOCK_MS)
        self.stats["pee4_lock1h_events"] += 1


def configure():
    start, end = FIXED_START, FIXED_END
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    for name, value in {
        "START": start, "END": end, "START_MS": start_ms, "END_MS": end_ms,
        "CAPITAL": CAPITAL, "LEVERAGE": LEVERAGE, "RISK_USDT": RISK_USDT,
        "RISK_PCT": RISK_PCT, "DAILY_LOSS": DAILY_LOSS,
        "COOLDOWN_MINUTES": 0, "STOP_ATR": STOP_ATR, "REWARD_R": REWARD_R,
    }.items():
        setattr(research.base, name, value)

    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms
    research.SPLIT_MS = int((start + (end - start) / 2).timestamp() * 1000)
    research.CAPITAL = CAPITAL
    research.LEVERAGE = LEVERAGE
    research.RISK_USDT = RISK_USDT
    research.RISK_PCT = RISK_PCT
    research.DAILY_LOSS = DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = BASE_POSITION_NOTIONAL
    research.STOP_ATR = STOP_ATR
    research.REWARD_R = REWARD_R
    research.COOLDOWN_MS = 0
    research.VERSION = TrendPullbackModel.VERSION
    research.BUILD = TrendPullbackModel.BUILD
    research.model = TrendPullbackModel
    research.Simulator.submit = _submit_trend

    # Restore the audited ordinary LIMIT fill / SL / TP / finish implementation.
    import run_v166_15m_boll_macd_adverse_only_360d_fast as proven
    research.Simulator.process_pending = proven._ORIG_PENDING
    research.Simulator.finish = proven._ORIG_FINISH

    # Install the exact PEE4 decision onto the proven PEE execution path.
    pee_src._decision = pee4._pee4_decision
    pee_src.PEE_VERSION = "4.0-tiered-boolean"
    pee_src.EXIT_REASON = PEE_EXIT_REASON
    pee_src._close_pee3 = _close_pee4_with_lock
    research.Simulator.process_exit = pee_src._process_exit_pee3

    return start, end


def verify(start=None, end=None):
    if start is None or end is None:
        start, end = FIXED_START, FIXED_END
    assert (end - start).days == DAYS
    assert abs(FRONT_MIN_R - 1.50) < 1e-12
    assert abs(COST_MAX_R - 0.30) < 1e-12
    assert STOP_ATR == 1.0 and REWARD_R == 2.0 and LOCK_MS == 3_600_000

    # Strict boundaries requested by user.
    assert not (1.50 > FRONT_MIN_R)
    assert 1.500001 > FRONT_MIN_R
    assert not (0.30 < COST_MAX_R)
    assert 0.299999 < COST_MAX_R

    # Trend is independent on 4H and 1H; 15m is trigger only.
    assert TrendPullbackModel.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert TrendPullbackModel.THRESHOLD == 0.0

    # Exact PEE4 tier checks.
    assert pee4._pee4_decision(60_000, -0.59, 0.0, 0, {}, 0.0, {}) is None
    assert pee4._pee4_decision(60_000, -0.65, 0.60, 2, {}, 0.0, {}) is None
    d = pee4._pee4_decision(
        60_000, -0.65, 0.0, 2,
        {"opposite_closed_1h": True, "trend_reversal_15m": True}, 0.0, {}
    )
    assert d and d["allow"]


def _stats(rows):
    rows = list(rows)
    wins = [r for r in rows if _finite(r.get("net_pnl")) > 0]
    losses = [r for r in rows if _finite(r.get("net_pnl")) <= 0]
    gp = sum(max(_finite(r.get("net_pnl")), 0.0) for r in rows)
    gl = -sum(min(_finite(r.get("net_pnl")), 0.0) for r in rows)
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": 100.0 * len(wins) / len(rows) if rows else 0.0,
        "net_pnl": sum(_finite(r.get("net_pnl")) for r in rows),
        "profit_factor": gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0),
        "expectancy": sum(_finite(r.get("net_pnl")) for r in rows) / len(rows) if rows else 0.0,
    }


def _analysis(rows):
    by_side = defaultdict(list)
    by_exit = defaultdict(list)
    by_front = defaultdict(list)
    by_cost = defaultdict(list)
    for row in rows:
        by_side[str(row.get("side") or "unknown")].append(row)
        by_exit[str(row.get("reason") or "unknown")].append(row)
        fr = _finite(row.get("front_r"), math.inf)
        if math.isinf(fr): fk = "unknown/no-front"
        elif fr < 2.0: fk = "1.5-2.0R"
        elif fr < 3.0: fk = "2.0-3.0R"
        else: fk = ">=3.0R"
        by_front[fk].append(row)
        cr = _finite(row.get("cost_r"), math.inf)
        ck = "<0.15R" if cr < 0.15 else "0.15-0.20R" if cr < 0.20 else "0.20-0.25R" if cr < 0.25 else "0.25-0.30R"
        by_cost[ck].append(row)
    return {
        "by_side": {k: _stats(v) for k, v in by_side.items()},
        "by_exit_reason": {k: _stats(v) for k, v in by_exit.items()},
        "by_front_r": {k: _stats(v) for k, v in by_front.items()},
        "by_cost_r": {k: _stats(v) for k, v in by_cost.items()},
    }


def _write_csv(rows, path):
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
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for row in rows:
            item = dict(row)
            for key, value in list(item.items()):
                if isinstance(value, (dict, list, tuple)):
                    item[key] = json.dumps(value, ensure_ascii=False)
            w.writerow(item)


def main():
    started = time.perf_counter()
    start, end = configure()
    verify(start, end)
    OUT.mkdir(parents=True, exist_ok=True)

    print(
        "TREND_PULLBACK_180D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "entry=4H+1H_full_trend_then_closed15m_BOLL_outer_direct",
        "front=>1.50R_strict", "cost=<0.30R_strict",
        "score=OFF", "rsi=OFF", "macd=OFF", "volume=OFF", "entry_drift=OFF", "boll_width=OFF",
        "sl=1H_ATR_x1", "tp=2R_full", "pee=PEE4", "lock=1H_after_PEE4", "be=OFF",
        flush=True,
    )

    meta, data, funding, cache_manifest = load_market(research.base, TIMEFRAMES)
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    for tf in TIMEFRAMES:
        rows = data[tf]
        step = research.base.BAR_MS[tf]
        assert rows and all(int(b["t"]) - int(a["t"]) == step for a, b in zip(rows, rows[1:])), f"AUDIT FAIL {tf}"

    pee_src._prime_feature_map(data["5m"], data["15m"], data["1H"])
    print("AUDIT PASS: closed bars + 4H/1H full trend + strict 15m outer touch + strict Front/Cost + exact PEE4", flush=True)

    sim, raw_metrics = research.run(data, ts, meta, funding, variant="trend_pullback_180d")
    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    pee_rows = [r for r in rows if str(r.get("reason") or "") == PEE_EXIT_REASON]

    payload = {
        "research": "4H+1H full-trend direct 15m BOLL outer pullback, strict Front>1.50R and Cost<0.30R, 180D",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": CAPITAL,
        "strategy_lock": {
            "4h_trend": "close vs EMA200 + EMA20/EMA50 ordering + both EMA slopes via indicators up/down",
            "1h_trend": "close vs EMA200 + EMA20/EMA50 ordering + both EMA slopes via indicators up/down",
            "trend_requirement": "4H and 1H must be same full trend",
            "15m_trigger": "latest CLOSED candle touches direction-side BOLL outer",
            "long_trigger": "15m low <= lower",
            "short_trigger": "15m high >= upper",
            "score_system": False,
            "rsi_entry_gate": False,
            "macd_entry_gate": False,
            "volume_entry_gate": False,
            "entry_drift_gate": False,
            "boll_width_gate": False,
            "front_structure": "nearest strong adverse structure from 1H/15m; unknown front passes",
            "front_r_rule": "> 1.50R strictly",
            "cost_r_rule": "< 0.30R strictly",
            "position": "fixed 1x",
            "entry": "LIMIT at first closed-1m mark after closed-15m signal; 60s historical touch proxy",
            "stop": "1.0 x 1H ATR",
            "tp": "2R full position",
            "break_even": False,
            "pee_version": "4.0-tiered-boolean",
            "pee_monitor_max_hours": 4,
            "pee_mfe_cutoff_r": 0.60,
            "pee_starts_at_r": -0.60,
            "pee4_post_exit_entry_lock_minutes": 60,
            "pee4_post_exit_entry_lock_scope": "GLOBAL both sides",
        },
        "metrics": metrics,
        "analysis": _analysis(rows),
        "pee4": {
            "early_exit_count": len(pee_rows),
            "by_path": dict(Counter(str(r.get("early_exit_path") or "unknown") for r in pee_rows)),
            "net_pnl_rows": sum(_finite(r.get("net_pnl")) for r in pee_rows),
            "lock_events": int(sim.stats.get("pee4_lock1h_events", 0)),
            "lock_blocked_submit_attempts": int(sim.stats.get("pee4_lock1h_blocked_submit", 0)),
        },
        "simulator_stats": dict(sim.stats),
        "top_blockers": sim.blockers.most_common(30),
        "transitions": dict(sim.transitions),
        "cache": cache_manifest,
        "timing_sec": {"total": time.perf_counter() - started},
        "production_strategy_variables_changed": False,
    }
    (OUT / RESULT_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(rows, OUT / TRADES_NAME)
    (OUT / "README.txt").write_text(
        "KAYTRADE Trend Pullback 180D research.\n"
        "4H+1H same full trend -> latest closed 15m direction-side BOLL outer touch -> "
        "Front R >1.50 and Cost R <0.30 -> fixed 1x LIMIT. "
        "Exit: 1H ATR SL, full 2R TP, PEE4, global Lock1H, No-BE.\n",
        encoding="utf-8",
    )

    print("TREND_PULLBACK_180D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

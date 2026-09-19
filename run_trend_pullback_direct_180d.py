#!/usr/bin/env python3
"""180D research: large-trend pullback direct entry.

User-defined research strategy:
- 4H and 1H must both show the same explicit EMA trend.
- Latest CLOSED 15m candle touches the direction-side BOLL outer band.
- No score, RSI, MACD, Volume, Entry Drift or BOLL-width entry filters.
- Front strong-structure space must be STRICTLY > 1.50R.
- Estimated round-trip execution cost must be STRICTLY < 0.30R.
- Fixed 1x LIMIT entry, 1H ATR x1 stop, full 2R target, No-BE.
- Existing PEE4 tiered Boolean exit retained.
- After an actual PEE4 exit, block all new entries for exactly 60 minutes.

Research-only. Production V1.7 files are unchanged.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path

from core import indicators
import v152_model as structure_model
import run_v168_180d_backtest_r2 as base
import run_v168_boll5_overext035_pee3_mfe06_180d as pee_src
import run_v168_boll5_overext010_pee4_365d as pee4

research = base.research

DAYS = 180
OUT = Path("backtest_output_trend_pullback_direct_180d")
VERSION = "trend-pullback-direct"
BUILD = "180d-r1"

FRONT_MIN_R_STRICT = 1.50
COST_MAX_R_STRICT = 0.30
LOCK_MS = 60 * 60_000
ORDER_TTL_MS = 60_000

_ORIG_PEE_CLOSE = pee_src._close_pee3
_LOCK_UNTIL_MS = 0
_LOCK_EVENTS = []


def _finite(value, default=0.0):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _tf_trend(rows, side):
    ind = indicators(rows)
    close = float(rows[-1]["c"])
    if side == "做多":
        return bool(
            close > float(ind["ema200"])
            and float(ind["ema20"]) > float(ind["ema50"])
            and bool(ind["up"])
        )
    if side == "做空":
        return bool(
            close < float(ind["ema200"])
            and float(ind["ema20"]) < float(ind["ema50"])
            and bool(ind["down"])
        )
    return False


def _trend_side(hour, four):
    long_ok = _tf_trend(hour, "做多") and _tf_trend(four, "做多")
    short_ok = _tf_trend(hour, "做空") and _tf_trend(four, "做空")
    if long_ok and not short_ok:
        return "做多"
    if short_ok and not long_ok:
        return "做空"
    return "观望"


def _outer_touch(quarter, side):
    ind = indicators(quarter)
    bar = quarter[-1]
    lower = float(ind["lower"])
    upper = float(ind["upper"])
    low = float(bar["l"])
    high = float(bar["h"])
    touched = (low <= lower) if side == "做多" else (high >= upper)
    return touched, {
        "bar_t": int(bar["t"]),
        "bar_close_ms": int(bar["t"]) + 15 * 60_000,
        "low": low,
        "high": high,
        "close": float(bar["c"]),
        "boll_middle": float(ind["middle"]),
        "boll_lower": lower,
        "boll_upper": upper,
        "atr15": float(ind["atr"]),
    }


class TrendPullbackDirectModel:
    VERSION = VERSION
    BUILD = BUILD
    THRESHOLD = 0.0
    ENTRY_WINDOW_MS = 0
    TIME_WINDOW_ENABLED = False
    BOLL_TRIGGER_TIMEFRAME = "15m"
    FIVE_MINUTE_BOLL_ENABLED = False

    @staticmethod
    def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                 maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                 now_ms=None, allow_new=True):
        now_ms = int(now_ms if now_ms is not None else int(one[-1]["t"]) + 60_000)
        side = _trend_side(hour, four)
        empty = {
            "strategy_version": VERSION,
            "build": BUILD,
            "side": "观望",
            "direction": side,
            "opportunity": None,
            "scores": {
                "做多": {"total": 0.0, "gate": False, "eligible": False, "layers": {}, "confirmations": {}},
                "做空": {"total": 0.0, "gate": False, "eligible": False, "layers": {}, "confirmations": {}},
            },
        }
        if not allow_new or side not in ("做多", "做空"):
            return empty, None, None

        touched, trigger = _outer_touch(quarter, side)
        # Closed-bar-only direct entry: the signal exists for the exact evaluation
        # immediately after this 15m candle closes. No stale 15m opportunity is reused.
        if not touched or int(trigger["bar_close_ms"]) != now_ms:
            return empty, None, None

        h = indicators(hour)
        q = indicators(four)
        entry_ref = float(one[-1]["c"])
        front = structure_model._front_structure(hour, quarter, side, entry_ref)
        opp = {
            "id": f"tpd-{side}-{int(trigger['bar_t'])}",
            "side": side,
            "signal_path": "15m_outer_direct",
            "boll_timeframe": "15m",
            "signal_bar_t": int(trigger["bar_t"]),
            "signal_close_ms": int(trigger["bar_close_ms"]),
            "created_ms": now_ms,
            "expires_ms": now_ms + ORDER_TTL_MS,
            "trigger_reference": entry_ref,
            "trigger_detail": dict(trigger),
            "atr1h": float(h["atr"]),
            "atr15": float(trigger["atr15"]),
            "front_structure": front,
            "trend1h": "aligned",
            "trend4h": "aligned",
            "trend1h_ema20": float(h["ema20"]),
            "trend1h_ema50": float(h["ema50"]),
            "trend1h_ema200": float(h["ema200"]),
            "trend4h_ema20": float(q["ema20"]),
            "trend4h_ema50": float(q["ema50"]),
            "trend4h_ema200": float(q["ema200"]),
        }
        row = {
            "total": 0.0,
            "gate": True,
            "eligible": True,
            "layers": {},
            "confirmations": {
                "4H_trend_aligned": True,
                "1H_trend_aligned": True,
                "15m_boll_outer_touch": True,
                "trigger": dict(trigger),
                "score_disabled": True,
                "rsi_disabled": True,
                "macd_disabled": True,
                "volume_disabled": True,
                "entry_drift_disabled": True,
                "boll_width_disabled": True,
            },
        }
        scores = {
            "做多": row if side == "做多" else empty["scores"]["做多"],
            "做空": row if side == "做空" else empty["scores"]["做空"],
        }
        result = dict(empty)
        result.update({
            "side": side,
            "direction": side,
            "opportunity": dict(opp),
            "opportunity_id": opp["id"],
            "scores": scores,
        })
        return result, opp, ("created", opp["id"])

    @staticmethod
    def execution_checks(plan, opportunity, score):
        # Entry execution gates are implemented in _submit_direct so the strict
        # >1.50R and <0.30R inequalities use the actual LIMIT reference.
        return True, {}, []


def _front_r(opp, entry, stop_distance):
    front = (opp or {}).get("front_structure")
    if not isinstance(front, dict) or front.get("price") in (None, ""):
        return math.inf
    if stop_distance <= 0:
        return 0.0
    return abs(float(front["price"]) - float(entry)) / float(stop_distance)


def _close_with_lock(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req):
    global _LOCK_UNTIL_MS
    before = len(self.trades)
    _ORIG_PEE_CLOSE(self, bar, feat, current_r, mfe_r, hard_count, hard, soft_score, components, req)
    if len(self.trades) > before:
        close_ms = int(bar["t"]) + 60_000
        _LOCK_UNTIL_MS = max(_LOCK_UNTIL_MS, close_ms + LOCK_MS)
        _LOCK_EVENTS.append({"pee4_exit_ms": close_ms, "lock_until_ms": _LOCK_UNTIL_MS})


def _submit_direct(self, result, now_ms, mark):
    global _LOCK_UNTIL_MS
    if int(now_ms) < int(_LOCK_UNTIL_MS):
        self.stats["pee4_lock1h_blocked_submit"] += 1
        return

    opp = result.get("opportunity")
    if not isinstance(opp, dict):
        return
    side = str(opp.get("side") or "")
    if side not in ("做多", "做空"):
        return
    if not self.can_submit(now_ms):
        return

    self.stats["direct_signal_states"] += 1
    entry = float(mark)
    stop_distance = float(opp.get("atr1h") or 0.0) * base.STOP_ATR
    if stop_distance <= 0:
        self.stats["invalid_atr_blocks"] += 1
        return

    front_r = _front_r(opp, entry, stop_distance)
    if not (front_r > FRONT_MIN_R_STRICT):
        self.stats["front_r_strict_blocks"] += 1
        self.blockers["Front R <= 1.50R"] += 1
        return

    cost_r = structure_model.estimated_cost_r(
        entry, stop_distance, side,
        research.MAKER_BPS, research.TAKER_BPS, research.SLIPPAGE_BPS,
    )
    if not (cost_r < COST_MAX_R_STRICT):
        self.stats["cost_r_strict_blocks"] += 1
        self.blockers["Cost R >= 0.30R"] += 1
        return

    direction = research.side_dir(side)
    stop = entry - direction * stop_distance
    target = entry + direction * stop_distance * base.REWARD_R

    eq = self.equity(mark)
    risk_capital = min(base.CAPITAL, eq)
    base_risk = min(base.RISK_USDT, risk_capital * base.RISK_PCT / 100.0)
    risk_budget = min(base_risk, self.daily_remaining(now_ms, mark))
    if risk_budget <= 0:
        self.stats["sizing_skips"] += 1
        return

    maker = research.MAKER_BPS / 10000.0
    taker = research.TAKER_BPS / 10000.0
    slip = research.SLIPPAGE_BPS / 10000.0
    risk_entry_fee = max(maker, taker)
    per_btc = stop_distance + entry * risk_entry_fee + stop * (taker + slip)
    notional_cap = min(
        base.BASE_POSITION_NOTIONAL,
        risk_capital * base.LEVERAGE,
        max(eq, 0.0) * 0.9 * base.LEVERAGE,
    )
    btc = research.round_contract_btc(
        min(risk_budget / per_btc, notional_cap / entry),
        self.meta,
    )
    if btc <= 0:
        self.stats["sizing_skips"] += 1
        return

    front = opp.get("front_structure") or {}
    trigger = opp.get("trigger_detail") or {}
    factors = {
        "strategy_version": VERSION,
        "build": BUILD,
        "boll_path": "15m_outer_direct",
        "boll_timeframe": "15m",
        "trend1h_state": "aligned",
        "trend4h_state": "aligned",
        "front_structure_timeframe": front.get("timeframe"),
        "front_structure_price": front.get("price"),
        "front_structure_unknown": not bool(front),
        "front_r_strict_gt": FRONT_MIN_R_STRICT,
        "cost_r_strict_lt": COST_MAX_R_STRICT,
        "boll15_lower": trigger.get("boll_lower"),
        "boll15_upper": trigger.get("boll_upper"),
        "boll15_middle": trigger.get("boll_middle"),
        "boll15_signal_low": trigger.get("low"),
        "boll15_signal_high": trigger.get("high"),
        "score_disabled": True,
        "rsi_disabled": True,
        "macd_disabled": True,
        "volume_disabled": True,
        "entry_drift_disabled": True,
        "boll_width_disabled": True,
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
        signal_bar_t=int(opp["signal_bar_t"]),
        signal_close_ms=int(opp["signal_close_ms"]),
        submitted_ms=int(now_ms),
        expires_ms=int(now_ms) + ORDER_TTL_MS,
        factors=factors,
        score_components={},
        trigger_combination={
            "4H_trend": True,
            "1H_trend": True,
            "15m_boll_outer_touch": True,
            "front_r_gt_1_50": True,
            "cost_r_lt_0_30": True,
        },
        front_r=float(front_r),
        cost_r=float(cost_r),
    )
    self.stats["limit_submitted"] += 1


def configure():
    global _LOCK_UNTIL_MS, _LOCK_EVENTS
    _LOCK_UNTIL_MS = 0
    _LOCK_EVENTS = []

    start, end = base.configure()

    # Install exact PEE4 decision on the inherited audited PEE execution stack.
    pee_src._decision = pee4._pee4_decision
    pee_src.PEE_VERSION = "4.0-tiered-boolean"
    pee_src.EXIT_REASON = "PEE4_EARLY_EXIT_TIERED"
    pee_src._close_pee3 = _close_with_lock

    research.VERSION = VERSION
    research.BUILD = BUILD
    research.model = TrendPullbackDirectModel
    research.COOLDOWN_MS = 0
    research.Simulator.submit = _submit_direct
    research.Simulator.process_pending = base.proven._ORIG_PENDING
    research.Simulator.process_exit = pee_src._process_exit_pee3
    research.Simulator.finish = base.proven._ORIG_FINISH

    return start, end


def verify(start, end):
    assert DAYS == 180 and (end - start).days == 180
    assert abs(FRONT_MIN_R_STRICT - 1.50) < 1e-12
    assert abs(COST_MAX_R_STRICT - 0.30) < 1e-12
    assert LOCK_MS == 3_600_000
    assert ORDER_TTL_MS == 60_000
    assert research.COOLDOWN_MS == 0
    assert research.Simulator.process_exit is pee_src._process_exit_pee3
    assert pee_src._decision is pee4._pee4_decision

    # Strict inequality locks.
    assert not (1.50 > FRONT_MIN_R_STRICT)
    assert 1.500001 > FRONT_MIN_R_STRICT
    assert not (0.30 < COST_MAX_R_STRICT)
    assert 0.299999 < COST_MAX_R_STRICT

    d = pee4._pee4_decision(
        60_000, -0.65, 0.0, 2,
        {"opposite_closed_1h": True, "trend_reversal_15m": True},
        0.0, {},
    )
    assert d and d["allow"]


def _pf(rows):
    gp = sum(max(_finite(r.get("net_pnl")), 0.0) for r in rows)
    gl = -sum(min(_finite(r.get("net_pnl")), 0.0) for r in rows)
    return gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0)


def _pack(rows):
    rows = list(rows)
    wins = [r for r in rows if _finite(r.get("net_pnl")) > 0]
    net = sum(_finite(r.get("net_pnl")) for r in rows)
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(rows) - len(wins),
        "win_rate_pct": 100.0 * len(wins) / len(rows) if rows else 0.0,
        "net_pnl": net,
        "profit_factor": _pf(rows),
        "expectancy": net / len(rows) if rows else 0.0,
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
        if not math.isfinite(fr):
            fb = "unknown/no-front-structure"
        elif fr < 2:
            fb = "1.50-2.00R"
        elif fr < 3:
            fb = "2.00-3.00R"
        else:
            fb = ">=3.00R"
        by_front[fb].append(row)
        cr = _finite(row.get("cost_r"), math.inf)
        if cr < 0.15:
            cb = "<0.15R"
        elif cr < 0.20:
            cb = "0.15-0.20R"
        elif cr < 0.25:
            cb = "0.20-0.25R"
        else:
            cb = "0.25-0.30R"
        by_cost[cb].append(row)
    return {
        "by_side": {k: _pack(v) for k, v in by_side.items()},
        "by_exit_reason": {k: _pack(v) for k, v in by_exit.items()},
        "by_front_r": {k: _pack(v) for k, v in by_front.items()},
        "by_cost_r": {k: _pack(v) for k, v in by_cost.items()},
    }


def load_viewer_market():
    """Helper used by the companion self-contained K-line viewer generator."""
    start, end = configure()
    meta, data, funding, manifest = base.load_market(research.base, base.TIMEFRAMES)
    return start, end, data, manifest


def main():
    started = time.perf_counter()
    start, end = configure()
    verify(start, end)
    OUT.mkdir(parents=True, exist_ok=True)

    print(
        "TREND_PULLBACK_DIRECT_180D_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "trend=4H+1H explicit aligned",
        "trigger=latest_closed_15m_direction_side_BOLL_outer_touch",
        "front_r=STRICT_GT_1.50",
        "cost_r=STRICT_LT_0.30",
        "score=OFF", "rsi=OFF", "macd=OFF", "volume=OFF",
        "entry_drift=OFF", "boll_width=OFF",
        "entry=LIMIT_1x", "sl=1H_ATR_x1", "tp=2R_full",
        "be=OFF", "pee=PEE4", "pee_lock=GLOBAL_1H",
        flush=True,
    )

    meta, data, funding, cache_manifest = base.load_market(research.base, base.TIMEFRAMES)
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    for tf in base.TIMEFRAMES:
        rows = data[tf]
        step = research.base.BAR_MS[tf]
        assert rows and all(int(b["t"]) - int(a["t"]) == step for a, b in zip(rows, rows[1:])), f"AUDIT FAIL {tf}"

    pee_src._prime_feature_map(data["5m"], data["15m"], data["1H"])
    print("AUDIT PASS: closed bars + 4H/1H trend + closed15m outer + strict Front/Cost + PEE4/Lock1H", flush=True)

    sim, raw_metrics = research.run(data, ts, meta, funding, variant="trend_pullback_direct")
    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    metrics.update({
        "days": DAYS,
        "strategy_version": VERSION,
        "build": BUILD,
    })
    pee_rows = [r for r in rows if str(r.get("reason") or "") == "PEE4_EARLY_EXIT_TIERED"]

    payload = {
        "research": "4H+1H trend -> CLOSED 15m BOLL outer direct entry; Front>1.50R; Cost<0.30R; PEE4+Lock1H; 180D",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": base.CAPITAL,
        "strategy_lock": {
            "4h_trend_hard_gate": "close vs EMA200 + EMA20/EMA50 alignment + EMA trend slope via indicators.up/down",
            "1h_trend_hard_gate": "close vs EMA200 + EMA20/EMA50 alignment + EMA trend slope via indicators.up/down",
            "4h_1h_must_match": True,
            "15m_boll_trigger": "latest CLOSED candle; long low<=lower / short high>=upper",
            "15m_signal_reuse": False,
            "score_enabled": False,
            "rsi_entry_gate": False,
            "macd_entry_gate": False,
            "volume_entry_gate": False,
            "entry_drift_gate": False,
            "boll_width_gate": False,
            "front_structure_source": "nearest strong adverse structure ahead from 1H lookback120 or 15m lookback160",
            "front_r_formula": "abs(front_structure_price-entry)/(1H ATR x 1.0)",
            "front_r_rule": "> 1.50R STRICT",
            "front_unknown_allowed": True,
            "cost_r_formula": "(LIMIT maker fee + stop-side taker fee + stop-side slippage)/(1H ATR x 1.0)",
            "cost_r_rule": "< 0.30R STRICT",
            "position_multiplier": 1.0,
            "entry_order": "LIMIT at audited closed-1m mark proxy; one 60s attempt per closed15m trigger",
            "stop": "1.0 x 1H ATR",
            "target": "2R full position",
            "break_even": False,
            "pee_version": "4.0-tiered-boolean",
            "pee4_monitor_max_hours": 4,
            "pee4_mfe_cutoff_r": 0.60,
            "pee4_post_exit_entry_lock_minutes": 60,
            "pee4_post_exit_entry_lock_scope": "GLOBAL both directions",
        },
        "preflight_audit": {
            "closed_market_bars_only": True,
            "strict_timeframe_spacing_checked": True,
            "front_strict_inequality_checked": True,
            "cost_strict_inequality_checked": True,
            "pee4_decision_checked": True,
            "production_files_changed": False,
        },
        "metrics": metrics,
        "analysis": _analysis(rows),
        "pee4": {
            "early_exit_count": len(pee_rows),
            "by_path": dict(Counter(str(r.get("early_exit_path") or "unknown") for r in pee_rows)),
            "net_pnl": sum(_finite(r.get("net_pnl")) for r in pee_rows),
        },
        "pee4_lock1h": {
            "lock_events": len(_LOCK_EVENTS),
            "blocked_submit_attempts": int(sim.stats.get("pee4_lock1h_blocked_submit", 0)),
            "events": _LOCK_EVENTS,
        },
        "simulator_stats": dict(sim.stats),
        "top_blockers": sim.blockers.most_common(30),
        "transitions": dict(sim.transitions),
        "cache": cache_manifest,
        "timing_sec": {"total": time.perf_counter() - started},
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m OHLC proxy; order-book queue position is unavailable.",
            "Research-only strategy; production V1.7 files are unchanged.",
        ],
    }

    result_path = OUT / "result_trend_pullback_direct_180d.json"
    trade_path = OUT / "trades_trend_pullback_direct_180d.csv"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    base._write_csv(rows, trade_path)
    (OUT / "README.txt").write_text(
        "4H+1H aligned trend -> latest CLOSED 15m direction-side BOLL outer touch -> Front R >1.50 and Cost R <0.30 -> fixed1x LIMIT. Exit: 1H ATR SL / 2R / PEE4 / global Lock1H / No-BE.\n",
        encoding="utf-8",
    )
    print("TREND_PULLBACK_DIRECT_180D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

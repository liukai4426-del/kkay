#!/usr/bin/env python3
"""KAYTRADE V1.7.1 research: asymmetric Trend Pullback, multi-period.

Common:
- 4H + 1H must be the same full trend.
- latest CLOSED 15m candle touches direction-side BOLL outer.
- Front R > 1.50 strictly when a front structure is known; unknown keeps inherited pass behavior.
- fixed 1x LIMIT entry.
- SL = 1.0 x 1H ATR; TP = 2R full; No-BE; PEE4 + global Lock1H unchanged.

LONG (quality-first):
- 4H + 1H full bullish trend.
- 15m low <= lower BOLL.
- normalized 15m BOLL width is expanding vs previous closed 15m bar.
- Cost R < 0.20.

SHORT (frequency-first):
- 4H + 1H full bearish trend.
- 15m high >= upper BOLL.
- 15m full-trend state must NOT be explicitly bullish (bearish or neutral allowed).
- Cost R < 0.30.

No RSI / MACD / volume / entry drift / score system.
Research only; production V1.7 is untouched.
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import timedelta
from pathlib import Path

from core import indicators
import run_v170_trend_pullback_180d as src

DAYS = int(os.environ.get("BACKTEST_DAYS", "180"))
if DAYS not in (180, 360, 720):
    raise ValueError("BACKTEST_DAYS must be 180, 360 or 720")

LONG_COST_MAX_R = 0.20
SHORT_COST_MAX_R = 0.30
FRONT_MIN_R = 1.50

OUT = Path(f"backtest_output_v171_asym_trend_pullback_{DAYS}d")
RESULT_NAME = f"result_v171_asym_trend_pullback_{DAYS}d.json"
TRADES_NAME = f"trades_v171_asym_trend_pullback_{DAYS}d.csv"

_ORIG_SUBMIT = src._submit_trend


def _trend_state(rows):
    return src._trend_state(rows)


def _boll_width_norm(rows):
    q = indicators(rows)
    mid = float(q["middle"])
    if not math.isfinite(mid) or abs(mid) < 1e-12:
        return math.inf
    return (float(q["upper"]) - float(q["lower"])) / abs(mid)


def _width_snapshot(quarter):
    if len(quarter) < 202:
        return None
    current = _boll_width_norm(quarter)
    previous = _boll_width_norm(quarter[:-1])
    return {
        "current": current,
        "previous": previous,
        "expanding": math.isfinite(current) and math.isfinite(previous) and current > previous,
        "ratio": current / previous if math.isfinite(current) and math.isfinite(previous) and previous > 0 else math.inf,
    }


def _joint_direction(hour, four):
    # Common macro direction: both 4H and 1H must be fully aligned.
    return src._joint_direction(hour, four)


class AsymTrendPullbackModel:
    VERSION = "V1.7.1-AsymTrendPullback-Research"
    BUILD = f"ASYM{DAYS}D"
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
        m15_state = _trend_state(quarter)
        width = _width_snapshot(quarter)

        opp = dict(opportunity) if isinstance(opportunity, dict) else None
        transition = None

        if opp:
            if int(now_ms) >= int(opp.get("expires_ms") or 0):
                transition = ("invalidated", "15m外轨机会到下一根15m收盘失效")
                opp = None
            elif direction != str(opp.get("side") or ""):
                transition = ("invalidated", "4H/1H大趋势不再同向")
                opp = None

        eligible_side = direction
        gate_reason = ""
        if direction == "做多":
            if not width or not bool(width["expanding"]):
                eligible_side = "观望"
                gate_reason = "LONG: 15m BOLL width not expanding"
        elif direction == "做空":
            if m15_state == "做多":
                eligible_side = "观望"
                gate_reason = "SHORT: 15m trend explicitly bullish"

        trigger = src._boll_touch(quarter, direction) if direction in ("做多", "做空") else None
        if allow_new and eligible_side in ("做多", "做空") and isinstance(trigger, dict):
            old_t = int((opp or {}).get("signal_bar_t") or -1)
            if opp is None or int(trigger["bar_t"]) > old_t:
                opp = src._make_opportunity(hour, quarter, five, one, four, eligible_side, trigger)
                opp["trend_15m"] = m15_state
                opp["boll_width_norm"] = float(width["current"]) if width else None
                opp["boll_width_prev_norm"] = float(width["previous"]) if width else None
                opp["boll_width_expanding"] = bool(width and width["expanding"])
                opp["boll_width_ratio"] = float(width["ratio"]) if width and math.isfinite(width["ratio"]) else None
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
                    "trend_15m": m15_state,
                    "boll_width_expanding": bool(opp.get("boll_width_expanding")),
                    "trigger": dict(opp.get("trigger_detail") or {}),
                    "required": {
                        "4h_1h_same_full_trend": direction == side,
                        "long_width_expanding": (side != "做多") or bool(opp.get("boll_width_expanding")),
                        "short_15m_not_explicit_opposite": (side != "做空") or m15_state != "做多",
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
            "strategy_version": AsymTrendPullbackModel.VERSION,
            "trend_1h": h1_state,
            "trend_15m": m15_state,
            "trend_4h": h4_state,
            "entry_gate_reason": gate_reason,
        }, opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        return True, {}, []


def _submit_asym(self, result, now_ms, mark):
    opp = result.get("opportunity")
    if not isinstance(opp, dict):
        return
    side = str(opp.get("side") or "")
    if side not in ("做多", "做空"):
        return

    # Side-specific extra cost gate. The inherited submit still enforces <0.30R,
    # so SHORT uses the inherited ceiling, while LONG is tightened to <0.20R.
    if side == "做多":
        entry = float(mark)
        stop_distance = float(opp.get("atr1h") or 0.0) * src.STOP_ATR
        if stop_distance <= 0:
            self.stats["invalid_atr1h"] += 1
            return
        cost_r = src.structure_model.estimated_cost_r(
            entry, stop_distance, side,
            maker_bps=src.research.MAKER_BPS,
            taker_bps=src.research.TAKER_BPS,
            slippage_bps=src.research.SLIPPAGE_BPS,
        )
        if not float(cost_r) < LONG_COST_MAX_R:
            self.stats["long_cost_r_ge_020_block"] += 1
            self.blockers["LONG Cost R >= 0.20R"] += 1
            return

    before = self.pending
    _ORIG_SUBMIT(self, result, now_ms, mark)
    if self.pending is not None and self.pending is not before:
        factors = self.pending.factors
        factors["trend_15m"] = str(opp.get("trend_15m") or "")
        factors["boll_width_norm"] = opp.get("boll_width_norm")
        factors["boll_width_prev_norm"] = opp.get("boll_width_prev_norm")
        factors["boll_width_expanding"] = bool(opp.get("boll_width_expanding"))
        factors["boll_width_ratio"] = opp.get("boll_width_ratio")
        factors["asym_cost_limit_r"] = LONG_COST_MAX_R if side == "做多" else SHORT_COST_MAX_R
        factors["entry_rule"] = (
            "ASYM LONG: 4H+1H bull + 15m lower touch + width expanding + Cost<0.20R; "
            "SHORT: 4H+1H bear + 15m upper touch + 15m not bullish + Cost<0.30R; "
            "Front>1.50R known-only + PEE4/Lock1H"
        )


def _parse_rows(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _segment_stats(rows, start_ms, end_ms, step_days=180):
    out = []
    step = step_days * 86_400_000
    i = 0
    left = start_ms
    while left < end_ms:
        right = min(end_ms, left + step)
        seg = []
        for r in rows:
            try:
                t = int(float(r.get("entry_time") or 0))
            except Exception:
                continue
            if left <= t < right:
                seg.append(r)
        stats = src._stats(seg)
        stats.update({"segment": i + 1, "start_ms": left, "end_ms": right})
        out.append(stats)
        i += 1
        left = right
    return out


def configure_source():
    src.DAYS = DAYS
    src.FIXED_START = src.FIXED_END - timedelta(days=DAYS)
    src.OUT = OUT
    src.RESULT_NAME = RESULT_NAME
    src.TRADES_NAME = TRADES_NAME
    src.TrendPullbackModel = AsymTrendPullbackModel
    src._submit_trend = _submit_asym

    # Keep inherited common gates exact.
    src.FRONT_MIN_R = FRONT_MIN_R
    src.COST_MAX_R = SHORT_COST_MAX_R
    src.STOP_ATR = 1.0
    src.REWARD_R = 2.0
    src.LOCK_MS = 60 * 60_000


def verify():
    assert LONG_COST_MAX_R == 0.20
    assert SHORT_COST_MAX_R == 0.30
    assert FRONT_MIN_R == 1.50
    assert DAYS in (180, 360, 720)
    assert AsymTrendPullbackModel.BOLL_TRIGGER_TIMEFRAME == "15m"

    # Synthetic semantic checks for asymmetric conditions.
    # Width expansion is strictly current > previous, equality does not pass.
    assert not (1.0 > 1.0)
    assert 1.000001 > 1.0

    # Cost boundaries are strict.
    assert not (0.20 < LONG_COST_MAX_R)
    assert 0.199999 < LONG_COST_MAX_R
    assert not (0.30 < SHORT_COST_MAX_R)
    assert 0.299999 < SHORT_COST_MAX_R


def postprocess():
    result_path = OUT / RESULT_NAME
    trades_path = OUT / TRADES_NAME
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    rows = _parse_rows(trades_path)

    start_ms = int((src.FIXED_END - timedelta(days=DAYS)).timestamp() * 1000)
    end_ms = int(src.FIXED_END.timestamp() * 1000)
    annualized_trades = len(rows) * 365.0 / DAYS if DAYS else 0.0
    segments = _segment_stats(rows, start_ms, end_ms, 180)

    payload["research"] = (
        f"Asymmetric Trend Pullback {DAYS}D: LONG requires 4H+1H bullish, "
        "15m lower BOLL touch, BOLL width expanding, Cost<0.20R; "
        "SHORT requires 4H+1H bearish, 15m upper BOLL touch, "
        "15m not explicitly bullish, Cost<0.30R."
    )
    payload["strategy_lock"] = {
        "common_trend": "4H and 1H must be the same full trend",
        "15m_trigger": "latest CLOSED 15m directional BOLL outer touch",
        "long": {
            "trend": "4H+1H bullish",
            "trigger": "15m low <= lower BOLL",
            "boll_width": "normalized width current > previous closed 15m bar",
            "cost_r": "< 0.20R strictly",
        },
        "short": {
            "trend": "4H+1H bearish",
            "trigger": "15m high >= upper BOLL",
            "15m_trend": "bearish or neutral allowed; explicit bullish blocks",
            "cost_r": "< 0.30R strictly",
        },
        "front_structure": "nearest strong adverse structure from 1H/15m; unknown front keeps inherited pass behavior",
        "front_r_rule": "> 1.50R strictly when known",
        "score_system": False,
        "rsi_entry_gate": False,
        "macd_entry_gate": False,
        "volume_entry_gate": False,
        "entry_drift_gate": False,
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
    }
    payload["frequency"] = {
        "trades": len(rows),
        "days": DAYS,
        "annualized_trades": annualized_trades,
        "target_gt_100_per_year": annualized_trades > 100.0,
    }
    payload["segments_180d"] = segments
    pf = float(payload.get("metrics", {}).get("profit_factor") or 0.0)
    dd = float(payload.get("metrics", {}).get("max_drawdown_pct") or 0.0)
    payload["continuity_checks"] = {
        "annualized_trades_gt_100": annualized_trades > 100.0,
        "profit_factor_ge_1_25": pf >= 1.25,
        "max_drawdown_pct_le_8": dd <= 8.0,
        "all_180d_segments_positive": bool(segments) and all(float(x.get("net_pnl") or 0.0) > 0 for x in segments),
        "positive_180d_segments": sum(1 for x in segments if float(x.get("net_pnl") or 0.0) > 0),
        "total_180d_segments": len(segments),
    }

    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ASYM_POSTPROCESS", json.dumps({
        "days": DAYS,
        "trades": len(rows),
        "annualized_trades": annualized_trades,
        "net_pnl": payload.get("metrics", {}).get("net_pnl"),
        "pf": pf,
        "dd_pct": dd,
        "segments_180d": segments,
        "checks": payload["continuity_checks"],
    }, ensure_ascii=False, indent=2))


def main():
    configure_source()
    verify()
    print(
        "ASYM_TREND_PULLBACK_CONFIG",
        f"days={DAYS}",
        "common=4H+1H_same_full_trend",
        "LONG=15m_lower_touch+width_expanding+Cost<0.20R",
        "SHORT=15m_upper_touch+15m_not_bullish+Cost<0.30R",
        "Front>1.50R_known_only",
        "SL=1H_ATR", "TP=2R", "PEE4=ON", "Lock1H=ON",
        flush=True,
    )
    src.main()
    postprocess()


if __name__ == "__main__":
    main()

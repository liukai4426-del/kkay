"""Research-only KAYTRADE regime-layer overlay for the 360D validation run.

Keeps V1.6.0 BOLL paths, hard execution checks and position sizing semantics,
while applying the user's revised market-environment and scoring rules:
- 4H aligned is mandatory and scores +2; any non-aligned 4H state blocks entry.
- 4H BOLL location scores +2: long middle->upper, short middle->lower.
- Replace 15m directional pullback +2 with 1H directional pullback +1.
- Remove 5m MACD improving from the score.
- Middle path still requires MACD improvement, but only across the latest two
  closed 5m histogram values to reduce lag.
"""
from __future__ import annotations

import math

from core import indicators
import v152_model as legacy
import v154_model as base

VERSION = "1.6.1-research-regime"
THRESHOLD = 6.0
ENTRY_WINDOW_MS = base.ENTRY_WINDOW_MS
TIME_WINDOW_ENABLED = base.TIME_WINDOW_ENABLED
MIDDLE_TOUCH_ATR = base.MIDDLE_TOUCH_ATR
SIGNAL_DRIFT_ATR = base.SIGNAL_DRIFT_ATR
LONG_MIDDLE_RSI_MIN = base.LONG_MIDDLE_RSI_MIN
SHORT_MIDDLE_RSI_MAX = base.SHORT_MIDDLE_RSI_MAX
FRONT_MIN_R = base.FRONT_MIN_R
COST_MAX_R = base.COST_MAX_R
STOP_BUFFER_ATR = base.STOP_BUFFER_ATR
OVERLAP_ATR_TOL = base.OVERLAP_ATR_TOL

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
boll_entry_signal = base.boll_entry_signal
_entry_ok = base._entry_ok
_window_state = base._window_state
new_opportunity = base.new_opportunity

OUTER_PATHS = {"lower_band", "upper_band"}
MIDDLE_PATH = "middle_rsi"
SCORE_MAX = 11.0


def _path_from(row, result=None, opportunity=None):
    for source in (
        row.get("opportunity") if isinstance(row, dict) else None,
        (result or {}).get("opportunity") if isinstance(result, dict) else None,
        opportunity,
    ):
        if isinstance(source, dict):
            path = str(source.get("signal_path") or source.get("path") or "")
            if path:
                return path
    return ""


def _macd_improving_2bar(five, side):
    hist = legacy._macd_hist(five)
    if len(hist) < 2:
        return False, []
    prev, cur = float(hist[-2]), float(hist[-1])
    ok = cur > prev if side == "做多" else cur < prev
    return bool(ok), [prev, cur]


def _macd_improving(row):
    """Compatibility hook used by the shared V1.6.0 backtest submitter."""
    try:
        return bool(((row or {}).get("confirmations") or {}).get("middle_macd_improving_2bar"))
    except Exception:
        return False


def _four_hour_boll_ok(four, side):
    q = indicators(four)
    price = float(four[-1]["c"])
    middle = float(q["middle"])
    upper = float(q["upper"])
    lower = float(q["lower"])
    if side == "做多":
        ok = middle <= price <= upper
    else:
        ok = lower <= price <= middle
    return bool(ok), {
        "price": price,
        "middle": middle,
        "upper": upper,
        "lower": lower,
    }


def _one_hour_pullback_ok(hour, five, side):
    """Mirror the old 15m directional-pullback definition on the 1H EMA20/50 zone."""
    if len(five) < 2:
        return False, {}
    h = indicators(hour)
    atr = float(h["atr"])
    core_low = min(float(h["ema20"]), float(h["ema50"]))
    core_high = max(float(h["ema20"]), float(h["ema50"]))
    zone_low = core_low - 0.25 * atr
    zone_high = core_high + 0.25 * atr
    prev, cur = five[-2], five[-1]
    if side == "做多":
        ok = float(prev["c"]) > zone_high and float(cur["l"]) <= zone_high
    else:
        ok = float(prev["c"]) < zone_low and float(cur["h"]) >= zone_low
    return bool(ok), {
        "zone_low": zone_low,
        "zone_high": zone_high,
        "core_low": core_low,
        "core_high": core_high,
        "atr1h": atr,
    }


def _dedupe(values):
    out = []
    for value in values:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _apply_research_rules(result, opp, hour, quarter, five, one, four):
    if not isinstance(result, dict):
        return result
    result["strategy_version"] = VERSION
    result["score_max"] = SCORE_MAX
    if not isinstance(opp, dict):
        return result

    side = str(opp.get("side") or "")
    row = ((result.get("scores") or {}).get(side) or {})
    if not isinstance(row, dict):
        return result

    path = str(opp.get("signal_path") or "")
    old_layers = dict(row.get("layers") or {})
    confirmations = row.setdefault("confirmations", {})
    required = confirmations.setdefault("required", {})
    blockers = list(confirmations.get("blockers") or [])

    qstate = four_hour_state(four, side)
    four_aligned = qstate == "aligned"
    four_boll_ok, four_boll = _four_hour_boll_ok(four, side)
    hour_pullback_ok, hour_zone = _one_hour_pullback_ok(hour, five, side)
    middle_macd_ok, middle_macd_hist = _macd_improving_2bar(five, side)

    # Revised scoring table. Hard-gate variables are scored only where the user
    # explicitly requested that behaviour (4H aligned +2). MACD is not scored.
    components = {
        "trend4h_aligned": 2.0 if four_aligned else 0.0,
        "boll4h_direction_zone": 2.0 if four_boll_ok else 0.0,
        "direction_pullback_1h": 1.0 if hour_pullback_ok else 0.0,
        "entry_near_zone_15m": float(old_layers.get("entry_near_zone") or 0.0),
        "structure_overlap_15m": float(old_layers.get("structure_overlap") or 0.0),
        "boll_entry_5m": 2.0,
        "ema50_15m_move": float(old_layers.get("ema50_15m_move") or 0.0),
        "volume5": float(old_layers.get("volume5") or 0.0),
        "kdj5_signal": float(old_layers.get("kdj5_signal") or 0.0),
    }
    raw = sum(components.values())
    total = max(0.0, min(SCORE_MAX, round(raw * 2.0) / 2.0))

    # Market-environment hard lock: only a fully aligned 4H trend may trade.
    if not four_aligned:
        blockers.append("4H非同向：市场环境趋势层禁止开仓")

    # Middle-path hard lock: latest two CLOSED 5m MACD histogram values must improve.
    if path == MIDDLE_PATH and not middle_macd_ok:
        blockers.append("中轨开仓禁止：最近2根5m MACD Histogram未朝交易方向改善")

    blockers = _dedupe(blockers)
    required["boll_entry_signal"] = bool(required.get("boll_entry_signal", True))
    required["market_4h_aligned"] = four_aligned
    if path == MIDDLE_PATH:
        required["middle_macd_improving_2bar"] = middle_macd_ok
    else:
        required.pop("middle_macd_improving", None)
        required.pop("middle_macd_improving_2bar", None)

    gate = all(bool(v) for v in required.values()) and not blockers
    eligible = gate and total >= THRESHOLD
    multiplier = 2.0 if path in OUTER_PATHS else 1.0

    row["raw"] = raw
    row["total"] = total
    row["required"] = THRESHOLD
    row["gate"] = gate
    row["eligible"] = eligible
    row["signal_tier"] = 1 if total >= THRESHOLD else 0
    row["position_multiplier"] = multiplier if eligible else 0.0
    row["boll_position_multiplier"] = multiplier
    row["layers"] = components
    row["items"] = [
        ("4H同向【市场环境】", components["trend4h_aligned"], 2.0),
        ("4H BOLL方向区间", components["boll4h_direction_zone"], 2.0),
        ("1H正确方向回调区共振", components["direction_pullback_1h"], 1.0),
        ("计划入场价接近15m回调区", components["entry_near_zone_15m"], 1.0),
        ("水平结构与15m回调区重合", components["structure_overlap_15m"], 1.0),
        ("5m BOLL中轨/外轨触发【开仓必要】", components["boll_entry_5m"], 2.0),
        ("15m EMA50向交易方向移动", components["ema50_15m_move"], 1.0),
        ("5m成交量>=20均量1.2x", components["volume5"], 0.5),
        ("5m KDJ对应交叉", components["kdj5_signal"], 0.5),
    ]

    confirmations["required"] = required
    confirmations["blockers"] = blockers
    confirmations["4H_trend_state"] = qstate
    confirmations["4H_boll_direction_zone"] = four_boll_ok
    confirmations["4H_boll"] = four_boll
    confirmations["1H_direction_pullback"] = hour_pullback_ok
    confirmations["1H_pullback_zone"] = hour_zone
    confirmations["middle_macd_required"] = path == MIDDLE_PATH
    confirmations["middle_macd_improving_2bar"] = middle_macd_ok if path == MIDDLE_PATH else None
    confirmations["middle_macd_hist_2bar"] = middle_macd_hist if path == MIDDLE_PATH else None
    confirmations["middle_macd_rule"] = "latest_two_closed_5m_histogram_improve"

    if eligible:
        row["level"] = (
            "开仓信号 · 外轨2×基础仓位" if path in OUTER_PATHS
            else "开仓信号 · 中轨1×基础仓位"
        )
        row["reason"] = f"研究版允许开仓｜评分 {total:g}/11｜4H同向通过"
        result["side"] = side
        result["status"] = "允许开仓"
        result["why"] = row["reason"]
    else:
        row["level"] = "禁止开仓" if blockers else "未达开仓线"
        if blockers:
            row["reason"] = "禁止：" + "；".join(blockers)
        else:
            row["reason"] = f"评分 {total:g}/11，未达 {THRESHOLD:g} 分开仓线"
        result["side"] = "观望"
        result["why"] = row["reason"]

    return result


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    result, opp, transition = base.evaluate(
        hour, quarter, five, one, four,
        opportunity=opportunity,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    return _apply_research_rules(result, opp, hour, quarter, five, one, four), opp, transition


def execution_checks(plan, opportunity, score):
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    blockers = list(blockers or [])
    confirmations = ((score or {}).get("confirmations") or {})
    path = _path_from(score if isinstance(score, dict) else {}, opportunity=opportunity)

    if confirmations.get("4H_trend_state") != "aligned":
        blockers.append("4H非同向：市场环境趋势层禁止开仓")
    if path == MIDDLE_PATH and not bool(confirmations.get("middle_macd_improving_2bar")):
        blockers.append("中轨开仓禁止：最近2根5m MACD Histogram未朝交易方向改善")

    blockers = _dedupe(blockers)
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "boll_signal_path": path,
        "4H_trend_state": confirmations.get("4H_trend_state"),
        "4H_boll_direction_zone": confirmations.get("4H_boll_direction_zone"),
        "middle_macd_required": path == MIDDLE_PATH,
        "middle_macd_improving_2bar": confirmations.get("middle_macd_improving_2bar"),
        "position_multiplier": 2.0 if path in OUTER_PATHS else 1.0,
    })
    return not blockers, diag, blockers

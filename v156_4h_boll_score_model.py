"""Research overlay for KAYTRADE V1.5.6 scoring experiment.

Changes versus the live V1.5.6/Build1544 model:
- 15m correct-direction pullback-zone resonance: +1 instead of +2.
- 4H BOLL location is a mandatory entry prerequisite AND contributes +1:
  long  -> latest closed 4H close in [middle, upper]
  short -> latest closed 4H close in [lower, middle]
- all other score factors, hard blockers and execution mechanics are preserved.
"""
from __future__ import annotations

from copy import deepcopy

from core import indicators
import v154_model as base

VERSION = "1.5.6-research-4h-boll-score"
THRESHOLD = base.THRESHOLD

# Save the production functions before the backtest runner monkey-patches the
# shared v154_model module to route decisions through this research overlay.
_BASE_EVALUATE = base.evaluate
_BASE_EXECUTION_CHECKS = base.execution_checks


def _four_hour_boll_aligned(four, side):
    q = indicators(four)
    close = float(four[-1]["c"])
    middle = float(q["middle"])
    upper = float(q["upper"])
    lower = float(q["lower"])
    if side == "做多":
        return middle <= close <= upper
    if side == "做空":
        return lower <= close <= middle
    return False


def _rewrite_score_row(row, four, side):
    if not isinstance(row, dict) or not row.get("opportunity"):
        return row

    aligned = _four_hour_boll_aligned(four, side)
    items = list(row.get("items") or [])
    rewritten = []
    delta = 0.0
    has_4h_boll = False

    for item in items:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            rewritten.append(item)
            continue
        label, score, maximum = item[0], float(item[1]), float(item[2])
        text = str(label)
        if "15m正确方向回调区共振" in text:
            new_score = 1.0 if score > 0 else 0.0
            delta += new_score - score
            rewritten.append((label, new_score, 1.0))
        elif "4H BOLL" in text:
            has_4h_boll = True
            new_score = 1.0 if aligned else 0.0
            delta += new_score - score
            rewritten.append(("4H BOLL中上轨/下中轨区域【开仓必要】", new_score, 1.0))
        else:
            rewritten.append(item)

    if not has_4h_boll:
        boll_score = 1.0 if aligned else 0.0
        rewritten.append(("4H BOLL中上轨/下中轨区域【开仓必要】", boll_score, 1.0))
        delta += boll_score

    old_raw = float(row.get("raw", row.get("total", 0.0)) or 0.0)
    new_raw = old_raw + delta
    new_total = max(0.0, min(10.0, round(new_raw * 2.0) / 2.0))
    row["raw"] = new_raw
    row["total"] = new_total
    row["items"] = rewritten

    # Keep score-component diagnostics consistent with the score shown/used.
    layers = dict(row.get("layers") or {})
    layers["direction_pullback"] = 1.0 if float(layers.get("direction_pullback") or 0.0) > 0 else 0.0
    layers["four_hour_boll"] = 1.0 if aligned else 0.0
    row["layers"] = layers

    # 4H BOLL is a true prerequisite, not merely an auxiliary score item.
    confirmations = row.setdefault("confirmations", {})
    required = confirmations.setdefault("required", {})
    required["four_hour_boll_region"] = bool(aligned)
    blockers = list(confirmations.get("blockers") or [])
    required_ok = all(bool(v) for v in required.values()) if required else True
    gate = bool(required_ok and not blockers)
    eligible = bool(gate and new_total >= THRESHOLD)
    row["gate"] = gate
    row["eligible"] = eligible
    row["signal_tier"] = 1 if new_total >= THRESHOLD else 0
    row["position_multiplier"] = 1.0 if eligible else 0.0

    if not aligned:
        row["level"] = "等待4H BOLL区域"
        row["reason"] = (
            f"4H BOLL开仓必要条件未通过｜评分 {new_total:.1f}/{THRESHOLD:.1f}｜"
            + ("做多需4H收盘位于中轨至上轨" if side == "做多" else "做空需4H收盘位于下轨至中轨")
        )
    elif blockers:
        row["level"] = "限制条件未通过"
        # Preserve production hard-block explanation when present.
    elif new_total < THRESHOLD:
        row["level"] = "评分不足"
        row["reason"] = f"4H BOLL条件通过｜评分 {new_total:.1f}/{THRESHOLD:.1f}，等待辅助评分改善"
    else:
        row["level"] = "开仓信号 · 1×仓位"
        row["reason"] = f"4H BOLL条件通过｜评分 {new_total:.1f}/{THRESHOLD:.1f}｜全部开仓必要项通过"
    return row


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    result, opp, transition = _BASE_EVALUATE(
        hour, quarter, five, one, four,
        opportunity=opportunity,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    result = deepcopy(result)
    for side in ("做多", "做空"):
        row = (result.get("scores") or {}).get(side)
        if isinstance(row, dict):
            _rewrite_score_row(row, four, side)

    # Re-select the side from the rewritten eligibility. This is required because
    # moving one point from the 15m factor to 4H BOLL can both promote and demote
    # opportunities around the 6-point threshold.
    opp_side = str((opp or {}).get("side") or "") if isinstance(opp, dict) else ""
    if opp_side in ("做多", "做空"):
        row = (result.get("scores") or {}).get(opp_side) or {}
        if row.get("eligible"):
            result["side"] = opp_side
            result["status"] = "允许开仓"
            result["why"] = row.get("reason") or "允许开仓"
        else:
            result["side"] = "观望"
            result["status"] = row.get("level") or "等待评分/限制条件"
            result["why"] = row.get("reason") or "评分/限制条件未通过"

    result["strategy_version"] = VERSION
    result["threshold"] = THRESHOLD
    return result, opp, transition


def execution_checks(plan, opportunity, score):
    # Production execution_checks already rejects any false item in
    # score.confirmations.required, so the injected four_hour_boll_region gate is
    # enforced again immediately before LIMIT submission.
    return _BASE_EXECUTION_CHECKS(plan, opportunity, score)


new_opportunity = base.new_opportunity
boll_entry_signal = base.boll_entry_signal
trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
ENTRY_WINDOW_MS = base.ENTRY_WINDOW_MS
TIME_WINDOW_ENABLED = base.TIME_WINDOW_ENABLED
COST_MAX_R = base.COST_MAX_R

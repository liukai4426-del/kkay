"""Research overlay for KAYTRADE V1.5.6 scoring experiment.

Changes versus the live V1.5.6/Build1544 model:
- 15m correct-direction pullback-zone resonance: +1 instead of +2.
- add +1 for 4H BOLL location alignment:
  long  -> latest closed 4H close in [middle, upper]
  short -> latest closed 4H close in [lower, middle]
All live hard blockers and entry mechanics are preserved by delegating to v154_model.
"""
from __future__ import annotations

from copy import deepcopy

from core import indicators
import v154_model as base

VERSION = "1.5.6-research-4h-boll-score"
THRESHOLD = base.THRESHOLD


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

    items = list(row.get("items") or [])
    rewritten = []
    delta = 0.0
    found_pullback = False
    has_4h_boll = False

    for item in items:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            rewritten.append(item)
            continue
        label, score, maximum = item[0], float(item[1]), float(item[2])
        text = str(label)
        if "15m正确方向回调区共振" in text:
            found_pullback = True
            new_score = 1.0 if score > 0 else 0.0
            delta += new_score - score
            rewritten.append((label, new_score, 1.0))
        elif "4H BOLL" in text:
            has_4h_boll = True
            aligned = _four_hour_boll_aligned(four, side)
            new_score = 1.0 if aligned else 0.0
            delta += new_score - score
            rewritten.append((label, new_score, 1.0))
        else:
            rewritten.append(item)

    if not has_4h_boll:
        aligned = _four_hour_boll_aligned(four, side)
        boll_score = 1.0 if aligned else 0.0
        rewritten.append(("4H BOLL中上轨/下中轨区域【辅助评分】", boll_score, 1.0))
        delta += boll_score

    old_raw = float(row.get("raw", row.get("total", 0.0)) or 0.0)
    old_total = float(row.get("total", old_raw) or 0.0)
    new_raw = old_raw + delta
    new_total = max(0.0, min(10.0, new_raw))
    row["raw"] = new_raw
    row["total"] = new_total
    row["items"] = rewritten

    # Preserve all existing mandatory and hard-block logic; only recompute the score threshold gate.
    confirmations = row.get("confirmations") or {}
    blockers = list(confirmations.get("blockers") or [])
    required = confirmations.get("required") or {}
    required_ok = all(bool(v) for v in required.values()) if required else True
    row["gate"] = bool(required_ok and not blockers and new_total >= THRESHOLD)
    row["eligible"] = bool(row.get("gate"))

    # Keep the existing level/reason semantics but ensure a score-only threshold failure is truthful.
    if required_ok and not blockers and new_total < THRESHOLD:
        row["level"] = "评分不足"
        row["reason"] = f"评分 {new_total:.1f}/{THRESHOLD:.1f}，等待辅助评分改善"
    return row


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
    result = deepcopy(result)
    for side in ("做多", "做空"):
        row = (result.get("scores") or {}).get(side)
        if isinstance(row, dict):
            _rewrite_score_row(row, four, side)

    chosen = result.get("side")
    if chosen in ("做多", "做空"):
        row = result["scores"][chosen]
        if not row.get("eligible"):
            result["side"] = "观望"
            result["why"] = row.get("reason") or "评分/限制条件未通过"
    result["strategy_version"] = VERSION
    result["threshold"] = THRESHOLD
    return result, opp, transition


new_opportunity = base.new_opportunity
execution_checks = base.execution_checks
boll_entry_signal = base.boll_entry_signal
trend_direction = base.trend_direction
four_hour_state = base.four_hour_state

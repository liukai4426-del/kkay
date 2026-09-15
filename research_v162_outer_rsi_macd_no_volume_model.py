"""Research A/B: remove the 5m volume +0.5 score from the verified outer-only model.

Everything else is inherited unchanged from research_v162_outer_rsi_macd_model:
- outer BOLL only;
- RSI 30..70 on both sides;
- formal 5m MACD improvement required;
- 4H aligned hard gate and inherited structural/cost gates;
- threshold remains 6.0.

Volume is still retained in the opportunity/diagnostic data, but contributes
zero points and is not a hard gate.
"""
from __future__ import annotations

import re

import research_v162_outer_rsi_macd_model as base

VERSION = "1.6.2-research-outer-rsi30-70-macd-no-volume-score"
THRESHOLD = base.THRESHOLD
ENTRY_WINDOW_MS = base.ENTRY_WINDOW_MS
TIME_WINDOW_ENABLED = base.TIME_WINDOW_ENABLED
SIGNAL_DRIFT_ATR = base.SIGNAL_DRIFT_ATR
FRONT_MIN_R = base.FRONT_MIN_R
COST_MAX_R = base.COST_MAX_R
STOP_BUFFER_ATR = base.STOP_BUFFER_ATR
OVERLAP_ATR_TOL = base.OVERLAP_ATR_TOL
OUTER_PATHS = base.OUTER_PATHS
MIDDLE_PATH = base.MIDDLE_PATH
SCORE_MAX = float(base.SCORE_MAX) - 0.5
RSI_MIN = base.RSI_MIN
RSI_MAX = base.RSI_MAX

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
boll_entry_signal = base.boll_entry_signal
_entry_ok = base._entry_ok
_window_state = base._window_state
new_opportunity = base.new_opportunity
_macd_improving = base._macd_improving
_path_from = base._path_from
_install_signal_patch = base._install_signal_patch
_apply_outer_research_rules = base._apply_outer_research_rules


def _remove_volume_score(row):
    """Set the volume score contribution to zero while retaining diagnostics."""
    if not isinstance(row, dict):
        return row

    layers = row.setdefault("layers", {})
    old_bonus = float(layers.get("volume5") or 0.0)
    confirmations = row.setdefault("confirmations", {})
    confirmations["volume5_score_enabled"] = False
    confirmations["volume5_observed_bonus"] = old_bonus

    # Preserve the observed volume condition in opportunity/diagnostics, but it
    # must no longer contribute to raw/total score.
    layers["volume5"] = 0.0
    raw = float(row.get("raw") or 0.0) - old_bonus
    total = max(0.0, min(SCORE_MAX, round(raw * 2.0) / 2.0))
    row["raw"] = raw
    row["total"] = total
    row["required"] = THRESHOLD
    row["signal_tier"] = 1 if total >= THRESHOLD else 0

    rewritten = []
    for item in row.get("items") or []:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            rewritten.append(item)
            continue
        values = list(item)
        if "成交量" in str(values[0]):
            values[0] = "5m信号K线成交量≥20均量1.2×（仅记录，不计分）"
            values[1] = 0.0
            values[2] = 0.0
        rewritten.append(tuple(values) if isinstance(item, tuple) else values)
    row["items"] = rewritten

    gate = bool(row.get("gate"))
    eligible = gate and total >= THRESHOLD
    row["eligible"] = eligible
    row["position_multiplier"] = 1.0 if eligible else 0.0
    if eligible:
        row["level"] = "研究信号 · 外轨RSI+MACD · Volume不计分 · 1×"
    elif gate and total < THRESHOLD:
        row["level"] = "Volume不计分后未达开仓线"

    reason = str(row.get("reason") or "")
    reason = re.sub(r"评分\s+[0-9.]+/10", f"评分 {total:g}/{SCORE_MAX:g}", reason)
    if gate and total < THRESHOLD and "Volume不计分后未达" not in reason:
        reason += f"｜Volume不计分后未达{THRESHOLD:g}分门槛"
    row["reason"] = reason
    return row


def _reselect(scores):
    qualified = [side for side, row in scores.items()
                 if isinstance(row, dict) and row.get("eligible")]
    if len(qualified) == 1:
        return qualified[0]
    if len(qualified) == 2:
        a, b = qualified
        sa = float(scores[a].get("total") or 0.0)
        sb = float(scores[b].get("total") or 0.0)
        if sa != sb:
            return a if sa > sb else b
    return "观望"


def _rewrite_no_volume(result):
    if not isinstance(result, dict):
        return result
    scores = result.get("scores") or {}
    for row in scores.values():
        _remove_volume_score(row)
    selected = _reselect(scores)
    result["side"] = selected
    result["score_max"] = SCORE_MAX
    result["strategy_version"] = VERSION
    if selected != "观望":
        result["why"] = str(scores[selected].get("reason") or "")
    elif str(result.get("direction") or "") in scores:
        candidate = scores.get(result.get("direction")) or {}
        result["why"] = str(candidate.get("reason") or result.get("why") or "")
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
    return _rewrite_no_volume(result), opp, transition


def execution_checks(plan, opportunity, score):
    # All execution hard gates are unchanged. Score eligibility is already
    # recomputed in evaluate() after removing the volume contribution.
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "volume5_score_enabled": False,
        "score_max": SCORE_MAX,
    })
    return ok, diag, blockers

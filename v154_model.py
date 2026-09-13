"""Shared KAYTRADE V1.5.4 BOLL pullback-entry model.

V1.5.4 keeps the V1.5.3 BOLL signal/scoring/risk model while removing the
post-trigger one-minute time limit.  A valid 5m BOLL opportunity remains
eligible until the strategy invalidates/replaces it (direction/structure/new
5m state), rather than expiring because a wall-clock minute elapsed.

Execution remains LIMIT-entry / market-on-trigger exit in the repaired runtime.
1m remains timing/execution data only; no EMA/KDJ/RSI/MACD 1m confirmation is
reintroduced.
"""
from __future__ import annotations

import v153_model as base

VERSION = "1.5.4"
THRESHOLD = base.THRESHOLD
# 0 means: no wall-clock expiry for the BOLL opportunity.
ENTRY_WINDOW_MS = 0
TIME_WINDOW_ENABLED = False
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


def _window_state(now_ms, opportunity):
    """No time expiry: once the closed 5m signal exists it stays open.

    Before signal_close_ms it is not yet actionable.  At/after signal close it
    remains open until the normal strategy invalidation/replacement path clears
    the opportunity.
    """
    start = int((opportunity or {}).get("signal_close_ms") or 0)
    now = int(now_ms)
    if start <= 0 or now < start:
        return False, 0, max(0, start - now)
    return True, 0, 0


# v153.evaluate resolves these globals at call time.  Patch the shared model so
# direct live calls and research/backtest calls see the same no-expiry rule.
base.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
base._window_state = _window_state


def new_opportunity(hour, quarter, five, one, side, signal):
    base.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    base._window_state = _window_state
    opp = base.new_opportunity(hour, quarter, five, one, side, signal)
    opp["expires_ms"] = 0
    opp["time_window_enabled"] = False
    return opp


def _normalize_opportunity(opportunity):
    if not isinstance(opportunity, dict):
        return opportunity
    opp = dict(opportunity)
    opp["expires_ms"] = 0
    opp["time_window_enabled"] = False
    return opp


def _rewrite_text(value):
    if not isinstance(value, str):
        return value
    out = value.replace("V1.5.3", "V1.5.4")
    replacements = (
        ("BOLL信号首分钟窗口已过期", "BOLL信号持续有效，等待评分/限制条件"),
        ("BOLL信号4分钟窗口已过期", "BOLL信号持续有效，等待评分/限制条件"),
        ("BOLL信号/首分钟窗口", "BOLL信号"),
        ("BOLL信号/4分钟窗口", "BOLL信号"),
        ("BOLL信号执行窗口 · 第1个1m", "BOLL信号持续有效"),
        ("BOLL信号执行窗口 · 第0个1m", "BOLL信号持续有效"),
        ("等待BOLL执行窗口", "等待5m BOLL信号生效"),
        ("首分钟窗口", "持续有效信号"),
        ("4分钟窗口", "持续有效信号"),
        ("仅第1根1m", "1m仅负责执行，不设时间截止"),
        ("4根1m", "1m仅负责执行，不设时间截止"),
        ("｜窗口剩余 00:00｜", "｜无时间限时｜"),
    )
    for old, new in replacements:
        out = out.replace(old, new)
    return out


def _tag_score_items(row):
    """Make scoring-role labels explicit without changing any score values."""
    if not isinstance(row, dict):
        return
    tagged = []
    for item in row.get("items") or []:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            tagged.append(item)
            continue
        label, score, maximum = item[0], item[1], item[2]
        text = str(label)
        # Remove legacy prose markers so the role appears once, consistently.
        text = text.replace("（辅助）", "").replace("（必须）", "")
        if "5m BOLL" in text:
            role = "【开仓必要】"
        else:
            role = "【辅助评分】"
        if role not in text:
            text = f"{text}{role}"
        tagged.append((text, score, maximum))
    row["items"] = tagged


def _rewrite_required(row):
    if not isinstance(row, dict):
        return
    confirmations = row.get("confirmations")
    if not isinstance(confirmations, dict):
        return
    required = confirmations.get("required")
    if isinstance(required, dict):
        required.pop("entry_window_4x1m", None)
        required.pop("entry_window_1x1m", None)
        # BOLL itself remains a hard opening prerequisite.
        if row.get("opportunity") is not None:
            required["boll_entry_signal"] = bool(required.get("boll_entry_signal", True))
    confirmations["status"] = _rewrite_text(confirmations.get("status"))
    confirmations["entry_window_minute"] = 0
    confirmations["time_window_enabled"] = False
    blockers = confirmations.get("blockers")
    if isinstance(blockers, list):
        confirmations["blockers"] = [_rewrite_text(x) for x in blockers]


def _rewrite_result(result):
    if not isinstance(result, dict):
        return result
    result["strategy_version"] = VERSION
    result["status"] = _rewrite_text(result.get("status"))
    result["why"] = _rewrite_text(result.get("why"))
    result["entry_window_minute"] = 0
    result["opportunity_remaining_seconds"] = 0
    result["time_window_enabled"] = False
    for row in (result.get("scores") or {}).values():
        if isinstance(row, dict):
            row["reason"] = _rewrite_text(row.get("reason"))
            _rewrite_required(row)
            _tag_score_items(row)
    opp = result.get("opportunity")
    if isinstance(opp, dict):
        opp["expires_ms"] = 0
        opp["time_window_enabled"] = False
    return result


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    """Evaluate the shared BOLL model with no post-trigger time expiry."""
    base.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    base._window_state = _window_state
    opportunity = _normalize_opportunity(opportunity)
    # Repaired V1.5.4 uses LIMIT entry: preserve Maker entry economics here.
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
    if isinstance(opp, dict):
        opp["expires_ms"] = 0
        opp["time_window_enabled"] = False
    return _rewrite_result(result), opp, transition


def execution_checks(plan, opportunity, score):
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    cleaned = []
    for text in blockers:
        text = _rewrite_text(text)
        text = text.replace("评分必须项未全部通过（BOLL信号/4分钟窗口）", "评分开仓必要项未全部通过（5m BOLL信号）")
        text = text.replace("评分必须项未全部通过（BOLL信号/首分钟窗口）", "评分开仓必要项未全部通过（5m BOLL信号）")
        cleaned.append(text)
    diag = dict(diag or {})
    diag["time_window_enabled"] = False
    return not cleaned, diag, cleaned

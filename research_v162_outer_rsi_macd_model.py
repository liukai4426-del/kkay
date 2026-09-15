"""Research-only overlay for V1.6.2: outer bands only + RSI 30..70 + MACD improvement.

This module does NOT modify the production V1.6.2 release. It is used only by
an isolated 360-day backtest branch.

Research changes versus V1.6.2:
- remove middle_rsi entry path completely;
- long entry signal only on 5m BOLL lower-band touch;
- short entry signal only on 5m BOLL upper-band touch;
- both sides require signal-time 5m RSI in [30, 70];
- both sides require the existing formal 5m macd_improving state at execution;
- keep V1.6.2 4H aligned hard gate and all inherited structure/cost gates;
- keep 1x LIMIT sizing, 1H ATR stop, 2R full TP, BE off in the runner.
"""
from __future__ import annotations

from core import indicators
import v153_model as signal_core
import v162_model as base

VERSION = "1.6.2-research-outer-rsi30-70-macd"
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
SCORE_MAX = base.SCORE_MAX
RSI_MIN = 30.0
RSI_MAX = 70.0

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
_entry_ok = base._entry_ok
_window_state = base._window_state
new_opportunity = base.new_opportunity
_macd_improving = base._macd_improving
_path_from = base._path_from


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def boll_entry_signal(five, side):
    """Research signal: outer band only, both directions require RSI 30..70."""
    if len(five) < 2:
        return None
    cur = indicators(five)
    bar = five[-1]
    upper = float(cur["upper"])
    lower = float(cur["lower"])
    middle = float(cur["middle"])
    rsi = float(cur["rsi"])
    atr5 = float(cur["atr"])

    if not (RSI_MIN <= rsi <= RSI_MAX):
        return None
    if side == "做多":
        if float(bar["l"]) > lower:
            return None
        path, reference = "lower_band", lower
    elif side == "做空":
        if float(bar["h"]) < upper:
            return None
        path, reference = "upper_band", upper
    else:
        return None

    bar_t = int(bar["t"])
    return {
        "path": path,
        "bar_t": bar_t,
        "signal_close_ms": bar_t + 5 * 60 * 1000,
        "reference": float(reference),
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "rsi5": rsi,
        "atr5": atr5,
        "bar_low": float(bar["l"]),
        "bar_high": float(bar["h"]),
    }


def _install_signal_patch():
    # v154/v160/v161/v162 ultimately execute v153.evaluate(), whose module-level
    # boll_entry_signal global is resolved at call time. Patch only in this
    # research process so production source files remain untouched.
    signal_core.boll_entry_signal = boll_entry_signal


def _apply_outer_research_rules(row, path):
    if not isinstance(row, dict):
        return row
    confirmations = row.setdefault("confirmations", {})
    required = confirmations.setdefault("required", {})
    blockers = list(confirmations.get("blockers") or [])

    if path == MIDDLE_PATH:
        required["middle_path_disabled"] = False
        blockers.append("研究规则：中轨路径已取消，仅允许BOLL外轨")
        row["gate"] = False
        row["eligible"] = False
        row["position_multiplier"] = 0.0
        row["level"] = "中轨已关闭"
    elif path in OUTER_PATHS:
        rsi = float(confirmations.get("rsi5") or 0.0)
        rsi_ok = RSI_MIN <= rsi <= RSI_MAX
        macd_ok = _macd_improving(row)
        required["outer_rsi_30_70"] = rsi_ok
        required["outer_macd_improving"] = macd_ok
        confirmations["outer_rsi_rule"] = "30 <= 5m RSI <= 70"
        confirmations["outer_macd_rule"] = "formal existing 5m macd_improving"
        if not rsi_ok:
            blockers.append("研究规则：外轨开仓要求5m RSI处于30-70区间")
        if not macd_ok:
            blockers.append("研究规则：外轨开仓要求5m MACD柱连续向交易方向改善")
        if not (rsi_ok and macd_ok):
            row["gate"] = False
            row["eligible"] = False
            row["position_multiplier"] = 0.0
            row["level"] = "等待外轨RSI/MACD共振"
        elif row.get("eligible") and float(row.get("total") or 0.0) >= THRESHOLD:
            row["position_multiplier"] = 1.0
            row["level"] = "研究信号 · 外轨RSI+MACD · 1×"

    confirmations["blockers"] = _dedupe(blockers)
    return row


def _reselect(scores):
    qualified = [s for s, r in scores.items() if isinstance(r, dict) and r.get("eligible")]
    if len(qualified) == 1:
        return qualified[0]
    if len(qualified) == 2:
        a, b = qualified
        sa = float(scores[a].get("total") or 0.0)
        sb = float(scores[b].get("total") or 0.0)
        if sa != sb:
            return a if sa > sb else b
    return "观望"


def _rewrite_result(result, opportunity=None):
    if not isinstance(result, dict):
        return result
    result["strategy_version"] = VERSION
    result["score_max"] = SCORE_MAX
    scores = result.get("scores") or {}
    original = result.get("side")
    for row in scores.values():
        if not isinstance(row, dict):
            continue
        path = _path_from(row, result=result, opportunity=opportunity)
        _apply_outer_research_rules(row, path)
    selected = original
    if selected not in scores or not isinstance(scores.get(selected), dict) or not scores[selected].get("eligible"):
        selected = _reselect(scores)
    result["side"] = selected
    if selected != "观望":
        result["why"] = str(scores[selected].get("reason") or "")
    elif original != "观望":
        result["why"] = "研究规则过滤：仅外轨，RSI 30-70，且5m MACD必须连续改善"
    return result


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    _install_signal_patch()
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
    return _rewrite_result(result, opp), opp, transition


def execution_checks(plan, opportunity, score):
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    blockers = list(blockers or [])
    path = _path_from(score if isinstance(score, dict) else {}, opportunity=opportunity)
    confirmations = (score or {}).get("confirmations") or {}
    rsi = float(confirmations.get("rsi5") or 0.0)
    if path == MIDDLE_PATH:
        blockers.append("研究规则：中轨路径已取消")
    elif path in OUTER_PATHS:
        if not (RSI_MIN <= rsi <= RSI_MAX):
            blockers.append("研究规则：外轨开仓要求5m RSI处于30-70区间")
        if not _macd_improving(score or {}):
            blockers.append("研究规则：外轨开仓要求5m MACD柱连续向交易方向改善")
    else:
        blockers.append("研究规则：仅允许BOLL上下外轨路径")
    blockers = _dedupe(blockers)
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "boll_signal_path": path,
        "outer_only": True,
        "rsi_min": RSI_MIN,
        "rsi_max": RSI_MAX,
        "rsi5": rsi,
        "outer_macd_required": path in OUTER_PATHS,
    })
    return not blockers, diag, blockers

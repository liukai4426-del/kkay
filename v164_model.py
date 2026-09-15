"""KAYTRADE V1.6.4 strategy overlay.

Changes from V1.6.3:
- remove 5m signal-volume +0.5 from scoring;
- add the transferred +0.5 to the 5m BOLL outer-band trigger;
- turn >=1.20x prior-20 5m average volume into a hard opening gate;
- keep outer-only, RSI 30-70, formal 5m MACD improvement and 4H alignment.
"""
from __future__ import annotations

import math
import re

import v163_model as base

VERSION = "1.6.4"
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
RSI_MIN = base.RSI_MIN
RSI_MAX = base.RSI_MAX

VOLUME_HARD_GATE = 1.20
BOLL_OUTER_BONUS = 0.50
BOLL_OUTER_SCORE = 2.50

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
boll_entry_signal = base.boll_entry_signal
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


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _opportunity(row=None, result=None, opportunity=None):
    if isinstance(opportunity, dict) and opportunity:
        return opportunity
    if isinstance(row, dict):
        own = row.get("opportunity")
        if isinstance(own, dict) and own:
            return own
    if isinstance(result, dict):
        own = result.get("opportunity")
        if isinstance(own, dict) and own:
            return own
    return None


def _volume_ratio(row=None, result=None, opportunity=None):
    opp = _opportunity(row=row, result=result, opportunity=opportunity)
    if not isinstance(opp, dict):
        return None
    return _finite(opp.get("five_signal_volume_ratio"))


def _rewrite_items(row, path):
    if not isinstance(row, dict):
        return row
    layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
    rewritten = []
    for item in row.get("items") or []:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            rewritten.append(item)
            continue
        label = str(item[0])
        if "成交量" in label or "volume" in label.lower():
            continue
        value, maximum = item[1], item[2]
        if "BOLL" in label:
            if path in OUTER_PATHS and "boll_entry" in layers:
                value = layers.get("boll_entry", value)
            label, maximum = "5m BOLL外轨触发", BOLL_OUTER_SCORE
        elif "MACD" in label:
            label = "5m 外轨MACD连续改善（必须）"
        elif label.startswith("4H"):
            label = "4H同向（外轨 Hard Gate）"
        elif "15m EMA50" in label:
            label = "15m EMA50顺交易方向推进"
        elif "KDJ" in label:
            label = "5m KDJ动能交叉"
        elif "15m正确方向回调区共振" in label:
            label = "15m顺势回调区共振"
        elif "计划入场价接近15m回调区" in label:
            label = "入场位置接近15m回调区"
        elif "确认水平支撑/阻力与15m回调区重合" in label:
            label = "15m回调区 × 水平结构共振"
        values = [label, value, maximum]
        rewritten.append(tuple(values) if isinstance(item, tuple) else values)
    row["items"] = rewritten
    return row


def _reprice_score(row, path):
    layers = row.get("layers")
    if not isinstance(layers, dict):
        return
    layers.pop("volume5", None)
    if path in OUTER_PATHS:
        boll = _finite(layers.get("boll_entry"))
        if boll is not None and boll > 0:
            layers["boll_entry"] = boll + BOLL_OUTER_BONUS
    numeric = []
    for value in layers.values():
        number = _finite(value)
        if number is not None:
            numeric.append(number)
    raw = sum(numeric)
    total = max(0.0, min(SCORE_MAX, round(raw * 2.0) / 2.0))
    row["raw"] = raw
    row["total"] = total


def _apply_v164_row(row, path, result=None, opportunity=None):
    if not isinstance(row, dict):
        return row

    # Score detail is calibrated for every row, including empty/other-side rows.
    if path not in OUTER_PATHS:
        _rewrite_items(row, path)
        return row

    confirmations = row.setdefault("confirmations", {})
    required = confirmations.setdefault("required", {})
    blockers = list(confirmations.get("blockers") or [])

    ratio = _volume_ratio(row=row, result=result, opportunity=opportunity)
    volume_known = ratio is not None
    volume_ok = bool(volume_known and ratio < VOLUME_HARD_GATE)
    required["volume_below_1_2x"] = volume_ok
    confirmations["volume_ratio_5m"] = ratio
    confirmations["volume_hard_gate_ok"] = volume_ok
    confirmations["volume_hard_gate_rule"] = "5m signal volume must be < 1.20x prior-20 5m average"

    _reprice_score(row, path)
    _rewrite_items(row, path)

    if not volume_known:
        blockers.append("V1.6.4 Volume Hard Gate：5m信号K量能数据不可用，禁止开仓")
    elif not volume_ok:
        blockers.append(
            f"V1.6.4 Volume Hard Gate：5m信号K量能 {ratio:.2f}× ≥ {VOLUME_HARD_GATE:.2f}×，禁止开仓"
        )

    base_gate = bool(row.get("gate"))
    row["gate"] = bool(base_gate and volume_ok)
    total = float(row.get("total") or 0.0)
    row["eligible"] = bool(row["gate"] and total >= THRESHOLD)
    row["position_multiplier"] = 1.0 if row["eligible"] else 0.0

    old_reason = str(row.get("reason") or "")
    old_reason = re.sub(r"评分\s+[0-9.]+/10", f"评分 {total:g}/10", old_reason)
    ratio_text = "未知" if ratio is None else f"{ratio:.2f}×"
    if not volume_ok:
        row["level"] = "Volume Hard Gate · 禁止开仓"
        row["reason"] = blockers[-1]
    elif row["eligible"]:
        row["level"] = "V1.6.4外轨信号 · 1×"
        row["reason"] = (
            f"允许开仓｜V1.6.4外轨触发 +0.5｜Volume {ratio_text} < {VOLUME_HARD_GATE:.2f}×｜"
            f"评分 {total:g}/{SCORE_MAX:g}"
        )
    elif not base_gate:
        row["reason"] = (
            (old_reason + "｜") if old_reason else ""
        ) + f"V1.6.4 Volume {ratio_text} < {VOLUME_HARD_GATE:.2f}×，量能门槛允许"
    else:
        row["level"] = "未达开仓线"
        row["reason"] = (
            f"V1.6.4外轨触发 +0.5｜Volume {ratio_text} < {VOLUME_HARD_GATE:.2f}×｜"
            f"评分 {total:g}/{SCORE_MAX:g}，未达开仓阈值 {THRESHOLD:g}"
        )

    confirmations["blockers"] = _dedupe(blockers)
    return row


def _reselect(scores):
    qualified = [
        side for side, row in (scores or {}).items()
        if isinstance(row, dict) and row.get("eligible")
    ]
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
    for row in scores.values():
        if not isinstance(row, dict):
            continue
        path = _path_from(row, result=result, opportunity=opportunity)
        _apply_v164_row(row, path, result=result, opportunity=opportunity)

    selected = _reselect(scores)
    result["side"] = selected
    if selected != "观望":
        result["why"] = str(scores[selected].get("reason") or "")
    else:
        direction = str(result.get("direction") or "")
        if direction in scores and isinstance(scores.get(direction), dict):
            result["why"] = str(scores[direction].get("reason") or result.get("why") or "")
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
    return _rewrite_result(result, opp), opp, transition


def execution_checks(plan, opportunity, score):
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    blockers = list(blockers or [])
    path = _path_from(score if isinstance(score, dict) else {}, opportunity=opportunity)
    ratio = _volume_ratio(row=score if isinstance(score, dict) else {}, opportunity=opportunity)
    volume_ok = bool(ratio is not None and ratio < VOLUME_HARD_GATE)

    if path in OUTER_PATHS:
        if ratio is None:
            blockers.append("V1.6.4开仓禁止：5m信号K量能数据不可用")
        elif not volume_ok:
            blockers.append(
                f"V1.6.4开仓禁止：5m信号K量能 {ratio:.2f}× ≥ {VOLUME_HARD_GATE:.2f}×"
            )

    blockers = _dedupe(blockers)
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "volume_ratio_5m": ratio,
        "volume_hard_gate": VOLUME_HARD_GATE,
        "volume_hard_gate_ok": volume_ok if path in OUTER_PATHS else None,
        "boll_outer_score": BOLL_OUTER_SCORE,
        "volume_score": 0.0,
    })
    return not blockers, diag, blockers

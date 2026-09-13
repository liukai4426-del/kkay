"""Shared KAYTRADE V1.5.3 BOLL pullback-entry model.

Live runtime and backtest share this module. V1.5.3 removes the old mandatory
5m rejection + 1m recovery trigger pair. A closed 5m BOLL signal opens a
four-minute execution window; 1m carries no technical-entry requirement.
"""
from __future__ import annotations

import hashlib
import math

from core import indicators
import v152_model as base

VERSION = "1.5.3"
THRESHOLD = 6.0
ENTRY_WINDOW_MS = 4 * 60 * 1000
MIDDLE_TOUCH_ATR = 0.10
SIGNAL_DRIFT_ATR = 0.25
LONG_MIDDLE_RSI_MIN = 30.0
SHORT_MIDDLE_RSI_MAX = 70.0

FRONT_MIN_R = base.FRONT_MIN_R
COST_MAX_R = base.COST_MAX_R
STOP_BUFFER_ATR = base.STOP_BUFFER_ATR
OVERLAP_ATR_TOL = base.OVERLAP_ATR_TOL

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
estimated_cost_r = base.estimated_cost_r


def _finite(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _signal_id(side, bar_t, path):
    raw = f"{side}|{int(bar_t)}|{path}".encode()
    return "b153-" + hashlib.sha1(raw).hexdigest()[:16]


def _middle_touch(bar, middle, atr5):
    tol = max(0.0, float(atr5)) * MIDDLE_TOUCH_ATR
    low, high = float(bar["l"]), float(bar["h"])
    return low <= float(middle) + tol and high >= float(middle) - tol


def boll_entry_signal(five, side):
    """Return one closed-5m entry signal or None.

    Correct mirrored pullback paths:
    - long: from above -> middle with RSI>=30; fallback lower-band touch.
    - short: from below -> middle with RSI<=70; fallback upper-band touch.
    The two paths are mutually exclusive in scoring and together are worth +2.
    """
    if len(five) < 2:
        return None
    cur = indicators(five)
    prev = indicators(five[:-1])
    bar, prev_bar = five[-1], five[-2]
    atr5 = float(cur["atr"])
    middle = float(cur["middle"])
    upper = float(cur["upper"])
    lower = float(cur["lower"])
    rsi = float(cur["rsi"])
    prev_middle = float(prev["middle"])
    prev_close = float(prev_bar["c"])
    touch_middle = _middle_touch(bar, middle, atr5)

    if side == "做多":
        middle_ok = prev_close > prev_middle and touch_middle and rsi >= LONG_MIDDLE_RSI_MIN
        outer_ok = float(bar["l"]) <= lower
        if middle_ok:
            path, reference = "middle_rsi", middle
        elif outer_ok:
            path, reference = "lower_band", lower
        else:
            return None
    elif side == "做空":
        middle_ok = prev_close < prev_middle and touch_middle and rsi <= SHORT_MIDDLE_RSI_MAX
        outer_ok = float(bar["h"]) >= upper
        if middle_ok:
            path, reference = "middle_rsi", middle
        elif outer_ok:
            path, reference = "upper_band", upper
        else:
            return None
    else:
        return None

    bar_t = int(bar["t"])
    close_ms = bar_t + 5 * 60 * 1000
    return {
        "path": path,
        "bar_t": bar_t,
        "signal_close_ms": close_ms,
        "reference": float(reference),
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "rsi5": rsi,
        "atr5": atr5,
        "bar_low": float(bar["l"]),
        "bar_high": float(bar["h"]),
    }


def _aux_15m_zone(quarter):
    return base.pullback_zone(quarter)


def _aux_pullback_from_correct_side(five, zone, side):
    if len(five) < 2:
        return False
    prev, cur = five[-2], five[-1]
    if side == "做多":
        return float(prev["c"]) > float(zone["high"]) and float(cur["l"]) <= float(zone["high"])
    return float(prev["c"]) < float(zone["low"]) and float(cur["h"]) >= float(zone["low"])


def _near_aux_zone(price, zone, side):
    p, atr = float(price), float(zone["atr15"])
    if side == "做多":
        return p <= float(zone["high"]) + 0.25 * atr
    return p >= float(zone["low"]) - 0.25 * atr


def _entry_ok(price, opportunity):
    """Prevent chasing too far away from the frozen 5m BOLL trigger reference."""
    p = float(price)
    ref = float(opportunity["trigger_reference"])
    atr5 = max(float(opportunity["atr5"]), 1e-12)
    if opportunity["side"] == "做多":
        return p <= ref + SIGNAL_DRIFT_ATR * atr5
    return p >= ref - SIGNAL_DRIFT_ATR * atr5


def _window_state(now_ms, opportunity):
    start = int(opportunity["signal_close_ms"])
    end = int(opportunity["expires_ms"])
    now = int(now_ms)
    opened = start <= now < end
    minute = 0
    if opened:
        minute = min(4, max(1, int((now - start) // 60_000) + 1))
    return opened, minute, max(0, end - now)


def _update_extreme(one, opp):
    if not one:
        return
    cur = one[-1]
    if opp["side"] == "做多":
        opp["extreme"] = min(float(opp.get("extreme", cur["l"])), float(cur["l"]))
    else:
        opp["extreme"] = max(float(opp.get("extreme", cur["h"])), float(cur["h"]))


def new_opportunity(hour, quarter, five, one, side, signal):
    zone = _aux_15m_zone(quarter)
    overlap, structure = base._horizontal_overlap(quarter, side, zone)
    front = base._front_structure(hour, quarter, side, float(one[-1]["c"]))
    h = indicators(hour)
    vok, ratio = base._volume_confirm(five, len(five) - 1)
    kdj = base._kdj_cross(five, side)
    aux_pullback = _aux_pullback_from_correct_side(five, zone, side)
    extreme = float(signal["bar_low"] if side == "做多" else signal["bar_high"])
    reference = float(signal["reference"])
    atr5 = float(signal["atr5"])
    return {
        "id": _signal_id(side, signal["bar_t"], signal["path"]),
        "side": side,
        "created_ms": int(signal["signal_close_ms"]),
        "signal_close_ms": int(signal["signal_close_ms"]),
        "expires_ms": int(signal["signal_close_ms"]) + ENTRY_WINDOW_MS,
        "signal_bar_t": int(signal["bar_t"]),
        "signal_path": signal["path"],
        "trigger_reference": reference,
        "zone_low": reference - SIGNAL_DRIFT_ATR * atr5,
        "zone_high": reference + SIGNAL_DRIFT_ATR * atr5,
        "atr5": atr5,
        "atr15": float(zone["atr15"]),
        "atr1h": float(h["atr"]),
        "aux_zone_low": float(zone["low"]),
        "aux_zone_high": float(zone["high"]),
        "aux_15m_pullback": bool(aux_pullback),
        "structure": structure,
        "structure_overlap": bool(overlap),
        "front_structure": front,
        "extreme": extreme,
        "five_signal_volume_ok": bool(vok),
        "five_signal_volume_ratio": float(ratio),
        "kdj_on_signal": bool(kdj),
        "trigger_detail": {
            "boll_path": signal["path"],
            "boll_middle": float(signal["middle"]),
            "boll_upper": float(signal["upper"]),
            "boll_lower": float(signal["lower"]),
            "rsi5": float(signal["rsi5"]),
            "one_minute_technical_confirmation": False,
        },
        "consumed": False,
    }


def _blank_items():
    return [
        ("15m正确方向回调区共振（辅助）", 0.0, 2.0),
        ("计划入场价接近15m回调区（辅助）", 0.0, 1.0),
        ("确认水平支撑/阻力与15m回调区重合", 0.0, 1.0),
        ("5m BOLL中轨+RSI / 外轨触发（必须）", 0.0, 2.0),
        ("5m MACD柱连续两根改善", 0.0, 1.0),
        ("4H同向 / 中性 / 逆向", 0.0, 1.0),
        ("15m EMA50较3根前向交易方向移动", 0.0, 1.0),
        ("5m信号K线成交量≥20均量1.2×", 0.0, 0.5),
        ("5m信号KDJ对应交叉", 0.0, 0.5),
    ]


def _empty_result(h, m, f, o, q, direction, status, transition=None):
    reason = status if direction == "观望" else f"{status}｜当前方向 {direction}"

    def row(side):
        allowed = direction == side
        return {
            "total": 0.0, "raw": 0.0, "gate": False, "eligible": False,
            "required": THRESHOLD, "signal_tier": 0, "position_multiplier": 0.0,
            "level": "等待5m BOLL信号" if allowed else "方向过滤未通过",
            "reason": reason if allowed else ("1H/15m方向过滤未通过" if direction != "观望" else "1H方向不明确，等待"),
            "items": _blank_items(), "layers": {},
            "confirmations": {"required": {}, "blockers": [], "status": status},
            "structure": {}, "opportunity": None,
        }

    return {
        "q": q, "h": h, "m": m, "f": f, "o": o,
        "side": "观望", "direction": direction, "why": reason,
        "scores": {"做多": row("做多"), "做空": row("做空")},
        "threshold": THRESHOLD, "score_max": 10.0, "strategy_version": VERSION,
        "status": status, "opportunity": None, "opportunity_id": "",
        "opportunity_remaining_seconds": 0, "entry_window_minute": 0,
        "front_space_status": "空间未知", "front_r": math.inf,
        "cost_r": math.inf, "transition": transition,
    }


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    if min(len(hour), len(quarter), len(five), len(one), len(four)) < 201:
        raise ValueError("V1.5.3指标数据不足")

    now_ms = int(now_ms if now_ms is not None else int(one[-1]["t"]) + 60_000)
    h, m, f, o, q = indicators(hour), indicators(quarter), indicators(five), indicators(one), indicators(four)
    direction = trend_direction(hour, quarter)
    transition = None
    opp = dict(opportunity) if isinstance(opportunity, dict) else None

    if opp and opp.get("consumed"):
        opp = None
    if opp:
        if direction != opp.get("side"):
            transition = ("invalidated", "1H方向或15m均线排列失效")
            opp = None
        elif base._structure_broken(quarter, opp):
            transition = ("invalidated", "已确认15m关键结构被收盘价突破")
            opp = None

    current_signal = boll_entry_signal(five, direction) if direction in ("做多", "做空") else None
    if allow_new and current_signal:
        old_t = int((opp or {}).get("signal_bar_t", -1))
        if opp is None or int(current_signal["bar_t"]) > old_t:
            opp = new_opportunity(hour, quarter, five, one, direction, current_signal)
            transition = ("created", opp["id"])

    status = "等待趋势" if direction == "观望" else "等待5m BOLL回调信号"
    if transition and transition[0] == "invalidated":
        status = "机会失效"
    if not opp:
        return _empty_result(h, m, f, o, q, direction, status, transition), None, transition

    _update_extreme(one, opp)
    side = opp["side"]
    entry = float(one[-1]["c"])
    stop_distance = float(opp["atr1h"]) * float(stop_atr)
    window_open, window_minute, remaining_ms = _window_state(now_ms, opp)
    entry_ok = _entry_ok(entry, opp)
    aux_zone = {
        "low": float(opp["aux_zone_low"]), "high": float(opp["aux_zone_high"]),
        "atr15": float(opp["atr15"]),
    }
    aux_near = _near_aux_zone(entry, aux_zone, side)
    macd_improve, macd_adverse, macd_hist = base._macd_state(five, side)
    ema50_ok = base._ema50_move(quarter, side)
    qstate = four_hour_state(four, side)
    qscore = 1.0 if qstate == "aligned" else -1.0 if qstate == "opposite" else 0.0
    front_r, front_status = base._front_r(opp, entry, stop_distance)
    stop_ok, stop_px, buffer_boundary = base._stop_buffer_ok(entry, stop_distance, opp)
    cost_r = estimated_cost_r(entry, stop_distance, side, maker_bps, taker_bps, slippage_bps)
    adverse4_zone, adverse4_dist, adverse4_block = base._adverse_4h(four, side, entry)

    required = {
        "boll_entry_signal": True,
        "entry_window_4x1m": bool(window_open),
    }
    components = {
        "direction_pullback": 2.0 if opp.get("aux_15m_pullback") else 0.0,
        "entry_near_zone": 1.0 if aux_near else 0.0,
        "structure_overlap": 1.0 if opp.get("structure_overlap") else 0.0,
        "boll_entry": 2.0,
        "macd_improving": 1.0 if macd_improve else 0.0,
        "trend4h": qscore,
        "ema50_15m_move": 1.0 if ema50_ok else 0.0,
        "volume5": 0.5 if opp.get("five_signal_volume_ok") else 0.0,
        "kdj5_signal": 0.5 if opp.get("kdj_on_signal") else 0.0,
    }
    raw = sum(components.values())
    total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))

    blockers = []
    if direction != side:
        blockers.append("1H方向不明确或15m均线排列相反")
    if macd_adverse:
        blockers.append("5m MACD柱连续两根向交易反方向恶化")
    if not entry_ok:
        blockers.append("计划限价偏离5m BOLL触发位置过远")
    if front_status == "blocked":
        blockers.append(f"前方强结构空间 {front_r:.2f}R < {FRONT_MIN_R:.1f}R")
    if not stop_ok:
        blockers.append("1H ATR止损仍在5m回调极值结构缓冲内侧")
    if cost_r > COST_MAX_R:
        blockers.append(f"预计手续费与滑点 {cost_r:.2f}R > {COST_MAX_R:.2f}R")
    if adverse4_block:
        blockers.append("接近V1.5.1保留的4H不利支撑/压力结构")

    required_ok = all(required.values())
    gate = required_ok and not blockers
    eligible = gate and total >= THRESHOLD
    if not window_open:
        status = "BOLL信号4分钟窗口已过期" if now_ms >= int(opp["expires_ms"]) else "等待BOLL执行窗口"
    elif eligible:
        status = "允许开仓"
    else:
        status = f"BOLL信号执行窗口 · 第{window_minute}个1m"

    remaining = max(0, int(math.ceil(remaining_ms / 1000.0)))
    front_text = "空间未知" if front_status == "unknown" else f"{front_r:.2f}R"
    path_text = {
        "middle_rsi": "BOLL中轨+RSI",
        "lower_band": "BOLL下轨",
        "upper_band": "BOLL上轨",
    }.get(opp.get("signal_path"), str(opp.get("signal_path") or "BOLL"))
    reason = (
        f"{status}｜方向 {side}｜触发 {path_text}｜窗口剩余 {remaining//60:02d}:{remaining%60:02d}｜"
        f"评分 {total:g}/10｜必须项 {'通过' if required_ok else '未通过'}｜"
        f"前方 {front_text}｜成本 {cost_r:.2f}R"
    )
    if blockers:
        reason += "｜禁止：" + "；".join(blockers)
    elif required_ok and total < THRESHOLD:
        reason += f"｜未达{THRESHOLD:g}分门槛"

    row = {
        "total": total, "raw": raw, "gate": gate, "eligible": eligible,
        "required": THRESHOLD, "signal_tier": 1 if total >= THRESHOLD else 0,
        "position_multiplier": 1.0 if eligible else 0.0,
        "level": "开仓信号 · 1×仓位" if total >= THRESHOLD else "未达开仓线",
        "reason": reason,
        "items": [
            ("15m正确方向回调区共振（辅助）", components["direction_pullback"], 2.0),
            ("计划入场价接近15m回调区（辅助）", components["entry_near_zone"], 1.0),
            ("确认水平支撑/阻力与15m回调区重合", components["structure_overlap"], 1.0),
            ("5m BOLL中轨+RSI / 外轨触发（必须）", components["boll_entry"], 2.0),
            ("5m MACD柱连续两根改善", components["macd_improving"], 1.0),
            ("4H同向 / 中性 / 逆向", components["trend4h"], 1.0),
            ("15m EMA50较3根前向交易方向移动", components["ema50_15m_move"], 1.0),
            ("5m信号K线成交量≥20均量1.2×", components["volume5"], 0.5),
            ("5m信号KDJ对应交叉", components["kdj5_signal"], 0.5),
        ],
        "layers": dict(components),
        "confirmations": {
            "required": required, "blockers": blockers, "direction": side, "status": status,
            "trigger": dict(opp.get("trigger_detail") or {}),
            "entry_window_minute": window_minute,
            "one_minute_technical_confirmation": False,
            "5m_macd_hist": macd_hist, "5m_macd_adverse": macd_adverse,
            "4H_trend_state": qstate,
            "boll5_middle": float(opp["trigger_detail"]["boll_middle"]),
            "boll5_upper": float(opp["trigger_detail"]["boll_upper"]),
            "boll5_lower": float(opp["trigger_detail"]["boll_lower"]),
            "rsi5": float(opp["trigger_detail"]["rsi5"]),
        },
        "structure": {
            "opportunity_id": opp["id"], "structure": opp.get("structure"),
            "front": opp.get("front_structure"), "front_r": front_r,
            "front_space_status": "空间未知" if front_status == "unknown" else ("充足" if front_status == "ok" else "不足"),
            "cost_r": cost_r, "stop_buffer_ok": stop_ok, "planned_sl": stop_px,
            "buffer_boundary": buffer_boundary, "pullback_extreme": opp["extreme"],
            "adverse_4h": adverse4_zone, "adverse_4h_distance_atr": adverse4_dist,
            "adverse_4h_hard_block": adverse4_block,
        },
        "opportunity": dict(opp),
    }

    other = "做空" if side == "做多" else "做多"
    other_row = {
        "total": 0.0, "raw": 0.0, "gate": False, "eligible": False,
        "required": THRESHOLD, "signal_tier": 0, "position_multiplier": 0.0,
        "level": "方向过滤未通过", "reason": f"1H/15m方向过滤当前仅允许{side}",
        "items": [(x[0], 0.0, x[2]) for x in row["items"]], "layers": {},
        "confirmations": {"required": {}, "blockers": ["方向过滤未通过"], "status": "等待趋势"},
        "structure": {}, "opportunity": None,
    }
    scores = {side: row, other: other_row}
    result = {
        "q": q, "h": h, "m": m, "f": f, "o": o,
        "side": side if eligible else "观望", "direction": side, "why": reason,
        "scores": {"做多": scores["做多"], "做空": scores["做空"]},
        "threshold": THRESHOLD, "score_max": 10.0, "strategy_version": VERSION,
        "status": status, "opportunity": dict(opp), "opportunity_id": opp["id"],
        "opportunity_remaining_seconds": remaining, "entry_window_minute": window_minute,
        "front_space_status": row["structure"]["front_space_status"],
        "front_r": front_r, "cost_r": cost_r,
        "estimated_sl": stop_px,
        "estimated_tp": entry + (1 if side == "做多" else -1) * stop_distance * 2.0,
        "transition": transition,
    }
    return result, opp, transition


def execution_checks(plan, opportunity, score):
    if not isinstance(opportunity, dict):
        return False, {}, ["缺少有效5m BOLL信号状态"]
    entry = float(plan["px"])
    stop_distance = float(plan["stop_distance"])
    entry_ok = _entry_ok(entry, opportunity)
    front_r, front_status = base._front_r(opportunity, entry, stop_distance)
    stop_ok, stop_px, buffer_boundary = base._stop_buffer_ok(entry, stop_distance, opportunity)
    btc = max(_finite(plan.get("btc"), 0.0), 0.0)
    worst_cost = _finite(plan.get("worst_roundtrip_cost"), math.inf)
    risk_money = btc * stop_distance
    cost_r = worst_cost / risk_money if risk_money > 0 else math.inf
    blockers = []
    if not entry_ok:
        blockers.append("实际限价偏离5m BOLL触发位置过远")
    if front_status == "blocked":
        blockers.append(f"实际限价前方空间 {front_r:.2f}R < {FRONT_MIN_R:.1f}R")
    if not stop_ok:
        blockers.append("实际1H ATR止损未越过5m回调极值+结构缓冲")
    if cost_r > COST_MAX_R:
        blockers.append(f"实际预计成本 {cost_r:.2f}R > {COST_MAX_R:.2f}R")
    required = ((score or {}).get("confirmations") or {}).get("required") or {}
    if required and not all(bool(v) for v in required.values()):
        blockers.append("评分必须项未全部通过（BOLL信号/4分钟窗口）")
    diag = {
        "entry_near_signal": entry_ok, "front_r": front_r,
        "front_space_status": "空间未知" if front_status == "unknown" else ("充足" if front_status == "ok" else "不足"),
        "cost_r": cost_r, "stop_buffer_ok": stop_ok, "planned_sl": stop_px,
        "buffer_boundary": buffer_boundary, "opportunity_id": opportunity.get("id"),
        "signal_path": opportunity.get("signal_path"),
    }
    return not blockers, diag, blockers

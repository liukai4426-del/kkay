"""Shared KAYTRADE V1.5.2 pullback-entry model.

Runtime and backtest both import this module so direction, opportunity state,
scoring and hard-gate definitions cannot drift.

Frozen V1.5.2 definitions:
- confirmed horizontal structure: existing strategy._zones swing/cluster algorithm
- horizontal overlap tolerance: 0.25 x 15m ATR
- stop structure buffer: 0.10 x 15m ATR beyond the pullback extreme
- front strong-structure minimum: 1.5R
- opportunity timeout: 30 minutes
"""
from __future__ import annotations

import hashlib
import math

from core import indicators, ema
import strategy

VERSION = "1.5.2"
THRESHOLD = 6.0
OPPORTUNITY_TTL_MS = 30 * 60 * 1000
ZONE_ATR_TOL = 0.25
OVERLAP_ATR_TOL = 0.25
STOP_BUFFER_ATR = 0.10
FRONT_MIN_R = 1.5
COST_MAX_R = 0.30


def _finite(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _ema_series(rows, period):
    return ema([float(r["c"]) for r in rows], period)


def _macd_hist(rows):
    close = [float(r["c"]) for r in rows]
    e12 = ema(close, 12)
    e26 = ema(close, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    return [2 * (a - b) for a, b in zip(dif, dea)]


def trend_direction(hour, quarter):
    h = indicators(hour)
    m = indicators(quarter)
    hc = float(hour[-1]["c"])
    long_ok = hc > float(h["ema200"]) and float(h["ema20"]) > float(h["ema50"]) and bool(h["up"]) and float(m["ema20"]) > float(m["ema50"])
    short_ok = hc < float(h["ema200"]) and float(h["ema20"]) < float(h["ema50"]) and bool(h["down"]) and float(m["ema20"]) < float(m["ema50"])
    if long_ok and not short_ok:
        return "做多"
    if short_ok and not long_ok:
        return "做空"
    return "观望"


def four_hour_state(four, side):
    q = indicators(four)
    close = float(four[-1]["c"])
    buy = side == "做多"
    if buy:
        aligned = close > q["ema200"] and q["ema20"] > q["ema50"] and q["up"]
        opposite = close < q["ema200"] and q["ema20"] < q["ema50"] and q["down"]
    else:
        aligned = close < q["ema200"] and q["ema20"] < q["ema50"] and q["down"]
        opposite = close > q["ema200"] and q["ema20"] > q["ema50"] and q["up"]
    return "aligned" if aligned else "opposite" if opposite else "neutral"


def pullback_zone(quarter):
    m = indicators(quarter)
    atr = float(m["atr"])
    core_low = min(float(m["ema20"]), float(m["ema50"]))
    core_high = max(float(m["ema20"]), float(m["ema50"]))
    return {
        "core_low": core_low, "core_high": core_high,
        "low": core_low - ZONE_ATR_TOL * atr,
        "high": core_high + ZONE_ATR_TOL * atr,
        "atr15": atr, "ema20": float(m["ema20"]), "ema50": float(m["ema50"]),
    }


def _nearest_confirmed_structure(quarter, side, price=None):
    m = indicators(quarter)
    atr = float(m["atr"])
    zones = strategy._zones(quarter, atr, 160)
    kind = "support" if side == "做多" else "resistance"
    ref = float(quarter[-1]["c"]) if price is None else float(price)
    return strategy._nearest(zones, ref, kind), zones


def _front_structure(hour, quarter, side, price=None):
    h = indicators(hour)
    m = indicators(quarter)
    ref = float(quarter[-1]["c"]) if price is None else float(price)
    adverse = "resistance" if side == "做多" else "support"
    ahead = "above" if side == "做多" else "below"
    rows = []
    for tf, candles, ind, lookback in (("1H", hour, h, 120), ("15m", quarter, m, 160)):
        zones = strategy._zones(candles, float(ind["atr"]), lookback)
        z = strategy._nearest([x for x in zones if x.get("strong")], ref, adverse, ahead)
        if z:
            rows.append((abs(float(z["price"]) - ref), tf, z))
    if not rows:
        return None
    _, tf, zone = min(rows, key=lambda x: x[0])
    return {"timeframe": tf, **zone}


def _adverse_4h(four, side, price):
    q = indicators(four)
    zones = strategy._zones(four, float(q["atr"]), 180)
    kind = "resistance" if side == "做多" else "support"
    ahead = "above" if side == "做多" else "below"
    z = strategy._nearest(zones, float(price), kind, ahead)
    if not z:
        return None, math.inf, False
    dist = abs(float(z["price"]) - float(price)) / float(q["atr"])
    return z, dist, dist <= 0.25


def _opportunity_id(side, created_ms, low, high):
    raw = f"{side}|{int(created_ms)}|{low:.8f}|{high:.8f}".encode()
    return "pb-" + hashlib.sha1(raw).hexdigest()[:16]


def _touch_from_correct_side(one, zone, side):
    if len(one) < 2:
        return False
    prev, cur = one[-2], one[-1]
    if side == "做多":
        return float(prev["c"]) > zone["high"] and float(cur["l"]) <= zone["high"]
    return float(prev["c"]) < zone["low"] and float(cur["h"]) >= zone["low"]


def _horizontal_overlap(quarter, side, zone):
    structure, _ = _nearest_confirmed_structure(quarter, side)
    if not structure:
        return False, None
    p = float(structure["price"])
    tol = OVERLAP_ATR_TOL * float(zone["atr15"])
    distance = 0.0 if zone["low"] <= p <= zone["high"] else min(abs(p - zone["low"]), abs(p - zone["high"]))
    return distance <= tol, structure


def _structure_broken(quarter, opportunity):
    structure = opportunity.get("structure")
    if not isinstance(structure, dict):
        return False
    price = _finite(structure.get("price"), math.nan)
    if not math.isfinite(price):
        return False
    atr = _finite(opportunity.get("atr15"), 0.0)
    if atr <= 0:
        return True
    close = float(quarter[-1]["c"])
    if opportunity["side"] == "做多":
        return close < price - 0.30 * atr
    return close > price + 0.30 * atr


def _five_confirm(five, opportunity):
    c = five[-1]
    close_ms = int(c["t"]) + 5 * 60 * 1000
    if close_ms < int(opportunity["created_ms"]):
        return False
    o, h, l, close = map(float, (c["o"], c["h"], c["l"], c["c"]))
    mid = (h + l) / 2.0
    return (close > o and close >= mid) if opportunity["side"] == "做多" else (close < o and close <= mid)


def _one_trigger(one, side, not_before_ms):
    if len(one) < 5:
        return False, {"ema_reclaim": False, "break_3": False}
    cur, prev = indicators(one), indicators(one[:-1])
    c = one[-1]
    close_ms = int(c["t"]) + 60 * 1000
    if close_ms < int(not_before_ms or 0):
        return False, {"ema_reclaim": False, "break_3": False}
    prev_c, close = float(one[-2]["c"]), float(c["c"])
    if side == "做多":
        reclaim = prev_c <= float(prev["ema20"]) and close > float(cur["ema20"])
        break3 = close > max(float(r["h"]) for r in one[-4:-1])
    else:
        reclaim = prev_c >= float(prev["ema20"]) and close < float(cur["ema20"])
        break3 = close < min(float(r["l"]) for r in one[-4:-1])
    return bool(reclaim or break3), {"ema_reclaim": bool(reclaim), "break_3": bool(break3)}


def _macd_state(five, side):
    hist = _macd_hist(five)
    a, b, c = hist[-3], hist[-2], hist[-1]
    if side == "做多":
        improving = c > b > a
        adverse = c < b < a and c < 0
    else:
        improving = c < b < a
        adverse = c > b > a and c > 0
    return bool(improving), bool(adverse), [a, b, c]


def _ema50_move(quarter, side):
    values = _ema_series(quarter, 50)
    return values[-1] > values[-4] if side == "做多" else values[-1] < values[-4]


def _volume_confirm(five, idx):
    if idx < 20:
        return False, 0.0
    history = [float(r["v"]) for r in five[idx - 20:idx]]
    avg = sum(history) / len(history) if history else 0.0
    ratio = float(five[idx]["v"]) / avg if avg > 0 else 0.0
    return ratio >= 1.2, ratio


def _kdj_cross(five, side):
    cur = indicators(five)
    return bool(cur["cross_up"] if side == "做多" else cur["cross_down"])


def _entry_ok(price, opportunity):
    p, atr = float(price), float(opportunity["atr15"])
    if opportunity["side"] == "做多":
        return p <= float(opportunity["zone_high"]) + 0.25 * atr
    return p >= float(opportunity["zone_low"]) - 0.25 * atr


def _front_r(opportunity, entry, stop_distance):
    front = opportunity.get("front_structure")
    if not isinstance(front, dict) or front.get("price") in (None, ""):
        return math.inf, "unknown"
    if stop_distance <= 0:
        return 0.0, "blocked"
    r = abs(float(front["price"]) - float(entry)) / float(stop_distance)
    return r, "ok" if r >= FRONT_MIN_R else "blocked"


def estimated_cost_r(entry, stop_distance, side, maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0):
    if stop_distance <= 0:
        return math.inf
    d = 1 if side == "做多" else -1
    stop = max(1e-12, float(entry) - d * float(stop_distance))
    cost = float(entry) * float(maker_bps) / 10000.0 + stop * (float(taker_bps) + float(slippage_bps)) / 10000.0
    return cost / float(stop_distance)


def _stop_buffer_ok(entry, stop_distance, opportunity):
    buffer = STOP_BUFFER_ATR * float(opportunity["atr15"])
    if opportunity["side"] == "做多":
        sl = float(entry) - float(stop_distance)
        boundary = float(opportunity["extreme"]) - buffer
        return sl <= boundary, sl, boundary
    sl = float(entry) + float(stop_distance)
    boundary = float(opportunity["extreme"]) + buffer
    return sl >= boundary, sl, boundary


def new_opportunity(hour, quarter, one, side):
    zone = pullback_zone(quarter)
    close_ms = int(one[-1]["t"]) + 60 * 1000
    overlap, structure = _horizontal_overlap(quarter, side, zone)
    front = _front_structure(hour, quarter, side, float(one[-1]["c"]))
    h = indicators(hour)
    extreme = float(one[-1]["l"] if side == "做多" else one[-1]["h"])
    return {
        "id": _opportunity_id(side, close_ms, zone["low"], zone["high"]),
        "side": side, "created_ms": close_ms, "expires_ms": close_ms + OPPORTUNITY_TTL_MS,
        "zone_low": zone["low"], "zone_high": zone["high"], "zone_core_low": zone["core_low"], "zone_core_high": zone["core_high"],
        "atr15": zone["atr15"], "atr1h": float(h["atr"]), "structure": structure,
        "structure_overlap": bool(overlap), "front_structure": front, "extreme": extreme,
        "five_confirmed_at": 0, "five_confirm_index": None, "five_confirm_volume_ratio": 0.0,
        "five_confirm_volume_ok": False, "kdj_after_touch": False, "triggered_at": 0,
        "trigger_detail": {"ema_reclaim": False, "break_3": False}, "consumed": False,
    }


def _update_extreme(one, opp):
    cur = one[-1]
    if opp["side"] == "做多":
        opp["extreme"] = min(float(opp.get("extreme", cur["l"])), float(cur["l"]))
    else:
        opp["extreme"] = max(float(opp.get("extreme", cur["h"])), float(cur["h"]))


def _blank_items():
    return [
        ("正确方向回调进入固定区域（必须）", 0.0, 2.0),
        ("计划入场价未偏离区域过远（必须）", 0.0, 1.0),
        ("确认水平支撑/阻力与回调区重合", 0.0, 1.0),
        ("5m拒绝继续回调确认（必须）", 0.0, 1.0),
        ("1m恢复触发（必须）", 0.0, 1.0),
        ("5m MACD柱连续两根改善", 0.0, 1.0),
        ("4H同向 / 中性 / 逆向", 0.0, 1.0),
        ("15m EMA50较3根前向交易方向移动", 0.0, 1.0),
        ("5m确认K线成交量≥20均量1.2×", 0.0, 0.5),
        ("触区后5m KDJ对应交叉", 0.0, 0.5),
    ]


def _empty_result(h, m, f, o, q, direction, status, transition):
    reason = status if direction == "观望" else f"{status}｜当前方向 {direction}"
    def row(side):
        allowed = direction == side
        return {"total": 0.0, "raw": 0.0, "gate": False, "eligible": False, "required": THRESHOLD,
                "signal_tier": 0, "position_multiplier": 0.0, "level": "等待回调" if allowed else "方向过滤未通过",
                "reason": reason if allowed else ("1H/15m方向过滤未通过" if direction != "观望" else "1H方向不明确，等待"),
                "items": _blank_items(), "layers": {}, "confirmations": {"required": {}, "blockers": [], "status": status},
                "structure": {}, "opportunity": None}
    return {"q": q, "h": h, "m": m, "f": f, "o": o, "side": "观望", "direction": direction, "why": reason,
            "scores": {"做多": row("做多"), "做空": row("做空")}, "threshold": THRESHOLD, "score_max": 10.0,
            "strategy_version": VERSION, "status": status, "opportunity": None, "opportunity_id": "",
            "opportunity_remaining_seconds": 0, "front_space_status": "空间未知", "front_r": math.inf,
            "cost_r": math.inf, "transition": transition}


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0, now_ms=None, allow_new=True):
    if min(len(hour), len(quarter), len(five), len(one), len(four)) < 201:
        raise ValueError("V1.5.2指标数据不足")
    now_ms = int(now_ms if now_ms is not None else int(one[-1]["t"]) + 60 * 1000)
    h, m, f, o, q = indicators(hour), indicators(quarter), indicators(five), indicators(one), indicators(four)
    direction = trend_direction(hour, quarter)
    transition = None
    opp = dict(opportunity) if isinstance(opportunity, dict) else None

    if opp and opp.get("consumed"):
        transition = ("invalidated", "机会已成交，本次回调不可重复开仓")
        opp = None
    if opp:
        if now_ms >= int(opp["expires_ms"]):
            transition = ("invalidated", "回调机会30分钟超时")
            opp = None
        elif direction != opp.get("side"):
            transition = ("invalidated", "1H方向或15m均线排列失效")
            opp = None
        elif _structure_broken(quarter, opp):
            transition = ("invalidated", "已确认15m关键结构被收盘价突破")
            opp = None

    if not opp and transition is None and allow_new and direction in ("做多", "做空"):
        z = pullback_zone(quarter)
        if _touch_from_correct_side(one, z, direction):
            opp = new_opportunity(hour, quarter, one, direction)
            transition = ("created", opp["id"])

    status = "等待趋势" if direction == "观望" else "等待回调"
    if transition and transition[0] == "invalidated":
        status = "机会失效"
    if not opp:
        return _empty_result(h, m, f, o, q, direction, status, transition), None, transition

    _update_extreme(one, opp)
    if not opp.get("five_confirmed_at") and _five_confirm(five, opp):
        opp["five_confirmed_at"] = int(five[-1]["t"]) + 5 * 60 * 1000
        opp["five_confirm_index"] = len(five) - 1
        vok, ratio = _volume_confirm(five, len(five) - 1)
        opp["five_confirm_volume_ok"] = bool(vok)
        opp["five_confirm_volume_ratio"] = float(ratio)
        transition = ("five_confirmed", opp["id"])
    if int(five[-1]["t"]) + 5 * 60 * 1000 >= int(opp["created_ms"]) and _kdj_cross(five, opp["side"]):
        opp["kdj_after_touch"] = True
    if opp.get("five_confirmed_at") and not opp.get("triggered_at"):
        trig, detail = _one_trigger(one, opp["side"], opp["five_confirmed_at"])
        if trig:
            opp["triggered_at"] = int(one[-1]["t"]) + 60 * 1000
            opp["trigger_detail"] = detail
            transition = ("one_triggered", opp["id"])

    side = opp["side"]
    entry = float(one[-1]["c"])
    stop_distance = float(opp["atr1h"]) * float(stop_atr)
    entry_ok = _entry_ok(entry, opp)
    macd_improve, macd_adverse, macd_hist = _macd_state(five, side)
    ema50_ok = _ema50_move(quarter, side)
    qstate = four_hour_state(four, side)
    qscore = 1.0 if qstate == "aligned" else -1.0 if qstate == "opposite" else 0.0
    front_r, front_status = _front_r(opp, entry, stop_distance)
    stop_ok, stop_px, buffer_boundary = _stop_buffer_ok(entry, stop_distance, opp)
    cost_r = estimated_cost_r(entry, stop_distance, side, maker_bps, taker_bps, slippage_bps)
    adverse4_zone, adverse4_dist, adverse4_block = _adverse_4h(four, side, entry)

    required = {"direction_pullback": True, "entry_near_zone": bool(entry_ok),
                "five_confirm": bool(opp.get("five_confirmed_at")), "one_trigger": bool(opp.get("triggered_at"))}
    components = {
        "direction_pullback": 2.0, "entry_near_zone": 1.0 if entry_ok else 0.0,
        "structure_overlap": 1.0 if opp.get("structure_overlap") else 0.0,
        "five_confirm": 1.0 if opp.get("five_confirmed_at") else 0.0,
        "one_trigger": 1.0 if opp.get("triggered_at") else 0.0,
        "macd_improving": 1.0 if macd_improve else 0.0, "trend4h": qscore,
        "ema50_15m_move": 1.0 if ema50_ok else 0.0,
        "volume5": 0.5 if opp.get("five_confirm_volume_ok") else 0.0,
        "kdj5_after_touch": 0.5 if opp.get("kdj_after_touch") else 0.0,
    }
    raw = sum(components.values())
    total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
    blockers = []
    if direction != side:
        blockers.append("1H方向不明确或15m均线排列相反")
    if macd_adverse:
        blockers.append("5m MACD柱连续两根向交易反方向恶化")
    if not entry_ok:
        blockers.append("计划限价偏离固定回调区域过远")
    if front_status == "blocked":
        blockers.append(f"前方强结构空间 {front_r:.2f}R < {FRONT_MIN_R:.1f}R")
    if not stop_ok:
        blockers.append("1H ATR止损仍在回调极值结构缓冲内侧")
    if cost_r > COST_MAX_R:
        blockers.append(f"预计手续费与滑点 {cost_r:.2f}R > {COST_MAX_R:.2f}R")
    if adverse4_block:
        blockers.append("接近V1.5.1保留的4H不利支撑/压力结构")
    required_ok = all(required.values())
    gate = required_ok and not blockers
    eligible = gate and total >= THRESHOLD
    if not opp.get("five_confirmed_at"):
        status = "等待5m确认"
    elif not opp.get("triggered_at"):
        status = "等待1m触发"
    elif eligible:
        status = "允许开仓"
    else:
        status = "评分及限制检查"

    remaining = max(0, int((int(opp["expires_ms"]) - now_ms + 999) // 1000))
    front_text = "空间未知" if front_status == "unknown" else f"{front_r:.2f}R"
    reason = (f"{status}｜方向 {side}｜回调区 {opp['zone_low']:.2f}–{opp['zone_high']:.2f}｜"
              f"剩余 {remaining//60:02d}:{remaining%60:02d}｜评分 {total:g}/10｜"
              f"必须项 {'通过' if required_ok else '未通过'}｜前方 {front_text}｜成本 {cost_r:.2f}R")
    if blockers:
        reason += "｜禁止：" + "；".join(blockers)
    elif required_ok and total < THRESHOLD:
        reason += f"｜未达{THRESHOLD:g}分门槛"

    row = {
        "total": total, "raw": raw, "gate": gate, "eligible": eligible, "required": THRESHOLD,
        "signal_tier": 1 if total >= THRESHOLD else 0, "position_multiplier": 1.0 if eligible else 0.0,
        "level": "开仓信号 · 1×仓位" if total >= THRESHOLD else "未达开仓线", "reason": reason,
        "items": [
            ("正确方向回调进入固定区域（必须）", components["direction_pullback"], 2.0),
            ("计划入场价未偏离区域过远（必须）", components["entry_near_zone"], 1.0),
            ("确认水平支撑/阻力与回调区重合", components["structure_overlap"], 1.0),
            ("5m拒绝继续回调确认（必须）", components["five_confirm"], 1.0),
            ("1m恢复触发（必须）", components["one_trigger"], 1.0),
            ("5m MACD柱连续两根改善", components["macd_improving"], 1.0),
            ("4H同向 / 中性 / 逆向", components["trend4h"], 1.0),
            ("15m EMA50较3根前向交易方向移动", components["ema50_15m_move"], 1.0),
            ("5m确认K线成交量≥20均量1.2×", components["volume5"], 0.5),
            ("触区后5m KDJ对应交叉", components["kdj5_after_touch"], 0.5),
        ],
        "layers": dict(components),
        "confirmations": {"required": required, "blockers": blockers, "direction": side, "status": status,
                          "5m_macd_hist": macd_hist, "5m_macd_adverse": macd_adverse, "4H_trend_state": qstate,
                          "trigger": dict(opp.get("trigger_detail") or {}), "boll5_diagnostic": float(f["middle"]),
                          "boll15_diagnostic": float(m["middle"]), "rsi5_diagnostic": float(f["rsi"]),
                          "rsi15_diagnostic": float(m["rsi"])},
        "structure": {"opportunity_id": opp["id"], "zone_low": opp["zone_low"], "zone_high": opp["zone_high"],
                      "structure": opp.get("structure"), "front": opp.get("front_structure"), "front_r": front_r,
                      "front_space_status": "空间未知" if front_status == "unknown" else ("充足" if front_status == "ok" else "不足"),
                      "cost_r": cost_r, "stop_buffer_ok": stop_ok, "planned_sl": stop_px,
                      "buffer_boundary": buffer_boundary, "pullback_extreme": opp["extreme"],
                      "adverse_4h": adverse4_zone, "adverse_4h_distance_atr": adverse4_dist,
                      "adverse_4h_hard_block": adverse4_block},
        "opportunity": dict(opp),
    }
    other = "做空" if side == "做多" else "做多"
    other_row = {"total": 0.0, "raw": 0.0, "gate": False, "eligible": False, "required": THRESHOLD,
                 "signal_tier": 0, "position_multiplier": 0.0, "level": "方向过滤未通过",
                 "reason": f"1H/15m方向过滤当前仅允许{side}",
                 "items": [(x[0], 0.0, x[2]) for x in row["items"]], "layers": {},
                 "confirmations": {"required": {}, "blockers": ["方向过滤未通过"], "status": "等待趋势"},
                 "structure": {}, "opportunity": None}
    scores = {side: row, other: other_row}
    result = {"q": q, "h": h, "m": m, "f": f, "o": o, "side": side if eligible else "观望",
              "direction": side, "why": reason, "scores": {"做多": scores["做多"], "做空": scores["做空"]},
              "threshold": THRESHOLD, "score_max": 10.0, "strategy_version": VERSION, "status": status,
              "opportunity": dict(opp), "opportunity_id": opp["id"], "opportunity_remaining_seconds": remaining,
              "front_space_status": row["structure"]["front_space_status"], "front_r": front_r, "cost_r": cost_r,
              "estimated_sl": stop_px, "estimated_tp": entry + (1 if side == "做多" else -1) * stop_distance * 2.0,
              "transition": transition}
    return result, opp, transition


def execution_checks(plan, opportunity, score):
    if not isinstance(opportunity, dict):
        return False, {}, ["缺少有效回调机会状态"]
    entry = float(plan["px"])
    stop_distance = float(plan["stop_distance"])
    entry_ok = _entry_ok(entry, opportunity)
    front_r, front_status = _front_r(opportunity, entry, stop_distance)
    stop_ok, stop_px, buffer_boundary = _stop_buffer_ok(entry, stop_distance, opportunity)
    btc = max(_finite(plan.get("btc"), 0.0), 0.0)
    worst_cost = _finite(plan.get("worst_roundtrip_cost"), math.inf)
    risk_money = btc * stop_distance
    cost_r = worst_cost / risk_money if risk_money > 0 else math.inf
    blockers = []
    if not entry_ok:
        blockers.append("实际限价偏离固定回调区过远")
    if front_status == "blocked":
        blockers.append(f"实际限价前方空间 {front_r:.2f}R < {FRONT_MIN_R:.1f}R")
    if not stop_ok:
        blockers.append("实际1H ATR止损未越过回调极值+结构缓冲")
    if cost_r > COST_MAX_R:
        blockers.append(f"实际预计成本 {cost_r:.2f}R > {COST_MAX_R:.2f}R")
    required = ((score or {}).get("confirmations") or {}).get("required") or {}
    if required and not all(bool(v) for v in required.values()):
        blockers.append("评分必须项未全部通过")
    diag = {"entry_near_zone": entry_ok, "front_r": front_r,
            "front_space_status": "空间未知" if front_status == "unknown" else ("充足" if front_status == "ok" else "不足"),
            "cost_r": cost_r, "stop_buffer_ok": stop_ok, "planned_sl": stop_px,
            "buffer_boundary": buffer_boundary, "opportunity_id": opportunity.get("id")}
    return not blockers, diag, blockers

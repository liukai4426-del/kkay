"""KAYTRADE V1.7.0 Build1700 strategy/runtime/UI overlay.

V1.7.0 promotes the verified research combination into the production overlay:
- remove the 1H EMA9/26 +1 score completely; it is neither a score nor a hard gate;
- retain the existing 15m EMA50 directional +1 score from the inherited model;
- add a latched +1 score when a CLOSED 5m candle extends beyond the direction-side
  BOLL outer band by >=0.10 * Wilder ATR(14), valid only for the current 15m opportunity;
- retain 4H aligned as a mandatory hard gate +1, 5m MACD explicit-adverse-only gate
  with +1 improvement score, RSI/Volume gates, threshold 6 and fixed 1x LIMIT entry;
- add production PEE4: first four hours only, disabled forever after MFE >= +0.60R,
  tiered Boolean reverse-logic at -0.60/-0.70/-0.80R, followed by a 60-minute
  global new-entry lock after an accepted PEE4 market-close request;
- synchronize the UI: removed indicators disappear, BOLL5 overextension is visible,
  KDJ raw rows are removed, and a compact PEE4 panel sits directly below Trade Plan.

The existing V1.6.8 Build1681 network/write ambiguity, account, reconciliation and
Final Entry Guard safety stack is intentionally preserved underneath this overlay.
"""
from __future__ import annotations

import math
import re
import time
import tkinter as tk
import uuid

from v168_build1681_patch import apply as apply_previous
apply_previous()

import app
import core
import engine
import exchange
import strategy
import visual
import v138_strategy_patch as v138
import v152_model as signal_core
import v161_ui_status_patch as ui161
import v165_model as model
import v165_ui_patch as ui165
import v165_update_patch as runtime
import v166_ui_patch as ui166
import v168_update_patch as v168
import v168_build1681_patch as b1681

VERSION = "1.7.0"
BUILD = "1700"
THRESHOLD = 6.0
BOLL5_OVEREXT_ATR = 0.10
BOLL5_OVEREXT_SCORE = 1.0
PEE4_MAX_SECONDS = 4 * 60 * 60
PEE4_MFE_CUTOFF_R = 0.60
PEE4_LOCK_SECONDS = 60 * 60
PEE4_STRUCTURE_BREAK_ATR = 0.25
PEE4_VOLUME_RATIO = 1.20
PEE4_EVAL_SECONDS = 55.0

GREEN = app.GREEN
RED = app.RED
MUTED = app.MUTED
PANEL_ALT = app.PANEL_ALT
TEXT = "#eef5f7"
AMBER = "#e6bd65"

_PREVIOUS_MODEL_EVALUATE = model.evaluate
_PREVIOUS_EXECUTION_CHECKS = model.execution_checks
_PREVIOUS_PRE_SUBMIT_GUARD = runtime._pre_submit_guard
_PREVIOUS_RUNTIME_TEXT = runtime._rewrite_runtime_text
_PREVIOUS_SUBMIT = v138._submit_initial
_PREVIOUS_ENGINE_CYCLE = engine.Engine.cycle
_PREVIOUS_CANDLES = exchange.Exchange.candles
_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_EMIT = app.App.emit
_PREVIOUS_APP_DRAIN = app.App.drain
_PREVIOUS_STATUS_SNAPSHOT = ui161._status_snapshot
_PREVIOUS_RENDER_STATUS = ui161._render_status


def _finite(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _reprice(row):
    layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
    row["layers"] = layers
    raw = 0.0
    for value in layers.values():
        number = _finite(value)
        if number is not None:
            raw += number
    total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
    row["raw"] = raw
    row["total"] = total
    row["required"] = THRESHOLD
    reason = str(row.get("reason") or "")
    reason = re.sub(r"评分\s+[0-9.]+/10", f"评分 {total:g}/10", reason)
    row["reason"] = reason
    return total


def _boll5_state(side, five):
    if side not in ("做多", "做空") or not isinstance(five, (list, tuple)) or len(five) < 201:
        return False, None
    ind = core.indicators(five)
    bar = five[-1]
    close = float(bar["c"])
    lower = float(ind["lower"])
    upper = float(ind["upper"])
    atr14 = float(ind["atr"])
    if not all(math.isfinite(x) for x in (close, lower, upper, atr14)) or atr14 <= 0:
        return False, None
    deviation = max(0.0, lower - close) if side == "做多" else max(0.0, close - upper)
    ratio = deviation / atr14
    return ratio >= BOLL5_OVEREXT_ATR, {
        "five_bar_t": int(bar["t"]),
        "close": close,
        "lower": lower,
        "upper": upper,
        "atr14": atr14,
        "deviation_atr": ratio,
    }


def _rewrite_score_items_v170(row, over_value):
    rewritten = []
    inserted_over = False
    for item in row.get("items") or []:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            rewritten.append(item)
            continue
        values = list(item)
        label = str(values[0])
        if "1H EMA9/26" in label:
            continue
        if "KDJ" in label:
            continue
        if "BOLL超伸" in label or "BOLL Overextension" in label:
            continue
        rewritten.append(tuple(values) if isinstance(item, tuple) else values)
        if "15m BOLL外轨" in label and not inserted_over:
            extra = ("5m BOLL超伸 ≥0.10 ATR14（机会内锁存）", over_value, 1.0)
            rewritten.append(extra if isinstance(item, tuple) else list(extra))
            inserted_over = True
    if not inserted_over:
        rewritten.append(("5m BOLL超伸 ≥0.10 ATR14（机会内锁存）", over_value, 1.0))
    row["items"] = rewritten


def _apply_noema_overext(result, opportunity, five, now_ms):
    if not isinstance(result, dict):
        return result, opportunity

    opp = opportunity if isinstance(opportunity, dict) else None
    opp_side = str((opp or {}).get("side") or "")
    opened = False
    if opp is not None:
        opened = bool(model._signal_window(opp, int(now_ms or 0))[0])
        if opened and opp_side in ("做多", "做空"):
            hit, diag = _boll5_state(opp_side, five)
            if hit:
                opp["boll5_overext010_latched"] = True
                if not isinstance(opp.get("boll5_overext010_first"), dict):
                    opp["boll5_overext010_first"] = dict(diag or {})
            if diag:
                opp["boll5_overext010_latest"] = dict(diag)

    scores = result.get("scores") or {}
    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue
        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
        layers.pop("ema9_26_1h", None)
        active = bool(
            opened
            and side == opp_side
            and isinstance(opp, dict)
            and opp.get("boll5_overext010_latched")
        )
        layers["boll5_overextension"] = BOLL5_OVEREXT_SCORE if active else 0.0
        row["layers"] = layers

        conf = row.setdefault("confirmations", {})
        for key in (
            "1H_ema9", "1H_ema26", "1H_ema9_26_trend_ok",
            "1H_ema_rule", "1H_ema9_26_score_value",
        ):
            conf.pop(key, None)
        conf["1H_ema9_26_score_enabled"] = False
        conf["5m_boll_overextension_enabled"] = True
        conf["5m_boll_overextension_threshold_atr14"] = BOLL5_OVEREXT_ATR
        conf["5m_boll_overextension_latched"] = active
        if side == opp_side and isinstance(opp, dict):
            latest = opp.get("boll5_overext010_latest") or {}
            conf["5m_boll_overextension_latest_atr_ratio"] = latest.get("deviation_atr")
            first = opp.get("boll5_overext010_first") or {}
            conf["5m_boll_overextension_first_atr_ratio"] = first.get("deviation_atr")

        _rewrite_score_items_v170(row, BOLL5_OVEREXT_SCORE if active else 0.0)
        total = _reprice(row)
        row["eligible"] = bool(row.get("gate") and total >= THRESHOLD)
        row["position_multiplier"] = 1.0 if row["eligible"] else 0.0
        if row["eligible"]:
            row["level"] = "V1.7.0 外轨信号 · 1×"
        elif row.get("gate") and total < THRESHOLD:
            row["level"] = "未达开仓线"

        if isinstance(opp, dict) and side == opp_side:
            row["opportunity"] = dict(opp)

    qualified = [
        side for side, row in scores.items()
        if isinstance(row, dict) and row.get("eligible")
    ]
    selected = "观望"
    if len(qualified) == 1:
        selected = qualified[0]
    elif len(qualified) == 2:
        a, b = qualified
        av = float(scores[a].get("total") or 0.0)
        bv = float(scores[b].get("total") or 0.0)
        if av != bv:
            selected = a if av > bv else b
    result["side"] = selected
    if selected != "观望":
        result["why"] = str((scores.get(selected) or {}).get("reason") or "")

    if isinstance(opp, dict):
        result["opportunity"] = dict(opp)
    result.update({
        "strategy_version": VERSION,
        "build": BUILD,
        "threshold": THRESHOLD,
        "1h_ema9_26_score_enabled": False,
        "5m_boll_overextension_score_enabled": True,
        "5m_boll_overextension_threshold_atr14": BOLL5_OVEREXT_ATR,
        "pee_version": "4.0-tiered-boolean",
        "pee4_post_exit_entry_lock_minutes": 60,
    })
    return result, opp


def _evaluate_v170(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                   maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                   now_ms=None, allow_new=True):
    result, opp, transition = _PREVIOUS_MODEL_EVALUATE(
        hour, quarter, five, one, four,
        opportunity=opportunity,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    effective_now = int(
        now_ms if now_ms is not None
        else (int(one[-1]["t"]) + 60_000 if one else 0)
    )
    result, opp = _apply_noema_overext(result, opp, five, effective_now)
    return result, opp, transition


def _execution_checks_v170(plan, opportunity, score):
    ok, diag, blockers = _PREVIOUS_EXECUTION_CHECKS(plan, opportunity, score)
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "build": BUILD,
        "1h_ema9_26_score_enabled": False,
        "5m_boll_overextension_score_enabled": True,
        "5m_boll_overextension_threshold_atr14": BOLL5_OVEREXT_ATR,
    })
    return not list(blockers or []), diag, _dedupe(blockers)


def _lock_remaining(owner):
    store = getattr(owner, "store", None)
    if store is None:
        return 0.0
    until = _finite((store.data or {}).get("pee4_lock_until"), 0.0) or 0.0
    return max(0.0, until - time.time())


def _pre_submit_guard_v170(owner, market, score):
    blockers = list(_PREVIOUS_PRE_SUBMIT_GUARD(owner, market, score) or [])
    remaining = _lock_remaining(owner)
    if remaining > 0:
        blockers.append(f"PEE4退出后全局Lock1H：剩余 {math.ceil(remaining / 60):.0f} 分钟")
    return _dedupe(blockers)


def _rewrite_runtime_text_v170(data):
    text = _PREVIOUS_RUNTIME_TEXT(data)
    if not isinstance(text, str):
        return text
    text = text.replace("V1.6.8", "V1.7.0")
    text = text.replace("Build1681", "Build1700").replace("Build 1681", "Build 1700")
    text = text.replace("Build1680", "Build1700").replace("Build 1680", "Build 1700")
    text = text.replace("1H EMA9/26趋势+1", "1H EMA9/26评分已移除")
    text = text.replace("1H EMA9/26趋势：+1", "1H EMA9/26评分：已移除")
    if text.startswith("自动交易启动；"):
        return (
            "自动交易启动；V1.7.0 Build1700：15m BOLL仅使用最新已收盘上下外轨，外轨2分且信号有效至下一根15m收盘；"
            "删除1H EMA9/26 +1评分；保留15m EMA50方向评分；"
            "当前15m机会内，任一已收盘5m K线超出方向侧BOLL外轨≥0.10×ATR14时锁存+1；"
            "4H同向仍为Hard Gate+1；5m RSI 30-70、Volume<1.20×；5m MACD仅明确逆向禁止，改善+1；"
            "评分≥6，固定1× LIMIT，1×1H ATR止损、2R整仓止盈、No-BE；"
            "持仓前4小时启用PEE4三级Boolean反向退出，MFE≥+0.60R后永久关闭PEE4；实际PEE4退出后全局Lock1H"
        )
    return text


def _candles_v170(self, bar):
    rows = _PREVIOUS_CANDLES(self, bar)
    cache = getattr(self, "_v170_candle_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        self._v170_candle_cache = cache
    cache[str(bar)] = rows
    return rows


def _prior_volume_ratio(rows):
    if not isinstance(rows, (list, tuple)) or len(rows) < 21:
        return 0.0
    history = [float(r["v"]) for r in rows[-21:-1]]
    average = sum(history) / len(history) if history else 0.0
    return float(rows[-1]["v"]) / average if average > 0 else 0.0


def _structure_break_15m(quarter, side):
    if not isinstance(quarter, (list, tuple)) or len(quarter) < 230:
        return False
    try:
        current = core.indicators(quarter)
        previous = core.indicators(quarter[:-1])
        atr15 = float(current["atr"])
        atr_prev = float(previous["atr"])
        if atr15 <= 0 or atr_prev <= 0:
            return False
        prev_rows = list(quarter[max(0, len(quarter) - 221):-1])
        zones = strategy._zones(prev_rows, atr_prev, 160)
        current_close = float(quarter[-1]["c"])
        if side == "做多":
            support = strategy._nearest(zones, float(quarter[-2]["c"]), "support")
            if not support:
                return False
            return (float(support["price"]) - current_close) / atr15 >= PEE4_STRUCTURE_BREAK_ATR
        resistance = strategy._nearest(zones, float(quarter[-2]["c"]), "resistance")
        if not resistance:
            return False
        return (current_close - float(resistance["price"])) / atr15 >= PEE4_STRUCTURE_BREAK_ATR
    except Exception:
        return False


def _macd5_adverse(side, five):
    try:
        hist = signal_core._macd_hist(five)
        a, b, c = map(float, hist[-3:])
    except Exception:
        return False
    return bool(c < b < a) if side == "做多" else bool(c > b > a)


def _pee4_features(side, fill_ms, hold_ms, five, quarter, hour):
    f = core.indicators(five)
    pf = core.indicators(five[:-1])
    q = core.indicators(quarter)
    h = core.indicators(hour)

    fbar, pfbar = five[-1], five[-2]
    qbar, hbar = quarter[-1], hour[-1]
    post5 = int(fbar["t"]) >= int(fill_ms)
    post5_pair = int(pfbar["t"]) >= int(fill_ms)
    post15 = int(qbar["t"]) >= int(fill_ms)
    post1h = int(hbar["t"]) >= int(fill_ms)

    if side == "做多":
        boll_failure = (
            post5_pair
            and float(fbar["c"]) < float(f["lower"])
            and float(pfbar["c"]) < float(pf["lower"])
        )
        trend15 = post15 and bool(q["down"]) and float(qbar["c"]) < float(q["ema20"])
        hour_bad = post1h and bool(h["down"])
        adverse5 = post5 and float(fbar["c"]) < float(fbar["o"]) and _prior_volume_ratio(five) >= PEE4_VOLUME_RATIO
        adverse15 = post15 and float(qbar["c"]) < float(qbar["o"]) and _prior_volume_ratio(quarter) >= PEE4_VOLUME_RATIO
    else:
        boll_failure = (
            post5_pair
            and float(fbar["c"]) > float(f["upper"])
            and float(pfbar["c"]) > float(pf["upper"])
        )
        trend15 = post15 and bool(q["up"]) and float(qbar["c"]) > float(q["ema20"])
        hour_bad = post1h and bool(h["up"])
        adverse5 = post5 and float(fbar["c"]) > float(fbar["o"]) and _prior_volume_ratio(five) >= PEE4_VOLUME_RATIO
        adverse15 = post15 and float(qbar["c"]) > float(qbar["o"]) and _prior_volume_ratio(quarter) >= PEE4_VOLUME_RATIO

    return {
        "opposite_closed_1h": bool(hour_bad and hold_ms >= 120 * 60_000),
        "trend_reversal_15m": bool(trend15),
        "structure_break_15m": bool(post15 and _structure_break_15m(quarter, side)),
        "boll5_persistent_failure": bool(boll_failure),
        "macd5_adverse": bool(post5 and _macd5_adverse(side, five)),
        "adverse_volume": bool(adverse5 or adverse15),
    }


def _pee4_decision(hold_ms, current_r, mfe_r, features):
    if hold_ms < 0 or hold_ms > PEE4_MAX_SECONDS * 1000:
        return None
    if mfe_r >= PEE4_MFE_CUTOFF_R or current_r > -0.60:
        return None
    h1 = bool(features.get("opposite_closed_1h"))
    d15 = bool(features.get("trend_reversal_15m"))
    struct = bool(features.get("structure_break_15m"))
    boll = bool(features.get("boll5_persistent_failure"))
    macd5 = bool(features.get("macd5_adverse"))
    advvol = bool(features.get("adverse_volume"))

    if current_r > -0.70:
        allow = (h1 and d15) or (struct and (h1 or d15 or boll or advvol))
        path = "strict_060_070"
        tier = "严格"
    elif current_r > -0.80:
        allow = (h1 and d15) or struct or (d15 and boll) or (d15 and advvol)
        path = "strong_070_080"
        tier = "强"
    else:
        allow = h1 or struct or boll or (d15 and macd5) or (d15 and advvol)
        path = "ordinary_080_100"
        tier = "普通"
    return {"allow": bool(allow), "path": path, "tier": tier}


def _tier_for_r(current_r):
    if current_r is None or current_r > -0.60:
        return "未进入"
    if current_r > -0.70:
        return "严格"
    if current_r > -0.80:
        return "强"
    return "普通"


def _conditions_text(features):
    if not isinstance(features, dict):
        return "等待持仓指标"
    pairs = (
        ("1H反向", "opposite_closed_1h"),
        ("15m反向", "trend_reversal_15m"),
        ("15m结构破坏", "structure_break_15m"),
        ("BOLL5持续失败", "boll5_persistent_failure"),
        ("MACD5逆向", "macd5_adverse"),
        ("逆向量能", "adverse_volume"),
    )
    return " · ".join(label + (" ✓" if features.get(key) else " —") for label, key in pairs)


def _pee4_status(owner, state, current_r=None, mfe_r=None, tier="—", features=None, path=""):
    remaining = _lock_remaining(owner)
    data = {
        "state": str(state),
        "current_r": current_r,
        "mfe_r": mfe_r,
        "tier": tier,
        "conditions": _conditions_text(features),
        "path": path,
        "lock_remaining_seconds": remaining,
    }
    owner._v170_pee4_status = data
    owner.emit("pee4", data)
    return data


def _position_mfe_r(side, entry, risk, fill_ms, last_price, five):
    if risk <= 0:
        return 0.0
    relevant = [r for r in five if int(r["t"]) + 5 * 60_000 >= int(fill_ms)]
    if side == "做多":
        extreme = max([float(last_price)] + [float(r["h"]) for r in relevant])
        return (extreme - entry) / risk
    extreme = min([float(last_price)] + [float(r["l"]) for r in relevant])
    return (entry - extreme) / risk


def _pee4_submit_close(owner, active, positions, snapshot):
    if active.get("pee4_close_id"):
        return False
    matching = [
        p for p in positions
        if p.get("mgnMode") == "isolated"
        and p.get("posSide") == active.get("posSide")
        and abs(float(p.get("pos") or 0.0)) > 0
    ]
    if len(matching) != 1:
        raise engine.Halt("PEE4退出前仓位无法唯一匹配；不发送平仓请求")
    size = abs(float(matching[0]["pos"]))
    if size <= 0 or size > float(active.get("sz") or size) + 1e-10:
        raise engine.Halt("PEE4退出前仓位数量与本地记录不一致")

    close_id = "p4" + uuid.uuid4().hex[:28]
    active["pee4_close_id"] = close_id
    active["pee4_close_requested_at"] = time.time()
    active["pee4_trigger_snapshot"] = dict(snapshot or {})
    owner.store.save()

    body = {
        "instId": engine.INSTRUMENT,
        "tdMode": "isolated",
        "posSide": active["posSide"],
        "side": "sell" if active["posSide"] == "long" else "buy",
        "ordType": "market",
        "sz": str(size),
        "clOrdId": close_id,
    }
    try:
        reply = owner.x.post("/api/v5/trade/order", body)
    except Exception as exc:
        if getattr(exc, "write_rejected", False):
            active.pop("pee4_close_id", None)
            active.pop("pee4_close_requested_at", None)
            active.pop("pee4_trigger_snapshot", None)
            owner.store.save()
            owner.emit("log", "PEE4市价退出被OKX明确拒绝；未设置Lock1H，保留原TP/SL并等待下一次安全评估")
            return False
        raise

    row = reply[0] if reply else {}
    order_id = str(row.get("ordId") or "") if isinstance(row, dict) else ""
    if not order_id:
        raise engine.Halt("PEE4退出响应缺少ordId；本地已保留唯一平仓ID，禁止重复提交，请核对OKX")

    now = time.time()
    active["pee4_close_order_id"] = order_id
    active["pee4_close_ack_at"] = now
    owner.store.data["pee4_lock_until"] = now + PEE4_LOCK_SECONDS
    owner.store.data["pee4_last_exit_at"] = now
    owner.store.save()
    owner.store.record("PEE4提前退出", {
        "client_id": active.get("client_id"),
        "close_id": close_id,
        "order_id": order_id,
        "side": active.get("side"),
        "current_r": snapshot.get("current_r"),
        "mfe_r": snapshot.get("mfe_r"),
        "tier": snapshot.get("tier"),
        "path": snapshot.get("path"),
    })
    owner.emit(
        "log",
        f"PEE4已触发{snapshot.get('tier','')}档提前退出："
        f"当前 {float(snapshot.get('current_r') or 0):+.2f}R，"
        "已提交唯一市价平仓请求；新开仓全局Lock1H已启动，原交易所TP/SL保留至仓位核对完成",
    )
    return True


def _pee4_cycle(owner):
    store = getattr(owner, "store", None)
    if store is None:
        return
    active = store.data.get("active")
    if not isinstance(active, dict):
        _pee4_status(owner, "未持仓", tier="—")
        return
    if not active.get("pee4_eligible"):
        _pee4_status(owner, "旧仓位 · PEE4不接管", tier="—")
        return
    if not active.get("filled"):
        _pee4_status(owner, "等待开仓成交", tier="—")
        return
    if active.get("pee4_close_id"):
        _pee4_status(
            owner,
            "PEE4退出已提交",
            current_r=_finite((active.get("pee4_trigger_snapshot") or {}).get("current_r")),
            mfe_r=_finite((active.get("pee4_trigger_snapshot") or {}).get("mfe_r")),
            tier=str((active.get("pee4_trigger_snapshot") or {}).get("tier") or "—"),
            features=(active.get("pee4_trigger_snapshot") or {}).get("features"),
            path=str((active.get("pee4_trigger_snapshot") or {}).get("path") or ""),
        )
        return

    now_mono = time.monotonic()
    if now_mono - float(getattr(owner, "_v170_pee4_last_eval", 0.0) or 0.0) < PEE4_EVAL_SECONDS:
        return
    owner._v170_pee4_last_eval = now_mono

    cache = getattr(getattr(owner, "x", None), "_v170_candle_cache", {}) or {}
    five = cache.get("5m")
    quarter = cache.get("15m")
    hour = cache.get("1H")
    if not all(isinstance(rows, list) and len(rows) >= 230 for rows in (five, quarter, hour)):
        _pee4_status(owner, "等待已收盘指标缓存", tier="—")
        return

    fill_time = _finite(active.get("filled_at"), _finite(active.get("submitted"), 0.0)) or 0.0
    hold_seconds = max(0.0, time.time() - fill_time)
    if hold_seconds > PEE4_MAX_SECONDS:
        _pee4_status(owner, "超过4H · PEE4已失效", tier="—")
        return

    try:
        positions = owner.x.positions()
        matching = [
            p for p in positions
            if p.get("mgnMode") == "isolated"
            and p.get("posSide") == active.get("posSide")
            and abs(float(p.get("pos") or 0.0)) > 0
        ]
        if len(matching) != 1:
            _pee4_status(owner, "等待仓位核对", tier="—")
            return
        ticker = owner.x.ticker()
        last = float(ticker["last"])
    except Exception:
        _pee4_status(owner, "行情读取暂不可用", tier="—")
        return

    entry = _finite(matching[0].get("avgPx"), _finite(active.get("px"), 0.0)) or 0.0
    stop = _finite(active.get("sl"), 0.0) or 0.0
    risk = abs(entry - stop)
    if entry <= 0 or risk <= 0:
        _pee4_status(owner, "风险基准不可用", tier="—")
        return

    side = str(active.get("side") or ("做多" if active.get("posSide") == "long" else "做空"))
    direction = 1.0 if side == "做多" else -1.0
    current_r = (last - entry) * direction / risk
    fill_ms = int(fill_time * 1000.0)
    observed_mfe = _position_mfe_r(side, entry, risk, fill_ms, last, five)
    old_mfe = _finite(active.get("pee4_mfe_r"), -math.inf)
    mfe_r = max(observed_mfe, old_mfe if old_mfe is not None else -math.inf)
    if not math.isfinite(mfe_r):
        mfe_r = observed_mfe
    if _finite(active.get("pee4_mfe_r")) != mfe_r:
        active["pee4_mfe_r"] = float(mfe_r)
        store.save()

    if active.get("pee4_disabled_mfe") or mfe_r >= PEE4_MFE_CUTOFF_R:
        if not active.get("pee4_disabled_mfe"):
            active["pee4_disabled_mfe"] = True
            active["pee4_disabled_mfe_at"] = time.time()
            store.save()
        _pee4_status(owner, "MFE≥+0.60R · PEE4已关闭", current_r, mfe_r, "—")
        return

    try:
        features = _pee4_features(side, fill_ms, int(hold_seconds * 1000), five, quarter, hour)
    except Exception:
        _pee4_status(owner, "PEE4指标计算等待", current_r, mfe_r, _tier_for_r(current_r))
        return

    decision = _pee4_decision(int(hold_seconds * 1000), current_r, mfe_r, features)
    tier = _tier_for_r(current_r)
    snapshot = _pee4_status(
        owner,
        "监控中" if decision is None or not decision.get("allow") else "满足退出条件",
        current_r,
        mfe_r,
        decision.get("tier") if decision else tier,
        features,
        decision.get("path") if decision else "",
    )
    snapshot["features"] = dict(features)
    owner._v170_pee4_status = snapshot

    if decision and decision.get("allow"):
        snapshot["state"] = "满足退出条件"
        if _pee4_submit_close(owner, active, positions, snapshot):
            snapshot["state"] = "PEE4退出已提交"
            snapshot["lock_remaining_seconds"] = _lock_remaining(owner)
            owner._v170_pee4_status = snapshot
            owner.emit("pee4", snapshot)


def _cycle_v170(self):
    result = _PREVIOUS_ENGINE_CYCLE(self)
    _pee4_cycle(self)
    return result


def _submit_v170(self, market, score, equity, available, remaining):
    store = getattr(self, "store", None)
    before = store.data.get("active") if store is not None else None
    result = _PREVIOUS_SUBMIT(self, market, score, equity, available, remaining)
    if store is not None:
        active = store.data.get("active")
        if isinstance(active, dict) and (before is None or active is not before):
            active.update({
                "version": VERSION,
                "build": BUILD,
                "pee4_eligible": True,
                "pee4_version": "4.0-tiered-boolean",
                "pee4_mfe_cutoff_r": PEE4_MFE_CUTOFF_R,
                "pee4_monitor_max_hours": 4,
            })
            store.save()
    return result


def _status_snapshot_v170(owner, data):
    snapshot = dict(_PREVIOUS_STATUS_SNAPSHOT(owner, data) or {})
    snapshot.pop("ema1h", None)
    if not isinstance(data, dict):
        snapshot["boll5_overext"] = None
        return snapshot
    _side, row = ui161._candidate(data)
    conf = (row or {}).get("confirmations") or {}
    enabled = bool(conf.get("5m_boll_overextension_enabled"))
    snapshot["boll5_overext"] = bool(conf.get("5m_boll_overextension_latched")) if enabled else None
    return snapshot


def _render_status_v170(owner):
    _PREVIOUS_RENDER_STATUS(owner)
    labels = getattr(owner, "_v161_status_labels", None)
    if not isinstance(labels, dict):
        return
    snapshot = getattr(owner, "_v161_status_snapshot", {}) or {}
    widget = labels.get("boll5_overext")
    if widget is not None:
        value = snapshot.get("boll5_overext")
        text = "5m BOLL超伸 +1：已触发" if value is True else "5m BOLL超伸 +1：未触发" if value is False else "5m BOLL超伸 +1"
        try:
            widget.configure(fg=GREEN if value is True else MUTED, text="● " + text)
        except Exception:
            pass


def _find_trade_plan_location(owner):
    plan_surface = None
    for widget in ui166._walk(owner.root):
        try:
            if isinstance(widget, tk.Label) and str(widget.cget("text") or "") == "交易计划":
                plan_surface = widget.master.master
                break
        except Exception:
            continue
    if plan_surface is None:
        return None, None
    dash = plan_surface.master
    children = list(dash.winfo_children())
    try:
        start = children.index(plan_surface) + 1
    except ValueError:
        start = 0
    detail = next((w for w in children[start:] if isinstance(w, visual.Tabs)), None)
    return dash, detail


def _build_pee4_panel(owner):
    dash, detail = _find_trade_plan_location(owner)
    if dash is None:
        return None
    card = visual.Card(dash, height=178)
    kwargs = {"fill": "x", "pady": (0, 12)}
    if detail is not None:
        kwargs["before"] = detail
    card.pack(**kwargs)

    visual.label(card.body, text="PEE4 风控", size=13, bold=True, color=TEXT).pack(anchor="w")
    visual.label(
        card.body,
        text="首4小时 · MFE≥+0.60R后关闭 · -0.60/-0.70/-0.80R三级Boolean · 退出后全局Lock1H",
        size=9, color=MUTED,
    ).pack(anchor="w", pady=(3, 8))

    values = {
        "state": tk.StringVar(value="未持仓"),
        "r": tk.StringVar(value="—"),
        "tier": tk.StringVar(value="—"),
        "lock": tk.StringVar(value="未锁定"),
        "conditions": tk.StringVar(value="等待持仓指标"),
    }
    row = tk.Frame(card.body, bg=card.body.cget("bg"))
    row.pack(fill="x")
    labels = {}
    for index, (key, title) in enumerate((
        ("state", "状态"), ("r", "当前R / MFE"), ("tier", "当前档位"), ("lock", "Lock1H"),
    )):
        box = tk.Frame(row, bg=PANEL_ALT, padx=10, pady=7)
        box.grid(row=0, column=index, sticky="nsew", padx=(0, 6 if index < 3 else 0))
        row.columnconfigure(index, weight=1, uniform="v170pee4")
        visual.label(box, text=title, size=8, color=MUTED, bg=PANEL_ALT).pack(anchor="w")
        label = visual.label(box, variable=values[key], size=10, bold=True, color=TEXT, bg=PANEL_ALT)
        label.pack(anchor="w", pady=(3, 0))
        labels[key] = label
    condition = visual.label(card.body, variable=values["conditions"], size=9, color=MUTED)
    condition.pack(anchor="w", fill="x", pady=(8, 0))

    owner._v170_pee4_card = card
    owner._v170_pee4_vars = values
    owner._v170_pee4_labels = labels
    owner._v170_pee4_condition_label = condition
    return card


def _remove_retired_indicator_rows(owner):
    matrix = getattr(owner, "matrix", None)
    if matrix is None:
        return
    for key in ("k", "d", "j"):
        try:
            if matrix.exists(key):
                matrix.delete(key)
        except Exception:
            pass


def _render_pee4_panel(owner):
    values = getattr(owner, "_v170_pee4_vars", None)
    if not isinstance(values, dict):
        return
    data = dict(getattr(owner, "_v170_pee4_snapshot", {}) or getattr(getattr(owner, "engine", None), "_v170_pee4_status", {}) or {})
    state = str(data.get("state") or "未持仓")
    current_r = _finite(data.get("current_r"))
    mfe_r = _finite(data.get("mfe_r"))
    tier = str(data.get("tier") or "—")
    remaining = 0.0
    eng = getattr(owner, "engine", None)
    if eng is not None:
        remaining = _lock_remaining(eng)
    if remaining <= 0:
        remaining = max(0.0, _finite(data.get("lock_remaining_seconds"), 0.0) or 0.0)

    values["state"].set(state)
    values["r"].set(
        ("—" if current_r is None else f"{current_r:+.2f}R")
        + " / "
        + ("—" if mfe_r is None else f"MFE {mfe_r:+.2f}R")
    )
    values["tier"].set(tier)
    values["lock"].set(f"剩余 {math.ceil(remaining / 60):.0f} min" if remaining > 0 else "未锁定")
    values["conditions"].set(str(data.get("conditions") or "等待持仓指标"))

    state_label = getattr(owner, "_v170_pee4_labels", {}).get("state")
    if state_label is not None:
        color = RED if "退出" in state or "满足" in state else GREEN if "关闭" in state else AMBER if "监控" in state else MUTED
        try:
            state_label.configure(fg=color)
        except Exception:
            pass
    lock_label = getattr(owner, "_v170_pee4_labels", {}).get("lock")
    if lock_label is not None:
        try:
            lock_label.configure(fg=RED if remaining > 0 else MUTED)
        except Exception:
            pass


def _authorization_text_v170(owner, settings):
    env = "OKX模拟盘" if owner.engine.x.demo else "真实账户"
    base_risk = min(settings.risk_usdt, settings.capital * settings.risk_pct / 100)
    return (
        f"{env}  ·  BTC-USDT-SWAP  ·  逐仓 {settings.leverage}×\n\n"
        "V1.7.0 当前生效策略\n"
        "• BOLL只使用最新已收盘15m上下外轨；外轨2分，信号有效至下一根15m收盘。\n"
        "• 删除1H EMA9/26 +1评分；不作为评分，也不作为Hard Gate。15m EMA50方向评分保留。\n"
        "• 当前15m机会内，已收盘5m K线超出方向侧BOLL外轨≥0.10×ATR14时锁存+1，直到该15m机会失效。\n"
        "• 4H必须同向：Hard Gate +1；5m RSI 30–70；Volume <1.20×；5m MACD仅明确逆向时禁止，改善+1。\n"
        "• 评分≥6；首次开仓固定1× LIMIT；止损=1×1H ATR；整仓2R止盈；No-BE。\n"
        "• PEE4仅监控持仓前4小时；历史MFE达到+0.60R后永久关闭本单PEE4。\n"
        "• PEE4从≤-0.60R启动：严格(-0.60~-0.70)、强(-0.70~-0.80)、普通(-0.80R以下)，仅按1H/15m/结构/BOLL5/MACD/量能Boolean反逻辑退出。\n"
        "• PEE4实际提交退出后，全局禁止新开仓60分钟；平仓请求先持久化唯一ID，网络结果不明确时禁止重复提交。\n\n"
        f"资金预算 {settings.capital:g} USDT  ·  最大名义仓位 {settings.max_notional:g} USDT  ·  "
        f"基础单笔风险≤{base_risk:g} USDT\n\n"
        "确认后将启动自动交易；取消则保持停止新开仓。"
    )


def _show_authorization_dialog_v170(owner, settings):
    result = {"confirmed": False}
    win = tk.Toplevel(owner.root)
    win.title("启动自动交易 · V1.7.0")
    win.configure(bg=visual.BG)
    win.transient(owner.root)
    win.resizable(False, False)
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    outer = tk.Frame(win, bg=visual.BG, padx=18, pady=18)
    outer.pack(fill="both", expand=True)
    card = tk.Frame(outer, bg=visual.PANEL, padx=24, pady=22)
    card.pack(fill="both", expand=True)
    visual.label(card, text="启动自动交易", size=20, bold=True, color=visual.TEXT, bg=visual.PANEL).pack(anchor="w")
    visual.label(card, text="V1.7.0 · 当前正式策略确认", size=11, color=visual.GREEN, bg=visual.PANEL).pack(anchor="w", pady=(5, 16))
    tk.Label(
        card, text=_authorization_text_v170(owner, settings), justify="left", anchor="nw",
        wraplength=620, bg=visual.PANEL, fg=visual.TEXT,
        font=("Helvetica", 11), bd=0, highlightthickness=0,
    ).pack(fill="x", anchor="w")
    buttons = tk.Frame(card, bg=visual.PANEL)
    buttons.pack(fill="x", pady=(22, 0))
    visual.RoundedButton(buttons, text="取消", command=win.destroy, variant="neutral", width=150, height=44, radius=16).pack(side="right")
    def confirm():
        result["confirmed"] = True
        win.destroy()
    visual.RoundedButton(buttons, text="确认", command=confirm, variant="accent", width=150, height=44, radius=16).pack(side="right", padx=(0, 10))
    win.update_idletasks()
    width = 700
    height = max(650, min(830, win.winfo_reqheight()))
    try:
        x = owner.root.winfo_rootx() + max(0, (owner.root.winfo_width() - width) // 2)
        y = owner.root.winfo_rooty() + max(0, (owner.root.winfo_height() - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")
    except Exception:
        win.geometry(f"{width}x{height}")
    win.grab_set()
    win.focus_force()
    owner.root.wait_window(win)
    return bool(result["confirmed"])


def _app_init_v170(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    try:
        self.root.title("KAYTRADE 1.7.0 · BTC 策略控制台 · Build 1700")
    except Exception:
        pass
    try:
        self.signal.set(
            "V1.7.0 · 15m BOLL外轨2分/15m有效期 · BOLL5超伸0.10ATR +1 · "
            "NoEMA1H · 15m EMA50保留 · 4H同向Hard Gate+1 · PEE4 + Lock1H"
        )
    except Exception:
        pass
    for widget in ui166._walk(self.root):
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            continue
        if text.startswith("BTC / USDT"):
            try:
                widget.configure(text="BTC / USDT   ·   V1.7.0")
                self._v170_version_label = widget
            except Exception:
                pass
    _remove_retired_indicator_rows(self)
    _build_pee4_panel(self)
    self._v170_pee4_snapshot = {"state": "未持仓", "tier": "—", "conditions": "等待持仓指标"}
    self._v170_strategy_ui_ready = True


def _app_emit_v170(self, kind, data):
    if kind == "pee4" and isinstance(data, dict):
        self._v170_pee4_snapshot = dict(data)
        return None
    outgoing = _rewrite_runtime_text_v170(data) if isinstance(data, str) else data
    return _PREVIOUS_APP_EMIT(self, kind, outgoing)


def _app_drain_v170(self):
    result = _PREVIOUS_APP_DRAIN(self)
    try:
        _render_pee4_panel(self)
    except Exception:
        pass
    return result


def apply():
    if getattr(model, "_kaytrade_v170_applied", False):
        return

    model.evaluate = _evaluate_v170
    model.execution_checks = _execution_checks_v170
    model.VERSION = VERSION
    model.BUILD = BUILD
    model.THRESHOLD = THRESHOLD

    runtime._pre_submit_guard = _pre_submit_guard_v170
    runtime._rewrite_runtime_text = _rewrite_runtime_text_v170
    runtime.VERSION = VERSION
    runtime.BUILD = BUILD

    v138._submit_initial = _submit_v170
    engine.Engine.cycle = _cycle_v170
    exchange.Exchange.candles = _candles_v170

    ui161._STATUS_ITEMS = tuple(
        item for item in ui161._STATUS_ITEMS if item[0] != "ema1h"
    )
    status_items = list(ui161._STATUS_ITEMS)
    if not any(key == "boll5_overext" for key, _ in status_items):
        index = next((i + 1 for i, (key, _) in enumerate(status_items) if key == "signal_window"), len(status_items))
        status_items.insert(index, ("boll5_overext", "5m BOLL超伸 +1"))
    ui161._STATUS_ITEMS = tuple(status_items)
    ui161._status_snapshot = _status_snapshot_v170
    ui161._render_status = _render_status_v170

    ui165._authorization_text = _authorization_text_v170
    ui165._show_authorization_dialog = _show_authorization_dialog_v170
    ui165.VERSION = VERSION
    ui165.BUILD = BUILD
    ui166.VERSION = VERSION
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.7.0 · BTC 策略控制台 · Build 1700"
    ui166.VERSION_SUBTITLE = "BTC / USDT   ·   V1.7.0"

    v168.VERSION = VERSION
    v168.BUILD = BUILD
    b1681.VERSION = VERSION
    b1681.BUILD = BUILD

    app.App.__init__ = _app_init_v170
    app.App.emit = _app_emit_v170
    app.App.drain = _app_drain_v170

    model._kaytrade_v170_applied = True
    engine.Engine._kaytrade_v170_applied = True
    exchange.Exchange._kaytrade_v170_applied = True
    app.App._kaytrade_v170_applied = True


apply()

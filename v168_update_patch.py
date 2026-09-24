"""KAYTRADE V1.6.8 Build1680 strategy/runtime/UI overlay.

Confirmed V1.6.8 changes:
- strict BOLL source remains latest CLOSED 15m outer band only; no 5m BOLL path;
- one valid 15m outer trigger stays actionable until the NEXT 15m candle closes
  (15-minute lifecycle, replacing Build1671's five-minute execution window);
- 15m BOLL outer score is 2.0 points (was 2.5);
- 4H aligned is a +1 score AND a mandatory Hard Gate; neutral/opposite = 0 and
  cannot open a position;
- add 1H EMA9/EMA26 trend as a +1 auxiliary score: long EMA9>EMA26, short
  EMA9<EMA26; it is not a Hard Gate;
- 4H and 1H EMA scores are visible in real time even while waiting for BOLL;
- preserve V1.6.7 MACD adverse-only gate, MACD +1 momentum score, KDJ removal,
  5m RSI 30..70, Volume <1.20x gate, structure/cost/risk/write protections.
"""
from __future__ import annotations

import math
import re
import tkinter as tk

from core import ema
from v167_15m_boll_only_fix import apply as apply_previous
apply_previous()

import app
import visual
import v153_model as signal_core
import v161_ui_status_patch as ui161
import v164_model as v164
import v165_model as model
import v165_ui_patch as ui165
import v165_update_patch as runtime
import v166_ui_patch as ui166
import v167_strategy_patch as s167
import v167_algo_sync_patch as a167
import v167_15m_boll_only_fix as b1671

VERSION = "1.6.8"
BUILD = "1680"
THRESHOLD = 6.0
ENTRY_WINDOW_MS = 15 * 60 * 1000
BOLL_OUTER_SCORE = 2.0
EMA_FAST = 9
EMA_SLOW = 26

_PREVIOUS_EVALUATE = model.evaluate
_PREVIOUS_EXECUTION_CHECKS = model.execution_checks
_PREVIOUS_PRE_SUBMIT_GUARD = runtime._pre_submit_guard
_PREVIOUS_RUNTIME_TEXT = runtime._rewrite_runtime_text
_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_STATUS_SNAPSHOT = ui161._status_snapshot

GREEN = app.GREEN
RED = app.RED
MUTED = app.MUTED


def _finite(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _signal_window_15m(opportunity, now_ms):
    start = model._signal_close_ms(opportunity)
    now = int(now_ms or 0)
    end = start + ENTRY_WINDOW_MS if start > 0 else 0
    opened = bool(start > 0 and start <= now < end)
    remaining_ms = max(0, end - now) if end else 0
    age_ms = max(0, now - start) if start else 0
    return opened, age_ms, remaining_ms, end


def _window_state_15m(now_ms, opportunity):
    opened, age_ms, remaining_ms, _end = _signal_window_15m(opportunity, now_ms)
    minute = 0
    if opened:
        minute = min(15, max(1, int(age_ms // 60_000) + 1))
    return opened, minute, remaining_ms


def _normalize_opportunity_15m(opportunity):
    if not isinstance(opportunity, dict):
        return opportunity
    opp = dict(opportunity)
    start = model._signal_close_ms(opp)
    if start > 0:
        opp["expires_ms"] = start + ENTRY_WINDOW_MS
        opp["time_window_enabled"] = True
        opp["signal_valid_until_next_15m_close"] = True
        opp.pop("signal_valid_until_next_5m_close", None)
    return opp


def _pin_v168_runtime():
    """Pin the inherited window machinery to one full 15m signal lifecycle."""
    b1671._pin_15m_only()

    model.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    model.TIME_WINDOW_ENABLED = True
    model._signal_window = _signal_window_15m
    model._window_state_5m = _window_state_15m
    model._normalize_opportunity_5m = _normalize_opportunity_15m
    model.BOLL_OUTER_SCORE = BOLL_OUTER_SCORE
    model.BOLL_OUTER_BONUS = 0.0

    v164.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    v164.TIME_WINDOW_ENABLED = True
    v164._signal_window = _signal_window_15m
    v164._window_state_4m = _window_state_15m
    v164._normalize_opportunity_4m = _normalize_opportunity_15m
    v164.BOLL_OUTER_SCORE = BOLL_OUTER_SCORE
    v164.BOLL_OUTER_BONUS = 0.0

    signal_core.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    signal_core._window_state = _window_state_15m
    v164.window_base.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    v164.window_base.TIME_WINDOW_ENABLED = True
    v164.window_base._window_state = _window_state_15m


def _ema9_26(hour):
    if not isinstance(hour, (list, tuple)) or len(hour) < EMA_SLOW:
        return None, None
    closes = [float(row["c"]) for row in hour]
    return float(ema(closes, EMA_FAST)[-1]), float(ema(closes, EMA_SLOW)[-1])


def _ema_trend_ok(side, ema9, ema26):
    if ema9 is None or ema26 is None:
        return False
    if side == "做多":
        return ema9 > ema26
    if side == "做空":
        return ema9 < ema26
    return False


def _four_state(four, side):
    try:
        return str(model.four_hour_state(four, side) or "neutral")
    except Exception:
        return "neutral"


def _is_old_4h_gate_text(text):
    s = str(text or "")
    return (
        "外轨要求4H同向" in s
        or "4H必须与交易方向同向" in s
        or "4H中性/逆向禁止开仓" in s
        or "4H非同向，禁止开仓" in s
    )


def _rewrite_score_items(row, boll_value, trend4h_value, ema1h_value):
    rewritten = []
    inserted_ema = False
    found_4h = False
    found_boll = False
    for item in row.get("items") or []:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            rewritten.append(item)
            continue
        values = list(item)
        label = str(values[0])
        if "BOLL" in label:
            values[0] = "15m BOLL外轨信号（有效至下一根15m收盘）"
            values[1] = boll_value
            values[2] = BOLL_OUTER_SCORE
            found_boll = True
        elif label.startswith("4H") or "4H同向" in label:
            values[0] = "4H同向（Hard Gate，+1）"
            values[1] = trend4h_value
            values[2] = 1.0
            found_4h = True
        elif "1H EMA9/26" in label:
            values[0] = "1H EMA9/26趋势"
            values[1] = ema1h_value
            values[2] = 1.0
            inserted_ema = True
        rewritten.append(tuple(values) if isinstance(item, tuple) else values)
        if found_4h and not inserted_ema:
            rewritten.append(("1H EMA9/26趋势", ema1h_value, 1.0))
            inserted_ema = True
    if not found_boll:
        rewritten.append(("15m BOLL外轨信号（有效至下一根15m收盘）", boll_value, BOLL_OUTER_SCORE))
    if not found_4h:
        rewritten.append(("4H同向（Hard Gate，+1）", trend4h_value, 1.0))
    if not inserted_ema:
        rewritten.append(("1H EMA9/26趋势", ema1h_value, 1.0))
    row["items"] = rewritten


def _reprice(row):
    layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
    layers.pop("kdj5_signal", None)
    layers.pop("volume5", None)
    row["layers"] = layers
    numeric = []
    for value in layers.values():
        n = _finite(value)
        if n is not None:
            numeric.append(n)
    raw = sum(numeric)
    total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
    row["raw"] = raw
    row["total"] = total
    row["required"] = THRESHOLD
    reason = str(row.get("reason") or "")
    reason = reason.replace("2.5分", "2分")
    reason = reason.replace("5m执行窗口", "15m信号有效期")
    reason = re.sub(r"评分\s+[0-9.]+/10", f"评分 {total:g}/10", reason)
    row["reason"] = reason
    return total


def _decorate_scores(result, opportunity, hour, four, now_ms):
    if not isinstance(result, dict):
        return result
    scores = result.get("scores") or {}
    ema9, ema26 = _ema9_26(hour)
    direction = str(result.get("direction") or "")

    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue
        own_opp = opportunity if isinstance(opportunity, dict) and str(opportunity.get("side") or "") == side else None
        valid_boll = bool(own_opp and b1671._valid_15m_evidence(own_opp))
        qstate = _four_state(four, side)
        aligned = qstate == "aligned"
        ema_ok = _ema_trend_ok(side, ema9, ema26)

        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
        layers["trend4h"] = 1.0 if aligned else 0.0
        layers["ema9_26_1h"] = 1.0 if ema_ok else 0.0
        if valid_boll:
            layers["boll_entry"] = BOLL_OUTER_SCORE
        elif "boll_entry" in layers:
            layers["boll_entry"] = 0.0
        row["layers"] = layers

        conf = row.setdefault("confirmations", {})
        required = conf.setdefault("required", {})
        required["outer_4h_aligned"] = aligned
        conf["4H_trend_state"] = qstate
        conf["4H_aligned"] = aligned
        conf["4H_hard_gate_required"] = True
        conf["1H_ema9"] = ema9
        conf["1H_ema26"] = ema26
        conf["1H_ema9_26_trend_ok"] = ema_ok
        conf["1H_ema_rule"] = "long EMA9>EMA26; short EMA9<EMA26; +1 score only"

        opened = False
        remaining_ms = 0
        if valid_boll:
            opened, age_ms, remaining_ms, end_ms = _signal_window_15m(own_opp, now_ms)
            required["signal_window_15m"] = opened
            conf["signal_window_ok"] = opened
            conf["signal_age_ms"] = age_ms
            conf["signal_remaining_ms"] = remaining_ms
            conf["signal_expires_ms"] = end_ms
        else:
            required["signal_window_15m"] = False
            conf["signal_window_ok"] = False
        required.pop("signal_window_5m", None)
        required.pop("signal_window_4m", None)

        blockers = [b for b in conf.get("blockers") or [] if not _is_old_4h_gate_text(b)]
        if valid_boll and not aligned:
            blockers.append("V1.6.8 4H Hard Gate：4H非同向，禁止开仓")
        conf["blockers"] = _dedupe(blockers)

        _rewrite_score_items(
            row,
            BOLL_OUTER_SCORE if valid_boll else 0.0,
            1.0 if aligned else 0.0,
            1.0 if ema_ok else 0.0,
        )
        total = _reprice(row)

        if valid_boll:
            if not aligned:
                row["gate"] = False
                row["eligible"] = False
                row["position_multiplier"] = 0.0
                row["level"] = "4H非同向 · 禁止开仓"
            elif not opened:
                row["gate"] = False
                row["eligible"] = False
                row["position_multiplier"] = 0.0
                row["level"] = "15m BOLL信号已刷新/失效"
            else:
                # Preserve every inherited dynamic gate; only re-evaluate threshold after score changes.
                row["eligible"] = bool(row.get("gate") and total >= THRESHOLD)
                row["position_multiplier"] = 1.0 if row["eligible"] else 0.0
                if row["eligible"]:
                    row["level"] = "V1.6.8外轨信号 · 4H同向 · 1×"
                elif row.get("gate") and total < THRESHOLD:
                    row["level"] = "未达开仓线"
        else:
            # Real-time pre-signal scoring is informational only; BOLL remains mandatory.
            row["gate"] = False
            row["eligible"] = False
            row["position_multiplier"] = 0.0

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
        result["why"] = str(scores[selected].get("reason") or "")

    result.update({
        "strategy_version": VERSION,
        "build": BUILD,
        "threshold": THRESHOLD,
        "score_max": 10.0,
        "entry_window_ms": ENTRY_WINDOW_MS,
        "signal_lifecycle": "closed_15m_outer_valid_until_next_15m_close",
        "boll_outer_score": BOLL_OUTER_SCORE,
        "4h_aligned_hard_gate": True,
        "1h_ema9_26_score_enabled": True,
        "1h_ema9": ema9,
        "1h_ema26": ema26,
    })
    return result


def _evaluate_v168(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                   maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                   now_ms=None, allow_new=True):
    _pin_v168_runtime()
    incoming = _normalize_opportunity_15m(opportunity)
    result, opp, transition = _PREVIOUS_EVALUATE(
        hour, quarter, five, one, four,
        opportunity=incoming,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    _pin_v168_runtime()
    opp = _normalize_opportunity_15m(opp)
    effective_now = int(now_ms if now_ms is not None else (int(one[-1]["t"]) + 60_000 if one else 0))
    if isinstance(result, dict) and isinstance(opp, dict):
        result["opportunity"] = dict(opp)
        opened, age_ms, remaining_ms, end_ms = _signal_window_15m(opp, effective_now)
        result["opportunity_remaining_seconds"] = max(0, int(math.ceil(remaining_ms / 1000.0)))
        result["entry_window_minute"] = min(15, max(1, int(age_ms // 60_000) + 1)) if opened else 0
        for row in (result.get("scores") or {}).values():
            if isinstance(row, dict) and str((row.get("opportunity") or {}).get("id") or "") == str(opp.get("id") or ""):
                row["opportunity"] = dict(opp)
    result = _decorate_scores(result, opp, hour, four, effective_now)
    if isinstance(result, dict):
        result["status"] = _rewrite_runtime_text_v168(result.get("status"))
        result["why"] = _rewrite_runtime_text_v168(result.get("why"))
    return result, opp, transition


def _execution_checks_v168(plan, opportunity, score):
    _pin_v168_runtime()
    opportunity = _normalize_opportunity_15m(opportunity)
    ok, diag, blockers = _PREVIOUS_EXECUTION_CHECKS(plan, opportunity, score)
    diag = dict(diag or {})
    blockers = [_rewrite_runtime_text_v168(x) for x in (blockers or [])]
    conf = (score or {}).get("confirmations") or {}
    if str(conf.get("4H_trend_state") or "") != "aligned":
        blockers.append("V1.6.8开仓禁止：4H非同向 Hard Gate")
    now_ms = int(diag.get("now_ms") or 0)
    if now_ms > 0 and isinstance(opportunity, dict):
        opened, age_ms, remaining_ms, end_ms = _signal_window_15m(opportunity, now_ms)
        diag.update(signal_window_ok=opened, signal_age_ms=age_ms,
                    signal_remaining_ms=remaining_ms, signal_expires_ms=end_ms)
        if not opened:
            blockers.append("V1.6.8开仓禁止：15m BOLL信号已到下一根15m收盘，必须等待新15m信号")
    diag.update({
        "strategy_version": VERSION,
        "build": BUILD,
        "entry_window_ms": ENTRY_WINDOW_MS,
        "signal_lifecycle": "closed_15m_outer_valid_until_next_15m_close",
        "boll_outer_score": BOLL_OUTER_SCORE,
        "4h_aligned_hard_gate": True,
        "1h_ema9_26_score_enabled": True,
    })
    blockers = _dedupe(blockers)
    return not blockers, diag, blockers


def _pre_submit_guard_v168(owner, market, score):
    blockers = [_rewrite_runtime_text_v168(x) for x in (_PREVIOUS_PRE_SUBMIT_GUARD(owner, market, score) or [])]
    conf = (score or {}).get("confirmations") or {}
    required = conf.get("required") or {}
    if str(conf.get("4H_trend_state") or "") != "aligned":
        blockers.append("V1.6.8 Final Entry Guard：4H非同向，禁止开仓")
    if required.get("signal_window_15m") is not True:
        blockers.append("V1.6.8 Final Entry Guard：15m BOLL信号未处于本15m有效周期，禁止开仓")
    return _dedupe(blockers)


def _rewrite_runtime_text_v168(data):
    text = _PREVIOUS_RUNTIME_TEXT(data)
    if not isinstance(text, str):
        return text
    for old in ("V1.6.7", "V1.6.6", "V1.6.5", "V1.6.4"):
        text = text.replace(old, VERSION)
    for old in ("Build1671", "Build1670"):
        text = text.replace(old, "Build1680")
    for old in ("Build 1671", "Build 1670"):
        text = text.replace(old, "Build 1680")
    text = text.replace("15m BOLL外轨信号2.5分", "15m BOLL外轨信号2分")
    text = text.replace("15m BOLL外轨2.5分", "15m BOLL外轨2分")
    text = text.replace("外轨2.5分", "外轨2分")
    text = text.replace("BOLL +2.5", "BOLL +2")
    text = text.replace("15m BOLL触发后的5分钟执行窗口", "15m BOLL信号有效至下一根15m收盘")
    text = text.replace("15m触发后的5分钟执行窗口", "15m BOLL信号有效至下一根15m收盘")
    text = text.replace("15m BOLL触发的5m执行窗口", "15m BOLL信号有效至下一根15m收盘")
    text = text.replace("本根15m BOLL外轨触发的5m执行窗口已结束", "本根15m BOLL信号已到下一根15m收盘")
    text = text.replace("5m执行窗口", "15m BOLL信号有效期")
    text = text.replace("等待下一根15m收盘重新判断BOLL外轨", "等待下一根15m收盘重新判断BOLL外轨")
    if text.startswith("自动交易启动；"):
        return (
            "自动交易启动；V1.6.8 Build1680：BOLL仅使用最新已收盘15m上下外轨，外轨触发计2分；"
            "15m BOLL信号从触发K线收盘起持续有效至下一根15m K线收盘；5m不参与BOLL判断，只刷新RSI30-70、MACD与Volume；"
            "4H必须与交易方向同向，满足Hard Gate并+1分，4H中性/逆向直接禁止开仓；"
            "新增1H EMA9/26趋势评分：做多EMA9>EMA26、做空EMA9<EMA26时+1分，不作为Hard Gate；"
            "5m MACD仅明确逆向时禁止开仓，改善保留+1分；KDJ不计分；Volume必须<1.20×；评分≥6及结构/成本/止损/风险保护保持不变"
        )
    return text


def _status_snapshot_v168(owner, data):
    snapshot = dict(_PREVIOUS_STATUS_SNAPSHOT(owner, data) or {})
    snapshot["ema1h"] = None
    if not isinstance(data, dict):
        return snapshot
    direction = str(data.get("direction") or "")
    row = (data.get("scores") or {}).get(direction) if direction in ("做多", "做空") else None
    if not isinstance(row, dict):
        snapshot["outer_4h"] = None
        snapshot["signal_window"] = False
        return snapshot
    conf = row.get("confirmations") or {}
    qstate = str(conf.get("4H_trend_state") or "")
    snapshot["outer_4h"] = True if qstate == "aligned" else False if qstate else None
    ema_ok = conf.get("1H_ema9_26_trend_ok")
    snapshot["ema1h"] = bool(ema_ok) if ema_ok is not None else None
    opp = row.get("opportunity") or data.get("opportunity")
    snapshot["signal_window"] = bool(conf.get("signal_window_ok")) if isinstance(opp, dict) and opp else False
    return snapshot


def _render_status_v168(owner):
    labels = getattr(owner, "_v161_status_labels", None)
    if not isinstance(labels, dict):
        return
    snapshot = getattr(owner, "_v161_status_snapshot", {}) or {}
    static = dict(ui161._STATUS_ITEMS)
    for key, widget in labels.items():
        value = snapshot.get(key)
        if key == "ema1h":
            color = GREEN if value is True else MUTED
        else:
            color = GREEN if value is True else RED if value is False else MUTED
        text = static.get(key, key)
        if key == "outer_4h":
            text = "4H同向 Hard Gate：通过" if value is True else "4H非同向：禁止开仓" if value is False else "4H同向 Hard Gate"
        elif key == "ema1h":
            text = "1H EMA9/26趋势：+1" if value is True else "1H EMA9/26趋势：0" if value is False else "1H EMA9/26趋势"
        elif key == "signal_window":
            text = "15m BOLL信号：有效" if value is True else "15m BOLL信号：等待触发" if value is False else "15m BOLL信号有效"
        elif key == "volume_gate":
            text = "Volume：允许开仓" if value is True else "Volume：禁止开仓" if value is False else "Volume<1.20×"
        try:
            widget.configure(fg=color, text="● " + text)
        except Exception:
            pass


def _authorization_text_v168(owner, settings):
    env = "OKX模拟盘" if owner.engine.x.demo else "真实账户"
    base_risk = min(settings.risk_usdt, settings.capital * settings.risk_pct / 100)
    return (
        f"{env}  ·  BTC-USDT-SWAP  ·  逐仓 {settings.leverage}×\n\n"
        "V1.6.8 当前生效策略\n"
        "• 所有技术指标仅使用各自周期最新已收盘K线。\n"
        "• 1H / 15m 确认主方向；BOLL只使用最新已收盘15m上下外轨，中轨不触发开仓。\n"
        "• 15m BOLL外轨触发=2分；信号从该15m K线收盘起有效至下一根15m K线收盘。\n"
        "• 4H必须与交易方向同向：同向+1分并通过Hard Gate；中性/逆向均禁止开仓。\n"
        "• 1H EMA9/26趋势为新增+1评分：做多EMA9>EMA26，做空EMA9<EMA26；不作为Hard Gate。\n"
        "• 5m RSI必须30–70；5m MACD只有明确逆向/恶化时禁止开仓，动能改善继续+1分。\n"
        "• 5m Volume必须 < 前20根5m均量1.20×；Volume不计分。KDJ不计分。\n"
        "• 评分必须≥6；前方强结构≥1.5R、成本≤0.30R、止损结构及入场偏离限制继续生效。\n"
        "• Final Entry Guard下单前再次检查15m信号有效期、4H同向及全部动态Hard Gate。\n"
        "• 首次开仓固定1× LIMIT；止损=1×1H ATR；整仓2R止盈；No-BE。\n\n"
        f"资金预算 {settings.capital:g} USDT  ·  最大名义仓位 {settings.max_notional:g} USDT  ·  "
        f"基础单笔风险≤{base_risk:g} USDT\n\n"
        "确认后将启动自动交易；取消则保持停止新开仓。"
    )


def _show_authorization_dialog_v168(owner, settings):
    result = {"confirmed": False}
    win = tk.Toplevel(owner.root)
    win.title("启动自动交易 · V1.6.8")
    win.configure(bg=visual.BG)
    win.transient(owner.root)
    win.resizable(False, False)
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    outer = tk.Frame(win, bg=visual.BG, padx=18, pady=18)
    outer.pack(fill="both", expand=True)
    card = tk.Frame(outer, bg=visual.PANEL, padx=24, pady=22)
    card.pack(fill="both", expand=True)
    visual.label(card, text="启动自动交易", size=20, bold=True, color=visual.TEXT, bg=visual.PANEL).pack(anchor="w")
    visual.label(card, text="V1.6.8 · 当前正式策略确认", size=11, color=visual.GREEN, bg=visual.PANEL).pack(anchor="w", pady=(5, 16))
    tk.Label(card, text=_authorization_text_v168(owner, settings), justify="left", anchor="nw",
             wraplength=600, bg=visual.PANEL, fg=visual.TEXT,
             font=("Helvetica", 11), bd=0, highlightthickness=0).pack(fill="x", anchor="w")
    buttons = tk.Frame(card, bg=visual.PANEL)
    buttons.pack(fill="x", pady=(22, 0))
    visual.RoundedButton(buttons, text="取消", command=win.destroy, variant="neutral", width=150, height=44, radius=16).pack(side="right")
    def confirm():
        result["confirmed"] = True
        win.destroy()
    visual.RoundedButton(buttons, text="确认", command=confirm, variant="accent", width=150, height=44, radius=16).pack(side="right", padx=(0, 10))
    win.update_idletasks()
    width = 680
    height = max(620, min(800, win.winfo_reqheight()))
    try:
        x = owner.root.winfo_rootx() + max(0, (owner.root.winfo_width() - width) // 2)
        y = owner.root.winfo_rooty() + max(0, (owner.root.winfo_height() - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")
    except Exception:
        win.geometry(f"{width}x{height}")
    win.grab_set()
    win.focus_force()
    owner._v168_authorization_dialog = win
    owner.root.wait_window(win)
    return bool(result["confirmed"])


def _app_init_v168(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    try:
        self.root.title("KAYTRADE 1.6.8 · BTC 策略控制台 · Build 1680")
    except Exception:
        pass
    try:
        self.signal.set(
            "V1.6.8 · 15m BOLL外轨2分/有效至下一根15m收盘 · 4H同向Hard Gate+1 · "
            "1H EMA9/26趋势+1 · 5m RSI30–70 · MACD非逆向（改善+1） · Volume<1.20× · KDJ不计分"
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
                widget.configure(text="BTC / USDT   ·   V1.6.8")
                self._v168_version_label = widget
            except Exception:
                pass
    self._v168_strategy_ui_ready = True


def apply():
    if getattr(model, "_kaytrade_v168_applied", False):
        return
    _pin_v168_runtime()

    model.evaluate = _evaluate_v168
    model.execution_checks = _execution_checks_v168
    model.VERSION = VERSION
    model.BUILD = BUILD
    model.THRESHOLD = THRESHOLD
    model.SCORE_MAX = 10.0
    model.BOLL_OUTER_SCORE = BOLL_OUTER_SCORE
    model.BOLL_OUTER_BONUS = 0.0
    model.BOLL_TRIGGER_TIMEFRAME = "15m"
    model.FIVE_MINUTE_BOLL_ENABLED = False

    runtime._pre_submit_guard = _pre_submit_guard_v168
    runtime._rewrite_runtime_text = _rewrite_runtime_text_v168
    runtime.VERSION = VERSION
    runtime.BUILD = BUILD

    ui161._STATUS_ITEMS = (
        ("score", "评分≥6"),
        ("direction", "1H/15m主方向"),
        ("boll", "15m BOLL外轨触发"),
        ("signal_window", "15m BOLL信号有效"),
        ("rsi_gate", "5m RSI 30–70"),
        ("outer_4h", "4H同向 Hard Gate"),
        ("ema1h", "1H EMA9/26趋势"),
        ("macd_clean", "MACD非逆向"),
        ("entry_drift", "入场未偏离"),
        ("front_space", "前方≥1.5R"),
        ("stop_structure", "止损结构"),
        ("cost", "成本≤0.30R"),
        ("adverse_4h", "4H结构安全"),
        ("daily_risk", "日内风险"),
        ("volume_gate", "Volume<1.20×"),
        ("clock", "时钟同步"),
    )
    ui161._status_snapshot = _status_snapshot_v168
    ui161._render_status = _render_status_v168

    ui165._authorization_text = _authorization_text_v168
    ui165._show_authorization_dialog = _show_authorization_dialog_v168
    ui165.VERSION = VERSION
    ui165.BUILD = BUILD

    ui166.VERSION = VERSION
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.6.8 · BTC 策略控制台 · Build 1680"
    ui166.VERSION_SUBTITLE = "BTC / USDT   ·   V1.6.8"

    s167.BUILD = BUILD
    a167.BUILD = BUILD
    app.App.__init__ = _app_init_v168

    model._kaytrade_v168_applied = True
    app.App._kaytrade_v168_applied = True


apply()

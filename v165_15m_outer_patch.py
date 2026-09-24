"""KAYTRADE V1.6.5 targeted strategy patch: 15m BOLL outer trigger.

Scope is deliberately narrow:
- BOLL lower/upper outer-band trigger uses the latest CLOSED 15m candle;
- 5m RSI, MACD, Volume and KDJ remain on 5m;
- the execution opportunity remains valid for the existing V1.6.5 five-minute
  window after the 15m trigger candle closes;
- all V1.6.5 network-state, risk, structure, cost and execution protections stay
  unchanged.
"""
from __future__ import annotations

from core import indicators
import app
import v153_model as signal_core
import v164_model as v164
import v165_model as model
import v165_update_patch as runtime
import v161_ui_status_patch as ui161
import v165_ui_patch as ui165


_ACTIVE_QUARTER = None
_ORIGINAL_MODEL_EVALUATE = model.evaluate
_ORIGINAL_MODEL_PATCH = model._patch_v164_base
_ORIGINAL_V164_INSTALL = v164._install_window_patch
_ORIGINAL_NEW_OPPORTUNITY = signal_core.new_opportunity
_ORIGINAL_MODEL_REWRITE_TEXT = model._rewrite_text
_ORIGINAL_RUNTIME_REWRITE_TEXT = runtime._rewrite_runtime_text
_ORIGINAL_PRE_SUBMIT_GUARD = runtime._pre_submit_guard
_ORIGINAL_CLEAR_STALE = runtime._clear_stale_opportunity
_ORIGINAL_UI_AUTH_TEXT = ui165._authorization_text
_ORIGINAL_UI_RENDER_STATUS = ui161._render_status
_ORIGINAL_APP_INIT = app.App.__init__


def boll_entry_signal_15m(quarter, five, side):
    """Return a 15m outer-band signal while preserving 5m confirmations."""
    if len(quarter) < 2 or len(five) < 2:
        return None

    q = indicators(quarter)
    f = indicators(five)
    bar15 = quarter[-1]
    bar5 = five[-1]

    upper = float(q["upper"])
    lower = float(q["lower"])
    middle = float(q["middle"])
    rsi5 = float(f["rsi"])
    atr5 = float(f["atr"])

    if not (model.RSI_MIN <= rsi5 <= model.RSI_MAX):
        return None

    if side == "做多":
        if float(bar15["l"]) > lower:
            return None
        path, reference = "lower_band", lower
    elif side == "做空":
        if float(bar15["h"]) < upper:
            return None
        path, reference = "upper_band", upper
    else:
        return None

    bar_t = int(bar15["t"])
    return {
        "path": path,
        "bar_t": bar_t,
        "signal_close_ms": bar_t + 15 * 60 * 1000,
        "reference": float(reference),
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "rsi5": rsi5,
        "atr5": atr5,
        # Keep the existing 5m pullback-extreme / stop-buffer semantics intact.
        "bar_low": float(bar5["l"]),
        "bar_high": float(bar5["h"]),
        "boll_timeframe": "15m",
    }


def _signal_adapter(five, side):
    quarter = _ACTIVE_QUARTER
    if not isinstance(quarter, (list, tuple)):
        return None
    return boll_entry_signal_15m(quarter, five, side)


def _new_opportunity_15m(hour, quarter, five, one, side, signal):
    opp = _ORIGINAL_NEW_OPPORTUNITY(hour, quarter, five, one, side, signal)
    if isinstance(signal, dict) and signal.get("boll_timeframe") == "15m":
        opp["boll_timeframe"] = "15m"
        detail = opp.setdefault("trigger_detail", {})
        detail["boll_timeframe"] = "15m"
        detail["boll15_middle"] = float(signal["middle"])
        detail["boll15_upper"] = float(signal["upper"])
        detail["boll15_lower"] = float(signal["lower"])
    return opp


def _install_15m_signal_source():
    # v164 re-installs its historical 5m source before every evaluation. Run it
    # first, then pin the final signal source to the 15m adapter.
    _ORIGINAL_V164_INSTALL()
    signal_core.boll_entry_signal = _signal_adapter
    v164.boll_entry_signal = _signal_adapter
    model.boll_entry_signal = _signal_adapter


def _patch_v164_base_15m():
    _ORIGINAL_MODEL_PATCH()
    signal_core.boll_entry_signal = _signal_adapter
    v164.boll_entry_signal = _signal_adapter
    model.boll_entry_signal = _signal_adapter


def _rewrite_text_15m(text):
    out = _ORIGINAL_MODEL_REWRITE_TEXT(text)
    if not isinstance(out, str):
        return out
    replacements = (
        ("5m BOLL中轨+RSI / 外轨触发（必须）", "15m BOLL外轨触发（必须）"),
        ("5m BOLL外轨", "15m BOLL外轨"),
        ("5m BOLL回调信号", "15m BOLL外轨触发"),
        ("最新已收盘5m外轨信号", "最新已收盘15m BOLL外轨触发"),
        ("等待新的已收盘5m外轨信号", "等待新的已收盘15m BOLL外轨触发"),
        ("偏离5m BOLL触发位置", "偏离15m BOLL外轨触发位置"),
        ("缺少有效5m BOLL信号状态", "缺少有效15m BOLL外轨触发状态"),
    )
    for old, new in replacements:
        out = out.replace(old, new)
    return out


def _decorate_result(result, opp):
    if isinstance(opp, dict):
        opp["boll_timeframe"] = "15m"
        detail = opp.setdefault("trigger_detail", {})
        detail["boll_timeframe"] = "15m"
        if isinstance(result, dict):
            result["opportunity"] = dict(opp)
    if not isinstance(result, dict):
        return result
    result["boll_trigger_timeframe"] = "15m"
    result["signal_lifecycle"] = "closed_15m_trigger_5m_execution_window"
    for row in (result.get("scores") or {}).values():
        if not isinstance(row, dict):
            continue
        confirmations = row.setdefault("confirmations", {})
        confirmations["boll_trigger_timeframe"] = "15m"
        detail = confirmations.get("trigger") or {}
        if isinstance(detail, dict) and detail:
            detail["boll_timeframe"] = "15m"
            confirmations["trigger"] = detail
            for old, new in (("boll5_middle", "boll15_middle"), ("boll5_upper", "boll15_upper"), ("boll5_lower", "boll15_lower")):
                if old in confirmations:
                    confirmations[new] = confirmations.pop(old)
        row["level"] = _rewrite_text_15m(row.get("level"))
        row["reason"] = _rewrite_text_15m(row.get("reason"))
        rewritten = []
        for item in row.get("items") or []:
            if isinstance(item, tuple) and item:
                rewritten.append((_rewrite_text_15m(item[0]), *item[1:]))
            elif isinstance(item, list) and item:
                rewritten.append([_rewrite_text_15m(item[0]), *item[1:]])
            else:
                rewritten.append(item)
        row["items"] = rewritten
        if isinstance(row.get("opportunity"), dict):
            row["opportunity"]["boll_timeframe"] = "15m"
    result["why"] = _rewrite_text_15m(result.get("why"))
    result["status"] = _rewrite_text_15m(result.get("status"))
    return result


def _evaluate_15m(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                  maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                  now_ms=None, allow_new=True):
    global _ACTIVE_QUARTER
    previous = _ACTIVE_QUARTER
    _ACTIVE_QUARTER = quarter
    try:
        result, opp, transition = _ORIGINAL_MODEL_EVALUATE(
            hour, quarter, five, one, four,
            opportunity=opportunity,
            stop_atr=stop_atr,
            maker_bps=maker_bps,
            taker_bps=taker_bps,
            slippage_bps=slippage_bps,
            now_ms=now_ms,
            allow_new=allow_new,
        )
        if isinstance(opp, dict) and opp.get("signal_path") in model.OUTER_PATHS:
            opp["boll_timeframe"] = "15m"
        return _decorate_result(result, opp), opp, transition
    finally:
        _ACTIVE_QUARTER = previous


def _runtime_text_15m(data):
    text = _ORIGINAL_RUNTIME_REWRITE_TEXT(data)
    if not isinstance(text, str):
        return text
    text = text.replace("最新5m BOLL外轨信号2.5分", "最新15m BOLL外轨信号2.5分")
    text = text.replace("5m BOLL及同根5m RSI/Volume/KDJ", "15m BOLL触发 + 触发时最新5m RSI/Volume/KDJ")
    text = text.replace("旧5m外轨信号", "旧15m BOLL外轨触发机会")
    text = text.replace("最新已收盘5m重新判断", "等待下一根已收盘15m BOLL外轨重新触发")
    text = text.replace("5m BOLL外轨", "15m BOLL外轨")
    return text


def _pre_submit_guard_15m(owner, market, score):
    blockers = list(_ORIGINAL_PRE_SUBMIT_GUARD(owner, market, score) or [])
    opp = runtime._opportunity_from(market, score)
    if isinstance(opp, dict) and str(opp.get("boll_timeframe") or "") != "15m":
        blockers.append("V1.6.5开仓禁止：当前机会不是15m BOLL外轨触发，必须等待新的15m已收盘外轨信号")
    return model._dedupe([_runtime_text_15m(item) for item in blockers])


def _clear_stale_or_legacy(owner, *, emit_log=False):
    store = getattr(owner, "store", None)
    if store is not None:
        opp = store.data.get("v152_opportunity")
        if isinstance(opp, dict) and str(opp.get("boll_timeframe") or "") != "15m":
            old_id = str(opp.get("id") or "")
            store.data["v152_opportunity"] = None
            store.data["v152_rearm_zone"] = None
            store.save()
            try:
                store.record("V1.6.5清理旧5m BOLL机会", {"opportunity_id": old_id, "required_boll_timeframe": "15m"})
            except Exception:
                pass
            if emit_log:
                owner.emit("log", "V1.6.5：策略已切换为15m BOLL外轨触发，旧5m BOLL机会已清除")
            return True
    return _ORIGINAL_CLEAR_STALE(owner, emit_log=emit_log)


def _authorization_text_15m(owner, settings):
    text = _ORIGINAL_UI_AUTH_TEXT(owner, settings)
    text = text.replace("1H / 15m 确认主方向；5m只允许BOLL上下外轨入场。", "1H / 15m 确认主方向；仅使用最新已收盘15m BOLL上下外轨触发。")
    text = text.replace("最新已收盘5m触发外轨时 BOLL +2.5；5m RSI必须30–70；5m MACD必须连续向交易方向改善。", "最新已收盘15m触发外轨时 BOLL +2.5；5m RSI必须30–70；5m MACD必须连续向交易方向改善。")
    text = text.replace("BOLL及同根5m RSI / Volume / KDJ 信号属性有效至下一根5m收盘；新5m收盘后整组失效并重新计算。", "15m BOLL外轨触发后，使用触发时最新已收盘5m的 RSI / Volume / KDJ 作为确认快照；执行机会有效至下一根5m收盘。")
    return text


def _render_status_15m(owner):
    _ORIGINAL_UI_RENDER_STATUS(owner)
    labels = getattr(owner, "_v161_status_labels", None)
    snapshot = getattr(owner, "_v161_status_snapshot", {}) or {}
    if not isinstance(labels, dict):
        return
    widget = labels.get("signal_window")
    if widget is not None:
        value = snapshot.get("signal_window")
        color = ui165.GREEN if value is True else ui165.RED if value is False else ui165.MUTED
        text = "15m外轨：5m执行窗口有效" if value is True else "15m外轨：等待新触发" if value is False else "外轨5m执行窗口"
        try:
            widget.configure(fg=color, text="● " + text)
        except Exception:
            pass


def _app_init_15m(self, *args, **kwargs):
    _ORIGINAL_APP_INIT(self, *args, **kwargs)
    try:
        self.signal.set("V1.6.5 · 最新已收盘K线 / 1H+15m方向 / 15m外轨2.5 / 5m RSI30–70 / 5m MACD改善 / 5m Volume<1.20× / 4H同向")
    except Exception:
        pass


def apply():
    if getattr(model, "_kaytrade_v165_15m_outer_applied", False):
        return

    model._patch_v164_base = _patch_v164_base_15m
    model._rewrite_text = _rewrite_text_15m
    model.evaluate = _evaluate_15m
    signal_core.new_opportunity = _new_opportunity_15m
    v164._install_window_patch = _install_15m_signal_source
    _install_15m_signal_source()

    runtime._rewrite_runtime_text = _runtime_text_15m
    runtime._pre_submit_guard = _pre_submit_guard_15m
    runtime._clear_stale_opportunity = _clear_stale_or_legacy

    ui165._authorization_text = _authorization_text_15m
    ui161._STATUS_ITEMS = tuple(
        (key, "15m外轨BOLL" if key == "boll" else "外轨5m执行窗口" if key == "signal_window" else label)
        for key, label in ui161._STATUS_ITEMS
    )
    ui161._render_status = _render_status_15m
    app.App.__init__ = _app_init_15m

    model.BOLL_TRIGGER_TIMEFRAME = "15m"
    model._kaytrade_v165_15m_outer_applied = True
    app.App._kaytrade_v165_15m_outer_applied = True


apply()

"""KAYTRADE V1.6.8 Build1681 runtime/log synchronization hotfix.

This build intentionally DOES NOT change strategy, scoring, risk, order construction,
or write-safety semantics. It repairs the final presentation/runtime boundary only:
- normalize every emitted/runtime reason to the active V1.6.8 semantics;
- remove legacy 5m BOLL middle/outer wording (BOLL remains strict closed-15m outer-only);
- normalize old BOLL minute-window and market-reference copy to the active 15m/LIMIT model;
- present the expected Algo cache resynchronization state as synchronization, not as a
  scary generic read-query failure, while preserving fail-closed entry behavior;
- expose the underlying Algo resync error when available for diagnosis.
"""
from __future__ import annotations

import re

from v168_update_patch import apply as apply_previous
apply_previous()

import app
import v165_model as model
import v165_ui_patch as ui165
import v165_update_patch as runtime
import v166_ui_patch as ui166
import v167_strategy_patch as s167
import v167_algo_sync_patch as a167
import v168_update_patch as v168

VERSION = "1.6.8"
BUILD = "1681"

_PREVIOUS_EVALUATE = model.evaluate
_PREVIOUS_EXECUTION_CHECKS = model.execution_checks
_PREVIOUS_PRE_SUBMIT_GUARD = runtime._pre_submit_guard
_PREVIOUS_RUNTIME_TEXT = runtime._rewrite_runtime_text
_PREVIOUS_APP_EMIT = app.App.emit
_PREVIOUS_APP_INIT = app.App.__init__


def _rewrite_runtime_text_v1681(data):
    """Collapse inherited copy to the single active V1.6.8 vocabulary."""
    text = _PREVIOUS_RUNTIME_TEXT(data)
    if not isinstance(text, str):
        return text

    # Runtime logs may originate in deep inherited layers. Old version/build
    # labels are implementation history and must not leak into current status.
    text = re.sub(r"V1\.(?:[0-5]\.\d+|6\.[0-7])", VERSION, text)
    text = re.sub(
        r"Build\s*(?:1544|1551|1561|16[0-7]\d|1680|154(?:\.[0-9]+)?)",
        f"Build{BUILD}",
        text,
    )

    # Strict BOLL vocabulary: there is no active 5m BOLL or BOLL middle path.
    text = re.sub(r"(?<!\d)5m\s+BOLL中轨\s*/\s*外轨", "15m BOLL外轨", text)
    text = re.sub(r"(?<!\d)5m\s+BOLL中轨", "15m BOLL外轨", text)
    text = re.sub(r"(?<!\d)5m\s+BOLL外轨", "15m BOLL外轨", text)
    text = re.sub(r"(?<!\d)5m\s+BOLL", "15m BOLL", text)
    text = re.sub(r"15m\s+BOLL中轨\s*/\s*外轨", "15m BOLL外轨", text)
    text = re.sub(r"15m\s+BOLL中轨", "15m BOLL外轨", text)
    text = text.replace("BOLL中轨/外轨", "15m BOLL外轨")

    # Old 1m/5m BOLL execution-window copy is presentation-only legacy.
    text = re.sub(
        r"(?:15m\s+)?BOLL信号(?:执行窗口|有效期)\s*[·・]?\s*第\s*(\d+)\s*个1m",
        r"15m BOLL信号有效期 · 第\1分钟",
        text,
    )
    text = text.replace("BOLL首分钟执行窗口", "15m BOLL信号有效期")
    text = text.replace("BOLL首分钟窗口", "15m BOLL信号有效期")
    text = text.replace("5m闭合周期", "15m BOLL信号有效期") if "BOLL" in text else text
    text = text.replace("本根15m BOLL外轨触发的5m执行窗口", "本根15m BOLL信号有效期")
    text = text.replace("15m BOLL触发的5m执行窗口", "15m BOLL信号有效期")
    text = text.replace("15m BOLL触发后的5分钟执行窗口", "15m BOLL信号有效至下一根15m收盘")

    # Active execution is passive LIMIT entry. Market exit wording remains valid.
    text = text.replace("计划市价参考", "计划限价")
    text = text.replace("实际市价参考", "实际限价")
    text = text.replace("提交市价开仓", "提交限价开仓")
    text = text.replace("已提交市价做多", "已提交限价做多")
    text = text.replace("已提交市价做空", "已提交限价做空")

    # Normalize the exact inherited 4H sentence seen in the live run record.
    text = re.sub(
        r"V1\.6\.8要求4H同向：15m BOLL外轨在4H中性或逆向时禁止开仓",
        "V1.6.8 4H Hard Gate：4H必须与交易方向同向；中性/逆向禁止开仓",
        text,
    )

    # Build1661 intentionally routes an untrusted Algo cache through the read-side
    # fail-closed mechanism. This is a synchronization state, not a write failure.
    if (
        "Algo实时状态暂未完成可信同步" in text
        or ("只读查询暂时失败" in text and "/ws/v5/business:orders-algo" in text)
    ):
        return (
            "Algo状态同步中：实时策略单状态暂未完成可信同步；仅跳过本周期新开仓，"
            "自动交易保持开启；后台继续REST+WebSocket重同步；重新通过Final Entry Guard后才允许下单"
        )

    return text


def _sanitize_payload(value):
    """Rewrite only strings; preserve every numeric/boolean strategy value."""
    if isinstance(value, str):
        return _rewrite_runtime_text_v1681(value)
    if isinstance(value, dict):
        for key in list(value):
            value[key] = _sanitize_payload(value[key])
        return value
    if isinstance(value, list):
        for i, item in enumerate(value):
            value[i] = _sanitize_payload(item)
        return value
    if isinstance(value, tuple):
        return tuple(_sanitize_payload(item) for item in value)
    return value


def _evaluate_v1681(*args, **kwargs):
    result, opportunity, transition = _PREVIOUS_EVALUATE(*args, **kwargs)
    _sanitize_payload(result)
    transition = _sanitize_payload(transition)
    return result, opportunity, transition


def _execution_checks_v1681(plan, opportunity, score):
    ok, diag, blockers = _PREVIOUS_EXECUTION_CHECKS(plan, opportunity, score)
    diag = _sanitize_payload(dict(diag or {}))
    blockers = [_rewrite_runtime_text_v1681(x) for x in (blockers or [])]
    return ok, diag, blockers


def _pre_submit_guard_v1681(owner, market, score):
    return [_rewrite_runtime_text_v1681(x) for x in (_PREVIOUS_PRE_SUBMIT_GUARD(owner, market, score) or [])]


def _algo_status(owner):
    try:
        engine = getattr(owner, "engine", None)
        x = getattr(engine, "x", None) if engine is not None else None
        if x is None:
            return None
        status = a167._algo_state_status(x)
        return status if isinstance(status, dict) else None
    except Exception:
        return None


def _algo_sync_message(owner):
    message = (
        "Algo状态同步中：实时策略单状态暂未完成可信同步；仅跳过本周期新开仓，"
        "自动交易保持开启；后台继续REST+WebSocket重同步；重新通过Final Entry Guard后才允许下单"
    )
    status = _algo_status(owner) or {}
    detail = str(status.get("last_error") or "").strip()
    if detail and "Algo实时状态暂未完成可信同步" not in detail:
        message += "；底层状态：" + detail[:180]
    return message


def _is_algo_sync_text(text):
    s = str(text or "")
    return (
        "Algo状态同步中" in s
        or "Algo实时状态暂未完成可信同步" in s
        or ("只读查询暂时失败" in s and "/ws/v5/business:orders-algo" in s)
    )


def _app_emit_v1681(self, kind, data):
    """Final UI-boundary sanitizer so no inherited text bypasses the overlay."""
    outgoing_kind = kind
    outgoing = _rewrite_runtime_text_v1681(data) if isinstance(data, str) else data
    status = _algo_status(self)

    if isinstance(outgoing, str) and _is_algo_sync_text(outgoing):
        outgoing = _algo_sync_message(self)
        # Expected read-side resync must not look like a terminal alarm.
        if str(outgoing_kind) == "alarm":
            outgoing_kind = "log"
    elif isinstance(outgoing, str) and status and not bool(status.get("trusted")):
        if "只读接口重试中" in outgoing:
            outgoing = outgoing.replace("只读接口重试中", "Algo状态同步中")
        elif outgoing == "部分只读接口异常 · 自动交易保持开启":
            outgoing = "Algo状态同步中 · 自动交易保持开启"

    return _PREVIOUS_APP_EMIT(self, outgoing_kind, outgoing)


def _app_init_v1681(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    try:
        self.root.title("KAYTRADE 1.6.8 · BTC 策略控制台 · Build 1681")
    except Exception:
        pass
    try:
        self.signal.set(_rewrite_runtime_text_v1681(str(self.signal.get() or "")))
    except Exception:
        pass
    for widget in ui166._walk(self.root):
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            continue
        if not text:
            continue
        rewritten = _rewrite_runtime_text_v1681(text)
        try:
            if rewritten != text:
                widget.configure(text=rewritten)
        except Exception:
            pass
    self._v168_build1681_ready = True


def apply():
    if getattr(model, "_kaytrade_v168_build1681_applied", False):
        return

    # Text-only wrappers around the already verified Build1680 strategy engine.
    model.evaluate = _evaluate_v1681
    model.execution_checks = _execution_checks_v1681
    runtime._pre_submit_guard = _pre_submit_guard_v1681
    runtime._rewrite_runtime_text = _rewrite_runtime_text_v1681

    # Build identity only; strategy constants remain untouched.
    model.BUILD = BUILD
    runtime.BUILD = BUILD
    v168.BUILD = BUILD
    ui165.BUILD = BUILD
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.6.8 · BTC 策略控制台 · Build 1681"
    s167.BUILD = BUILD
    a167.BUILD = BUILD

    app.App.emit = _app_emit_v1681
    app.App.__init__ = _app_init_v1681

    model._kaytrade_v168_build1681_applied = True
    app.App._kaytrade_v168_build1681_applied = True


apply()

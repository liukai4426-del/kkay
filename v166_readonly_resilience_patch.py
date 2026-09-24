"""KAYTRADE V1.6.6 Build1661 read-only network resilience hotfix.

Goal:
- transient NetworkError on GET/read-only requests must NOT globally stop automatic trading;
- the failing cycle is aborted naturally and the next cycle retries with fresh market state;
- do not set App.network_paused for that read-only failure;
- do not call the persistent/soft-stop halt path for that read-only failure;
- POST/write ambiguity remains fail-closed exactly as before.

This patch does not change strategy, scoring, risk, order construction, order-write
retry semantics, or idempotency protections.
"""
from __future__ import annotations

import time

from v166_ui_patch import apply as apply_previous
apply_previous()

import app
import engine
import exchange
import v166_ui_patch as ui166

BUILD = "1661"
_READ_MARK_TTL = 5.0

_PREVIOUS_REQUEST = exchange.Exchange.request
_PREVIOUS_HALT = engine.Engine.halt


def _marker(exchange_obj):
    value = getattr(exchange_obj, "_v166_last_readonly_failure", None)
    return value if isinstance(value, dict) else None


def _fresh_marker(exchange_obj, reason=None):
    mark = _marker(exchange_obj)
    if not mark:
        return None
    try:
        age = time.monotonic() - float(mark.get("at") or 0.0)
    except Exception:
        return None
    if age < 0 or age > _READ_MARK_TTL:
        return None
    if reason is not None and str(reason) != str(mark.get("message") or ""):
        return None
    return mark


def _set_marker(exchange_obj, exc, method, path):
    if str(getattr(exc, "method", method) or method).upper() != "GET":
        exchange_obj._v166_last_readonly_failure = None
        return
    exchange_obj._v166_last_readonly_failure = {
        "at": time.monotonic(),
        "message": str(exc),
        "path": str(getattr(exc, "path", path) or path or ""),
        "code": str(getattr(exc, "code", "") or ""),
    }


def _request_v166_readonly_resilience(self, method, path, params=None, private=False):
    try:
        result = _PREVIOUS_REQUEST(self, method, path, params, private)
        # A successful request proves any old transient marker is stale.
        if str(method).upper() == "GET":
            self._v166_last_readonly_failure = None
        return result
    except exchange.NetworkError as exc:
        _set_marker(self, exc, method, path)
        raise


def _app_network_paused_get(self):
    return bool(self.__dict__.get("_v166_network_paused", False))


def _app_network_paused_set(self, value):
    want = bool(value)
    if want:
        owner = getattr(self, "engine", None)
        x = getattr(owner, "x", None) if owner is not None else None
        # App.worker sets network_paused=True immediately after catching a
        # NetworkError. If the exception came from a fresh GET marker, suppress
        # only that global pause transition. The following Engine.halt call will
        # consume the exact same marker and soft-handle the cycle.
        if x is not None and _fresh_marker(x) is not None:
            self.__dict__["_v166_network_paused"] = False
            self.__dict__["_v166_readonly_pause_suppressed"] = True
            return
    self.__dict__["_v166_network_paused"] = want
    if not want:
        self.__dict__["_v166_readonly_pause_suppressed"] = False


def _halt_v166_readonly_resilience(self, reason):
    x = getattr(self, "x", None)
    mark = _fresh_marker(x, reason) if x is not None else None
    if mark is None:
        return _PREVIOUS_HALT(self, reason)

    # Consume before emitting anything so a later unrelated failure can never be
    # misclassified as the old read-only exception.
    try:
        x._v166_last_readonly_failure = None
    except Exception:
        pass

    # Keep the user's authorization and auto-entry state intact. The current
    # cycle has already unwound due to the exception, so no write can occur from
    # incomplete data. Force a fresh market evaluation on the next cycle.
    self.market = None
    self.market_at = 0
    self.poll_at = 0
    path = str(mark.get("path") or "只读接口")
    code = str(mark.get("code") or "")
    suffix = f" / OKX {code}" if code else ""
    state = "保持开启" if bool(getattr(self, "enabled", False)) else "保持原状态"

    emit = getattr(self, "emit", None)
    if callable(emit):
        emit(
            "log",
            f"只读查询暂时失败 {path}{suffix}：{reason}；仅终止本周期，自动交易{state}；"
            "下一周期自动重新读取最新状态，只有重新通过Final Entry Guard才允许下单；写请求绝不自动重试",
        )
        if bool(getattr(self, "enabled", False)):
            env = "模拟盘" if bool(getattr(x, "demo", False)) else "实盘"
            emit("status", f"全自动运行 / {env} · 只读接口重试中")
        emit("network", "部分只读接口异常 · 自动交易保持开启")
    return False


def apply():
    if getattr(app.App, "_kaytrade_v166_readonly_resilience_applied", False):
        return

    # Keep visible/package build identity distinct from Build1660.
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = f"KAYTRADE {ui166.VERSION} · BTC 策略控制台 · Build {BUILD}"

    exchange.Exchange.request = _request_v166_readonly_resilience
    engine.Engine.halt = _halt_v166_readonly_resilience
    app.App.network_paused = property(_app_network_paused_get, _app_network_paused_set)

    app.App._kaytrade_v166_readonly_resilience_applied = True
    exchange.Exchange._kaytrade_v166_readonly_resilience_applied = True
    engine.Engine._kaytrade_v166_readonly_resilience_applied = True


apply()

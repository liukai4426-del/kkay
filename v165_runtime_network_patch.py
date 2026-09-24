"""V1.6.5 runtime network-state fix.

Goals:
- pending-algo reads never perform the inner 4-attempt retry loop;
- demo Trigger/Trailing 51054 uses a short circuit breaker instead of repeated reconnects;
- live trading keeps the existing strict multi-scope fail-closed audit;
- non-algo GETs retain the audited retry behavior.

Trading writes are untouched and are still never retried.
"""
from __future__ import annotations

import time

from v165_algo_fallback_patch import apply as apply_previous
apply_previous()

import engine
import exchange
import v165_algo_fallback_patch as fallback
import v165_update_patch as v165

_PATH = v165._ALGO_PENDING_PATH
_FAMILY_LABELS = dict(v165._ALGO_FAMILIES)
_DEMO_FAST_DEGRADE = {"trigger", "move_order_stop"}
_DEMO_BREAKER_SECONDS = 300.0

_PREVIOUS_REQUEST = exchange.Exchange.request


def _is_51054(exc):
    return isinstance(exc, exchange.NetworkError) and str(getattr(exc, "code", "") or "") == "51054"


def _network_request_v165_state(self, method, path, params=None, private=False):
    """Algo reads get one transport attempt; outer family logic owns fallback.

    This removes the nested `4 retries x 3 scopes` behavior that could block the
    single worker for more than a minute and then be mistaken for a Mac sleep.
    """
    algo_type = str((params or {}).get("ordType") or "") if method == "GET" and path == _PATH else ""
    label = _FAMILY_LABELS.get(algo_type, "")
    if not label:
        return _PREVIOUS_REQUEST(self, method, path, params, private)

    sent = time.time()
    try:
        result = self._request_once(method, path, params, private)
        self.last_timing = (sent, time.time())
        self.network_event("正常")
        return result
    except exchange.NetworkError as exc:
        # Partial read failure only. Do not announce global reconnect here.
        self.network_event(f"部分只读接口异常：{label}")
        raise exchange.NetworkError(
            f"{label} 查询失败：{exc}",
            getattr(exc, "code", ""),
            getattr(exc, "http_status", None),
            getattr(exc, "method", method),
            getattr(exc, "path", path),
        ) from None


def _breaker_map(self):
    value = getattr(self, "_v165_demo_algo_breaker", None)
    if not isinstance(value, dict):
        value = {}
        self._v165_demo_algo_breaker = value
    return value


def _family_read_v165_state(self, ord_type, label):
    # Demo Trigger/Trailing are the two families repeatedly observed returning
    # 51054. Query the exact BTC scope once. After a 51054, suppress repeated
    # calls for five minutes, keeping the rest of OKX connectivity fully live.
    if bool(getattr(self, "demo", False)) and ord_type in _DEMO_FAST_DEGRADE:
        now = time.monotonic()
        breakers = _breaker_map(self)
        until = float(breakers.get(ord_type, 0.0) or 0.0)
        if now < until:
            return []
        try:
            rows = self.get(_PATH, {"instId": engine.INSTRUMENT, "ordType": ord_type}, True)
            breakers.pop(ord_type, None)
            return fallback._filter_target(rows, label)
        except exchange.NetworkError as exc:
            if not _is_51054(exc):
                raise
            breakers[ord_type] = now + _DEMO_BREAKER_SECONDS
            self.network_event(
                f"模拟盘降级：{label}返回51054；未来5分钟跳过该只读预检，其他行情/账户接口继续正常"
            )
            return []

    # OCO/Conditional and all live-account families retain strict multi-scope
    # audit. Because request() above does only one transport attempt for an algo
    # read, each fallback scope has a bounded single timeout instead of 4 nested retries.
    return fallback._family_read(self, ord_type, label)


def _algos_v165_state(self):
    rows = []
    for ord_type, label in v165._ALGO_FAMILIES:
        rows.extend(_family_read_v165_state(self, ord_type, label))
    return rows


def apply():
    if getattr(exchange.Exchange, "_kaytrade_v165_runtime_network_state_applied", False):
        return
    exchange.Exchange.request = _network_request_v165_state
    exchange.Exchange.algos = _algos_v165_state
    exchange.Exchange._kaytrade_v165_runtime_network_state_applied = True


apply()

"""KAYTRADE V1.6.5 pending-algo fallback hotfix.

Pending algo preflight remains fail-closed for live trading. For a 51054 timeout
on a precise BTC-USDT-SWAP algo query, progressively retry using SWAP scope and
then account scope, filtering every successful response locally back to the
strategy instrument.

Demo trading gets one additional safety-limited behavior: if Trigger or Trailing
Stop keeps returning 51054 across all supported read scopes, the unlock check
may continue with an explicit degraded warning. This never applies to live
trading. OCO and Conditional remain strict even in demo because they are closer
to protective TP/SL state.

Trading writes are untouched.
"""
from __future__ import annotations

from v165_update_patch import apply as apply_previous
apply_previous()

import engine
import exchange
import v165_update_patch as v165

_TARGET = engine.INSTRUMENT
_PATH = v165._ALGO_PENDING_PATH
_FAMILIES = v165._ALGO_FAMILIES
_PAGE_LIMIT = "100"
_MAX_PAGES = 20
_DEMO_DEGRADABLE = {"trigger", "move_order_stop"}


def _filter_target(rows, label):
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            raise exchange.APIError(f"{label}回退响应结构异常；保持故障锁")
        inst_id = str(row.get("instId") or "").strip()
        if not inst_id:
            raise exchange.APIError(f"{label}回退响应缺少instId；无法安全过滤，保持故障锁")
        if inst_id == _TARGET:
            out.append(row)
    return out


def _paged_read(self, params, label):
    """Exhaust one supported fallback scope; fail closed on pagination ambiguity."""
    base = dict(params or {})
    base["limit"] = _PAGE_LIMIT
    rows = []
    after = ""
    for _ in range(_MAX_PAGES):
        page_params = dict(base)
        if after:
            page_params["after"] = after
        page = self.get(_PATH, page_params, True)
        if not isinstance(page, list):
            raise exchange.APIError(f"{label}回退响应不是列表；保持故障锁")
        rows.extend(page)
        if len(page) < int(_PAGE_LIMIT):
            return rows
        last = page[-1] if page else None
        next_after = str((last or {}).get("algoId") or "").strip() if isinstance(last, dict) else ""
        if not next_after or next_after == after:
            raise exchange.APIError(f"{label}回退分页无法继续；保持故障锁")
        after = next_after
    raise exchange.APIError(f"{label}回退超过最大分页范围；保持故障锁")


def _is_51054(exc):
    return isinstance(exc, exchange.NetworkError) and str(getattr(exc, "code", "") or "") == "51054"


def _family_read(self, ord_type, label):
    attempts = (
        ("精确BTC", {"instId": _TARGET, "ordType": ord_type}),
        ("SWAP范围", {"instType": "SWAP", "ordType": ord_type}),
        ("账户范围", {"ordType": ord_type}),
    )
    last_exc = None
    for idx, (scope_label, params) in enumerate(attempts):
        try:
            rows = self.get(_PATH, params, True) if idx == 0 else _paged_read(self, params, label)
            filtered = _filter_target(rows, label)
            suffix = "" if idx == 0 else f"（{label}已使用{scope_label}只读回退）"
            self.network_event("正常" + suffix)
            return filtered
        except exchange.NetworkError as exc:
            if not _is_51054(exc):
                raise
            last_exc = exc
            if idx + 1 < len(attempts):
                self.network_event(f"{label}{scope_label}查询超时：切换{attempts[idx + 1][0]}只读回退")
                continue
            break

    # Demo-only escape hatch for the two OKX algo families observed returning
    # persistent 51054 in demo. Live trading remains strictly fail-closed.
    if ord_type in _DEMO_DEGRADABLE and bool(getattr(self, "demo", False)):
        self.network_event(
            f"模拟盘降级：{label}三层只读查询均51054；仅跳过{label}预检，实盘不会放行"
        )
        return []

    raise exchange.NetworkError(
        f"{label}精确BTC / SWAP范围 / 账户范围查询均失败：{last_exc}",
        getattr(last_exc, "code", ""),
        getattr(last_exc, "http_status", None),
        getattr(last_exc, "method", "GET"),
        getattr(last_exc, "path", _PATH),
    ) from None


def _algos_v165_fallback(self):
    rows = []
    for ord_type, label in _FAMILIES:
        rows.extend(_family_read(self, ord_type, label))
    return rows


def apply():
    exchange.Exchange.algos = _algos_v165_fallback
    exchange.Exchange._kaytrade_v165_algo_fallback_applied = True
    exchange.Exchange._kaytrade_v165_algo_fallback_revision = 3


apply()

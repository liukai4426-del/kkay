"""KAYTRADE V1.6.5 pending-algo fallback hotfix.

If OKX repeatedly returns 51054 for a precise
orders-algo-pending?instId=BTC-USDT-SWAP&ordType=... read, retry the same
read at account scope (ordType only), then filter the response locally back to
BTC-USDT-SWAP.  This preserves fail-closed safety while avoiding a backend
instrument-filter timeout from blocking unlock forever.

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


def _filter_target(rows, label):
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            raise exchange.APIError(f"{label}账户级回退响应结构异常；保持故障锁")
        inst_id = str(row.get("instId") or "").strip()
        if not inst_id:
            raise exchange.APIError(f"{label}账户级回退响应缺少instId；无法安全过滤，保持故障锁")
        if inst_id == _TARGET:
            out.append(row)
    return out


def _family_read(self, ord_type, label):
    precise = {"instId": _TARGET, "ordType": ord_type}
    try:
        return self.get(_PATH, precise, True)
    except exchange.NetworkError as exc:
        if str(getattr(exc, "code", "") or "") != "51054":
            raise

        self.network_event(f"{label}精确查询超时：切换账户级只读回退")
        try:
            rows = self.get(_PATH, {"ordType": ord_type}, True)
        except exchange.NetworkError as fallback_exc:
            raise exchange.NetworkError(
                f"{label}精确查询与账户级回退均失败：{fallback_exc}",
                getattr(fallback_exc, "code", ""),
                getattr(fallback_exc, "http_status", None),
                getattr(fallback_exc, "method", "GET"),
                getattr(fallback_exc, "path", _PATH),
            ) from None
        except Exception:
            raise

        filtered = _filter_target(rows, label)
        self.network_event(f"正常（{label}已使用账户级只读回退）")
        return filtered


def _algos_v165_fallback(self):
    rows = []
    for ord_type, label in _FAMILIES:
        rows.extend(_family_read(self, ord_type, label))
    return rows


def apply():
    if getattr(exchange.Exchange, "_kaytrade_v165_algo_fallback_applied", False):
        return
    exchange.Exchange.algos = _algos_v165_fallback
    exchange.Exchange._kaytrade_v165_algo_fallback_applied = True


apply()

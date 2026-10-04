"""Codex MCP adapter for KAYTRADE V1.8.4 OKX Demo Execution.

The adapter never receives OKX credentials. It reads the short-lived local
bridge descriptor created by the KAYTRADE app and talks only to 127.0.0.1.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

BRIDGE_FILE = Path.home() / "Library" / "Application Support" / "OKXLocal" / "ai_bridge.json"

mcp = FastMCP(
    "kaytrade-v184-okx-demo",
    instructions=(
        "KAYTRADE V1.8.4 Build1840 Clean executes LIMIT orders directly on OKX Demo Trading. Entry, reductions, take-profit and stop-loss exits are LIMIT only. "
        "Inspect get_kaytrade_state before proposing a trade. "
        "Trade-management tools submit writes to OKX Demo Trading only; live-account writes remain disabled. "
        "KAYTRADE must be connected to OKX Demo and the user must enable the OKX Demo AI channel. "
        "Use publish_trade_plan for tier-1/tier-2 recommendations. With auto execution enabled, each complete entry plan is immediately submitted as an OKX Demo LIMIT order; do not wait for market price to reach the entry. "
        "Always provide direction, size, leverage, limit entry price, take-profit and stop-loss. Protection exits are limit-only. Never propose a market order. "
        "Never ask for, read, or expose OKX API credentials."
    ),
)


def _descriptor():
    if not BRIDGE_FILE.exists():
        raise RuntimeError("KAYTRADE AI Bridge is not running")
    data = json.loads(BRIDGE_FILE.read_text())
    if data.get("version") != "1.8.4" or str(data.get("build")) != "1840":
        raise RuntimeError("KAYTRADE bridge must be V1.8.4 Build1840 Clean")
    if data.get("mode") != "OKX_DEMO_EXECUTION":
        raise RuntimeError("KAYTRADE bridge is not in OKX Demo execution mode")
    if data.get("paper_runtime_present") is not False:
        raise RuntimeError("KAYTRADE clean-runtime marker missing; refuse stale/Paper bridge")
    if data.get("demo_exchange_writes") is not True or data.get("live_ai_writes") is not False:
        raise RuntimeError("Unexpected bridge safety state")
    return data


def _normalize_limit_exit(value, trigger, name):
    if value is None:
        return trigger
    try:
        numeric = float(value)
    except Exception as exc:
        raise RuntimeError(f"KAYTRADE LIMIT ONLY: {name} must be numeric") from exc
    if numeric <= 0:
        if trigger is None:
            raise RuntimeError(
                f"KAYTRADE LIMIT ONLY: {name} <= 0 requires a positive trigger price"
            )
        return trigger
    return value


def _request(method, path, payload=None):
    cfg = _descriptor()
    url = f"http://{cfg['host']}:{cfg['port']}{path}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['token']}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {"error": f"HTTP {exc.code}"}
        raise RuntimeError(detail.get("error") or str(detail)) from None


@mcp.tool()
def get_kaytrade_status() -> dict:
    """Return KAYTRADE AI Bridge health and safety mode."""
    return _request("GET", "/health")


@mcp.tool()
def get_kaytrade_state() -> dict:
    """Return current demo ticker/account/position state without exposing credentials."""
    return _request("GET", "/v1/state")


@mcp.tool()
def publish_trade_plan(
    tier: int,
    direction: str,
    reason: str,
    operation_advice: str,
    suggested_entry: float | None = None,
    order_type: str = "limit",
    limit_price: float | None = None,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    size: float | None = None,
    leverage: int | None = None,
    plan_id: str | None = None,
    tp_exit_type: str = "limit",
    tp_limit_price: float | None = None,
    sl_exit_type: str = "limit",
    sl_limit_price: float | None = None,
) -> dict:
    """Publish an AI recommendation.

    If auto execution is ON, a complete open plan is immediately submitted
    to OKX Demo Trading as a LIMIT order. TP and SL are mandatory and use LIMIT exits.
    The adapter never submits to a live OKX account.
    """
    if order_type.lower() != "limit":
        raise RuntimeError("KAYTRADE LIMIT ONLY: order_type must be limit")
    if limit_price is None and suggested_entry is None:
        raise RuntimeError("KAYTRADE LIMIT ONLY: limit_price or suggested_entry is required")
    if tp_exit_type.lower() != "limit" or sl_exit_type.lower() != "limit":
        raise RuntimeError("KAYTRADE LIMIT ONLY: TP/SL exit type must be limit")
    tp_limit_price = _normalize_limit_exit(
        tp_limit_price, take_profit, "tp_limit_price"
    )
    sl_limit_price = _normalize_limit_exit(
        sl_limit_price, stop_loss, "sl_limit_price"
    )

    payload = {
        "tier": tier,
        "direction": direction,
        "reason": reason,
        "operation_advice": operation_advice,
        "suggested_entry": suggested_entry,
        "order_type": order_type,
        "limit_price": limit_price,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "size": size,
        "leverage": leverage,
        "plan_id": plan_id,
        "tp_exit_type": tp_exit_type,
        "tp_limit_price": tp_limit_price,
        "sl_exit_type": sl_exit_type,
        "sl_limit_price": sl_limit_price,
    }
    return _request("POST", "/v1/plan", payload)


@mcp.tool()
def submit_trade_proposal(
    action: str,
    direction: str,
    reason: str,
    size: float | None = None,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    leverage: int = 5,
    proposal_id: str | None = None,
    order_type: str = "limit",
    limit_price: float | None = None,
    tier: int = 1,
    operation_advice: str = "",
    tp_exit_type: str = "limit",
    tp_limit_price: float | None = None,
    sl_exit_type: str = "limit",
    sl_limit_price: float | None = None,
) -> dict:
    """Submit a structured AI trade request to KAYTRADE V1.8.4 OKX Demo.

    action: open or close.
    direction: long or short.
    For open: size, take_profit and stop_loss are mandatory.
    order_type: limit only; limit_price is mandatory.
    For close: a limit_price is also mandatory.
    """
    if order_type.lower() != "limit":
        raise RuntimeError("KAYTRADE LIMIT ONLY: order_type must be limit")
    if limit_price is None:
        raise RuntimeError("KAYTRADE LIMIT ONLY: limit_price is required")
    if tp_exit_type.lower() != "limit" or sl_exit_type.lower() != "limit":
        raise RuntimeError("KAYTRADE LIMIT ONLY: TP/SL exit type must be limit")
    tp_limit_price = _normalize_limit_exit(
        tp_limit_price, take_profit, "tp_limit_price"
    )
    sl_limit_price = _normalize_limit_exit(
        sl_limit_price, stop_loss, "sl_limit_price"
    )

    payload = {
        "action": action,
        "direction": direction,
        "reason": reason,
        "leverage": leverage,
        "tier": tier,
        "operation_advice": operation_advice,
        "order_type": order_type,
        "limit_price": limit_price,
        "tp_exit_type": tp_exit_type,
        "tp_limit_price": tp_limit_price,
        "sl_exit_type": sl_exit_type,
        "sl_limit_price": sl_limit_price,
    }
    if proposal_id is not None:
        payload["proposal_id"] = proposal_id
    if size is not None:
        payload["size"] = size
    if take_profit is not None:
        payload["take_profit"] = take_profit
    if stop_loss is not None:
        payload["stop_loss"] = stop_loss
    payload["order_type"] = "limit"
    payload["limit_price"] = limit_price
    return _request("POST", "/v1/trade", payload)



@mcp.tool()
def cancel_demo_entry(tier: int) -> dict:
    """Cancel the unfilled remainder of a Tier 1/2 OKX Demo entry order."""
    return _request("POST", "/v1/demo/cancel-entry", {"tier": tier})


@mcp.tool()
def amend_demo_entry(
    tier: int,
    new_price: float | None = None,
    new_size: float | None = None,
) -> dict:
    """Amend a live OKX Demo limit entry."""
    payload = {"tier": tier}
    if new_price is not None:
        payload["new_price"] = new_price
    if new_size is not None:
        payload["new_size"] = new_size
    return _request("POST", "/v1/demo/amend-entry", payload)


@mcp.tool()
def amend_demo_protection(
    tier: int,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    tp_exit_type: str | None = None,
    tp_limit_price: float | None = None,
    sl_exit_type: str | None = None,
    sl_limit_price: float | None = None,
) -> dict:
    """Amend TP/SL trigger prices. Protection exits are always LIMIT."""
    if tp_exit_type not in (None, "limit") or sl_exit_type not in (None, "limit"):
        raise RuntimeError("KAYTRADE LIMIT ONLY: TP/SL exit type must be limit")
    tp_limit_price = _normalize_limit_exit(
        tp_limit_price, take_profit, "tp_limit_price"
    )
    sl_limit_price = _normalize_limit_exit(
        sl_limit_price, stop_loss, "sl_limit_price"
    )
    payload = {"tier": tier, "tp_exit_type": "limit", "sl_exit_type": "limit"}
    for key, value in {
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "tp_limit_price": tp_limit_price,
        "sl_limit_price": sl_limit_price,
    }.items():
        if value is not None:
            payload[key] = value
    return _request("POST", "/v1/demo/amend-protection", payload)


@mcp.tool()
def close_demo_position(
    size: float,
    order_type: str = "limit",
    limit_price: float | None = None,
    tier: int | None = None,
    reason: str = "AI指定减仓",
) -> dict:
    """Reduce a specified OKX Demo position size with a LIMIT close order."""
    if order_type.lower() != "limit":
        raise RuntimeError("KAYTRADE LIMIT ONLY: close order_type must be limit")
    if limit_price is None:
        raise RuntimeError("KAYTRADE LIMIT ONLY: limit close requires limit_price")
    payload = {
        "size": size,
        "order_type": "limit",
        "limit_price": limit_price,
        "reason": reason,
    }
    if tier is not None:
        payload["tier"] = tier
    return _request("POST", "/v1/demo/close", payload)


if __name__ == "__main__":
    mcp.run(transport="stdio")

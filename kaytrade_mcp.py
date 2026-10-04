"""Codex MCP adapter for KAYTRADE V1.8.1 AI Only.

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
    "kaytrade-v181-paper",
    instructions=(
        "KAYTRADE V1.8.1 is PAPER and LIMIT ONLY. Entry, partial close, take-profit and stop-loss exits must all use limit orders. "
        "Inspect get_kaytrade_state before proposing a trade. "
        "All trade-management tools mutate local paper state only; no tool sends OKX write requests. "
        "KAYTRADE must be connected to OKX Demo for read-only market data and the user must enable the Paper AI channel. "
        "Use publish_trade_plan for tier-1/tier-2 recommendations. If the user has enabled AI-plan auto execution in KAYTRADE, an executable entry plan is immediately converted into a local Paper order. "
        "Always provide direction, size, leverage, limit entry price, take-profit and stop-loss. Protection exits are limit-only. Never propose a market order. "
        "Never ask for, read, or expose OKX API credentials."
    ),
)


def _descriptor():
    if not BRIDGE_FILE.exists():
        raise RuntimeError("KAYTRADE AI Bridge is not running")
    data = json.loads(BRIDGE_FILE.read_text())
    if data.get("version") != "1.8.1":
        raise RuntimeError("KAYTRADE bridge is not V1.8.1 Paper Execution")
    if data.get("live_ai_writes") is not False:
        raise RuntimeError("Unexpected bridge safety state")
    return data


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

    If the user-controlled auto-execution setting is ON, a complete open plan
    is immediately converted into a local Paper LIMIT order. TP and SL are mandatory and use LIMIT exits.
    No OKX write request is sent.
    """
    if order_type.lower() != "limit":
        raise RuntimeError("KAYTRADE LIMIT ONLY: order_type must be limit")
    if limit_price is None and suggested_entry is None:
        raise RuntimeError("KAYTRADE LIMIT ONLY: limit_price or suggested_entry is required")
    if tp_exit_type.lower() != "limit" or sl_exit_type.lower() != "limit":
        raise RuntimeError("KAYTRADE LIMIT ONLY: TP/SL exit type must be limit")
    if tp_limit_price is None and take_profit is not None:
        tp_limit_price = take_profit
    if sl_limit_price is None and stop_loss is not None:
        sl_limit_price = stop_loss

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
    """Submit a structured AI trade request to KAYTRADE V1.8.1.

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
    if tp_limit_price is None and take_profit is not None:
        tp_limit_price = take_profit
    if sl_limit_price is None and stop_loss is not None:
        sl_limit_price = stop_loss

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
def cancel_paper_entry(tier: int) -> dict:
    """Cancel the unfilled remainder of a Tier 1/2 paper entry order."""
    return _request("POST", "/v1/paper/cancel-entry", {"tier": tier})


@mcp.tool()
def amend_paper_entry(
    tier: int,
    new_price: float | None = None,
    new_size: float | None = None,
) -> dict:
    """Amend a live paper limit entry. Its original 60-minute deadline is preserved."""
    payload = {"tier": tier}
    if new_price is not None:
        payload["new_price"] = new_price
    if new_size is not None:
        payload["new_size"] = new_size
    return _request("POST", "/v1/paper/amend-entry", payload)


@mcp.tool()
def amend_paper_protection(
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
    if tp_limit_price is None and take_profit is not None:
        tp_limit_price = take_profit
    if sl_limit_price is None and stop_loss is not None:
        sl_limit_price = stop_loss
    payload = {"tier": tier, "tp_exit_type": "limit", "sl_exit_type": "limit"}
    for key, value in {
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "tp_limit_price": tp_limit_price,
        "sl_limit_price": sl_limit_price,
    }.items():
        if value is not None:
            payload[key] = value
    return _request("POST", "/v1/paper/amend-protection", payload)


@mcp.tool()
def close_paper_position(
    size: float,
    order_type: str = "limit",
    limit_price: float | None = None,
    tier: int | None = None,
    reason: str = "AI指定减仓",
) -> dict:
    """Reduce a specified paper position size with a LIMIT close order."""
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
    return _request("POST", "/v1/paper/close", payload)


if __name__ == "__main__":
    mcp.run(transport="stdio")

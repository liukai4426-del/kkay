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
        "KAYTRADE V1.8.1 is paper-only and supports market/limit entry, two same-direction tiers, amend/cancel, partial close and market/limit protection exits. "
        "Inspect get_kaytrade_state before proposing a trade. "
        "All trade-management tools mutate local paper state only; no tool sends OKX write requests. "
        "KAYTRADE must be connected to OKX Demo for read-only market data and the user must enable the Paper AI channel. "
        "Use publish_trade_plan to keep the dashboard updated with tier-1/tier-2 recommendations and operation advice. "
        "Always provide take-profit and stop-loss for open requests. "
        "Never ask for, read, or expose OKX API credentials."
    ),
)


def _descriptor():
    if not BRIDGE_FILE.exists():
        raise RuntimeError("KAYTRADE AI Bridge is not running")
    data = json.loads(BRIDGE_FILE.read_text())
    if data.get("version") != "1.8.0" or data.get("mode") != "AI_ONLY":
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
    order_type: str = "market",
    limit_price: float | None = None,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    size: float | None = None,
    leverage: int | None = None,
    plan_id: str | None = None,
) -> dict:
    """Publish an AI recommendation to the KAYTRADE dashboard without placing an order."""
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
    order_type: str = "market",
    limit_price: float | None = None,
    tier: int = 1,
    operation_advice: str = "",
    tp_exit_type: str = "market",
    tp_limit_price: float | None = None,
    sl_exit_type: str = "market",
    sl_limit_price: float | None = None,
) -> dict:
    """Submit a structured AI trade request to KAYTRADE V1.8.1.

    action: open or close.
    direction: long or short.
    For open: size, take_profit and stop_loss are mandatory.
    order_type: market or limit. limit requires limit_price.
    For close: KAYTRADE closes its currently managed position.
    """
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
    if action.lower() == "open":
        payload["order_type"] = order_type
        if order_type.lower() == "limit":
            if limit_price is None:
                raise RuntimeError("limit order requires limit_price")
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
    """Amend TP/SL trigger prices and choose market or limit exits after trigger."""
    payload = {"tier": tier}
    for key, value in {
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "tp_exit_type": tp_exit_type,
        "tp_limit_price": tp_limit_price,
        "sl_exit_type": sl_exit_type,
        "sl_limit_price": sl_limit_price,
    }.items():
        if value is not None:
            payload[key] = value
    return _request("POST", "/v1/paper/amend-protection", payload)


@mcp.tool()
def close_paper_position(
    size: float,
    order_type: str = "market",
    limit_price: float | None = None,
    tier: int | None = None,
    reason: str = "AI指定减仓",
) -> dict:
    """Reduce a specified paper position size with a market or limit close order."""
    payload = {
        "size": size,
        "order_type": order_type,
        "reason": reason,
    }
    if tier is not None:
        payload["tier"] = tier
    if order_type.lower() == "limit":
        if limit_price is None:
            raise RuntimeError("limit close requires limit_price")
        payload["limit_price"] = limit_price
    return _request("POST", "/v1/paper/close", payload)


if __name__ == "__main__":
    mcp.run(transport="stdio")

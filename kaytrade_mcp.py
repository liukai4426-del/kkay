"""Codex MCP adapter for KAYTRADE V1.8.0 AI Only.

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
    "kaytrade-v180-ai-only",
    instructions=(
        "KAYTRADE V1.8.0 has no local indicator strategy and supports market/limit entry orders. "
        "Inspect get_kaytrade_state before proposing a trade. "
        "submit_trade_proposal is permitted only when KAYTRADE is connected to OKX Demo "
        "and the user has enabled the AI execution channel. "
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
        raise RuntimeError("KAYTRADE bridge is not V1.8.0 AI ONLY")
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
) -> dict:
    """Submit a structured AI trade request to KAYTRADE V1.8.0.

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


if __name__ == "__main__":
    mcp.run(transport="stdio")

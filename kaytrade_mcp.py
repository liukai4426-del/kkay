"""Codex MCP adapter for KAYTRADE V1.9.1 Dual Environment Execution.

DEMO: AI auto execution on OKX simulated trading.
LIVE: guarded AI auto execution on the real OKX account when the UI switch is ON.

Both environments use LIMIT parent entries/reductions and trigger-market TP/SL.
Every tool call must explicitly specify environment='demo' or 'live'.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

BASE = Path.home() / "Library" / "Application Support" / "OKXLocal"
BRIDGES = {
    "demo": BASE / "ai_bridge_demo.json",
    "live": BASE / "ai_bridge_live.json",
}

mcp = FastMCP(
    "kaytrade-v191-live-execution",
    instructions=(
        "KAYTRADE V1.9.1 Build1910 has two isolated environments. "
        "Always pass environment='demo' or environment='live' explicitly. "
        "DEMO and LIVE support AI auto execution only when that environment's UI channel is enabled. "
        "Parent entry and explicit reduction orders must be LIMIT. "
        "TP/SL are exchange-attached trigger-market protections using OKX order-price -1. "
        "Inspect get_kaytrade_state(environment) before publishing a plan. "
        "Never request, read, print or expose OKX credentials."
    ),
)


def _env(environment: str) -> str:
    value = str(environment or "").strip().lower()
    if value not in ("demo", "live"):
        raise RuntimeError("environment must be explicitly 'demo' or 'live'")
    return value


def _descriptor(environment: str) -> dict:
    environment = _env(environment)
    path = BRIDGES[environment]
    if not path.exists():
        raise RuntimeError(f"KAYTRADE {environment} bridge is not running")
    data = json.loads(path.read_text())
    if data.get("version") != "1.9.1" or str(data.get("build")) != "1910":
        raise RuntimeError("KAYTRADE bridge must be V1.9.1 Build1910")
    expected_mode = "OKX_DEMO_EXECUTION" if environment == "demo" else "OKX_LIVE_EXECUTION"
    if data.get("environment") != environment or data.get("mode") != expected_mode:
        raise RuntimeError("Environment Match Gate: bridge descriptor mismatch")
    if data.get("paper_runtime_present") is not False:
        raise RuntimeError("Paper runtime must be absent")
    if data.get("limit_entry_only") is not True:
        raise RuntimeError("LIMIT parent-entry marker missing")
    if data.get("market_protection") is not True:
        raise RuntimeError("trigger-market TP/SL marker missing")
    if environment == "live" and data.get("live_ai_writes") is not True:
        raise RuntimeError("LIVE execution bridge capability missing")
    return data


def _market_exit_sentinel(trigger, name):
    try:
        numeric = float(trigger)
    except Exception as exc:
        raise RuntimeError(f"{name} trigger must be numeric") from exc
    if numeric <= 0:
        raise RuntimeError(f"{name} trigger must be positive")
    return -1


def _request(environment: str, method: str, path: str, payload=None):
    environment = _env(environment)
    cfg = _descriptor(environment)
    if payload is not None:
        payload = dict(payload)
        payload["environment"] = environment
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
        raise RuntimeError(detail.get("detail") or detail.get("error") or str(detail)) from None


@mcp.tool()
def get_kaytrade_status(environment: str) -> dict:
    """Return bridge health for exactly one environment."""
    return _request(environment, "GET", "/health")


@mcp.tool()
def get_kaytrade_state(environment: str) -> dict:
    """Return current state for exactly one environment."""
    return _request(environment, "GET", "/v1/state")


@mcp.tool()
def publish_trade_plan(
    environment: str,
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
    tp_limit_price: float | None = None,
    sl_limit_price: float | None = None,
) -> dict:
    """Publish an environment-scoped execution plan.

    If that environment's AI channel is ON, a complete plan is submitted to OKX.
    Entry is LIMIT; attached TP/SL execute at market after their trigger prices.
    """
    environment = _env(environment)
    if str(order_type).lower() != "limit":
        raise RuntimeError("KAYTRADE LIMIT ONLY: order_type must be limit")
    if limit_price is None and suggested_entry is None:
        raise RuntimeError("limit_price or suggested_entry is required")
    if take_profit is None or stop_loss is None:
        raise RuntimeError("take_profit and stop_loss are required")
    if size is None or leverage is None:
        raise RuntimeError("size and leverage are required")
    tp_limit_price = _market_exit_sentinel(take_profit, "take_profit")
    sl_limit_price = _market_exit_sentinel(stop_loss, "stop_loss")

    payload = {
        "environment": environment,
        "tier": tier,
        "direction": direction,
        "reason": reason,
        "operation_advice": operation_advice,
        "suggested_entry": suggested_entry,
        "order_type": "limit",
        "limit_price": limit_price,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "size": size,
        "leverage": leverage,
        "plan_id": plan_id,
        "tp_exit_type": "market",
        "tp_limit_price": tp_limit_price,
        "sl_exit_type": "market",
        "sl_limit_price": sl_limit_price,
    }
    return _request(environment, "POST", "/v1/plan", payload)


@mcp.tool()
def submit_demo_trade_proposal(
    action: str,
    direction: str,
    reason: str,
    size: float | None = None,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    leverage: int = 5,
    proposal_id: str | None = None,
    limit_price: float | None = None,
    tier: int = 1,
    operation_advice: str = "",
    tp_limit_price: float | None = None,
    sl_limit_price: float | None = None,
) -> dict:
    """Submit a DEMO-only LIMIT entry with trigger-market TP/SL."""
    if limit_price is None:
        raise RuntimeError("limit_price is required")
    if action == "open" and (size is None or take_profit is None or stop_loss is None):
        raise RuntimeError("open requires size, take_profit and stop_loss")
    tp_limit_price = _market_exit_sentinel(take_profit, "take_profit")
    sl_limit_price = _market_exit_sentinel(stop_loss, "stop_loss")
    payload = {
        "environment": "demo",
        "action": action,
        "direction": direction,
        "reason": reason,
        "size": size,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "leverage": leverage,
        "proposal_id": proposal_id,
        "order_type": "limit",
        "limit_price": limit_price,
        "tier": tier,
        "operation_advice": operation_advice,
        "tp_exit_type": "market",
        "tp_limit_price": tp_limit_price,
        "sl_exit_type": "market",
        "sl_limit_price": sl_limit_price,
    }
    return _request("demo", "POST", "/v1/trade", payload)


@mcp.tool()
def cancel_demo_entry(tier: int, reason: str = "AI撤销DEMO入场") -> dict:
    """Cancel a DEMO entry. No LIVE equivalent is exposed."""
    return _request(
        "demo",
        "POST",
        "/v1/cancel-entry",
        {"environment": "demo", "tier": tier, "reason": reason},
    )


@mcp.tool()
def amend_demo_entry(
    tier: int,
    new_price: float | None = None,
    new_size: float | None = None,
) -> dict:
    """Amend a DEMO LIMIT entry. No LIVE equivalent is exposed."""
    payload = {"environment": "demo", "tier": tier}
    if new_price is not None:
        payload["new_price"] = new_price
    if new_size is not None:
        payload["new_size"] = new_size
    return _request("demo", "POST", "/v1/amend-entry", payload)


@mcp.tool()
def amend_demo_protection(
    tier: int,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    tp_limit_price: float | None = None,
    sl_limit_price: float | None = None,
) -> dict:
    """Amend DEMO trigger-market TP/SL. No LIVE equivalent is exposed."""
    tp_limit_price = _market_exit_sentinel(take_profit, "take_profit") if take_profit is not None else None
    sl_limit_price = _market_exit_sentinel(stop_loss, "stop_loss") if stop_loss is not None else None
    payload = {
        "environment": "demo",
        "tier": tier,
        "tp_exit_type": "market",
        "sl_exit_type": "market",
    }
    for key, value in {
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "tp_limit_price": tp_limit_price,
        "sl_limit_price": sl_limit_price,
    }.items():
        if value is not None:
            payload[key] = value
    return _request("demo", "POST", "/v1/amend-protection", payload)


@mcp.tool()
def close_demo_position(
    size: float,
    limit_price: float,
    tier: int | None = None,
    reason: str = "AI指定DEMO限价减仓",
) -> dict:
    """Submit a DEMO LIMIT reduction. No LIVE equivalent is exposed."""
    payload = {
        "environment": "demo",
        "size": size,
        "order_type": "limit",
        "limit_price": limit_price,
        "reason": reason,
    }
    if tier is not None:
        payload["tier"] = tier
    return _request("demo", "POST", "/v1/close", payload)


if __name__ == "__main__":
    mcp.run(transport="stdio")

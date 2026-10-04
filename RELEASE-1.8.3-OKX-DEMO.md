# KAYTRADE V1.8.3 OKX DEMO EXECUTION · Build1831

Build1831 removes the local Paper runtime from the packaged program entirely; only OKX Demo exchange execution remains.

## Core behavior

When AI auto execution is enabled and a complete entry plan arrives:

1. KAYTRADE validates the plan locally.
2. KAYTRADE immediately sends a LIMIT order to OKX Demo Trading.
3. The order remains visible on OKX as a normal exchange order until it fills, is amended, or is canceled.
4. KAYTRADE never waits for the local ticker to touch the requested entry before submitting.
5. Fill state and average fill price come from OKX order/fill data only.

There is no local simulated fill engine in the active V1.8.3 path.

## LIMIT ONLY

All execution remains LIMIT-only:

- entry orders;
- TP order price;
- SL order price;
- partial close / reduction orders.

Market orders are rejected.

If separate TP/SL limit prices are omitted, their trigger prices are used as the attached limit prices.

## AI plan auto execution

The new OKX Demo execution state defaults to auto execution ON after the user enables the OKX Demo AI channel.

A complete executable plan requires:

- tier 1 or 2;
- long or short direction;
- LIMIT entry price;
- size;
- leverage;
- take-profit trigger;
- stop-loss trigger.

The plan is submitted to OKX immediately.

If the same unfilled Tier already has an identical plan, no duplicate order is sent.
If the same unfilled Tier receives a changed plan, KAYTRADE sends an OKX amend-order request instead of creating a second entry order.
A filled or partially-filled Tier is never overwritten by a new entry plan.

## Two-tier behavior

Tier 1 and Tier 2 may both be active at the same time only when:

- they use the same direction;
- they use the same leverage.

Each Tier has its own OKX order ID, client order ID, attached TP/SL client IDs, entry price, TP/SL, 60-minute deadline, and execution state.

## 60-minute expiry

A live or partially-filled entry order is monitored from its accepted time.

If the entry is not fully filled within 60 minutes, KAYTRADE submits an OKX cancel-order request for the remaining entry order.

The already-filled quantity, if any, remains a managed position.

## Amend / cancel / protection

Supported OKX Demo management operations:

- cancel an incomplete entry;
- amend entry price / total target size;
- amend attached TP/SL protection;
- submit a LIMIT partial-close order.

Before the parent entry is filled, attached TP/SL changes use OKX amend-order with attachAlgoOrds.
After the parent is filled, protection amendments use OKX amend-algos.

## Hard limits

The aggregate execution limits remain:

- leverage <= 20x;
- Tier 1 + Tier 2 notional <= 3500 USDT;
- Tier 1 + Tier 2 estimated maximum stop-loss <= 100 USDT.

## Safety boundary

- only OKX Demo Trading may receive automated writes;
- real-account writes are still hard-disabled;
- API credentials remain inside KAYTRADE and are never returned through the AI bridge;
- ambiguous write results remain fail-closed and are not automatically retried.

## Trading Overview

The V1.8.2 layout is retained, but runtime wording now reflects exchange-backed Demo execution:

- BTC price at top;
- AI auto execution;
- AI plan board;
- order / position execution;
- OKX Demo AI channel controls;
- runtime log.

Paper wording is removed from the active UI.
Order/fill status shown in the execution panel comes from the OKX Demo state.

## Package

- Version: 1.8.3
- Build: 1831
- Mode: OKX DEMO EXECUTION · LIMIT ONLY
- Intel macOS 14+

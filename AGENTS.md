# KAYTRADE V1.8.4 Build1840 · OKX DEMO ONLY

KAYTRADE Build1840 has no local Paper execution runtime.

## Runtime contract

Before publishing any executable plan:
1. call get_kaytrade_state;
2. confirm version=1.8.4 and build=1840;
3. confirm mode=OKX_DEMO_EXECUTION;
4. confirm demo_exchange_writes=true;
5. confirm live_ai_writes=false;
6. confirm paper_runtime_present=false;
7. inspect Tier 1 / Tier 2, OKX positions and pending orders;
8. keep simultaneous Tier 1 / Tier 2 orders in the same direction and leverage;
9. provide LIMIT entry, explicit size, leverage, take-profit and stop-loss.

When auto_execute_plans=true, publish_trade_plan immediately submits or amends the OKX Demo LIMIT order. Do not wait for market price to reach the entry before publishing.

Order fill status and average fill price must come from OKX only. Never infer a fill from the local ticker crossing the limit price.

## Execution rules

- entry: LIMIT only;
- TP: trigger + LIMIT order price;
- SL: trigger + LIMIT order price;
- reductions / partial closes: LIMIT only;
- two same-direction tiers are supported;
- incomplete entry remainder is canceled after 60 minutes;
- changed unfilled plan for the same tier uses OKX amend-order rather than creating a duplicate;
- partially-filled or filled entry is never overwritten by a new entry recommendation;
- aggregate leverage/notional/estimated-stop limits must remain enforced.

## Safety boundary

Automated writes are permitted only when KAYTRADE is connected to OKX Demo Trading.
Live-account AI writes remain hard-disabled.
Never request, read, print or expose OKX credentials.
Never bypass KAYTRADE with direct exchange writes.
Never propose or retry a market order.

## Removed behavior

The old local Paper matching engine, Paper API routes, Paper UI patch and Paper state are removed from Build1840.
Do not reference or call /v1/paper/* endpoints.


## V1.8.4 protection-price rule

OKX uses -1 in tpOrdPx/slOrdPx (and newTpOrdPx/newSlOrdPx) as a market-execution sentinel.
KAYTRADE V1.8.4 must never emit that sentinel.
If an AI tool supplies a missing, zero or negative TP/SL limit price, normalize it to the corresponding positive trigger price before submission.
The exchange transport layer must reject any outbound TP/SL protection payload that still contains -1.

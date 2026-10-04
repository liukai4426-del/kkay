# KAYTRADE V1.9.0 Dual Environment Safe · Build1900

V1.9.0 introduces two isolated OKX environments without restoring any local Paper runtime.

## DEMO

- environment: OKX simulated trading
- AI channel: automatic execution ON/OFF
- AI plan -> immediate OKX Demo LIMIT order when enabled
- LIMIT ONLY for entry, TP, SL and reductions
- TP/SL -1 market sentinels remain blocked
- Tier 1 / Tier 2 same-direction support
- 60-minute incomplete-entry cancel
- amend/cancel/protection/partial-close remain supported

## LIVE

- environment: OKX real account
- separate real-account API profile in memory
- separate state directory
- separate localhost AI bridge
- separate bridge token
- real positions/orders/balance are read-only
- AI channel ON/OFF controls plan validation/review
- when ON, valid plans become READY_FOR_MANUAL_EXECUTION
- no automated OKX live write endpoint is exposed
- LIVE plans must be manually executed/confirmed in OKX

## Isolation

DEMO and LIVE do not share:

- API profile values;
- Engine instances;
- State files;
- Tier state;
- AI plan history;
- Bridge descriptors;
- Bearer tokens.

All AI bridge requests require an explicit matching environment.

Mismatch examples:

- live plan -> demo bridge: rejected
- demo plan -> live bridge: rejected
- live trade write -> live bridge: rejected
- demo order state -> live state: never migrated

## UI

Trading Overview adds a dedicated environment card above the AI execution card:

- OKX 模拟盘
- OKX 实盘
- independent connection/channel state

The refined V1.8.4 AI plan board remains:

- entry label above price;
- LONG green / SHORT red;
- rounded TP/SL;
- rounded recommendation/advice/history panels.

## Safety

- no Paper runtime
- DEMO automated writes only
- LIVE automated writes disabled
- LIMIT ONLY
- TP/SL -1 market sentinel firewall retained
- API withdrawal permission is rejected for LIVE read-only connection

# KAYTRADE V1.9.0 Dual OKX · Build1900

V1.9.0 extends the stable V1.8.4 Build1840 LIMIT-only execution path to two strictly separated exchange environments.

## Execution model

AI only produces a structured trading plan. KAYTRADE decides the active environment from the desktop connection and submits the order through the corresponding OKX API session.

- OKX Demo: exchange-backed automatic execution, auto-execution defaults ON after channel enable.
- OKX Live: real-account exchange execution, auto-execution resets OFF on every Live connection.
- AI payloads cannot select Demo or Live.
- Only the currently connected desktop environment can receive new writes.
- Switching environments requires stopping the current AI channel and reconnecting.
- Existing exchange orders and attached TP/SL remain on OKX; they are reconciled again when that environment is reconnected.

## Isolation

The underlying Engine already keys Store files by host + simulated/live flag + OKX uid. Build1900 keeps this and also separates execution buckets:

- Demo: `okx_demo_execution`
- Live: `okx_live_execution`

API credentials are cached separately in memory for the Demo and Live selectors and are never persisted to disk.

## Live authorization

Live uses two independent gates:

1. Live AI execution channel must be explicitly authorized by typing `LIVE` for the current session.
2. `AI方案自动执行` must then be turned ON.

A reconnect or restart resets the Live auto-execution state to OFF. Stopping the Live AI channel also forces auto-execution OFF.

## Order contract inherited from V1.8.4

- Entry: LIMIT only
- TP: trigger + positive LIMIT price
- SL: trigger + positive LIMIT price
- Reduction / close: LIMIT only
- OKX `tpOrdPx=-1` / `slOrdPx=-1` and amend equivalents remain blocked
- two same-direction tiers supported
- same leverage required across active tiers
- 60-minute unfilled remainder cancel retained
- changed unfilled AI plan amends the existing order instead of stacking a duplicate
- partially filled tiers cannot be overwritten by a new AI entry plan

## Risk

Demo and Live persist their risk-limit dictionaries independently inside their environment-specific Store files. Build1900 starts both from the existing V1.8.4 hard caps unless changed in code/config:

- max leverage: 20x
- max aggregate notional: 3500 USDT
- max aggregate estimated stop loss: 100 USDT

## AI Bridge

The bridge exposes generic routes only:

- `/v1/state`
- `/v1/plan`
- `/v1/trade`
- `/v1/cancel-entry`
- `/v1/amend-entry`
- `/v1/amend-protection`
- `/v1/close`

There is no Demo/Live route selector. Payload fields attempting to choose the environment are rejected.

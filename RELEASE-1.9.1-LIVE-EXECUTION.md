# KAYTRADE V1.9.1 Live Execution · Build1911

V1.9.1 upgrades the isolated LIVE environment from review-only to guarded real-account AI execution.

## Execution contract

- DEMO and LIVE remain fully isolated: API profile, Engine, State, Bridge, Token, Tier state and history.
- LIVE connects with the write gate OFF.
- LIVE writes become possible only after the user explicitly enables the LIVE AI auto-execution channel.
- LIVE API must have trade permission and must not have withdrawal permission.
- Parent entry and explicit reduction orders remain LIMIT.
- TP/SL are attached conditional protections and execute at market after their trigger prices (`tpOrdPx=-1`, `slOrdPx=-1`).
- Stopping LIVE or a worker-cycle fault closes the LIVE write gate.
- Paper runtime remains absent.

## State persistence

- State writes use a per-Store re-entrant lock.
- Every save uses a unique temporary file before atomic replace.
- Concurrent bridge polling and execution-state saves no longer share one fixed `.tmp` file.

## Bridge modes

- DEMO: `OKX_DEMO_EXECUTION`
- LIVE: `OKX_LIVE_EXECUTION`

## Notes

- Existing V1.9.0 review-only LIVE state is not promoted into real orders.
- LIVE starts disabled again after reconnect/restart and requires explicit user arming.
- Trigger-market TP/SL can experience market slippage after the trigger; the trigger price is not a guaranteed fill price.

## Build1911 hotfix

- LIVE activation failures are now shown to the user instead of disappearing in a packaged Tk callback.
- The UI performs a read-only activation preflight before opening the write gate.
- Engine / state / write-gate status is verified after activation.
- Any activation error explicitly leaves the LIVE write gate closed.

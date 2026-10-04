# KAYTRADE V1.9.0 Build1900 · Dual Environment Safe

KAYTRADE has two isolated OKX environments.

## DEMO

- environment=demo
- mode=OKX_DEMO_EXECUTION
- AI auto execution may be enabled/disabled
- complete AI plans may be submitted automatically to OKX simulated trading
- entry/TP/SL/reductions are LIMIT ONLY

## LIVE

- environment=live
- mode=OKX_LIVE_REVIEW
- LIVE AI review channel may be enabled/disabled
- real account data is read-only
- AI plans may be validated and staged as READY_FOR_MANUAL_EXECUTION
- no automated live trade-write tools are available
- LIVE orders must be manually executed/confirmed in OKX

## Required workflow

Before every AI plan:
1. explicitly choose environment=demo or environment=live;
2. call get_kaytrade_state for that same environment;
3. verify bridge mode matches the requested environment;
4. inspect current Tier 1 / Tier 2 and account state;
5. use LIMIT entry only;
6. provide size, leverage, TP and SL;
7. never use market orders or OKX -1 TP/SL sentinels.

## Isolation contract

DEMO and LIVE must never share:
- API profile values;
- Engine instances;
- State files;
- Tier state;
- AI history;
- Bridge descriptor;
- Bridge token.

A bridge/plan environment mismatch must be rejected.

## Safety boundaries

- Paper runtime is absent.
- DEMO automated writes are allowed only to OKX simulated trading.
- LIVE automated writes remain disabled.
- LIVE API must not have withdrawal permission.
- Never ask for, read, print, log or expose OKX API credentials.

# KAYTRADE V1.9.1 Build1911 · Dual Environment Execution

KAYTRADE has two isolated OKX environments.

## DEMO

- environment=demo
- mode=OKX_DEMO_EXECUTION
- AI auto execution may be enabled/disabled
- complete AI plans may be submitted automatically to OKX simulated trading
- parent entry/reduction orders are LIMIT
- attached TP/SL are trigger-market protections (`tpOrdPx=-1`, `slOrdPx=-1`)

## LIVE

- environment=live
- mode=OKX_LIVE_EXECUTION
- LIVE writes are hard-locked OFF after connect/restart
- the user must explicitly enable the LIVE AI auto-execution channel in KAYTRADE
- when enabled, complete AI plans may be submitted automatically to the real OKX account
- parent entry/reduction orders are LIMIT
- attached TP/SL are trigger-market protections
- LIVE API must include trade permission and must not include withdrawal permission

## Required workflow

Before every AI plan:
1. explicitly choose environment=demo or environment=live;
2. call get_kaytrade_state for that same environment;
3. verify the bridge mode matches the requested environment;
4. inspect current Tier 1 / Tier 2, positions, pending orders and channel state;
5. use LIMIT entry only;
6. provide size, leverage, TP and SL;
7. TP/SL use OKX trigger-market execution after their trigger prices.

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
- No LIVE write is allowed until the user explicitly arms the LIVE channel.
- Stopping/faulting LIVE closes the write gate again.
- Main entry/reduction orders remain LIMIT.
- TP/SL use exchange-attached trigger-market protection.
- Never ask for, read, print, log or expose OKX API credentials.

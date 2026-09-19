# KAYTRADE V1.7.0 Build1702

Build1702 is an execution-safety hotfix on top of Build1701. It does not change the V1.7.0 signal, scoring, position sizing, SL/TP prices, PEE4, or Lock1H rules.

## TP/SL reconciliation fix

A fully filled parent order could be market-flattened about 15–20 seconds after entry even though the attached OKX TP/SL was already present. The inherited reconciler counted protective brackets only through `algoClOrdId`, while OKX / the live Algo feed can expose the same attached client id as `attachAlgoClOrdId`.

Build1702 now:

- normalizes `attachAlgoClOrdId` to the local bracket identity before legacy reconciliation;
- preserves the existing trusted WebSocket + REST Algo cache path;
- before the specific “position exceeds verifiable TP/SL quantity” emergency flatten, performs a fresh REST snapshot of all audited pending-algo families;
- cancels that emergency flatten only when fresh exchange evidence proves the exact local bracket id, isolated side, TP price, SL price and sufficient protected quantity;
- keeps the inherited one-time market emergency flatten when a successful fresh snapshot still cannot prove protection;
- gives transient read-only verification failures a short bounded retry window rather than treating a read failure itself as proof that the position is unprotected.

## Preserved V1.7.0 rules

No changes to strict closed-15m BOLL opportunity creation, NoEMA1H, 15m EMA50 score, 5m BOLL overextension score, 4H rules, RSI/Volume/MACD logic, threshold 6, 1x LIMIT entry, 1H ATR stop, full 2R target, No-BE, PEE4, or global Lock1H.

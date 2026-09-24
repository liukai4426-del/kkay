# KAYTRADE V1.6.7 Build1670

## Strategy
- Keep the production **15m BOLL outer-only** trigger; middle-band entry remains disabled.
- Keep the production **5-minute execution window** after the 15m trigger closes.
- Keep 4H aligned, 5m RSI 30–70, Volume <1.20×, structure/cost/stop and score >=6 safeguards.
- Change the MACD hard gate: neutral/non-improving MACD may proceed; only explicit adverse/opposite 5m MACD blocks a new entry.
- **MACD momentum/improvement +1 scoring remains.**
- Remove the 5m KDJ +0.5 score. KDJ is not a hard gate and no longer affects total score.
- Synchronize the score-detail table, opening-status panel, runtime text and automatic-trading authorization dialog with the V1.6.7 rules.

## Algo read-state resilience
- Replace normal-cycle serial polling of OCO / Conditional / Trigger / Trailing pending-algo REST routes with a trusted local cache.
- Establish OKX business WebSocket subscriptions for algo updates and maintain the cache incrementally.
- Build the initial/reconnect state from a **parallel four-family REST snapshot**, buffering and replaying WebSocket changes that arrive during the snapshot.
- When the WebSocket disconnects, mark the cache untrusted immediately, reconnect and resynchronize in the background.
- While cache state is untrusted, only the current new-entry cycle is skipped. Automatic-trading authorization remains enabled and resumes automatically after trusted synchronization returns.
- Existing exchange-native TP/SL orders remain on OKX; write-side ambiguity continues to fail closed.

## Safety preserved
- LIMIT entry and write idempotency behavior are unchanged.
- POST/order/cancel uncertainty is never automatically retried.
- 1× 1H ATR stop and one full 2R target remain unchanged.
- No-BE remains unchanged.

# KAYTRADE V1.6.3b Build1631

V1.6.3b is a runtime/network hotfix on top of V1.6.3. Trading strategy semantics are unchanged.

## Network recovery changes

- A read-only network/transport failure no longer immediately writes a permanent fault lock or stops the existing automatic-trading authorization.
- For the first 60 seconds after a read-only failure, KAYTRADE pauses the strategy cycle and performs one read-only recovery probe every 5 seconds.
- Recovery probes only read OKX time, account identity, balance, BTC positions, normal orders and algo orders. They never place, cancel or amend an order.
- If all recovery reads succeed within 60 seconds, KAYTRADE advances the 5m signal cursor so outage-era signals are not backfilled, then automatically resumes the previous auto-trading authorization.
- If the outage lasts 60 seconds or longer, KAYTRADE disables new automatic entries without writing a permanent fault lock. It keeps probing every 5 seconds; when the network later recovers, observation/position reconciliation resumes but new entries remain disabled until the user manually authorizes automatic trading again.
- Exchange-native TP/SL orders already placed on OKX are not removed by this network timeout.

## Write safety unchanged

- A POST/write NetworkError or otherwise uncertain write result is never automatically retried.
- Place/cancel/amend uncertainty remains fail-closed and requires state reconciliation to avoid duplicate orders.

## Strategy unchanged from V1.6.3

- 5m BOLL outer bands only.
- 5m RSI 30-70 for both directions.
- 5m MACD histogram must continuously improve in the trade direction.
- 4H same-direction hard gate.
- First entry 1x LIMIT.
- Initial stop 1x 1H ATR; full take-profit 2R; No-BE.

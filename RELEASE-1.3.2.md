# KAYTRADE V1.3.2

Strategy-scoring update on top of the V1.3.1 visual build.

## Scoring
- Detailed 10.0-point scale using 0.5-point increments.
- Default entry threshold: 4.0 / 10.0.
- 1H trend environment: three 0.5 confirmations, max 1.5.
- 4H favorable support/resistance proximity: +1.5.
- 1H favorable structure: +1.0; 15m favorable structure: +0.5.
- 1H EMA20/EMA50 dynamic support/resistance: +0.5 max.
- 5m + 15m RSI extreme resonance only: +1.5; one timeframe alone scores 0.
- 15m Setup components use 0.5/1.0 weights and cap at 2.0.
- 5m Trigger components use 0.5 weights and cap at 1.5.
- Strong countertrend penalty is -1.5; the old special >=11 countertrend gate is removed.
- Forward strong-structure 1.0-1.3R penalty scales to -1.0; <1R remains a hard block.

## Entry frequency
- 15m Setup hard minimum becomes 0.5.
- 5m Trigger hard minimum becomes 0.5.
- 4H/1H/15m favorable structure adds quality points but is not a mandatory entry gate.
- Cost filter and forward-structure hard block are not loosened.

## Market data
- Adds closed 4H candle loading and freshness validation.

## Preserved execution and safety
- Isolated margin / hedge-side execution.
- Normal limit entry and one-5m-candle expiry.
- Partial-fill cancel/flatten safety.
- Attached market TP/SL verification.
- No duplicate order on uncertain writes.
- Fail-closed network, candle, position and protection handling.
- China natural-day drawdown and consecutive-loss guard.

Use OKX demo first. This build does not guarantee profitability and has not completed live-capital end-to-end acceptance.

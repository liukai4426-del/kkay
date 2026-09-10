# OKX Local V1.2.3

## Expanded natural scoring

V1.2.3 no longer compresses the score back to 10. The positive signal weights are kept at their natural values, for a theoretical maximum of 12 points. The default automatic-entry threshold remains exactly 7 points.

- 15m + 1H RSI extreme combination: +2.
- 15m Bollinger touch + RSI oversold/overbought: +2.
- 15m Bollinger rejection back inside the band + current volume >= 1.3x the prior 20-bar average: +2.
- 15m KDJ J-value + K/D cross: +1.
- 5m EMA20 reclaim/break: +1.
- 15m reversal candle: +1.
- Multi-timeframe confirmation, counted once: 5m = +1; 5m + 15m = +2; 5m + 15m + 1H environment = +3.
- Strong opposite 1H trend: -2.
- Final score is clamped to 0-12. Exactly 7 remains eligible for the separate safety/risk checks.

The volume rule only scores a rejection that returns inside the Bollinger band. A high-volume candle that keeps closing outside the band is not treated as a reversal.

## ATR risk revision

- Opening SL distance uses the latest closed 15m ATR.
- Default stop distance is 1.0 x 15m ATR for new/default settings.
- TP remains reward-R based (default 1.5R), derived from the same 15m ATR stop distance.
- Existing saved user risk settings are preserved.

## Safety behavior retained

Closed-candle checks, exchange-clock calibration, CandlePending/CandleLag/CandleStale behavior, unknown-order/position/protection fail-closed locks, isolated margin checks, FOK entry behavior and attached exchange TP/SL remain unchanged. A high score never overrides those safety checks.

## Validation limits

CI uses fake exchange/synthetic market data and packaged UI smoke tests. It does not validate profitability and is not a real-account end-to-end trading test. The app is unsigned/not notarized.

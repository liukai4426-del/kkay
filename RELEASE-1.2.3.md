# OKX Local V1.2.3

## Current scoring revision

V1.2.3 remains a 10-point scoring system with a default automatic-entry threshold of 7/10.

- 15m + 1H RSI extreme combination: +2.
- 15m Bollinger touch + RSI oversold/overbought: +2.
- 15m Bollinger outer-band rejection back inside the band + current volume >= 1.3x the prior 20-bar average: +2.
- 15m KDJ J-value + K/D cross: +1.
- 5m EMA20 reclaim/break: +1.
- Multi-timeframe confirmation: 5m = +1; 5m + 15m = +2 maximum.
- 1H strong opposite trend: -2.
- RSI is not a hard gate; exactly 7 is eligible for the separate safety/risk checks.

The volume rule requires rejection back inside the Bollinger band. A high-volume candle that continues closing outside the band is not scored as a reversal.

## ATR risk revision

- Opening SL distance now uses the latest closed 15m ATR rather than 1H ATR.
- Default stop distance is 1.0 x 15m ATR for new/default settings.
- TP remains reward-R based (default 1.5R), so it is derived from the same 15m ATR stop distance.
- Existing saved user risk settings are preserved; the app does not silently overwrite a previously saved stop multiplier.

## Timeframes

- 5m: entry confirmation and signal-deduplication boundary.
- 15m: primary reversal structure, volume confirmation and ATR risk reference.
- 1H: market environment and strong-opposite penalty.

## Safety behavior retained from V1.2.2

Closed-candle checks, exchange-clock calibration, CandlePending/CandleLag/CandleStale behavior, unknown-order/position/protection fail-closed locks, isolated margin checks, FOK entry behavior and attached exchange TP/SL remain unchanged. A high score never overrides those safety checks.

## Validation limits

CI uses fake exchange/synthetic market data and packaged UI smoke tests. It does not validate profitability and is not a real-account end-to-end trading test. The app is unsigned/not notarized.

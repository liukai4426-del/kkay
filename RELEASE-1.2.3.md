# OKX Local V1.2.3

## Scope

V1.2.3 is a scoring and entry-timing revision built on the V1.2.2 stability hotfix. It does not implement the planned V1.3 ordinary-limit-order lifecycle changes.

## Scoring changes

- RSI is no longer a hard entry gate.
- 15m RSI <= 20 with 1H RSI <= 25 adds 2 long points.
- 15m RSI >= 75 with 1H RSI >= 70 adds 2 short points.
- 15m Bollinger touch plus RSI oversold/overbought adds 2 points.
- 15m KDJ J-value plus K/D cross confirmation adds 1 point.
- 5m EMA20 reclaim/break adds 1 point.
- 15m reversal candle adds 1 point.
- Multi-timeframe confirmation is stair-stepped and counted once: 5m = 1, 5m+15m = 2, 5m+15m+1H environment = 3.
- Strong opposite 1H trend subtracts 2 points.
- Final score is clamped to 0-10. Default automatic entry threshold remains 7/10, and exactly 7 is eligible.

## Timeframes

- 5m: entry confirmation and signal deduplication boundary.
- 15m: primary reversal structure.
- 1H: environment filter and ATR risk reference.

## Safety behavior retained from V1.2.2

- Closed-candle checks and exchange-clock calibration.
- CandlePending / CandleLag / CandleStale handling.
- Three consecutive late-candle confirmations pause new entries without writing a permanent lock.
- Structural candle faults, unknown order state, position mismatch and unverified TP/SL remain fail-closed/manual-lock conditions.
- Existing FOK opening order behavior and attached TP/SL are unchanged in V1.2.3.
- No high score can override account, network, candle or position safety checks.

## Validation limits

CI uses fake exchange/synthetic market data and packaged UI smoke tests. It does not validate profitability and is not a real-account end-to-end trading test. The app is unsigned/not notarized.

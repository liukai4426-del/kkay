# OKX Local V1.2.3

## Per-signal multi-timeframe confluence scoring

Each independent signal now receives its own 5m/15m/1H confluence ladder. Positive maximum: 19. Default entry threshold: 8.

- RSI confluence: max +3. Long thresholds: 5m <=20, 15m <=20, 1H <=25. Short: 5m >=75, 15m >=75, 1H >=70.
- Bollinger outer-band touch confluence: max +3.
- KDJ confluence: max +3.
- EMA20 reclaim/loss confluence: max +3.
- Reversal-candle confluence: max +3.
- 15m RSI + Bollinger combination: +2.
- 15m Bollinger rejection back inside + volume >=1.3x prior 20-bar average: +2.
- Strong opposite 1H trend: -3.

Each independent confluence ladder is replacement-style, not cumulative inside itself: 5m = +1; 5m+15m = +2; 5m+15m+1H = +3. Final score is clamped to 0-19 after the penalty.

Signal bands: 0-7 no entry; 8-10 ordinary; 11-14 strong; 15-19 high-confluence. A score >= configured threshold (default 8) only enters the separate safety/risk checks.

## ATR risk and safety

SL remains based on latest closed 15m ATR, default 1.0x. TP remains default 1.5R. V1.2.2 candle/network/order/position/protection fail-closed safety, isolated margin, FOK entry and attached TP/SL are unchanged.

A saved old threshold of 7 is migrated to 8 in the UI. CI is synthetic and does not validate profitability or real-account end-to-end execution. The app remains unsigned/not notarized.

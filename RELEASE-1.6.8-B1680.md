# KAYTRADE V1.6.8 Build1680

V1.6.8 is a strategy/lifecycle synchronization release on top of the verified V1.6.7 Build1671 runtime.

## Confirmed strategy changes

- **15m BOLL outer-only remains the sole BOLL source.** There is no active 5m BOLL trigger or score path.
- **15m BOLL outer score: 2.0 points** (reduced from 2.5).
- A valid closed-15m BOLL outer trigger remains actionable **until the next 15m candle closes** (15-minute lifecycle). 5m no longer determines BOLL signal expiry.
- **4H alignment is mandatory and scores +1.** If 4H is neutral or opposite to the intended trade direction, opening is prohibited by Hard Gate and Final Entry Guard.
- **1H EMA9/EMA26 trend adds +1 auxiliary score.** Long: EMA9 > EMA26. Short: EMA9 < EMA26. This score is not a Hard Gate.
- 4H state and 1H EMA9/26 score are calculated/displayed in real time even before a BOLL opportunity exists; BOLL itself remains mandatory for entry.

## Preserved rules

- 1H/15m main-direction filter.
- 5m RSI 30–70 Hard Gate.
- 5m MACD: only explicit adverse/opposite deterioration blocks; momentum improvement retains +1 score.
- 5m Volume must remain below 1.20× the prior-20 5m average; Volume does not score.
- KDJ remains removed from scoring and is not a Hard Gate.
- Threshold remains >=6.
- Front structure >=1.5R, cost <=0.30R, stop structure, entry-drift, daily-risk and other execution protections remain.
- First entry remains fixed 1× LIMIT; stop = 1×1H ATR; full take profit = 2R; No-BE.
- V1.6.7 Algo REST snapshot + Business WebSocket cache/resync remains in place; transient read-side failures do not disable authorization, while ambiguous writes remain fail-closed.

## UI synchronization

- Opening status now shows **4H同向 Hard Gate** continuously.
- 4H aligned = green/+1; neutral/opposite = red and prohibited.
- New **1H EMA9/26趋势** status and score row.
- Signal lifecycle text is now **15m BOLL信号有效至下一根15m收盘**; the old 5m execution-window wording is removed from the active V1.6.8 UI.

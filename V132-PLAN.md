# KAYTRADE V1.3.2 Strategy Plan

Implementation target based on approved conversation requirements.

- Keep V1.3.1 visual polish and all V1.3 execution/safety behavior.
- Change score system to detailed 10-point scale with 0.5 increments.
- Default auto-entry threshold: 4.0 / 10.
- Keep 15m Setup >= 0.5 and 5m Trigger >= 0.5 as hard prerequisites.
- Add 4H support/resistance proximity scoring.
- Add EMA dynamic support/resistance scoring.
- RSI only scores when 5m and 15m extreme conditions resonate in same direction.
- Remove special countertrend >= 11 gate; retain only countertrend score penalty scaled for 10-point system.
- Preserve cost filter, forward structure filter, isolated/hedge execution, limit-entry flow, TP/SL protection, fail-closed network/order/candle safety, China-day risk controls.
- Keep score components individually visible in UI.

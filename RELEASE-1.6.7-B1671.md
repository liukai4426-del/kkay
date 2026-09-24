# KAYTRADE V1.6.7 Build1671

Build1671 is an emergency strategy/runtime hotfix for BOLL signal-source consistency.

- BOLL trigger/scoring is now strictly **15m outer-band only**.
- **No 5m BOLL trigger, score, opportunity, or runtime log semantic remains active.**
- Short requires the latest closed 15m candle high to touch/break the 15m upper band.
- Long requires the latest closed 15m candle low to touch/break the 15m lower band.
- 5m remains only for RSI 30–70, MACD, Volume and the five-minute execution window after a valid 15m trigger.
- One closed 15m trigger may create only one five-minute execution window. After expiry, the same 15m candle cannot create a second opportunity; the engine waits for the next 15m close.
- Final Entry Guard verifies frozen 15m trigger evidence and side/path pairing before any order write.
- Existing V1.6.7 MACD adverse-only hard gate, MACD +1 momentum score, KDJ score removal, 4H aligned gate, Volume hard gate, LIMIT entry, 1H ATR stop and 2R full take-profit remain unchanged.
- Existing Algo REST snapshot + WebSocket synchronization and write-ambiguity fail-closed protections remain unchanged.

# KAYTRADE 1.5.2 — Pullback Entry Model

V1.5.2 rebuilds entry qualification around a persistent pullback-opportunity state machine. It is a strategy-validation release and does not claim profitability.

## Entry order

`1H direction -> 15m pullback opportunity -> closed 5m confirmation -> closed 1m recovery trigger -> score/hard gates -> LIMIT entry`

A high score cannot replace a missing mandatory stage.

## Direction gate

Long requires all of:
- 1H close > EMA200
- 1H EMA20 > EMA50 and both EMA20/EMA50 rising
- 15m EMA20 > EMA50

Short is the exact inverse. Neutral/incomplete direction waits. 1H direction no longer contributes +2 score.

## Persistent pullback opportunity

- Frozen zone = the 15m EMA20–EMA50 band plus 0.25 x 15m ATR on both edges.
- Long must enter the zone from above; short must enter from below.
- On touch, persist a unique opportunity id, frozen bounds, ATR, confirmed structure, front structure, direction and timestamps.
- Opportunity expires after 30 minutes or when trend/key structure invalidates.
- One opportunity can submit at most one entry. A new opportunity is impossible until price first leaves the previous frozen zone and later re-enters from the correct side.
- Opportunity/rearm state is stored locally, so restart does not create a duplicate opportunity.

## Mandatory confirmations

5m confirmation after touch:
- Long: bullish closed candle and close in its upper half.
- Short: bearish closed candle and close in its lower half.

1m trigger at or after that 5m confirmation:
- Long: upward EMA20 reclaim OR close above the prior three closed 1m highs.
- Short: downward EMA20 reclaim OR close below the prior three closed 1m lows.

KDJ does not independently qualify an entry.

## 10-point score

- +2 correct-direction pullback into the frozen zone — mandatory
- +1 planned entry not too far beyond the zone — mandatory
- +1 confirmed horizontal S/R overlaps the pullback zone
- +1 5m rejection confirmation — mandatory
- +1 1m recovery trigger — mandatory
- +1 5m MACD histogram improves in trade direction for two consecutive steps
- +1 / 0 / -1 for 4H aligned / neutral / opposite
- +1 15m EMA50 moved in trade direction versus three bars earlier
- +0.5 5m confirmation volume >= 1.2x prior-20 average
- +0.5 corresponding 5m KDJ cross after zone touch

Score is clamped to 0–10. Entry requires all mandatory items, no hard blocker and score >= **6.0**. BOLL/RSI remain diagnostics only. Position size is always one 1x signal; no add-on tiers return.

## Hard gates

- 1H/15m direction gate invalid
- saved 15m key structure broken by a close
- 5m MACD histogram worsens for two consecutive steps against the intended direction
- actual/planned limit too far beyond the frozen zone
- identified front strong structure leaves < **1.5R**
- 1H-ATR stop fails to clear the pullback extreme plus structural buffer
- declared fees + slippage > **0.30R**
- V1.5.1 adverse 4H support/resistance proximity gate remains
- timeout, active position, post-close cooldown, risk control or fault lock

If no front strong structure exists, status is explicitly **空间未知**. It is recorded separately and is never displayed as confirmed sufficient space.

## Shared structure definitions

Live runtime and V1.5.2 backtest use the same definitions:
- horizontal structure: existing radius-3 swing points, 0.25-ATR clustering, at least two tests, close-break invalidation; strong structure requires >=3 tests plus existing recency rule
- horizontal overlap tolerance: 0.25 x 15m ATR
- stop structural buffer: 0.10 x 15m ATR beyond the pullback extreme

## Position / exits

- One 1x position only; no add-ons or replenishment.
- LIMIT entry.
- First validation profile fixes stop at **1.0 x 1H ATR**.
- Full position TP at **2R**.
- TP/SL trigger exits execute as market orders.
- The stop is frozen after entry; later ATR expansion cannot widen it.
- 1.2/1.5 ATR stop profiles are intentionally reserved for separate later A/B tests and are not enabled in this first V1.5.2 runtime profile.

## Risk control

V1.5.1 behavior is preserved:
- three consecutive net losing cycles -> pause new entries exactly **1 hour** from the third close
- pause persists across date change and restart
- current streak resets at expiry; historical maximum streak remains recorded
- 30-minute post-close cooldown, daily drawdown limit and fault lock remain

## Backtest corrections / diagnostics

`v152_backtest_core.py` delegates strategy decisions to the same `v152_model.py` used by live runtime and adds:
- candle-continuity and funding-coverage validation
- maker entry / taker exit / declared slippage accounting
- full risk-state, one-hour loss-pause and 30-minute cooldown primitives for baseline and stress reruns
- strict limit-fill timing: the OHLC high/low of the bar that first fills the limit cannot be reused to claim a post-fill TP/SL; exit evaluation starts on the next 1m bar
- conservative SL-first handling when a later 1m bar touches both TP and SL
- per-trade opportunity id, score components, trigger combination, front-space R, cost R and MFE/MAE fields

Runtime logs deduplicate unchanged no-entry reasons for the same state/opportunity and keep explicit opportunity transitions.

## UI operation-lock hotfix

- Read-only account connection now finishes and releases the GUI operation lock after account/state verification; it no longer waits for multi-timeframe candle warm-up.
- Auto-trading authorization also releases its GUI operation lock before the first expensive strategy refresh.
- 4H / 1H / 15m / 5m / 1m candle warm-up starts on the following background strategy tick after authorization. Until valid closed candles are ready, the engine remains fail-closed and does not use stale signals.
- An idle connected session does not start candle warm-up before the user authorizes auto trading, unless a persisted local active order/position requires reconciliation.
- Trading writes remain serialized and ambiguous write states are never force-unlocked or retried by this UI fix.

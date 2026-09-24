# KAYTRADE 1.5.3 — 5m BOLL Pullback Entry

V1.5.3 replaces the former mandatory `5m rejection + 1m recovery trigger` entry chain with a mirrored 5m Bollinger pullback signal and a four-minute direct execution window. It remains a strategy-validation release and does not claim profitability.

## Entry order

`1H/15m direction -> closed 5m BOLL pullback signal -> four 1m execution minutes -> score/hard gates -> LIMIT entry`

1m is no longer a technical confirmation timeframe. It only defines the four-minute execution window after a valid closed 5m signal.

## Direction gate

Long still requires:
- 1H close > EMA200
- 1H EMA20 > EMA50 and the 1H trend state rising
- 15m EMA20 > EMA50

Short is the exact inverse. Neutral/incomplete direction waits.

## 5m BOLL entry module: fixed +2 points

The old `5m confirmation +1` and `1m recovery +1` are removed. They are replaced by one mutually exclusive BOLL entry module worth exactly +2 points.

### Long

Path A — middle + RSI:
- price approaches the 5m BOLL middle from above
- the closed 5m candle touches the middle within a tolerance of `0.10 x 5m ATR`
- 5m RSI >= 30
- result: +2

Path B — lower band fallback:
- if Path A did not qualify, the closed 5m low touches the BOLL lower band
- RSI is not required
- result: +2

### Short

Path A — middle + RSI:
- price approaches the 5m BOLL middle from below
- the closed 5m candle touches the middle within a tolerance of `0.10 x 5m ATR`
- 5m RSI <= 70
- result: +2

Path B — upper band fallback:
- if Path A did not qualify, the closed 5m high touches the BOLL upper band
- RSI is not required
- result: +2

The two paths never stack. The BOLL module contributes at most +2 for one direction and one closed 5m signal.

## Four-minute direct execution window

A valid closed 5m BOLL signal opens an execution window lasting exactly four minutes from that 5m close.

During those four 1m intervals:
- no 1m EMA reclaim is required
- no 1m prior-high/prior-low breakout is required
- no 1m KDJ, RSI or MACD confirmation is required
- if total score >= 6 and no hard blocker is active, the strategy may submit the LIMIT entry directly

At the end of minute four the signal expires. A stale 5m signal cannot be used later. The same signal cannot be used twice.

## Score

The score remains 0–10 with an entry threshold of 6.0. The BOLL module is mandatory and worth +2. Existing contextual factors remain available so the 10-point scale is preserved:
- +2 15m correct-direction pullback-zone resonance (auxiliary; no longer a mandatory stage)
- +1 planned entry remains near the 15m pullback zone (auxiliary)
- +1 confirmed horizontal S/R overlaps the 15m zone
- +2 5m BOLL entry module — mandatory
- +1 5m MACD histogram improves in trade direction for two consecutive steps
- +1 / 0 / -1 for 4H aligned / neutral / opposite
- +1 15m EMA50 moved in trade direction versus three bars earlier
- +0.5 signal 5m volume >= 1.2x prior-20 average
- +0.5 corresponding 5m KDJ cross on the signal candle

Mandatory execution checks are now the valid BOLL signal and its active four-minute window. The removed 5m-rejection and 1m-recovery gates cannot block V1.5.3.

## Hard gates retained

V1.5.2 risk and structural protections remain, including:
- direction invalidation
- confirmed structure break
- two-step adverse 5m MACD deterioration
- entry drifting too far from the frozen 5m BOLL trigger reference
- identified front strong structure leaving < 1.5R
- 1H-ATR stop failing to clear the 5m pullback extreme plus the structural buffer
- fees + slippage > 0.30R
- retained adverse 4H support/resistance proximity gate
- active position, post-close cooldown, daily drawdown, three-loss pause or fault lock

## Position / exits

- one 1x position only
- no add-on / second entry / replenishment mechanism
- LIMIT entry
- stop fixed at 1.0 x 1H ATR for this validation profile
- full-position TP at 2R
- TP/SL trigger exits use market orders
- stop cannot widen after entry

## Risk control

The existing controls remain:
- three consecutive net losing cycles -> pause new entries for exactly one hour
- 30-minute post-close cooldown
- daily drawdown control
- persisted fault lock and ambiguous-write fail-closed behavior

## UI scrolling

V1.5.3 hides visible vertical/horizontal scrollbar controls in the interface while preserving scrolling behavior:
- Mac trackpad two-finger scrolling remains active
- mouse-wheel scrolling remains active
- table/text regions retain their own scrolling
- horizontal wheel/trackpad behavior is retained where the widget supports horizontal scrolling

The goal is a cleaner interface without removing access to off-screen content.

# KAYTRADE V1.7.0 Build1700

V1.7.0 promotes the verified V1.6.8 NoEMA/BOLL0.10/PEE4 research branch into the packaged Intel Mac application while preserving the existing exchange-write safety and reconciliation stack.

## Strategy changes

- Removed the **1H EMA9/26 +1** score completely. It is no longer a score and is not a Hard Gate.
- Retained the existing **15m EMA50 directional +1** score.
- Kept **15m BOLL outer = 2 points**, valid until the next closed 15m candle.
- Added **5m BOLL overextension +1**: during the current 15m opportunity, a closed 5m candle that closes at least **0.10 × Wilder ATR(14)** beyond the direction-side outer band latches +1 until that opportunity expires.
- Kept **4H aligned = mandatory Hard Gate +1**.
- Kept **5m MACD explicit-adverse-only** entry blocking; directional improvement may score +1.
- Threshold remains **6.0**, position remains **fixed 1× LIMIT**, stop remains **1× 1H ATR**, target remains **full 2R**, **No-BE**.

## PEE4 production risk management

PEE4 monitors only the first four hours of a V1.7.0 position.

- Once historical MFE reaches **+0.60R**, PEE4 is permanently disabled for that trade.
- PEE4 starts only when current R is **≤ -0.60R**.
- Strict tier: **-0.60R to -0.70R**
- Strong tier: **-0.70R to -0.80R**
- Ordinary tier: **≤ -0.80R**
- Decision logic is Boolean and uses the verified 1H/15m direction reversal, 15m structure break, persistent 5m BOLL failure, adverse 5m MACD, and adverse volume conditions.
- An accepted PEE4 market-close request starts a **global 60-minute new-entry lock**.
- The close request persists a unique client ID before the exchange write. Ambiguous write outcomes remain fail-closed and are never automatically duplicated.

## UI synchronization

- Removed the retired 1H EMA9/26 score indicator from score/status UI.
- Added **5m BOLL overextension +1** to score/status UI.
- Removed retired K/D/J raw indicator rows.
- Added a dedicated **PEE4 风控** card directly below **交易计划** in **交易总览**.
- The PEE4 card shows current state, current R/MFE, active tier, reversal conditions, and Lock1H countdown.

## Safety inheritance

All Build1681 network/read-side recovery, account checks, Algo synchronization, Final Entry Guard, order ambiguity handling, LIMIT-entry safeguards, and reconciliation behavior remain underneath the V1.7.0 overlay.

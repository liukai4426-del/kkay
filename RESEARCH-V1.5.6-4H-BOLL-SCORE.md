# KAYTRADE V1.5.6 research — 4H BOLL score / 90D

Research branch only. Does not change the live V1.5.6 branch.

Requested score changes:
- Add +1 when 4H BOLL location agrees with the trade direction:
  - Long: latest closed 4H close is between the BOLL middle and upper band (inclusive).
  - Short: latest closed 4H close is between the lower band and BOLL middle (inclusive).
- Reduce 15m correct-direction pullback-zone resonance from +2 to +1.
- Preserve all other V1.5.6 / Build1544 entry triggers, hard blockers, LIMIT entry, 1.0x 1H ATR stop, 2R TP, Path C OFF, post-close cooldown OFF, consecutive-loss pause OFF.

Target backtest horizon: 90 days (~3 months).

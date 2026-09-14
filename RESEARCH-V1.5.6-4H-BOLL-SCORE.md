# KAYTRADE V1.5.6 research — 4H BOLL gate + score / 90D

Research branch only. Does not change the live V1.5.6 branch.

Requested changes:
- Add a mandatory 4H BOLL entry condition and award +1 when it passes:
  - Long: latest closed 4H close must be between the BOLL middle and upper band (inclusive).
  - Short: latest closed 4H close must be between the lower band and BOLL middle (inclusive).
  - If this condition fails, entry is not allowed even if the numeric score would otherwise reach 6.0.
- Reduce 15m correct-direction pullback-zone resonance from +2 to +1.
- Keep the total score scale capped at 10 and the opening threshold at 6.0.
- Preserve all other V1.5.6 / Build1544 entry triggers, hard blockers, LIMIT entry, 1.0x 1H ATR stop, 2R TP, Path C OFF, post-close cooldown OFF, consecutive-loss pause OFF.

Target backtest horizon: 90 days (~3 months), using the existing audited backtest execution/economic assumptions for comparability.

# KAYTRADE V1.3.8

V1.3.8 retains the existing 1-minute trigger, LIMIT entry, isolated-margin execution, whole-position TP=2R / SL=1R, fail-closed write handling and fixed execution-cost controls, while replacing the entry classification and consecutive-loss pause rules with the user-confirmed two-signal model.

## Two-signal scoring

- Final score remains on the 0-10 scale.
- Below 6.0: no automatic entry.
- 6.0-7.5: **开仓信号 / Opening Signal**.
- 8.0-10.0: **强信号 / Strong Signal**.
- No third signal level remains.
- If long and short are both eligible, the higher score is selected; an equal score remains in wait/observe state.

## Signal position sizing

- The Risk page now treats the existing position input as **第一信号仓位**.
- **第二信号仓位** is derived automatically as exactly 2x the first signal position and is displayed read-only.
- Opening Signal uses 1x sizing.
- Strong Signal uses 2x sizing.
- If an existing Opening Signal position later upgrades to a Strong Signal, the strategy can add the Strong Signal leg while preserving the 1:2 per-signal relationship; all existing capital, leverage, available-balance, daily-risk and exchange minimum-size limits still apply.

## Consecutive-loss protection

- Three consecutive losing completed cycles stop new entries for **6 hours**.
- The old "stop until the next day" behavior is removed.
- The pause survives application restarts through local strategy state.
- After the 6-hour pause expires, the consecutive-loss count is cleared and automatic entries may resume if all other checks pass.
- Existing positions and exchange-side TP/SL protection continue to be reconciled during the pause.

## Runtime log

- Runtime event and alarm lines show a `YYYY-MM-DD HH:MM:SS` timestamp in the visible log and in `events.log`.

## Preserved execution behavior

- Long and short entries use LIMIT orders.
- Entry/add-on decisions use the latest CLOSED 1m candle and 1m Trigger remains a hard gate.
- Isolated leverage remains 1-50x.
- Whole-position TP=2R and SL=1R remain unchanged.
- Expected-cost filter remains 1.20x minimum.
- Startup buffer, daily drawdown protection, order dedupe, account cross-check and ambiguous-write fail-closed handling remain in force.

This build is for controlled testing. Real OKX execution still requires account-side verification before production use.

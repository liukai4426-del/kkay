# KAYTRADE V1.3.8

V1.3.8 focuses on faster 1-minute execution timing and a revised 10-point scoring model while preserving the V1.3.7 tiered-entry, full-position 2R TP / 1R SL, fail-closed write handling and V1.3.6 cost controls.

## Strategy changes

- Long and short automatic entries use LIMIT orders.
- Isolated leverage setting expands from 1-10x to 1-50x.
- 1m Trigger replaces 5m Trigger. Trigger > 0 is required for both initial entries and add-ons and contributes up to +1 point.
- 5m + 15m RSI becomes a penalty: long >75/-1, >80/-2; short <25/-1, <20/-2. Penalties do not stack.
- Adverse 1H structure deducts -1: long near resistance / short near support.
- Adverse 4H structure deducts -1.5: long near resistance / short near support.
- 5m trend adds up to +2: MACD momentum +1 and Bollinger direction +1.
- 15m trend adds up to +2 with the same MACD + Bollinger logic.
- 15m favorable support/resistance remains up to +0.5; 15m Setup remains up to +1.5.
- 1D EMA5/10/20 support/resistance remains +1/+2/+3, highest only.
- 1H / 4H explicit countertrend penalties remain -1 each.
- Forward strong structure <1R still blocks the trade; 1.0-1.3R remains -1.
- Final score remains clipped to 0-10 with fixed auto-entry floor 3.5.

## Entry cadence

- Entry/add-on decisions are evaluated from the latest CLOSED 1m candle.
- One entry/add-on write maximum per closed 1m signal candle.
- Tier limits remain: 3.5-5.0 tier 1 x1, 5.5-7.0 tier 2 x1, 7.5-10 tier 3 x2.
- Unfilled 1m limit entries expire after the current 1m signal window and do not count as completed trades.

## Preserved safety behavior

- Whole-position TP=2R and SL=1R for each filled leg; no staged TP and no breakeven move.
- Expected-cost filter remains 1.20x minimum.
- Startup buffer, daily drawdown stop, consecutive-loss stop, order dedupe, account cross-check and ambiguous-write fail-closed handling remain in force.
- The fixed execution-cost panel remains borderless.

This build is for controlled testing. Real OKX execution still requires account-side verification before production use.

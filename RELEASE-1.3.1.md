# KAYTRADE V1.3.1 — Visual UI Update

Visual-only update based on KAYTRADE V1.3. Trading strategy, scoring rules, order execution and fail-closed risk controls are unchanged.

## Changes
- Score detail colors are direction-aware: long positive = green, long negative = red; short positive = red, short negative = green; zero = neutral.
- Input fields and option controls use dark rounded surfaces instead of native white blocks.
- Vertical scrollbars use a dark thumb/trough without white arrow blocks.
- Long/short score energy bars have no white outline and use smooth value transitions plus a subtle moving sheen.

## Safety
- No V1.3 strategy thresholds, indicators, position sizing, order routing, TP/SL, reconciliation, candle safety, network fail-closed logic or China-day risk guards were changed.
- Test in OKX Demo before live use.

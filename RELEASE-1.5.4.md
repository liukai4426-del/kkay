# KAYTRADE V1.5.4

V1.5.4 is a focused execution/UI update on top of the verified V1.5.3 BOLL pullback strategy. Scoring, direction gates, hard blockers, one-position rule, 1H ATR stop, 2R full take-profit, 30-minute post-close cooldown, daily drawdown control, 3-loss/1-hour pause, and fail-closed ambiguous-write handling remain unchanged unless listed below.

## 1. Smooth hidden scrolling

- Keep visible vertical/horizontal scrollbar controls hidden.
- Replace duplicate/global wheel handlers with one coalesced wheel/trackpad dispatcher.
- Accumulate small macOS trackpad deltas instead of converting every tiny delta into a full row jump.
- Coalesce bursts at an 18 ms UI cadence.
- Hidden `WideScrollbar` instances keep their state but do not repaint on every wheel event.
- Score detail, indicator table, history table, settings pages and other scrollable areas keep wheel/trackpad control without event duplication.

## 2. Run log scrolling

- The run-record `Text` area uses the same smooth wheel/trackpad dispatcher.
- If the user scrolls upward to inspect old logs, new log records do not force the view back to the bottom.
- If the user is already at the bottom, new records continue to auto-follow.
- Normal append-only updates avoid rebuilding the complete log body whenever possible.

## 3. BOLL entry window: first minute only

- V1.5.3 four-minute execution window is removed.
- A valid closed 5m BOLL signal is executable only during the immediately following first minute.
- Exact window: `signal_close_ms <= now_ms < signal_close_ms + 60_000`.
- At +60 seconds the signal is expired for opening purposes.
- 1m remains execution timing only; no EMA / breakout / KDJ / RSI / MACD technical confirmation is reintroduced.
- The order submit path rechecks wall-clock time immediately before the OKX write so a slow preparation/network step cannot submit after the first minute has expired.

## 4. Opening order: LIMIT -> MARKET

- Initial opening order changes from `ordType=limit` to `ordType=market`.
- Parent market order is still isolated-margin and still carries the existing full-position 2R TP / 1H ATR SL attached protection.
- Market-entry risk budget uses the executable side of the quote: ask for long, bid for short.
- Entry cost budget changes from Maker to Taker.
- Worst-case market execution budget reserves configured slippage on both market entry and market-on-trigger exit.
- Explicit OKX rejection releases the local opening reservation and stops new entries as before.
- Ambiguous network/write results remain fail-closed: local occupancy is preserved and duplicate submission is forbidden until reconciliation verifies exchange state.

## Unchanged strategy core

- Direction gate: 1H / 15m trend structure.
- 5m BOLL middle+RSI or outer-band signal remains the mandatory +2 BOLL module.
- Fixed opening threshold remains 6.0 / 10.
- One 1x strategy position only; no second entry or averaging.
- Front strong structure minimum remains 1.5R.
- Stop remains `1.0 x 1H ATR` validation geometry.
- Full-position take-profit remains `2R`.
- TP/SL triggers close at market.
- Post-close cooldown remains 30 minutes.
- Three consecutive net losses pause new entries for exactly one hour.

Build target: Intel Mac, macOS 14+.

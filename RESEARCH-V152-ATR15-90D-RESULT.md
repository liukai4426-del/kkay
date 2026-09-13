# KAYTRADE V1.5.2 — ATR15 90D Research Result

Window: 2026-06-15 03:16 UTC -> 2026-09-13 03:16 UTC (90 days)

Profile:
- score threshold >= 6.0
- stop = 1.5 x 15m ATR
- full take-profit = 3.0 x 15m ATR = 2R
- estimated fees + slippage <= 0.30R
- front strong-structure space >= 1.5R
- three consecutive net losses pause new entries for 1 hour
- limit entry; current V1.5.2 TP/SL trigger -> market exit model

## Core metrics

- Initial equity: 10,000 USDT
- Ending equity: 9,978.821416 USDT
- Net PnL: -21.178584 USDT
- Net return: -0.211786%
- Trades: 33
- Wins / losses: 12 / 21
- Win rate: 36.363636%
- Profit factor: 0.871184
- Expectancy: -0.641775 USDT/trade
- Average win: +11.935874 USDT
- Average loss: -7.829004 USDT
- Payoff ratio: 1.524571
- Max drawdown: 72.767622 USDT / 0.726532%
- Fees: 23.047658 USDT
- Funding: -0.855479 USDT
- Average hold: 364.878788 minutes
- Median hold: 151 minutes
- Stress net PnL (extra 5bps exit slippage): -37.647687 USDT
- Stress return: -0.376477%

## Direction

- Long: 20 trades, +8.766080 USDT, 40.0% win rate
- Short: 13 trades, -29.944663 USDT, 30.769231% win rate

## Score buckets

- 6.0: 6 trades, -6.307937 USDT, 33.33% WR, PF 0.779586
- 6.5: 2 trades, -13.537163 USDT, 0% WR, PF 0
- 7.0: 10 trades, +54.571019 USDT, 60% WR, PF 3.004559
- 7.5: 9 trades, -20.697124 USDT, 33.33% WR, PF 0.588557
- 8.0: 4 trades, -36.697535 USDT, 0% WR, PF 0
- 8.5: 2 trades, +1.490156 USDT, 50% WR, PF 1.185605

## Execution / risk funnel

- Pullback opportunities created: 521
- 5m confirmations: 297
- 1m triggers: 464
- Allow-entry states: 466
- Execution-gate blocks: 431
- Limit orders submitted: 35
- Limit orders filled: 33
- Limit orders unfilled: 2
- Closed trades: 33
- Three-loss 1h pause triggers: 4

Most execution-gate blocks were the 0.30R cost gate, concentrated between about 0.31R and 0.38R.

## Interpretation

The same profile was positive on the isolated last-30-day window (+23.37 USDT, PF 1.37) but is negative over this 90-day window. The 90-day result therefore does not support treating the 30-day result as robust. Long-side performance is slightly positive, while short-side performance is the main drag. The 7.0 score bucket remains strong in-sample, but higher scores are not monotonically better.

GitHub Actions Run: 34745285122
Job: 103691979760
Artifact: KAYTRADE-V1.5.2-ATR15-1.5x-90D
Artifact ID: 10313717757
Artifact SHA256: c770f37426f6a0e30d4a287fb68f84ace5477daa52619280e72cf4adecc0a6a3

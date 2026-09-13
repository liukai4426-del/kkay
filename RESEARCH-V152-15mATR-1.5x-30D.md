# V1.5.2 — 15m ATR ×1.5 Stop / 15m ATR ×3 TP — 30D Research Result

Window: 2026-08-14 03:16 UTC → 2026-09-13 03:16 UTC

Research-only parameters:
- Stop: 1.5 × 15m ATR
- Take profit: 3.0 × 15m ATR = 2R, full-position one-shot TP
- Entry threshold: 6.0
- Front-space minimum: 1.5R
- Cost maximum: 0.30R
- 3-loss pause: 1 hour
- Cooldown: 30 minutes
- Entry: limit
- Exit model in this research replay: TP/SL trigger → market exit with declared 5bps slippage

Results:
- Starting capital: 10,000 USDT
- Ending equity: 10,023.3717369 USDT
- Net PnL: +23.3717369 USDT
- Net return: +0.233717%
- Stress net PnL: +15.8755262 USDT
- Stress return: +0.158755%
- Trades: 15
- Wins / losses: 7 / 8
- Win rate: 46.6667%
- Profit factor: 1.370025
- Expectancy: +1.558116 USDT/trade
- Average win: +12.362049 USDT
- Average loss: -7.895325 USDT
- Payoff ratio: 1.565743
- Maximum drawdown: 50.979394 USDT / 0.506633%
- Fees: 10.484185 USDT
- Funding PnL: -0.582074 USDT
- Average hold: 418.67 minutes
- Median hold: 334 minutes

Side split:
- Long: 11 trades, +34.983189 USDT, 54.5455% win rate
- Short: 4 trades, -11.611452 USDT, 25.0% win rate

Exit split:
- TP2R: 7
- SL: 8

Score buckets:
- 6.0: 3 trades, -4.702330 USDT, 33.33% WR, PF 0.6635
- 7.0: 6 trades, +45.261475 USDT, 66.67% WR, PF 4.4281
- 7.5: 3 trades, -12.087654 USDT, 33.33% WR, PF 0.4342
- 8.0: 1 trade, -6.589909 USDT, 0% WR
- 8.5: 2 trades, +1.490156 USDT, 50% WR, PF 1.1856

Funnel:
- Allow-entry / eligible states: 133
- Limit submitted: 15
- Limit filled: 15
- Execution-gate blocks: 118
- 3-loss pause triggers: 1

All 118 execution-gate blocks were caused by estimated cost exceeding 0.30R, mainly in the 0.31R–0.38R range. Because the 15m ATR-based stop is usually tighter than the prior 1H ATR stop, the same absolute transaction cost occupies a larger fraction of R. Therefore this run tests the new stop model together with the internally consistent R-based gates; it is not a pure exit-only comparison.

Comparison with verified V1.5.2 30D baseline (1H ATR ×1.0 SL, 2R TP):
- Baseline: +16.512146 USDT, +0.165121%, 26 trades, 38.46% WR, PF 1.136719, max DD 0.729695%, fees 18.141294 USDT.
- 15m ATR ×1.5: +23.371737 USDT, +0.233717%, 15 trades, 46.67% WR, PF 1.370025, max DD 0.506633%, fees 10.484185 USDT.

Conclusion for this 30-day sample: 15m ATR ×1.5 / TP 3×ATR improved net PnL, win rate, PF, expectancy and drawdown versus the current V1.5.2 baseline, but the sample contains only 15 executed trades and should be expanded to at least 90 days before production adoption.

GitHub Actions Run: 34742701150
Job: 103684998861
Artifact: KAYTRADE-V1.5.2-15mATR-1.5x-30D
Artifact ID: 10312563479
Artifact SHA256: ea891b418d9184df70f30d647bf1a30017941adf1000fba62dd32c3b798de942

# V1.5.2 — 15m ATR ×1.5 Stop / 15m ATR ×3 TP / Cost Gate 0.40R — 30D

Window: 2026-08-14 03:16 UTC → 2026-09-13 03:16 UTC

Research-only parameters:
- Stop: 1.5 × 15m ATR
- Take profit: 3.0 × 15m ATR = 2R, full-position one-shot TP
- Cost hard gate: estimated fee + slippage > 0.40R blocks entry
- Entry threshold: 6.0
- Front-space minimum: 1.5R
- 3-loss pause: 1 hour
- Cooldown: 30 minutes
- Entry: limit
- Exit model in this replay: TP/SL trigger → market exit with declared 5bps slippage

Results:
- Starting capital: 10,000 USDT
- Ending equity: 9,976.0929446 USDT
- Net PnL: -23.9070554 USDT
- Net return: -0.239071%
- Stress net PnL: -36.8709578 USDT
- Stress return: -0.368710%
- Trades: 26
- Wins / losses: 8 / 18
- Win rate: 30.7692%
- Profit factor: 0.796819
- Expectancy: -0.919502 USDT/trade
- Average win: +11.719579 USDT
- Average loss: -6.536871 USDT
- Payoff ratio: 1.792842
- Maximum drawdown: 98.258186 USDT / 0.976490%
- Fees: 18.141189 USDT
- Funding PnL: -0.663554 USDT
- Average hold: 290.19 minutes
- Median hold: 139.5 minutes

Side split:
- Long: 19 trades, +3.934579 USDT, 36.8421% win rate
- Short: 7 trades, -27.841634 USDT, 14.2857% win rate

Exit split:
- TP2R: 8
- SL: 18

Score buckets:
- 6.0: 4 trades, -10.632227 USDT, 25.0% WR, PF 0.4659
- 6.5: 1 trade, -5.323680 USDT, 0% WR
- 7.0: 10 trades, +36.022009 USDT, 50.0% WR, PF 2.2143
- 7.5: 5 trades, -22.275151 USDT, 20.0% WR, PF 0.2940
- 8.0: 4 trades, -23.188162 USDT, 0% WR
- 8.5: 2 trades, +1.490156 USDT, 50.0% WR, PF 1.1856

Comparison with 0.30R cost gate under the same 15m ATR ×1.5 / TP 3×ATR setup:
- 0.30R: +23.371737 USDT, +0.233717%, 15 trades, 46.67% WR, PF 1.370025, max DD 0.506633%, fees 10.484185 USDT.
- 0.40R: -23.907055 USDT, -0.239071%, 26 trades, 30.77% WR, PF 0.796819, max DD 0.976490%, fees 18.141189 USDT.

Interpretation:
Relaxing the cost hard gate from 0.30R to 0.40R admitted 11 additional trades, but those added trades materially reduced signal quality. The strategy moved from positive expectancy and PF > 1 to negative expectancy and PF < 1. The short side deteriorated especially sharply. For this 30-day sample, the 0.30R cost gate is clearly superior to 0.40R.

GitHub Actions Run: 34743344851
Job: 103686675719
Artifact: KAYTRADE-V1.5.2-15mATR-1.5x-Cost040R-30D
Artifact ID: 10312823768
Artifact SHA256: de080fcc2089aa066edb7cf2c5864c8bf9814c7130e3b19fdc24a1d31816e5f2

# KAYTRADE V1.4.1

V1.4.1 is a focused UI, position-sizing, history-chart and exit-order update on top of V1.4.

## Signal position sizing

The previous `最大名义首仓` / `最大名义总仓位` controls are replaced in the UI by three independent signal-level notional caps:

- `第一信号仓位 USDT` for Tier 1 scores 4.0–6.0
- `第二信号仓位 USDT` for Tier 2 scores 6.5–7.5
- `第三信号仓位 USDT` for Tier 3 scores 8.0–10.0

Each tier can now enter at most once. A full trade cycle therefore has at most three entry events: Tier 1 once + Tier 2 once + Tier 3 once. If the first valid signal starts directly at Tier 2 or Tier 3, KAYTRADE uses that tier's configured signal position and does not backfill lower tiers.

The configured signal position is still only an upper notional cap. Actual order size remains constrained by 15m ATR risk sizing, available balance, leverage, daily risk, OKX minimum size and all existing execution safety checks.

Old V1.4/V1.3.9 settings are migrated when the UI opens. If the three new fields do not exist yet, V1.4.1 derives practical initial defaults from the previous initial/total notional values; after saving, the three new fields are persisted.

## Result dialogs

All KAYTRADE success/failure result dialogs now:

- center the icon, title, body text and confirm button
- center the popup relative to the main KAYTRADE window
- remove the contrasting dark/black outer gutter
- retain green success and red failure confirmation buttons

## Historical return chart

The `历史收益` trend chart now uses time and percentage performance directly:

- horizontal axis: closed-round date
- vertical axis: cumulative return rate (%)
- cumulative return rate = cumulative account-equity change recorded by KAYTRADE / current `策略资金预算` × 100%
- the chart includes dated ticks and percentage grid labels
- the historical detail table continues to show PnL and cumulative PnL in USDT

## TP / SL execution

All attached take-profit and stop-loss protection now uses trigger-limit execution instead of OKX's `-1` market-execution sentinel:

- take-profit trigger price = take-profit limit price
- stop-loss trigger price = stop-loss limit price
- both long and short positions use explicit limit prices for attached TP/SL
- the existing 2R full-position take-profit and 1R stop-loss levels are unchanged
- the conservative cost/risk model still budgets taker-like exit costs because a limit order is not guaranteed to earn maker fees

A limit stop is not guaranteed to fill during a fast price move. If an OKX TP/SL trigger creates a limit child order that remains pending, KAYTRADE keeps that limit exit in place, stops new entries and fault-locks for account verification rather than treating the position as closed. Manual flatten and emergency safety flatten remain separate safety operations and are not TP/SL orders.

## Preserved behavior

V1.4 scoring remains unchanged: fixed 4.0 entry floor, Tier 1 4.0–6.0, Tier 2 6.5–7.5, Tier 3 8.0–10.0. 1m Trigger, 5m/15m MACD+BOLL trend scoring, RSI penalties, 1H/4H structure penalties, LIMIT entries, 15m ATR 1R stop / 2R full take-profit, cost filter, fault lock, order dedupe and daily safety controls are unchanged.

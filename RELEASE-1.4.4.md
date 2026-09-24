# KAYTRADE V1.4.4

V1.4.4 is a presentation-only refinement on top of V1.4.3. The trading strategy, score arbitration, sizing, TP/SL, order type, deduplication and fault-lock logic remain unchanged.

## UI refinements

- All visible action buttons use a true capsule-like maximum corner radius.
- Button and scrollbar surfaces use supersampled anti-aliasing when Pillow is available to reduce visible jagged/pixelated edges on macOS.
- Main-page, score-detail, indicator-value and history scrollbars are forced to the same 20 px width and use maximum rounded ends.
- In Account Connection, `账户官方域名`, `环境`, `API Key`, `Secret Key`, and `Passphrase` labels use the same panel background instead of black label blocks.
- `网络自检（不下单）` is shortened to `网络自检`; `测试连接（只读）` is shortened to `测试连接`.
- Trade Overview / Trade Plan visible tiles now use the same one-third column width: the first row aligns to the same three-column grid as the rows below.
- The legacy startup hint `V1.3.6 最高10分 · 策略不变 · 新增自动下单逐步决策追踪` is removed. Live signal text still appears after market scoring begins.

## Preserved V1.4.3 behavior

- When long and short are simultaneously eligible, only the higher final score may open.
- Equal eligible scores wait for a later closed 1m refresh; no order is submitted on the tied cycle.
- Entry remains limit; attached TP/SL remain trigger-limit; manual/safety flatten remains market.

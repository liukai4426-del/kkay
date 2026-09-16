# KAYTRADE V1.6.8 Build1681

Build1681 is a runtime/log synchronization hotfix on top of the verified V1.6.8 Build1680 strategy. **No trading strategy, score, risk, order-construction or write-safety parameter is changed.**

## Fixed

- Final UI/log boundary now normalizes inherited old-version copy to the active V1.6.8 wording.
- Removed residual **5m BOLL中轨/外轨** wording. Active BOLL semantics remain strictly **latest closed 15m outer band only**; 5m continues only as RSI/MACD/Volume confirmation.
- Old BOLL execution-window copy such as `第5个1m` is normalized to the active 15m signal lifecycle.
- Old `计划市价参考` / market-entry wording is normalized to the current **LIMIT entry** semantics.
- Legacy version labels such as V1.5.4 / V1.6.2 / V1.6.6 no longer leak into current runtime reasons.
- Expected `/ws/v5/business:orders-algo` cache resynchronization is now displayed as **Algo状态同步中** instead of a generic `只读查询暂时失败` alarm.
- When available, the underlying Algo resynchronization error is appended for diagnosis.
- The Algo synchronization safety behavior itself is unchanged: an untrusted read-side cache skips the current new-entry cycle, keeps auto-trading authorization enabled, resynchronizes REST+WebSocket in the background, and must pass Final Entry Guard again before any order can be written.

## Strategy lock preserved from Build1680

- BOLL source: latest CLOSED **15m upper/lower outer band only**.
- 15m BOLL outer score: **+2.0**.
- Signal lifecycle: valid from trigger 15m close until the next 15m close (**900,000 ms**).
- 4H aligned: **+1 and mandatory Hard Gate**; neutral/opposite blocks entry.
- 1H EMA9/EMA26 aligned: **+1 score only**, not a Hard Gate.
- 5m RSI: **30–70 Hard Gate**.
- 5m MACD: only explicit adverse/opposite deterioration blocks; improvement retains **+1**.
- 5m Volume: must remain **< 1.20×** prior-20 average; no volume score.
- KDJ remains removed from score/gates.
- Threshold remains **>=6**.
- Fixed first entry **1× LIMIT**, SL = **1×1H ATR**, full TP = **2R**, No-BE.
- Existing structure, cost, entry-drift, daily-risk, idempotency and ambiguous-write fail-closed protections remain unchanged.

## Regression coverage added

The exact mixed legacy line observed in the live run record is now a regression fixture, covering:

- `V1.5.4未开仓`
- `BOLL信号执行窗口 · 第5个1m`
- `计划市价参考`
- `V1.6.2要求4H同向`
- `5m BOLL中轨/外轨`
- `V1.6.6 Volume Hard Gate`
- `/ws/v5/business:orders-algo` synchronization copy

Packaged smoke tests also verify the final app bundle reports Build1681 and still locks the Build1680 strategy constants.

# KAYTRADE V1.7.1 Build1710

V1.7.1 is a maintenance release based on V1.7.0 Build1701. The trading strategy, risk parameters, PEE4 logic and order sizing are unchanged.

## Fixed: strict 15m BOLL opportunity source

The Build1701 source lock remains mandatory:

- a new long opportunity requires the latest **closed 15m** candle low to touch or cross the 15m lower BOLL band;
- a new short opportunity requires the latest **closed 15m** candle high to touch or cross the 15m upper BOLL band;
- inherited/legacy 5m BOLL paths are evaluation-only and are always called with new-opportunity creation disabled;
- 5m BOLL may contribute only the V1.7 overextension score / PEE4 risk input;
- execution checks and Final Entry Guard reject any opportunity without immutable valid 15m outer-band evidence.

This prevents the observed case where a legacy 5m candidate could be presented as a 15m BOLL opportunity while the 15m candle itself had not touched the outer band.

## Fixed: closed-round history synchronization

The historical performance parser previously accepted only the exact event names `仓位归零` and `人工核对平仓`. Later strategy overlays write version-prefixed events such as `V1.3.7仓位归零`, so confirmed closed rounds could be missing from the History page even though the runtime log correctly reported the PnL.

V1.7.1 now accepts legacy and version-prefixed confirmed-flat events ending in:

- `仓位归零`
- `人工核对平仓`

Submission and early-exit request events such as `PEE4提前退出` remain excluded to prevent double counting. Records are still de-duplicated by program client order ID.

A regression fixture covers the reported example: previous cumulative +4.6675 USDT plus a confirmed -94.9380 USDT round becomes 7 closed rounds, 3 wins / 4 losses, approximately 42.9% win rate, and -90.2705 USDT cumulative.

## Preserved V1.7 strategy

- threshold: 6.0
- fixed 1× entry
- 15m BOLL outer-band score: 2
- 15m opportunity valid until the next 15m close
- 5m BOLL overextension >= 0.10 ATR14: +1 latched within the opportunity
- 15m EMA50 directional score retained
- 1H EMA9/26 score removed
- 4H aligned Hard Gate +1 retained
- 5m RSI / Volume / MACD rules retained
- 1× 1H ATR stop
- full 2R target
- No-BE
- PEE4 tiered Boolean risk exit + global Lock1H retained

## Package identity

- Version: 1.7.1
- Build: 1710
- Intel macOS target: macOS 14+

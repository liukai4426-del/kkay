# KAYTRADE V1.7.0 Build1701

Build1701 is a targeted production hotfix on top of Build1700. It does not change the verified V1.7.0 scoring, position sizing, SL/TP, PEE4 or Lock1H rules.

## Strict 15m BOLL opportunity source

- New entry opportunities can now be created only from the latest closed 15m BOLL outer-band evidence.
- Long requires: 15m low <= 15m lower band.
- Short requires: 15m high >= 15m upper band.
- The historical inherited evaluator is called with new-opportunity creation disabled. This prevents old 5m BOLL code paths from briefly creating a candidate before the later 15m validator removes it.
- Existing stored opportunities are accepted only when their immutable boll_timeframe=15m evidence and outer-band values validate.
- Execution checks and Final Entry Guard independently reject any opportunity without valid closed-15m BOLL evidence.
- 5m BOLL remains only the V1.7.0 overextension +1 / PEE4 input; it cannot create an entry opportunity.

## Run-log direction

Opening-related run records now explicitly identify the trade direction when known: 方向：做多 or 方向：做空.
This applies to opening blocks, signal-window records, invalidation records and entry-request related logs. Genuine opportunity invalidations also carry direction in the transition detail itself.

## Preserved V1.7.0 rules

No changes to NoEMA1H, 15m EMA50 +1, BOLL5 overextension 0.10 ATR14 +1, 4H aligned Hard Gate +1, RSI/Volume/MACD rules, threshold 6, fixed 1x LIMIT entry, 1H ATR stop, full 2R target, No-BE, PEE4 or global Lock1H.

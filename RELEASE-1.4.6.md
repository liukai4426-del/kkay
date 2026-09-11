# KAYTRADE V1.4.6

## UI polish

- LONG / SHORT score energy bars are 1.6× thicker than V1.4.5 and use maximum capsule rounding for both track and fill.
- Account state and network state stay on one horizontal row and share the same 15 px left alignment baseline as the main content panels.
- The top brand block is scaled to 1.2× the V1.4.5 presentation: larger circular gradient logo, KAYTRADE name, BTC / USDT and version line.
- The bottom log area is redesigned as a rounded `运行记录` card with subtitle `所有策略循环与安全拦截`.
- The legacy `全部` log filter/dropdown is removed from the visible interface.
- Run-record status dots are semantic: waiting = yellow, alarm/exception = red, normal operation = green.

## Preserved behavior

V1.4.6 is presentation-only. It inherits V1.4.5 layout fixes and V1.4.3/V1.4.4 trading behavior unchanged, including score arbitration, three signal-position caps, limit entries, trigger-limit TP/SL, market manual/safety flatten, ATR risk sizing, deduplication, drawdown/consecutive-loss stops and fault locks.

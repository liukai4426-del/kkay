# KAYTRADE V1.5.6 Build 1560

V1.5.6 is a UI/log presentation release based on the verified V1.5.5 Build1551 trading core.

## Changes

- Run Log card height reduced from 255 to 140 pixels.
- Run Log entries use a structured two-level layout: status dot/status text + full `YYYY-MM-DD HH:MM:SS` timestamp + bold title + muted detail.
- Run Log state colors are fixed to: waiting = yellow, normal running = green, alarm = red.
- Run Log scrollbar uses a borderless panel-matched track; the previous visible dark/black edge is removed.
- Mouse wheel/trackpad scrolling and reader-position preservation remain enabled.
- Any remaining visible `第二信号仓位` row/input is physically destroyed from Risk Settings.
- Any remaining visible `平仓后冷却` row/input is physically destroyed from Execution Settings.
- Backend compatibility metadata may remain, but neither removed control is editable or visible.

## Trading behavior preserved

- Path C remains OFF.
- Post-close cooldown remains OFF.
- Consecutive-loss pause remains OFF.
- Build1544 entry model, score threshold, hard blockers, LIMIT entry, 1H ATR stop and 2R take-profit remain unchanged.
- V1.5.5 first-signal isolated-margin semantics and leveraged-notional auto calculation remain unchanged.

## Packaging

- Intel Mac x86_64.
- Minimum deployment target remains macOS 14.
- PyInstaller keeps the single-runtime-hook architecture introduced by Build1551 to avoid recursive overlay loading.

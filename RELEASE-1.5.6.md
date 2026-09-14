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

## Opening stability hotfix

- OKX `51054 / Request timed out` is now classified as an ambiguous write result instead of an explicit rejection.
- A timed-out Place Order keeps the local idempotency/active-order placeholder so the program cannot immediately resubmit a duplicate order.
- The 51054 log includes the HTTP method and API path and explicitly requires order/fill/position reconciliation.
- Place Order requests now include OKX `expTime` with an 8-second effective deadline so delayed requests are discarded by the exchange instead of arriving stale.
- Before opening, the program reads the current isolated leverage first. If the target leverage is already active, it skips the old duplicate `set-leverage` POST.
- If `set-leverage` itself has an ambiguous timeout, the program does not retry the write. It reads leverage back from OKX and continues only when the target leverage is confirmed.
- Explicit leverage/order rejections remain fail-closed and still stop new entries.
- The trading model, score threshold, BOLL prerequisite, LIMIT entry price logic, stop and take-profit model are unchanged.

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

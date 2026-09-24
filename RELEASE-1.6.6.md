# KAYTRADE V1.6.6 Build1660

V1.6.6 is a UI-only maintenance release on top of the audited V1.6.5 runtime and the current 15m BOLL outer-trigger patch.

## UI changes

- Opening-status panel now separates `15m BOLL外轨触发` from `5m执行窗口`, avoiding duplicated-looking BOLL rows.
- Fixed the presentation bug that could turn `15m BOLL外轨` into `115m BOLL外轨` after repeated text rewriting.
- Run-log details now appear on the same line after the bold white title whenever space allows.
- Wrapped run-log detail lines align with the white title/content column rather than the status/time columns.
- Increased the horizontal gap between timestamp and content.
- Slightly increased vertical spacing in the opening-status panel for readability.
- Visible application version is V1.6.6 Build1660.

## Trading semantics

No trading logic is changed in this release. V1.6.5 score, Hard Gate, LIMIT entry, risk, SL/TP, network safety and exchange-write behavior remain unchanged.

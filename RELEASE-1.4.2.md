# KAYTRADE V1.4.2

V1.4.2 is a visual refresh on top of the fully preserved V1.4.1 trading strategy and execution behavior.

## Visual changes

- Primary green action buttons now use the same `GREEN` color as the long-score panel.
- Primary red / danger action buttons now use the same `RED` color as the short-score panel.
- This applies to actions including start auto trading, flatten this program's position, connection test, risk-setting save, and result-dialog confirmation buttons.
- Hover / pressed states remain in the same high-purity color family.
- All `RoundedButton` controls use a larger corner radius so the UI no longer looks square while staying short of a full pill shape.

## Top-right state

- Default unconnected state: `默认停止 · 未连接`, shown in yellow.
- Connected normal state: green.
- Fault / locked state: red.
- Existing status text continues to update with the running state; only its visual status treatment changes.

## Brand logo

- The user-selected circular green-to-cyan gradient mark is now the KAYTRADE header logo.
- The same selected mark is used to generate the macOS application icon on a dark rounded background.
- The transparent logo asset is packaged inside the application bundle.

## Preserved behavior

All V1.4.1 trading behavior is unchanged, including the fixed 4.0 entry floor, Tier 1/2/3 score ranges, one entry per tier, three independent signal-position caps, 1m trigger, 15m ATR risk model, limit entry, trigger-limit TP/SL, market manual flatten, market safety flatten, fault lock, order dedupe, daily safety controls, historical return chart, and centered result dialogs.

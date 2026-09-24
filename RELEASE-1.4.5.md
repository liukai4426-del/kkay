# KAYTRADE V1.4.5

## Main UI layout and feedback repair

- Fixes the V1.4.4/V1.4.5 regression where a selected main page could collapse into a narrow right-side region and leave a large unused area.
- Main tabs occupy the full application content width below the navigation strip.
- Account Connection is one responsive full-width panel; labels stay left and fields/results expand across available width.
- Network Self-check and Test Connection remain simplified without parenthetical suffixes.
- All KAYTRADE custom success/failure result dialogs use one centered layout: icon, title, message and confirmation button are centered, and the black outer frame/gutter is removed.
- Header branding is slightly enlarged: the KAYTRADE title and version line are larger, and the spherical logo artwork is only subtly enlarged while preserving the existing gradient design.
- The macOS application icon uses the same subtle spherical-logo enlargement.
- The connected-account display no longer uses a green rectangular background. Connected state is shown with green text on the normal dark background.
- The top-right status, realtime USDT equity and compact runtime description share the same right alignment with the main panel edge.
- The runtime description remains a small 9 pt muted-gray line.
- The one-line market/score summary above LONG/SHORT cards uses a smaller 11 pt muted font and aligns with the left edge of the score panels.
- Historical-return chart keeps date ticks and percentage ticks but removes the standalone axis-title words “日期” and “收益率”.
- V1.4.4 capsule buttons, anti-aliasing and unified rounded scrollbars are preserved.
- Width normalization remains scoped to peer panels inside the same section; unrelated page regions are not forced to the same width.

## Trading behavior

V1.4.5 remains a UI/layout-only repair. V1.4.3/V1.4.4 trading behavior is preserved unchanged, including score arbitration, signal tiers, position caps, TP/SL execution, risk controls, deduplication and fault locks.

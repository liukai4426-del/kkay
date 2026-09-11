# KAYTRADE V1.4.5

## Main UI layout repair

- Fixes the V1.4.4 regression where a selected main page could collapse into a narrow right-side region and leave a large unused area.
- Main tabs now occupy the full application content width below the navigation strip.
- The Account Connection page is rebuilt as one responsive full-width panel; labels stay on the left, fields expand across the available width, and the result area expands below them.
- Network Self-check and Test Connection remain simplified without parenthetical suffixes.
- The compact runtime description above the main tabs uses a smaller 9 pt font, muted gray text, and right alignment without changing the status/title styling.
- V1.4.4 capsule buttons, anti-aliasing, unified 20 px rounded scrollbars, logo, status colors and text cleanup are preserved.
- Width normalization remains scoped to peer panels inside the same section. Unrelated page regions are not forced to the same width.

## Trading behavior

V1.4.5 is a layout-only repair. V1.4.3/V1.4.4 trading behavior is preserved, including score arbitration, signal tiers, position caps, TP/SL execution, risk controls, deduplication and fault locks.

# KAYTRADE V1.4

## Scoring tiers
- Fixed automatic-entry floor: **4.0 / 10**.
- Tier 1: **4.0–6.0**, 1.0× position multiplier, max 1 Tier-1 entry per cycle.
- Tier 2: **6.5–7.5**, 1.5× position multiplier, max 1 Tier-2 add-on per cycle.
- Tier 3: **8.0–10.0**, 2.0× position multiplier, max 2 Tier-3 add-ons per cycle.
- **8.0 belongs to Tier 3** so the two tiers do not overlap at runtime.
- Existing 1m Trigger, 15m ATR risk model, full 2R TP / 1R SL, LIMIT entries, max-initial/max-total notional controls and all existing safety locks remain unchanged.

## Unified result dialogs
V1.4 adds a KAYTRADE-styled rounded status modal with one Confirm button.

Success uses the existing green accent; failure uses the existing red accent. The following user actions now receive explicit result feedback:
- account connection
- settings validation/save
- flattening the program-managed position
- fault-lock acknowledgement
- starting automatic trading

The flatten success dialog is shown only after reconciliation confirms that the program-managed position is actually cleared; sending the market-close request alone is not treated as success.

## Packaging
- Intel x86_64 macOS app
- macOS 14.0 minimum
- Bundle short version: 1.4
- Bundle version: 140

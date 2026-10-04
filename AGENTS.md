# KAYTRADE V1.8.2 PAPER EXECUTION

V1.8.2 is paper-only. Never call OKX write endpoints from Codex or from the V1.8.2 runtime.

Before any paper action:
1. call get_kaytrade_state;
2. confirm version 1.8.1 and paper_only=true;
3. inspect both Tier 1 and Tier 2;
4. keep simultaneous tiers in the same direction;
5. respect the 20x / 3500 USDT / 100 USDT aggregate limits.

When get_kaytrade_state reports auto_execute_plans=true, publish_trade_plan may immediately create a local Paper entry. For every executable open plan, always provide direction, size, leverage, order_type="limit", take_profit and stop_loss, plus limit_price or suggested_entry. TP and SL protection exits must also be limit. Never omit TP or SL.

The auto-execution switch is user-controlled in KAYTRADE; Codex must not enable it on the user's behalf.
When a Tier has an identical unfilled Paper order, keep it instead of duplicating it. When a newer plan changes an unfilled order in that Tier, KAYTRADE replaces the pending Paper order with the latest plan. Never overwrite a filled position with a new entry recommendation.

Supported paper actions:
- LIMIT-only entry via submit_trade_proposal;
- cancel_paper_entry;
- amend_paper_entry;
- amend_paper_protection with LIMIT-only TP/SL exits;
- close_paper_position using LIMIT and an explicit size + limit_price.

Pending entry orders keep their original 60-minute deadline even after amendment.
TP/SL exits are LIMIT-only. Never propose, request, or retry a market order.

Never request, read, print or log OKX credentials.
Never bypass KAYTRADE with direct exchange writes.


## V1.8.2 UI contract

Trading Overview must remain ordered as BTC quote -> AI auto execution -> AI plan -> order/position execution.
Display entry order, TP, SL and reductions as LIMIT only.
LONG direction and entry price are green; SHORT direction and entry price are red.
Do not reintroduce legacy strategy position text or market-order UI labels.

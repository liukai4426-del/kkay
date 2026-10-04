# KAYTRADE V1.8.1 PAPER EXECUTION · Build1812

V1.8.1 converts the V1.8 AI-only execution layer into a local paper-trading state machine.
OKX is used only for read-side market/account context; this build does not send OKX order,
amend, cancel, leverage or close writes.

## Paper order model

Two independent slots are persisted:

- Tier 1
- Tier 2

Both tiers may be active at the same time only when they share the same direction.
Each tier persists its own entry order, fill, TP/SL protection, exit order, timer and history.

## Entry

Supported:
- market paper entry;
- limit paper entry;
- Tier 1 + Tier 2 same-direction concurrent limit orders;
- 60-minute timeout from original acceptance time;
- automatic cancellation of the unfilled entry remainder when the deadline is reached;
- cancellation and amendment of a live limit entry;
- amendment preserves the original 60-minute deadline.

## AI plan auto execution

Trading Overview now includes a user-controlled **AI方案自动执行** switch.

When enabled:
- every complete AI **open** recommendation is immediately converted into a local Paper order;
- market recommendations fill at the current simulated market price;
- limit recommendations are placed at `limit_price`, or `suggested_entry` when the limit price is omitted;
- `take_profit` and `stop_loss` are both mandatory;
- direction, size, leverage and order type are also required;
- a limit recommendation additionally requires an entry price;
- if any mandatory field is missing or the plan violates the existing Paper limits, the recommendation remains visible but is marked rejected and no Paper order is created;
- if the same Tier already has an identical unfilled recommendation, the existing Paper order is kept without duplication;
- if that Tier has a changed but still-unfilled recommendation, the old pending Paper order is replaced with the newest plan and a new 60-minute deadline begins;
- if the Tier already has a filled position or active exit, a new entry recommendation is rejected instead of overwriting the position.

The switch is OFF by default and can only be enabled after the Paper AI channel is running.
Codex cannot turn this user setting on by itself.

## Protection

Each tier has independent TP and SL settings.

Both TP and SL can use:
- market exit after trigger;
- limit exit after trigger.

For a limit protection exit, the trigger first creates a local paper limit exit and the
position remains open until the simulated market reaches the specified limit price.

AI can amend:
- TP trigger;
- SL trigger;
- TP exit type;
- SL exit type;
- TP limit price;
- SL limit price.

## Partial close / reduction

Paper positions support:
- market reduction by a specified contract quantity;
- limit reduction by a specified contract quantity;
- targeting Tier 1 or Tier 2;
- aggregate reduction across both tiers when no tier is specified.

## Hard limits

The existing local limits remain:
- leverage <= 20x;
- combined Tier 1 + Tier 2 notional <= 3500 USDT;
- combined estimated maximum stop-loss <= 100 USDT.

## Trading Overview UI

- BTC price panel is moved to the very top of Trading Overview.
- The AI plan board remains in KAYTRADE's layered card visual language.
- LONG is shown in green.
- SHORT is shown in red.
- Tier 1 / Tier 2 execution panels show their own entry type, state, entry/fill, TP/SL and remaining paper position.
- A dedicated auto-execution settings card shows ON/OFF state and the mandatory TP + SL rule.

## Codex / MCP commands

Read:
- get_kaytrade_status
- get_kaytrade_state

Plan:
- publish_trade_plan

Paper execution:
- submit_trade_proposal
- cancel_paper_entry
- amend_paper_entry
- amend_paper_protection
- close_paper_position

No command in V1.8.1 sends an OKX write request.

## Package

- Version: 1.8.1
- Build: 1812
- Mode: PAPER AI
- Intel macOS 14+

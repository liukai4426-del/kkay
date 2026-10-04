# KAYTRADE V1.8.0 AI ONLY · Build1801

V1.8.0 keeps the V1.7.2 AI-only architecture and upgrades the execution/UI layer.

## Entry orders

AI open proposals support both:

- \`order_type="market"\`: submit a market entry.
- \`order_type="limit"\`: submit a limit entry and require \`limit_price\`.

Limit prices must follow the OKX instrument tick size. Risk calculations use the
requested limit price for limit entries and the latest market price for market
entries.

Pending limit orders are allowed to remain \`live\` without the 15-second market
order timeout. A partially filled limit order remains managed until filled or
cancelled. Once the parent order is filled, KAYTRADE continues to verify the
attached TP/SL protection.

Closing the currently managed position remains a market close in V1.8.0.

## AI plan board

The Trading Overview now uses KAYTRADE's own layered card layout:

- a dominant main-plan area for LONG/SHORT direction and suggested/limit entry;
- separate TP and SL emphasis blocks;
- compact Market/Limit, tier and leverage badges;
- AI recommendation reason and operation advice with different visual weight;
- a separate recent-plan strip rather than repeating identical tiles.

\`publish_trade_plan\` can update the plan board without placing an order.

## Order / position board

The order/position execution board returns to KAYTRADE's earlier trading-plan card language and shows:

- current order state;
- Market/Limit order type;
- requested order price;
- actual average fill price when known;
- order/position size;
- TP and SL;
- proposal ID;
- separate Tier 1 and Tier 2 plan summaries.

## Hard execution caps

- perpetual leverage <= 20x;
- notional <= 3500 USDT;
- estimated maximum stop-loss <= 100 USDT.

Mandatory TP/SL, proposal-id idempotency, account/position consistency checks,
and isolated long/short mode checks remain enabled.

## AI-only boundary

Local BOLL / EMA / RSI / MACD / score / Gate / PEE4 logic never creates a new
V1.8 order. New entries only originate from the structured AI proposal path.

Autonomous AI writes remain restricted to OKX Demo Trading. Live-account AI
writes are hard-disabled.

## Codex tools

- \`get_kaytrade_status\`
- \`get_kaytrade_state\`
- \`publish_trade_plan\`
- \`submit_trade_proposal\`

For a limit entry, submit for example:

\`\`\`text
submit_trade_proposal(
    action="open",
    direction="long",
    order_type="limit",
    limit_price=85000,
    size=2,
    leverage=10,
    take_profit=86500,
    stop_loss=84200,
    tier=1,
    operation_advice="挂限价等待回踩成交",
    reason="AI plan"
)
\`\`\`

## Package identity

- Version: 1.8.0
- Build: 1801
- Product mode: AI ONLY
- Intel macOS target: macOS 14+

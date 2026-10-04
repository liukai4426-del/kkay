# KAYTRADE V1.8.4 OKX DEMO CLEAN · Build1840

V1.8.4 resolves two regressions while preserving the clean OKX Demo-only runtime.

## 1. Refined AI plan board restored

The V1.8.2 refined layout is restored as a pure presentation layer, without restoring any Paper runtime:

- “建议 / 委托入场” is directly above the entry price;
- direction and entry price share the same left alignment;
- LONG direction + entry are green;
- SHORT direction + entry are red;
- TP and SL are separate rounded panels;
- recommendation reason, operation advice and recent-plan sections are rounded;
- meta badges are compact and rounded;
- page order remains BTC quote -> AI auto execution -> AI plan -> order/position execution.

## 2. OKX -1 market sentinel eliminated

OKX interprets tpOrdPx=-1 / slOrdPx=-1 and their amend equivalents as market execution.

Build1840 never forwards that sentinel.

Protection-price normalization is enforced at three layers:

1. Codex/MCP input:
   missing, zero or negative TP/SL limit prices are normalized to the positive TP/SL trigger price.
2. KAYTRADE execution engine:
   TP/SL protection limit prices must be positive and tick-aligned.
3. Exchange transport firewall:
   outbound market entry orders and any TP/SL -1 sentinel are rejected before HTTPS submission.

This applies to:
- new attached TP/SL;
- AI plan auto execution;
- direct structured trade proposals;
- protection amendments before fill;
- protection amendments after fill.

## Runtime

- Version: 1.8.4
- Build: 1840
- Mode: OKX_DEMO_EXECUTION
- local Paper runtime: absent
- live-account AI writes: disabled
- entry: LIMIT only
- TP/SL: trigger + positive LIMIT order price
- reductions: LIMIT only
- two same-direction tiers: supported
- 60-minute incomplete-entry cancel: supported

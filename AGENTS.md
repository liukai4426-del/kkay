# KAYTRADE V1.8.0 AI ONLY

This repository's V1.8.0 runtime has no local technical-indicator entry strategy.

## Mandatory workflow

Before proposing a trade:
1. Call \`get_kaytrade_state\`.
2. Confirm \`mode == "AI_ONLY"\`.
3. Confirm \`environment == "OKX_DEMO"\`.
4. Confirm \`ai_enabled == true\`.
5. Inspect current position and active KAYTRADE state.
6. Submit one structured proposal with a unique \`proposal_id\`.

## Open proposal requirements

Use \`submit_trade_proposal\` with:
- \`action="open"\`
- \`direction="long"\` or \`"short"\`
- contract \`size\`
- \`take_profit\`
- \`stop_loss\`
- leverage from 1 to 5
- concise \`reason\`
- unique \`proposal_id\`

KAYTRADE V1.8.0 uses market entry only.

## Close proposal

Use \`action="close"\` with the current managed direction and a unique
\`proposal_id\`. KAYTRADE closes only its currently managed isolated BTC swap
position.

## Safety invariants

- Never request, read, log, or expose OKX API key, secret, or passphrase.
- Never bypass KAYTRADE by calling OKX write endpoints directly.
- Never retry the same logical trade with a different proposal ID when the
  previous write result is ambiguous. Inspect KAYTRADE/OKX state first.
- Live-account autonomous AI writes are disabled in V1.8.0.
- The legacy BOLL/EMA/RSI/score/Gate code is compatibility-only and must not be
  used as a V1.8.0 order source.

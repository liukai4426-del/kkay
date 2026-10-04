# KAYTRADE V1.7.2 AI ONLY · Build1721

V1.7.2 changes the execution model completely.

## Build1721 hotfix

Build1721 fixes the Build1720 activation error `'dict' object has no attribute 'stop_atr'`.
The AI-channel button now uses a dedicated background activation path and never enters the
legacy `arm(Settings)` queue. The known Build1720 stop_atr fault lock is migrated safely
without touching positions or orders. The overview also hides retired score/indicator,
ATR-plan and PEE4 strategy surfaces.

## Runtime model

New orders no longer originate from KAYTRADE's local BOLL / EMA / RSI /
score / Gate strategy chain.

The only new-order path is:

\`\`\`
Codex / AI
    ↓
submit_trade_proposal
    ↓
localhost AI Bridge
    ↓
AIOnlyEngine.submit_ai_trade()
    ↓
hard execution checks
    ↓
OKX Demo Trading
\`\`\`

\`Engine.cycle()\` is reconciliation-only in V1.7.2. It cannot evaluate a
direction or create a new position.

## Codex bridge

The desktop app starts a local bridge on \`127.0.0.1\`.

- a random bearer token is generated every app session;
- the descriptor is written to
  \`~/Library/Application Support/OKXLocal/ai_bridge.json\`;
- the file is created with mode 0600;
- OKX credentials stay in the KAYTRADE process and are never returned through
  the bridge;
- \`kaytrade_mcp.py\` exposes:
  - \`get_kaytrade_status\`
  - \`get_kaytrade_state\`
  - \`submit_trade_proposal\`

Install the Codex-side MCP dependency once:

\`\`\`bash
python3 -m pip install -r requirements-codex.txt
\`\`\`

Codex reads the project-level \`.codex/config.toml\` after the repository is
trusted.

## Trade request

V1.7.2 supports:

- BTC-USDT-SWAP only;
- isolated margin;
- long/short account mode;
- market entry;
- mandatory TP and SL on every open request;
- AI-requested leverage from 1x to 5x;
- one KAYTRADE-managed position at a time;
- idempotent \`proposal_id\`.

Default hard execution limits:

- maximum leverage: 5x;
- maximum notional: 100 USDT;
- maximum estimated stop-loss loss: 5 USDT;
- minimum interval between distinct AI requests: 5 seconds.

The limits can be lowered/adjusted with local environment variables without
adding a directional strategy:

- \`KAYTRADE_AI_MAX_NOTIONAL_USDT\`
- \`KAYTRADE_AI_MAX_STOP_LOSS_USDT\`
- \`KAYTRADE_AI_MIN_ORDER_INTERVAL_SEC\`
- \`KAYTRADE_AI_BRIDGE_PORT\`

## Live-account boundary

Autonomous AI writes to a real-money OKX account are hard-disabled in this
build. If the GUI is connected to a live account, enabling the AI execution
channel and \`submit_ai_trade()\` both reject the request.

OKX Demo Trading remains fully automatic after the user enables the AI
execution channel in the KAYTRADE UI.

## Upgrade behavior

If an existing V1.7.1 KAYTRADE-managed position is still active during an
upgrade, V1.7.2 may continue the legacy reconciliation path until that position
is flat. It will never create another legacy-strategy order.

## Package identity

- Version: 1.7.2
- Build: 1721
- Product mode: AI ONLY
- Intel macOS target: macOS 14+

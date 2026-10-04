# KAYTRADE V1.8.2 PAPER · LIMIT ONLY · Build1820

V1.8.2 is the UI-alignment release on top of the V1.8.1 LIMIT-only Paper execution engine.

## Trading Overview order

The visible Trading Overview is now fixed to:

1. BTC / USDT live quote
2. AI方案自动执行
3. AI交易方案看板
4. 订单 / 持仓执行
5. Paper AI channel controls
6. runtime log

Legacy strategy-position wording is hidden from this page.

## AI plan board

The main AI recommendation block has been rebuilt:

- direction and entry use one left alignment line;
- “建议 / 委托入场” is directly above the entry price;
- LONG direction + entry price are green;
- SHORT direction + entry price are red;
- order type is always displayed as 限价;
- TP and SL are explicitly labelled as limit protection;
- main plan, meta badges, TP, SL, recommendation reason, operation advice and recent-plan surfaces use rounded backgrounds;
- TP/SL panels keep a fixed visible column so they do not collapse to thin strips.

## Auto execution card

The user-controlled AI auto-execution card is directly above the AI plan board.

When ON:
- complete AI entry plans are converted to local Paper LIMIT orders;
- direction, size, leverage, limit entry, TP and SL are mandatory;
- TP and SL protection remain LIMIT-only;
- non-limit execution is rejected.

## Order / position card

The previous KAYTRADE card-style execution layout is retained and aligned with the active runtime:

- order state;
- limit order type;
- requested entry;
- average fill;
- TP / SL limit protection;
- order / position size;
- proposal id;
- Tier 1 / Tier 2 execution summaries.

Tier headings and Paper position status follow LONG green / SHORT red.

## Runtime wording

The UI no longer reports “AI Only运行” while the active runtime is Paper.
Running status is shown as Paper运行 / OKX模拟盘.

The old “本程序仓位：无 / 待核对” strategy label is hidden.
Legacy “仅平本程序仓位” and “核对后解除故障锁” controls are hidden from the Paper overview.

## Execution semantics

Execution behavior is unchanged from Build1813:

- PAPER ONLY;
- LIMIT ONLY;
- Tier 1 + Tier 2 same-direction support;
- 60-minute pending-entry auto-cancel;
- amend/cancel entry;
- amend TP/SL protection;
- limit partial close;
- no OKX write path.

Hard limits remain:

- leverage <= 20x;
- combined notional <= 3500 USDT;
- combined estimated stop-loss <= 100 USDT.

## Package

- Version: 1.8.2
- Build: 1820
- Mode: PAPER AI · LIMIT ONLY
- Intel macOS 14+

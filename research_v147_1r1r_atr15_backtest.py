#!/usr/bin/env python3
"""V1.4.7 BTC 30-day comparison scenario: 1:1 TP/SL, 15m ATR x1.5 stop.

Keeps the prior 2,000 USDT profile, fee model, signal rules and exact 30-day
window so results are directly comparable with the preceding V1.4.7 run.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v147_1m_2000u_backtest as v

# Pin the exact same sample used by the prior run for apples-to-apples comparison.
END = datetime(2026, 9, 12, 7, 40, tzinfo=timezone.utc)
START = END - timedelta(days=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)

# Requested exit geometry.
STOP_ATR = 1.5
REWARD_R = 1.0

OUTDIR = Path('backtest_output_v147_1r1r_atr15')
OUTDIR.mkdir(parents=True, exist_ok=True)

# Override both the V1.4.7 research module and inherited execution simulator.
for module in (v, v.base):
    module.START = START
    module.END = END
    module.START_MS = START_MS
    module.END_MS = END_MS
    module.STOP_ATR = STOP_ATR
    module.REWARD_R = REWARD_R
    module.OUTDIR = OUTDIR

_original_write_report = v.write_report


def write_report(sim, metrics, leg_net, cycles, closed):
    _original_write_report(sim, metrics, leg_net, cycles, closed)

    renames = {
        'V147_BTC_SWAP_30D_2000U_Trades.csv': 'V147_BTC_SWAP_30D_2000U_1R1R_ATR15_Trades.csv',
        'V147_BTC_SWAP_30D_2000U_Cycles.csv': 'V147_BTC_SWAP_30D_2000U_1R1R_ATR15_Cycles.csv',
        'V147_BTC_SWAP_30D_2000U_DailyEquity.csv': 'V147_BTC_SWAP_30D_2000U_1R1R_ATR15_DailyEquity.csv',
        'V147_BTC_SWAP_30D_2000U_Backtest_Report.md': 'V147_BTC_SWAP_30D_2000U_1R1R_ATR15_Backtest_Report.md',
    }
    for old, new in renames.items():
        src = OUTDIR / old
        if src.exists():
            src.replace(OUTDIR / new)

    report = OUTDIR / 'V147_BTC_SWAP_30D_2000U_1R1R_ATR15_Backtest_Report.md'
    text = report.read_text(encoding='utf-8')
    text = text.replace(
        '**策略：** V1.4.7，1:2整仓止盈止损，15m ATR×1止损。',
        '**策略：** V1.4.7，1:1整仓止盈止损；止损距离=15m ATR×1.5，止盈距离=15m ATR×1.5。'
    )
    text = text.replace(
        '# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天回测报告',
        '# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天 1:1 / ATR1.5 回测报告'
    )
    report.write_text(text, encoding='utf-8')


v.write_report = write_report

if __name__ == '__main__':
    v.main()

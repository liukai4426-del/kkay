#!/usr/bin/env python3
"""V1.4.7 BTC 30-day comparison: minimum score 4.0 with tier caps in 1:2:3 ratio.

Everything else stays on the original V1.4.7 comparison profile:
SL = 1.0 x 15m ATR, TP = 2.0R, 2,000 USDT capital profile, same fee model,
and the exact same 30-day sample used by the prior V1.4.7 comparison runs.

Position notional caps (third tier fixed at 2,000 USDT for fair comparison):
Tier 1 / first signal = 666.67 USDT (1x)
Tier 2 / second signal = 1,333.33 USDT (2x)
Tier 3 / third signal = 2,000.00 USDT (3x)
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v147_1m_2000u_backtest as v

MIN_ENTRY_SCORE = 4.0
TIER_CAP = {1: 666.67, 2: 1333.33, 3: 2000.0}
END = datetime(2026, 9, 12, 7, 40, tzinfo=timezone.utc)
START = END - timedelta(days=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path('backtest_output_v147_tier123')
OUTDIR.mkdir(parents=True, exist_ok=True)

# Only change tier notional caps. Preserve the original 4.0 entry threshold and geometry.
for module in (v, v.base):
    module.START = START
    module.END = END
    module.START_MS = START_MS
    module.END_MS = END_MS
    module.STOP_ATR = 1.0
    module.REWARD_R = 2.0
    module.TIER_CAP = dict(TIER_CAP)
    module.OUTDIR = OUTDIR

_original_write_report = v.write_report


def write_report(sim, metrics, leg_net, cycles, closed):
    _original_write_report(sim, metrics, leg_net, cycles, closed)
    renames = {
        'V147_BTC_SWAP_30D_2000U_Trades.csv': 'V147_BTC_SWAP_30D_2000U_TIER123_Trades.csv',
        'V147_BTC_SWAP_30D_2000U_Cycles.csv': 'V147_BTC_SWAP_30D_2000U_TIER123_Cycles.csv',
        'V147_BTC_SWAP_30D_2000U_DailyEquity.csv': 'V147_BTC_SWAP_30D_2000U_TIER123_DailyEquity.csv',
        'V147_BTC_SWAP_30D_2000U_Backtest_Report.md': 'V147_BTC_SWAP_30D_2000U_TIER123_Backtest_Report.md',
    }
    for old, new in renames.items():
        src = OUTDIR / old
        if src.exists():
            src.replace(OUTDIR / new)

    report = OUTDIR / 'V147_BTC_SWAP_30D_2000U_TIER123_Backtest_Report.md'
    text = report.read_text(encoding='utf-8')
    text = text.replace(
        '# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天回测报告',
        '# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天 1:2:3仓位比例 回测报告'
    )
    text = text.replace(
        'Tier名义仓位上限1,000/1,500/2,000U。',
        '第一/第二/第三信号名义仓位上限666.67/1,333.33/2,000U，对应1x/2x/3x。'
    )
    text += (
        '\n\n**本情景唯一策略变化：** 最低开仓门槛保持4.0分；第一/第二/第三信号仓位按'
        '666.67/1,333.33/2,000 USDT配置（1:2:3）。SL=15m ATR×1.0、TP=2R及其他规则全部不变。\n'
    )
    report.write_text(text, encoding='utf-8')


v.write_report = write_report

if __name__ == '__main__':
    v.main()

#!/usr/bin/env python3
"""V1.4.7 BTC 30-day comparison with custom score bands and 1:2:3 position caps.

Scenario changes only:
- no entry below 6.0;
- first signal: 6.0-7.0 -> Tier 1;
- second signal: 7.5-8.5 -> Tier 2;
- third signal: 9.0-10.0 -> Tier 3;
- nominal position caps are 666.67 / 1333.33 / 2000 USDT (1:2:3).

Everything else remains on the same V1.4.7 comparison profile:
SL = 1.0 x 15m ATR, TP = 2.0R, 2,000 USDT capital, same fee/funding/risk
model, and the exact same 30-day sample as the previous comparisons.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v147_1m_2000u_backtest as v

MIN_ENTRY_SCORE = 6.0
TIER_CAP = {1: 666.67, 2: 1333.33, 3: 2000.0}
TIER_LABEL = {
    1: "First signal (6.0-7.0)",
    2: "Second signal (7.5-8.5)",
    3: "Third signal (9.0-10.0)",
}
END = datetime(2026, 9, 12, 7, 40, tzinfo=timezone.utc)
START = END - timedelta(days=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path("backtest_output_v147_bands6_7_75_85_9_10_tier123")
OUTDIR.mkdir(parents=True, exist_ok=True)


def tier_custom(total):
    """6.0-7.0 => Tier1; 7.5-8.5 => Tier2; 9.0-10 => Tier3."""
    if total >= 9.0:
        return 3
    if total >= 7.5:
        return 2
    if total >= 6.0:
        return 1
    return 0


for module in (v, v.base):
    module.START = START
    module.END = END
    module.START_MS = START_MS
    module.END_MS = END_MS
    module.STOP_ATR = 1.0
    module.REWARD_R = 2.0
    module.TIER_CAP = dict(TIER_CAP)
    module.TIER_LABEL = dict(TIER_LABEL)
    module.OUTDIR = OUTDIR

# V147Market.score resolves tier mapping through v.base.tier at runtime.
v.base.tier = tier_custom

_original_write_report = v.write_report


def write_report(sim, metrics, leg_net, cycles, closed):
    _original_write_report(sim, metrics, leg_net, cycles, closed)
    renames = {
        "V147_BTC_SWAP_30D_2000U_Trades.csv": "V147_BTC_SWAP_30D_2000U_BANDS6_7_75_85_9_10_TIER123_Trades.csv",
        "V147_BTC_SWAP_30D_2000U_Cycles.csv": "V147_BTC_SWAP_30D_2000U_BANDS6_7_75_85_9_10_TIER123_Cycles.csv",
        "V147_BTC_SWAP_30D_2000U_DailyEquity.csv": "V147_BTC_SWAP_30D_2000U_BANDS6_7_75_85_9_10_TIER123_DailyEquity.csv",
        "V147_BTC_SWAP_30D_2000U_Backtest_Report.md": "V147_BTC_SWAP_30D_2000U_BANDS6_7_75_85_9_10_TIER123_Backtest_Report.md",
    }
    for old, new in renames.items():
        src = OUTDIR / old
        if src.exists():
            src.replace(OUTDIR / new)

    report = OUTDIR / "V147_BTC_SWAP_30D_2000U_BANDS6_7_75_85_9_10_TIER123_Backtest_Report.md"
    text = report.read_text(encoding="utf-8")
    text = text.replace(
        "# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天回测报告",
        "# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天 自定义评分档位 / 1:2:3仓位 回测报告",
    )
    text = text.replace(
        "Tier名义仓位上限1,000/1,500/2,000U。",
        "第一/第二/第三信号名义仓位上限666.67/1,333.33/2,000U，对应1x/2x/3x。",
    )
    text = text.replace(
        "- 最低4分；Tier1 4.0–6.0、Tier2 6.5–7.5、Tier3 8.0–10.0；多空同时合格只取高分侧，同分观望。",
        "- 最低6分；第一信号6.0–7.0、第二信号7.5–8.5、第三信号9.0–10.0；多空同时合格只取高分侧，同分观望。",
    )
    text += (
        "\n\n**本情景策略变化：** 最低入仓评分6.0；第一信号6.0–7.0分、第二信号7.5–8.5分、"
        "第三信号9.0–10.0分；对应名义仓位上限666.67/1,333.33/2,000 USDT（1:2:3）。"
        "SL=15m ATR×1.0、TP=2R及其他V1.4.7规则全部不变。\n"
    )
    report.write_text(text, encoding="utf-8")


v.write_report = write_report

if __name__ == "__main__":
    v.main()

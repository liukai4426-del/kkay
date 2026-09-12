#!/usr/bin/env python3
"""V1.4.7 BTC 30-day comparison: remove first position, min score 6.0,
second/third signal positions in a 1:2 notional-cap ratio.

Only strategy changes in this scenario:
- scores below 6.0 cannot open;
- 6.0-7.5 maps directly to the second position (Tier 2);
- 8.0-10.0 maps to the third position (Tier 3);
- Tier 1 / first position is disabled;
- Tier 2 cap = 1,000 USDT; Tier 3 cap = 2,000 USDT.

Everything else remains on the same V1.4.7 comparison profile:
SL = 1.0 x 15m ATR, TP = 2.0R, 2,000 USDT capital, same fee/funding/risk
model, and the exact same 30-day sample as the previous comparisons.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v147_1m_2000u_backtest as v

MIN_ENTRY_SCORE = 6.0
TIER_CAP = {1: 0.0, 2: 1000.0, 3: 2000.0}
TIER_LABEL = {
    1: "Tier 1 DISABLED",
    2: "Second position (6.0-7.5)",
    3: "Third position (8.0-10.0)",
}
END = datetime(2026, 9, 12, 7, 40, tzinfo=timezone.utc)
START = END - timedelta(days=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path("backtest_output_v147_min6_no_tier1_tier23_1to2")
OUTDIR.mkdir(parents=True, exist_ok=True)


def tier_min6_no_first(total):
    """Remove Tier 1: 6.0-7.5 => Tier 2, >=8.0 => Tier 3."""
    if total >= 8.0:
        return 3
    if total >= 6.0:
        return 2
    return 0


# Override only the requested score-to-position mapping and notional caps.
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

# V147Market.score calls v.base.tier at runtime, so this changes the actual
# execution mapping rather than merely filtering the final report.
v.base.tier = tier_min6_no_first

_original_write_report = v.write_report


def write_report(sim, metrics, leg_net, cycles, closed):
    _original_write_report(sim, metrics, leg_net, cycles, closed)
    renames = {
        "V147_BTC_SWAP_30D_2000U_Trades.csv": "V147_BTC_SWAP_30D_2000U_MIN6_NO_TIER1_TIER23_1TO2_Trades.csv",
        "V147_BTC_SWAP_30D_2000U_Cycles.csv": "V147_BTC_SWAP_30D_2000U_MIN6_NO_TIER1_TIER23_1TO2_Cycles.csv",
        "V147_BTC_SWAP_30D_2000U_DailyEquity.csv": "V147_BTC_SWAP_30D_2000U_MIN6_NO_TIER1_TIER23_1TO2_DailyEquity.csv",
        "V147_BTC_SWAP_30D_2000U_Backtest_Report.md": "V147_BTC_SWAP_30D_2000U_MIN6_NO_TIER1_TIER23_1TO2_Backtest_Report.md",
    }
    for old, new in renames.items():
        src = OUTDIR / old
        if src.exists():
            src.replace(OUTDIR / new)

    report = OUTDIR / "V147_BTC_SWAP_30D_2000U_MIN6_NO_TIER1_TIER23_1TO2_Backtest_Report.md"
    text = report.read_text(encoding="utf-8")
    text = text.replace(
        "# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天回测报告",
        "# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天 取消第一仓位 / 最低6分 / 第二第三仓1:2 回测报告",
    )
    text = text.replace(
        "Tier名义仓位上限1,000/1,500/2,000U。",
        "第一仓位禁用；第二/第三信号名义仓位上限1,000/2,000U，对应1x/2x。",
    )
    text += (
        "\n\n**本情景策略变化：** 第一仓位完全取消；最低入仓评分6.0；"
        "6.0–7.5分直接使用第二仓位1,000U，8.0–10.0分使用第三仓位2,000U（1:2）。"
        "SL=15m ATR×1.0、TP=2R及其他V1.4.7规则全部不变。\n"
    )
    report.write_text(text, encoding="utf-8")


v.write_report = write_report

if __name__ == "__main__":
    v.main()

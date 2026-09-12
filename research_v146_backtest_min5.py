#!/usr/bin/env python3
"""V1.4.6 BTC-SWAP 3M scenario: keep TP/SL 1:2, raise minimum entry score to 5.0."""
import os
from pathlib import Path
import research_v146_backtest as bt


def tier_min5(total):
    if total >= 8.0:
        return 3
    if total >= 6.5:
        return 2
    if total >= 5.0:
        return 1
    return 0


def main():
    # Only change the automatic entry floor. Keep the original 1R SL / 2R TP and all other rules.
    bt.tier = tier_min5
    bt.TIER_LABEL = {
        1: "Tier 1 (5.0-6.0)",
        2: "Tier 2 (6.5-7.5)",
        3: "Tier 3 (8.0-10.0)",
    }
    bt.OUTDIR = Path(os.environ.get("BACKTEST_OUT", "backtest_output_min5"))
    bt.OUTDIR.mkdir(parents=True, exist_ok=True)
    bt.main()

    report = bt.OUTDIR / "V146_BTC_SWAP_3M_Backtest_Report.md"
    if report.exists():
        text = report.read_text(encoding="utf-8")
        text = text.replace(
            "# KAYTRADE V1.4.6 — BTC-USDT-SWAP 三个月历史回测报告",
            "# KAYTRADE V1.4.6 — BTC-USDT-SWAP 三个月最低5分开仓回测报告",
        )
        text = text.replace(
            "一级/二级/三级信号名义仓位上限分别为50/75/100 USDT。",
            "一级/二级/三级信号名义仓位上限分别为50/75/100 USDT；自动开仓最低总分提高到5.0分，4.0/4.5分信号禁止开仓。",
        )
        text += "\n\n> 本情景保持15m ATR×1止损、2R整仓止盈不变，仅将自动开仓最低总分从4.0提高到5.0；其余V1.4.6规则不变。\n"
        out = bt.OUTDIR / "V146_BTC_SWAP_3M_MIN5_Backtest_Report.md"
        out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()

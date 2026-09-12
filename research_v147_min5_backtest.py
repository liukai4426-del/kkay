#!/usr/bin/env python3
"""V1.4.7 BTC 30-day comparison: minimum entry score 5.0 only.

Everything else stays on the original V1.4.7 comparison profile:
SL = 1.0 x 15m ATR, TP = 2.0R, 2,000 USDT capital profile, same fee model,
and the exact same 30-day sample used by the prior V1.4.7 comparison runs.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import research_v147_1m_2000u_backtest as v

MIN_ENTRY_SCORE = 5.0
END = datetime(2026, 9, 12, 7, 40, tzinfo=timezone.utc)
START = END - timedelta(days=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path('backtest_output_v147_min5')
OUTDIR.mkdir(parents=True, exist_ok=True)

# Keep original production geometry/profile; only threshold changes.
for module in (v, v.base):
    module.START = START
    module.END = END
    module.START_MS = START_MS
    module.END_MS = END_MS
    module.STOP_ATR = 1.0
    module.REWARD_R = 2.0
    module.OUTDIR = OUTDIR

_original_score = v.V147Market.score


def score_min5(self, i1):
    sig = _original_score(self, i1)
    results = sig.get('scores') or {}
    if not results:
        return sig

    # Raise only the minimum automatic-entry threshold. Keep the original score,
    # tier mapping, hard gates and long/short arbitration otherwise unchanged.
    for side in ('做多', '做空'):
        row = results.get(side)
        if not row:
            continue
        row['eligible'] = bool(row.get('gate')) and float(row.get('total', 0.0)) >= MIN_ENTRY_SCORE and int(row.get('tier', 0)) > 0

    eligible = [side for side in ('做多', '做空') if results.get(side, {}).get('eligible')]
    if len(eligible) == 1:
        selected = eligible[0]
    elif len(eligible) == 2:
        a = float(results[eligible[0]]['total'])
        b = float(results[eligible[1]]['total'])
        selected = '观望' if abs(a - b) <= 1e-9 else max(eligible, key=lambda s: results[s]['total'])
    else:
        selected = '观望'
    sig['side'] = selected
    return sig


v.V147Market.score = score_min5
_original_write_report = v.write_report


def write_report(sim, metrics, leg_net, cycles, closed):
    _original_write_report(sim, metrics, leg_net, cycles, closed)
    renames = {
        'V147_BTC_SWAP_30D_2000U_Trades.csv': 'V147_BTC_SWAP_30D_2000U_MIN5_Trades.csv',
        'V147_BTC_SWAP_30D_2000U_Cycles.csv': 'V147_BTC_SWAP_30D_2000U_MIN5_Cycles.csv',
        'V147_BTC_SWAP_30D_2000U_DailyEquity.csv': 'V147_BTC_SWAP_30D_2000U_MIN5_DailyEquity.csv',
        'V147_BTC_SWAP_30D_2000U_Backtest_Report.md': 'V147_BTC_SWAP_30D_2000U_MIN5_Backtest_Report.md',
    }
    for old, new in renames.items():
        src = OUTDIR / old
        if src.exists():
            src.replace(OUTDIR / new)

    report = OUTDIR / 'V147_BTC_SWAP_30D_2000U_MIN5_Backtest_Report.md'
    text = report.read_text(encoding='utf-8')
    text = text.replace(
        '# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天回测报告',
        '# KAYTRADE V1.4.7 — BTC-USDT-SWAP 最近30天 最低5分 回测报告'
    )
    text = text.replace(
        '- 最低4分；Tier1 4.0–6.0、Tier2 6.5–7.5、Tier3 8.0–10.0；多空同时合格只取高分侧，同分观望。',
        '- 最低开仓门槛提高为5.0分；Tier映射仍保持原V1.4.7定义，4.0/4.5信号不允许开仓；其他多空仲裁不变。'
    )
    text += '\n\n**本情景唯一策略变化：** 自动开仓最低分从4.0提高到5.0；SL=15m ATR×1.0、TP=2R及其他规则全部不变。\n'
    report.write_text(text, encoding='utf-8')


v.write_report = write_report

if __name__ == '__main__':
    v.main()

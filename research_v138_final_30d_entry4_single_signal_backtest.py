#!/usr/bin/env python3
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path
import research_v138_final_30d_entry5_backtest as d5
v138=d5.v138; base=d5.base
START=datetime(2026,8,13,10,57,tzinfo=timezone.utc); END=datetime(2026,9,12,10,57,tzinfo=timezone.utc)
START_MS=int(START.timestamp()*1000); END_MS=int(END.timestamp()*1000)
OUTDIR=Path('backtest_output_v138_final_30d_entry4_single_signal'); OUTDIR.mkdir(parents=True,exist_ok=True)
for mod in (d5,v138,base):
    mod.START=START; mod.END=END; mod.START_MS=START_MS; mod.END_MS=END_MS; mod.OUTDIR=OUTDIR

def single_signal_tier(total: float)->int:
    return 1 if float(total or 0.0)>=4.0 else 0
v138.signal_tier=single_signal_tier

def _num(x): return '∞' if isinstance(x,float) and math.isinf(x) else f'{x:.3f}'

def postprocess():
    p=OUTDIR/'metrics.json'; m=json.loads(p.read_text())
    m.update({'window':'fixed_identical_30_days','entry_threshold':4.0,'signal_tiers':1,'single_signal_only':True,'add_on_enabled':False,'position_cap_usdt':1000.0,'comparison':'same 5-point single-signal baseline; only opening threshold changed 5.0 -> 4.0'})
    p.write_text(json.dumps(m,ensure_ascii=False,indent=2))
    ren={'V138_FINAL_BTC_SWAP_3M_Trades.csv':'V138_FINAL_BTC_SWAP_30D_ENTRY4_SINGLE_Trades.csv','V138_FINAL_BTC_SWAP_3M_Cycles.csv':'V138_FINAL_BTC_SWAP_30D_ENTRY4_SINGLE_Cycles.csv','V138_FINAL_BTC_SWAP_3M_DailyEquity.csv':'V138_FINAL_BTC_SWAP_30D_ENTRY4_SINGLE_DailyEquity.csv'}
    for a,b in ren.items():
        q=OUTDIR/a
        if q.exists(): q.replace(OUTDIR/b)
    q=OUTDIR/'V138_FINAL_BTC_SWAP_3M_Backtest_Report.md'
    if q.exists(): q.unlink()
    lines=['# KAYTRADE V1.3.8 FINAL — 30天 / 4分开仓 / 单信号不加仓','',f'**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC','**仓位：** >=4.0仅一个开仓信号，最大1000U；无第二档、无加仓。','', '| 指标 | 结果 |','|---|---:|',f"| 净收益 | {m['net_pnl']:+.2f} U |",f"| 净收益率 | {m['net_return_pct']:+.3f}% |",f"| 胜率 | {m['cycle_win_rate_pct']:.2f}% |",f"| Profit Factor | {_num(m['profit_factor'])} |",f"| 完整交易轮次 | {m['cycles']} |",f"| 最大回撤 | {m['max_drawdown_usdt']:.2f} U / {m['max_drawdown_pct']:.2f}% |",f"| 手续费 | {m['fees']:.2f} U |",f"| 压力情景收益率 | {m['stress_return_pct']:+.3f}% |"]
    (OUTDIR/'V138_FINAL_BTC_SWAP_30D_ENTRY4_SINGLE_Backtest_Report.md').write_text('\n'.join(lines))

def main():
    v138.main(); postprocess(); print('FINAL_30D_ENTRY4_SINGLE_METRICS'); print((OUTDIR/'metrics.json').read_text())
if __name__=='__main__': main()

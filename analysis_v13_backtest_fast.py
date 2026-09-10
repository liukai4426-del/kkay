import bisect

import analysis_v13_backtest as b
from core import indicators
from strategy import _setup, signal


def exact_signals_fast(five,q,h):
    # Exact logical prefilter: V1.3 hard-gates every entry on 15m Setup >= 2.
    # Compute that once per completed 15m bar, then run the full unmodified
    # strategy.signal only for 5m bars that can possibly pass the hard gate.
    setup_ok={}
    for i15 in range(1000,len(q)):
        qw=q[max(0,i15-b.WINDOW+1):i15+1]
        m=indicators(qw)
        lo=_setup(qw,m,True)[0]
        sh=_setup(qw,m,False)[0]
        setup_ok[i15]=(lo>=2,sh>=2)
        if i15%1500==0:
            print(f'15m exact setup prefilter: {i15}/{len(q)}',flush=True)

    qends=[r['t']+b.STEP15 for r in q]
    hends=[r['t']+b.STEP1H for r in h]
    out={}; full_checked=0; skipped=0
    for i5,r in enumerate(five):
        end=r['t']+b.STEP5
        if end<b.START_MS or end>b.END_MS: continue
        i15=bisect.bisect_right(qends,end)-1
        i1=bisect.bisect_right(hends,end)-1
        if i5<1000 or i15<1000 or i1<1000: continue
        possible=setup_ok.get(i15,(False,False))
        if not any(possible):
            skipped+=1; continue
        fw=five[max(0,i5-b.WINDOW+1):i5+1]
        qw=q[max(0,i15-b.WINDOW+1):i15+1]
        hw=h[max(0,i1-b.WINDOW+1):i1+1]
        value=signal(hw,qw,fw,b.THRESHOLD,b.STOP_ATR)
        full_checked+=1
        if value['side']!='观望':
            sc=value['scores'][value['side']]
            out[r['t']]={
                'side':'long' if value['side']=='做多' else 'short',
                'side_cn':value['side'],'score':sc['total'],'level':sc['level'],
                'layers':sc['layers'],'reason':sc['reason'],'atr15':value['m']['atr'],
                'atr1h':value['h']['atr'],'signal_close':r['c'],
                'front_r':sc['structure']['front_r'],'warning':sc['structure']['warning'],
                'strong_opposite':sc['confirmations']['strong_opposite']}
        if full_checked and full_checked%1000==0:
            print(f'Full V1.3 evaluations after exact Setup prefilter: {full_checked}',flush=True)
    print(f'Prefilter skipped={skipped}; full V1.3 evaluations={full_checked}; eligible bars={len(out)}',flush=True)
    return out


b.exact_signals=exact_signals_fast
b.main()

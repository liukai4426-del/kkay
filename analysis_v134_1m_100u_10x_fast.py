import bisect
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

import analysis_v134_1m_100u_10x as b
from core import indicators
from strategy import _setup, signal


def exact_signals(five,q,h,four,day):
    setup_ok={}
    for i15 in range(220,len(q)):
        qw=q[max(0,i15-b.WINDOW+1):i15+1]
        m=indicators(qw)
        setup_ok[i15]=(_setup(qw,m,True)[0]>=.5,_setup(qw,m,False)[0]>=.5)
    qends=[r['t']+b.STEP15 for r in q]; hends=[r['t']+b.STEP1H for r in h]
    fourends=[r['t']+b.STEP4H for r in four]; dends=[r['t']+b.STEPD for r in day]
    out={}; checked=0; prefiltered=0
    for i5,r in enumerate(five):
        end=r['t']+b.STEP5
        if end<b.START_MS or end>=b.END_MS: continue
        i15=bisect.bisect_right(qends,end)-1; i1=bisect.bisect_right(hends,end)-1
        i4=bisect.bisect_right(fourends,end)-1; iday=bisect.bisect_right(dends,end)-1
        if i5<1000 or i15<1000 or i1<1000 or i4<220 or iday<220: continue
        if not any(setup_ok.get(i15,(False,False))):
            prefiltered+=1; continue
        fw=five[max(0,i5-b.WINDOW+1):i5+1]; qw=q[max(0,i15-b.WINDOW+1):i15+1]
        hw=h[max(0,i1-b.WINDOW+1):i1+1]; fourw=four[max(0,i4-b.WINDOW+1):i4+1]
        dw=day[max(0,iday-b.WINDOW+1):iday+1]
        value=signal(hw,qw,fw,b.S.score_threshold,b.S.stop_atr,four=fourw,day=dw)
        checked+=1
        if value['side']!='观望':
            sc=value['scores'][value['side']]
            out[r['t']]={'side_cn':value['side'],'side':'long' if value['side']=='做多' else 'short',
                'score':float(sc['total']),'level':sc['level'],'position_multiplier':float(sc['position_multiplier']),
                'atr15':float(value['m']['atr']),'signal_close':float(r['c']),
                'daily_ema':float(sc['layers']['daily_ema']),'daily_ema_name':sc['confirmations']['daily_ema'],
                'structure_4h':float(sc['layers']['structure_4h']),'structure_1h15m':float(sc['layers']['structure']),
                'rsi_resonance':float(sc['layers']['rsi_resonance']),'setup':float(sc['layers']['setup']),
                'trigger':float(sc['layers']['trigger']),'trend_penalty_1h':float(sc['layers']['trend_penalty_1h']),
                'trend_penalty_4h':float(sc['layers']['trend_penalty_4h']),'front_penalty':float(sc['layers']['front_penalty'])}
        if checked and checked%1000==0: print(f'full eval={checked}, eligible={len(out)}, prefiltered={prefiltered}',flush=True)
    print(f'Prefiltered={prefiltered}; full evaluations={checked}; eligible bars={len(out)}',flush=True)
    return out


def group_stats(trades,key):
    groups=defaultdict(list)
    for t in trades: groups[str(t.get(key))].append(t)
    out={}
    for k,v in sorted(groups.items()):
        wins=sum(x['net_pnl']>0 for x in v)
        out[k]={'trades':len(v),'wins':wins,'losses':len(v)-wins,'win_rate_pct':100*wins/len(v),'net_pnl':sum(x['net_pnl'] for x in v)}
    return out


def main():
    five=b.fetch_candles('5m',b.WARMUP_MS,b.END_MS,b.STEP5)
    quarter=b.aggregate(five,b.STEP15); hour=b.aggregate(five,b.STEP1H); four=b.aggregate(five,b.STEP4H)
    day=b.fetch_candles('1Dutc',b.DAY_WARMUP_MS,b.END_MS,b.STEPD)
    meta=b.instrument_meta(); signals=exact_signals(five,quarter,hour,four,day)
    sim=b.simulate(five,signals,meta); trades=sim.pop('trades')
    period=[r for r in five if b.START_MS<=r['t']<b.END_MS]
    report={'strategy':'KAYTRADE V1.3.4 exact strategy.signal + staged TP execution model',
      'period':{'start_utc':datetime.fromtimestamp(b.START_MS/1000,timezone.utc).isoformat(),'end_utc':datetime.fromtimestamp(b.END_MS/1000,timezone.utc).isoformat(),'days':30},
      'parameters':{'start_equity_usdt':100,'max_leverage':10,'max_notional_usdt':1000,'base_risk_usdt':1,'risk_pct_config':1,
        'score_threshold':3.5,'position_tiers':'3.5-4.5=1x, 5.0-6.5=1.5x, 7.0-10=2x','stop':'1.0x ATR15',
        'exit':'TP1 1R 50%; remainder BE; TP2 2R 50%','maker_bps':2,'taker_bps':5,'exit_slippage_bps':5,
        'daily_loss_usdt':3,'consecutive_losses':3,'cooldown_minutes':30,'funding':'excluded'},
      'market':{'five_minute_candles':len(period),'btc_start':period[0]['o'],'btc_end':period[-1]['c'],'btc_return_pct':100*(period[-1]['c']/period[0]['o']-1)},
      'signal_bars':len(signals),'result':sim,'exit_outcomes':dict(Counter(t['exit_reason'] for t in trades)),
      'by_side':group_stats(trades,'side'),'by_score':group_stats(trades,'score'),'by_multiplier':group_stats(trades,'position_multiplier'),
      'by_daily_ema':group_stats(trades,'daily_ema_name'),
      'notes':['Setup prefilter is mathematically lossless because V1.3.4 hard-gates every trade at 15m Setup >= 0.5.',
               '10x is a maximum; make_plan reserves 10% available-balance buffer, so effective leverage can be lower.',
               'Parent entry: one next-5m-bar limit at signal close. Before TP1, SL wins same-bar ambiguity. After TP1 is established, BE wins same-later-bar ambiguity versus TP2.',
               'Funding, latency, partial fills and queue priority are excluded.']}
    with open('v134_1m_100u_10x_report.json','w') as f: json.dump(report,f,ensure_ascii=False,indent=2)
    fields=sorted({k for t in trades for k in t if k!='plan'})
    with open('v134_1m_100u_10x_trades.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows({k:v for k,v in t.items() if k!='plan'} for t in trades)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()

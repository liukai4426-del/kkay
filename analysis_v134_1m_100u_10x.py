import bisect
import csv
import json
import math
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from engine import Settings, Halt, make_plan
from strategy import signal

INST='BTC-USDT-SWAP'
START_MS=int(datetime(2026,8,11,18,30,tzinfo=timezone.utc).timestamp()*1000)
END_MS=int(datetime(2026,9,10,18,30,tzinfo=timezone.utc).timestamp()*1000)
WARMUP_MS=int(datetime(2026,6,15,0,0,tzinfo=timezone.utc).timestamp()*1000)
DAY_WARMUP_MS=int(datetime(2025,12,1,0,0,tzinfo=timezone.utc).timestamp()*1000)
STEP5=300_000; STEP15=900_000; STEP1H=3_600_000; STEP4H=14_400_000; STEPD=86_400_000
WINDOW=1500
CN=ZoneInfo('Asia/Shanghai')
S=Settings(capital=100.0,max_notional=1000.0,leverage=10,risk_usdt=1.0,risk_pct=1.0,
           daily_loss=3.0,consecutive_losses=3,cooldown_minutes=30,stop_atr=1.0,
           reward_r=2.0,score_threshold=3.5,fee_bps=2.0,taker_fee_bps=5.0,slippage_bps=5.0).validate()


def okx_get(path,params,retries=7):
    url='https://www.okx.com'+path+'?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'kaytrade-v134-1m-backtest/1.0'})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req,timeout=25) as r: payload=json.load(r)
            if payload.get('code')!='0': raise RuntimeError(payload)
            return payload['data']
        except Exception as exc:
            if attempt+1==retries: raise
            print(f'OKX retry {attempt+1}/{retries}: {exc}',flush=True)
            time.sleep(min(6,.6*(2**attempt)))


def fetch_candles(bar,start_ms,end_ms,step):
    rows={}; cursor=end_ms+step; calls=0
    while cursor>start_ms:
        batch=okx_get('/api/v5/market/history-candles',{'instId':INST,'bar':bar,'after':str(cursor),'limit':'100'})
        calls+=1
        if not batch: break
        oldest=cursor
        for r in batch:
            ts=int(r[0]); oldest=min(oldest,ts)
            if start_ms<=ts<end_ms and len(r)>=9 and str(r[8])=='1':
                rows[ts]={'t':ts,'o':float(r[1]),'h':float(r[2]),'l':float(r[3]),'c':float(r[4]),'v':float(r[5])}
        if oldest>=cursor: break
        cursor=oldest
        if calls%100==0: print(f'{bar}: calls={calls}, rows={len(rows)}',flush=True)
        time.sleep(.08)
    out=[rows[k] for k in sorted(rows)]
    gaps=[(a['t'],b['t']) for a,b in zip(out,out[1:]) if b['t']-a['t']!=step]
    if gaps: raise RuntimeError(f'{bar} gaps: {gaps[:5]} total={len(gaps)}')
    print(f'Fetched {len(out)} closed {bar} candles in {calls} calls',flush=True)
    return out


def aggregate(rows,step):
    need=step//STEP5; groups=defaultdict(list)
    for r in rows: groups[(r['t']//step)*step].append(r)
    out=[]
    for t in sorted(groups):
        g=sorted(groups[t],key=lambda x:x['t'])
        if len(g)!=need or any(b['t']-a['t']!=STEP5 for a,b in zip(g,g[1:])): continue
        out.append({'t':t,'o':g[0]['o'],'h':max(x['h'] for x in g),'l':min(x['l'] for x in g),'c':g[-1]['c'],'v':sum(x['v'] for x in g)})
    return out


def instrument_meta():
    data=okx_get('/api/v5/public/instruments',{'instType':'SWAP','instId':INST})
    if not data: raise RuntimeError('instrument metadata missing')
    row=data[0]
    return {k:row[k] for k in ('tickSz','ctVal','ctMult','minSz','lotSz')}


def exact_signals(five,q,h,four,day):
    qends=[r['t']+STEP15 for r in q]; hends=[r['t']+STEP1H for r in h]
    fourends=[r['t']+STEP4H for r in four]; dends=[r['t']+STEPD for r in day]
    out={}; checked=0
    for i5,r in enumerate(five):
        end=r['t']+STEP5
        if end<START_MS or end>=END_MS: continue
        i15=bisect.bisect_right(qends,end)-1; i1=bisect.bisect_right(hends,end)-1
        i4=bisect.bisect_right(fourends,end)-1; iday=bisect.bisect_right(dends,end)-1
        if i5<1000 or i15<1000 or i1<1000 or i4<220 or iday<220: continue
        fw=five[max(0,i5-WINDOW+1):i5+1]; qw=q[max(0,i15-WINDOW+1):i15+1]
        hw=h[max(0,i1-WINDOW+1):i1+1]; fourw=four[max(0,i4-WINDOW+1):i4+1]
        dw=day[max(0,iday-WINDOW+1):iday+1]
        value=signal(hw,qw,fw,S.score_threshold,S.stop_atr,four=fourw,day=dw)
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
        if checked and checked%3000==0: print(f'V1.3.4 eval={checked}, eligible={len(out)}',flush=True)
    print(f'Full evaluations={checked}; eligible bars={len(out)}',flush=True)
    return out


def cn_day(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).astimezone(CN).strftime('%Y-%m-%d')


def simulate(five,signals,meta):
    equity=S.capital; global_peak=equity; max_dd=0.0
    day=''; day_peak=equity; streak=0; cooldown_until=0
    pending=None; pos=None; trades=[]
    counts=Counter(); leverage=[]
    maker=S.fee_bps/10000; taker=S.taker_fee_bps/10000; slip=S.slippage_bps/10000

    def update_dd():
        nonlocal global_peak,max_dd
        global_peak=max(global_peak,equity)
        max_dd=max(max_dd,(global_peak-equity)/global_peak if global_peak>0 else 0)

    def exit_fill(trigger,d,qty):
        fill=trigger*(1-d*slip)
        fee=fill*qty*taker
        return fill,fee

    def finish(p,t,reason):
        nonlocal equity,streak,cooldown_until,pos
        net=p['gross']-p['entry_fee']-p['exit_fees']
        equity+=net; update_dd()
        p.update(exit_t=t+STEP5,exit_reason=reason,net_pnl=net,equity_after=equity)
        trades.append(p.copy()); counts[reason]+=1
        streak=streak+1 if net<0 else 0
        cooldown_until=t+STEP5+S.cooldown_minutes*60_000
        pos=None

    def close_full(p,t,trigger,reason):
        qty=p['btc']; fill,fee=exit_fill(trigger,p['d'],qty)
        p['gross']+=p['d']*(fill-p['entry'])*qty; p['exit_fees']+=fee
        p['last_exit']=fill; finish(p,t,reason)

    def take_tp1(p,t):
        qty=p['btc']/2; fill,fee=exit_fill(p['tp1'],p['d'],qty)
        p['gross']+=p['d']*(fill-p['entry'])*qty; p['exit_fees']+=fee
        p['tp1_done']=True; p['tp1_fill']=fill

    def take_tp2_finish(p,t,reason='TP1_TP2'):
        qty=p['btc']/2; fill,fee=exit_fill(p['tp2'],p['d'],qty)
        p['gross']+=p['d']*(fill-p['entry'])*qty; p['exit_fees']+=fee
        p['tp2_fill']=fill; finish(p,t,reason)

    def take_be_finish(p,t,reason='TP1_BE'):
        qty=p['btc']/2; fill,fee=exit_fill(p['entry'],p['d'],qty)
        p['gross']+=p['d']*(fill-p['entry'])*qty; p['exit_fees']+=fee
        p['be_fill']=fill; finish(p,t,reason)

    last=None
    for r in five:
        t=r['t']; last=r
        if t<START_MS: continue
        if t>=END_MS: break
        today=cn_day(t)
        if today!=day:
            day=today; day_peak=equity; streak=0
        else:
            day_peak=max(day_peak,equity)

        if pending and t==pending['entry_bar_t']:
            d=pending['d']; limit=pending['entry']
            touched=r['l']<=limit if d==1 else r['h']>=limit
            if touched:
                counts['filled_limits']+=1
                plan=pending['plan']; btc=float(plan['btc'])
                pos={k:v for k,v in pending.items() if k!='plan'}
                pos.update(btc=btc,sl=float(plan['sl']),tp1=float(plan['tp1']),tp2=float(plan['tp2']),
                           notional=float(plan['notional']),estimated_loss=float(plan['estimated_loss']),
                           cost_multiple=float(plan['cost_multiple']),entry_fee=limit*btc*maker,
                           gross=0.0,exit_fees=0.0,tp1_done=False,entry_t=t)
                leverage.append(pos['notional']/max(equity,1e-12)); pending=None
                hit_sl=r['l']<=pos['sl'] if d==1 else r['h']>=pos['sl']
                hit_tp1=r['h']>=pos['tp1'] if d==1 else r['l']<=pos['tp1']
                hit_tp2=r['h']>=pos['tp2'] if d==1 else r['l']<=pos['tp2']
                if hit_sl: close_full(pos,t,pos['sl'],'SL')
                elif hit_tp2:
                    take_tp1(pos,t); take_tp2_finish(pos,t)
                elif hit_tp1: take_tp1(pos,t)
            else:
                counts['canceled_limits']+=1; pending=None
        elif pending and t>pending['entry_bar_t']:
            counts['canceled_limits']+=1; pending=None

        if pos:
            d=pos['d']
            if not pos['tp1_done']:
                hit_sl=r['l']<=pos['sl'] if d==1 else r['h']>=pos['sl']
                hit_tp1=r['h']>=pos['tp1'] if d==1 else r['l']<=pos['tp1']
                hit_tp2=r['h']>=pos['tp2'] if d==1 else r['l']<=pos['tp2']
                if hit_sl: close_full(pos,t,pos['sl'],'SL')
                elif hit_tp2:
                    take_tp1(pos,t); take_tp2_finish(pos,t)
                elif hit_tp1: take_tp1(pos,t)
            else:
                hit_be=r['l']<=pos['entry'] if d==1 else r['h']>=pos['entry']
                hit_tp2=r['h']>=pos['tp2'] if d==1 else r['l']<=pos['tp2']
                if hit_be: take_be_finish(pos,t)
                elif hit_tp2: take_tp2_finish(pos,t)

        if pos or pending or t+STEP5<cooldown_until: continue
        s=signals.get(t)
        if not s: continue
        remaining=S.daily_loss-max(0.0,day_peak-equity)
        if remaining<=0: counts['daily_loss_blocked']+=1; continue
        if streak>=S.consecutive_losses: counts['streak_blocked']+=1; continue
        ticker={'bidPx':str(s['signal_close']),'askPx':str(s['signal_close'])}
        try:
            plan=make_plan(S,s['side_cn'],ticker,meta,s['atr15'],equity,remaining,s['position_multiplier'])
        except Halt:
            counts['sizing_blocked']+=1; continue
        if float(plan['cost_multiple'])<2:
            counts['cost_blocked']+=1; continue
        counts['submitted_limits']+=1
        d=1 if s['side']=='long' else -1
        pending={**s,'plan':plan,'d':d,'entry':float(plan['px']),'signal_t':t+STEP5,'entry_bar_t':t+STEP5}

    if pos and last:
        close_full(pos,min(last['t'],END_MS-STEP5),last['c'],'END_CLOSE')
    if pending: counts['canceled_limits']+=1

    wins=[x for x in trades if x['net_pnl']>0]; losses=[x for x in trades if x['net_pnl']<0]; flats=[x for x in trades if abs(x['net_pnl'])<1e-12]
    gp=sum(x['net_pnl'] for x in wins); gl=-sum(x['net_pnl'] for x in losses)
    return {'trades':trades,'start_equity':S.capital,'end_equity':equity,'return_pct':100*(equity/S.capital-1),
            'max_dd_pct':100*max_dd,'wins':len(wins),'losses':len(losses),'flats':len(flats),
            'win_rate_pct':100*len(wins)/len(trades) if trades else 0,'profit_factor':gp/gl if gl else None,
            'counts':dict(counts),'avg_effective_leverage':sum(leverage)/len(leverage) if leverage else 0,
            'max_effective_leverage':max(leverage) if leverage else 0}


def group_stats(trades,key):
    groups=defaultdict(list)
    for t in trades: groups[str(t.get(key))].append(t)
    out={}
    for k,v in sorted(groups.items()):
        wins=sum(x['net_pnl']>0 for x in v); net=sum(x['net_pnl'] for x in v)
        out[k]={'trades':len(v),'wins':wins,'losses':len(v)-wins,'win_rate_pct':100*wins/len(v),'net_pnl':net}
    return out


def main():
    five=fetch_candles('5m',WARMUP_MS,END_MS,STEP5)
    quarter=aggregate(five,STEP15); hour=aggregate(five,STEP1H); four=aggregate(five,STEP4H)
    day=fetch_candles('1Dutc',DAY_WARMUP_MS,END_MS,STEPD)
    meta=instrument_meta()
    signals=exact_signals(five,quarter,hour,four,day)
    sim=simulate(five,signals,meta); trades=sim.pop('trades')
    period_five=[r for r in five if START_MS<=r['t']<END_MS]
    report={
      'strategy':'KAYTRADE V1.3.4 exact strategy.signal + staged TP execution model',
      'period':{'start_utc':datetime.fromtimestamp(START_MS/1000,timezone.utc).isoformat(),'end_utc':datetime.fromtimestamp(END_MS/1000,timezone.utc).isoformat(),'days':30},
      'parameters':{'start_equity_usdt':100,'max_leverage':10,'max_notional_usdt':1000,'base_risk_usdt':1,'risk_pct_config':1,
                    'score_threshold':3.5,'position_tiers':'3.5-4.5=1x, 5.0-6.5=1.5x, 7.0-10=2x','stop':'1.0x ATR15',
                    'exit':'TP1 1R 50%; remainder BE; TP2 2R 50%','maker_bps':2,'taker_bps':5,'exit_slippage_bps':5,
                    'daily_loss_usdt':3,'consecutive_losses':3,'cooldown_minutes':30,'funding':'excluded'},
      'market':{'five_minute_candles':len(period_five),'btc_start':period_five[0]['o'],'btc_end':period_five[-1]['c'],
                'btc_return_pct':100*(period_five[-1]['c']/period_five[0]['o']-1)},
      'signal_bars':len(signals),'result':sim,
      'exit_outcomes':dict(Counter(t['exit_reason'] for t in trades)),
      'by_side':group_stats(trades,'side'), 'by_score':group_stats(trades,'score'),
      'by_multiplier':group_stats(trades,'position_multiplier'),'by_daily_ema':group_stats(trades,'daily_ema_name'),
      'notes':['10x is a maximum leverage setting; make_plan keeps a 10% available-balance buffer, so effective leverage can be below 10x.',
               'Parent entry is modeled as a one-5m-bar limit at the signal close; no fill means cancel.',
               'Before TP1, if SL and TP are both inside one 5m bar, SL wins. After TP1 was established, BE wins over TP2 when both are inside the same later bar.',
               'If TP1 and TP2 are both reached in the same bar without the original SL, both profit targets are counted because TP2 lies beyond TP1 in the same direction.',
               'Funding, latency, partial fills, queue priority and spread beyond the fixed fee/slippage assumptions are not reconstructed.']}
    with open('v134_1m_100u_10x_report.json','w') as f: json.dump(report,f,ensure_ascii=False,indent=2)
    fields=sorted({k for t in trades for k in t if k!='plan'})
    with open('v134_1m_100u_10x_trades.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows({k:v for k,v in t.items() if k!='plan'} for t in trades)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()

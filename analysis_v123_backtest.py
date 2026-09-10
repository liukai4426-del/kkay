import bisect
import csv
import json
import math
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

INST='BTC-USDT-SWAP'
START_MS=int(datetime(2026,6,10,tzinfo=timezone.utc).timestamp()*1000)
END_MS=int(datetime(2026,9,10,10,45,tzinfo=timezone.utc).timestamp()*1000)
WARMUP_MS=int(datetime(2026,4,1,tzinfo=timezone.utc).timestamp()*1000)
STEP5=300_000
STEP15=900_000
STEP1H=3_600_000
THRESHOLD=8
COOLDOWN_MS=30*60*1000
STOP_ATR=1.0
REWARD_R=1.5


def okx_get(path, params, retries=6):
    url='https://www.okx.com'+path+'?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'kkay-v123-backtest/1.0'})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req,timeout=20) as r:
                payload=json.load(r)
            if payload.get('code')!='0':
                raise RuntimeError(payload)
            return payload['data']
        except Exception:
            if attempt+1==retries: raise
            time.sleep(min(5,0.5*(2**attempt)))


def fetch_5m():
    rows={}
    cursor=END_MS+STEP5
    calls=0
    while cursor>WARMUP_MS:
        batch=okx_get('/api/v5/market/history-candles',{
            'instId':INST,'bar':'5m','after':str(cursor),'limit':'100'
        })
        calls+=1
        if not batch: break
        oldest=cursor
        for r in batch:
            ts=int(r[0]); oldest=min(oldest,ts)
            if WARMUP_MS<=ts<END_MS and len(r)>=9 and r[8]=='1':
                rows[ts]={'t':ts,'o':float(r[1]),'h':float(r[2]),'l':float(r[3]),'c':float(r[4]),'v':float(r[5])}
        if oldest>=cursor: break
        cursor=oldest
        if calls%15==0: time.sleep(.3)
        else: time.sleep(.11)
    out=[rows[k] for k in sorted(rows)]
    gaps=[(a['t'],b['t']) for a,b in zip(out,out[1:]) if b['t']-a['t']!=STEP5]
    if gaps:
        raise RuntimeError(f'5m gaps detected: {gaps[:5]} total={len(gaps)}')
    print(f'Fetched {len(out)} closed 5m candles in {calls} OKX calls',flush=True)
    return out


def aggregate(rows, step):
    need=step//STEP5
    groups=defaultdict(list)
    for r in rows:
        groups[(r['t']//step)*step].append(r)
    out=[]
    for t in sorted(groups):
        g=groups[t]
        if len(g)!=need: continue
        g=sorted(g,key=lambda x:x['t'])
        if any(b['t']-a['t']!=STEP5 for a,b in zip(g,g[1:])): continue
        out.append({'t':t,'o':g[0]['o'],'h':max(x['h'] for x in g),'l':min(x['l'] for x in g),'c':g[-1]['c'],'v':sum(x['v'] for x in g)})
    return out


def ema_series(vals,p):
    a=2/(p+1); out=[]; v=vals[0]
    for x in vals:
        v += a*(x-v); out.append(v)
    return out


def indicator_series(rows):
    n=len(rows); c=[r['c'] for r in rows]
    e20=ema_series(c,20); e50=ema_series(c,50); e200=ema_series(c,200)
    rsi=[None]*n; atr=[None]*n
    gains=[max(c[i]-c[i-1],0) for i in range(1,n)]
    losses=[max(c[i-1]-c[i],0) for i in range(1,n)]
    tr=[max(rows[i]['h']-rows[i]['l'],abs(rows[i]['h']-c[i-1]),abs(rows[i]['l']-c[i-1])) for i in range(1,n)]
    if n>14:
        g=sum(gains[:14])/14; l=sum(losses[:14])/14; a=sum(tr[:14])/14
        def rr(g,l): return 50 if g==l==0 else 100 if l==0 else 100-100/(1+g/l)
        rsi[14]=rr(g,l); atr[14]=a
        for i in range(15,n):
            g=(g*13+gains[i-1])/14; l=(l*13+losses[i-1])/14; a=(a*13+tr[i-1])/14
            rsi[i]=rr(g,l); atr[i]=a
    upper=[None]*n; lower=[None]*n
    for i in range(19,n):
        w=c[i-19:i+1]; mean=sum(w)/20; sd=math.sqrt(sum((x-mean)**2 for x in w)/20)
        upper[i]=mean+2*sd; lower[i]=mean-2*sd
    k=[None]*n; d=[None]*n; j=[None]*n; cross_up=[False]*n; cross_down=[False]*n
    kv=dv=50.0
    for i in range(8,n):
        low=min(rows[z]['l'] for z in range(i-8,i+1)); high=max(rows[z]['h'] for z in range(i-8,i+1))
        rsv=50 if high==low else 100*(c[i]-low)/(high-low)
        pk,pd=kv,dv; kv=(2*kv+rsv)/3; dv=(2*dv+kv)/3
        k[i]=kv; d[i]=dv; j[i]=3*kv-2*dv; cross_up[i]=pk<=pd and kv>dv; cross_down[i]=pk>=pd and kv<dv
    out=[]
    for i in range(n):
        out.append({
            'ema20':e20[i],'ema50':e50[i],'ema200':e200[i],
            'up':i>0 and e20[i]>e20[i-1] and e50[i]>e50[i-1],
            'down':i>0 and e20[i]<e20[i-1] and e50[i]<e50[i-1],
            'rsi':rsi[i],'atr':atr[i],'upper':upper[i],'lower':lower[i],
            'k':k[i],'d':d[i],'j':j[i],'cross_up':cross_up[i],'cross_down':cross_down[i]
        })
    return out


def period_feature(rows, ind, i, buy):
    if i<1 or ind[i]['j'] is None: return {'kdj':False,'ema':False,'reversal':False}
    cur,prev=ind[i],ind[i-1]; candle,pc=rows[i],rows[i-1]
    kdj=(cur['j']<=30 and cur['cross_up']) if buy else (cur['j']>=70 and cur['cross_down'])
    ema=(pc['c']<=prev['ema20'] and candle['c']>cur['ema20']) if buy else (pc['c']>=prev['ema20'] and candle['c']<cur['ema20'])
    rev=(candle['c']>candle['o'] and candle['c']>pc['h']) if buy else (candle['c']<candle['o'] and candle['c']<pc['l'])
    return {'kdj':kdj,'ema':ema,'reversal':rev}


def ladder(a,b,c):
    return 3 if a and b and c else 2 if a and b else 1 if a else 0


def level(total):
    return '高共振' if total>=15 else '强' if total>=11 else '普通' if total>=8 else '未达标'


def score_side(five,fi,i5,q,qi,i15,h,hi,i1,buy):
    if min(i5,i15,i1)<1: return None
    F,M,H=fi[i5],qi[i15],hi[i1]
    if any(x is None for x in (F['rsi'],M['rsi'],H['rsi'],F['lower'],M['lower'],H['lower'],H['atr'])): return None
    ff=period_feature(five,fi,i5,buy); mf=period_feature(q,qi,i15,buy); hf=period_feature(h,hi,i1,buy)
    r5=F['rsi']<=20 if buy else F['rsi']>=75
    r15=M['rsi']<=20 if buy else M['rsi']>=75
    r1=H['rsi']<=25 if buy else H['rsi']>=70
    b5=five[i5]['l']<=F['lower'] if buy else five[i5]['h']>=F['upper']
    b15=q[i15]['l']<=M['lower'] if buy else q[i15]['h']>=M['upper']
    b1=h[i1]['l']<=H['lower'] if buy else h[i1]['h']>=H['upper']
    rsi_score=ladder(r5,r15,r1); boll_score=ladder(b5,b15,b1)
    kdj=ladder(ff['kdj'],mf['kdj'],hf['kdj']); ema=ladder(ff['ema'],mf['ema'],hf['ema']); rev=ladder(ff['reversal'],mf['reversal'],hf['reversal'])
    combo=(q[i15]['l']<=M['lower'] and M['rsi']<30) if buy else (q[i15]['h']>=M['upper'] and M['rsi']>70)
    vb=False; vr=0.0
    if i15>=20:
        av=sum(x['v'] for x in q[i15-20:i15])/20
        vr=q[i15]['v']/av if av>0 else 0
        returned=(q[i15]['l']<=M['lower'] and q[i15]['c']>M['lower']) if buy else (q[i15]['h']>=M['upper'] and q[i15]['c']<M['upper'])
        vb=returned and vr>=1.3
    strong=(h[i1]['c']<H['ema200'] and H['ema20']<H['ema50'] and H['down']) if buy else (h[i1]['c']>H['ema200'] and H['ema20']>H['ema50'] and H['up'])
    items={'RSI':rsi_score,'BOLL':boll_score,'KDJ':kdj,'EMA20':ema,'反转K线':rev,'RSI+BOLL':2*int(combo),'量+BOLL回归':2*int(vb),'1H逆势':-3*int(strong)}
    raw=sum(items.values()); total=max(0,min(19,raw))
    return {'total':total,'level':level(total),'items':items,'volume_ratio':vr,'strong_opposite':strong}


def build_signals(five,q,h):
    fi,qi,hi=indicator_series(five),indicator_series(q),indicator_series(h)
    qends=[r['t']+STEP15 for r in q]; hends=[r['t']+STEP1H for r in h]
    signals={}
    for i5,r in enumerate(five):
        end=r['t']+STEP5
        if end<START_MS or end>END_MS: continue
        i15=bisect.bisect_right(qends,end)-1; i1=bisect.bisect_right(hends,end)-1
        if i15<0 or i1<0: continue
        lo=score_side(five,fi,i5,q,qi,i15,h,hi,i1,True)
        sh=score_side(five,fi,i5,q,qi,i15,h,hi,i1,False)
        if not lo or not sh: continue
        elig=[('long',lo),('short',sh)]
        elig=[x for x in elig if x[1]['total']>=THRESHOLD]
        side=None; sc=None
        if len(elig)==1: side,sc=elig[0]
        elif len(elig)==2 and elig[0][1]['total']!=elig[1][1]['total']:
            side,sc=max(elig,key=lambda x:x[1]['total'])
        if side:
            signals[r['t']]={'side':side,'score':sc['total'],'level':sc['level'],'atr15':qi[i15]['atr'],'items':sc['items']}
    return signals


def simulate(five,signals):
    trades=[]; pos=None; pending=None; cooldown_until=0
    for i,r in enumerate(five):
        if r['t']<START_MS: continue
        if r['t']>=END_MS: break
        if pending and not pos and r['t']>=cooldown_until:
            d=1 if pending['side']=='long' else -1
            entry=r['o']; dist=pending['atr15']*STOP_ATR
            pos=dict(pending,entry=entry,dist=dist,sl=entry-d*dist,tp=entry+d*dist*REWARD_R,entry_t=r['t'],direction=d)
            pending=None
        if pos:
            d=pos['direction']; hit_sl=(r['l']<=pos['sl']) if d==1 else (r['h']>=pos['sl']); hit_tp=(r['h']>=pos['tp']) if d==1 else (r['l']<=pos['tp'])
            if hit_sl or hit_tp:
                if hit_sl: exit_px=pos['sl']; rr=-1.0; reason='SL'
                else: exit_px=pos['tp']; rr=REWARD_R; reason='TP'
                trades.append({**pos,'exit':exit_px,'exit_t':r['t']+STEP5,'r':rr,'reason':reason})
                pos=None; cooldown_until=r['t']+STEP5+COOLDOWN_MS
        if not pos and not pending and r['t']+STEP5>=cooldown_until:
            s=signals.get(r['t'])
            if s and i+1<len(five): pending=dict(s,signal_t=r['t']+STEP5)
    open_mark=None
    if pos:
        last=next((x for x in reversed(five) if x['t']<END_MS),None)
        rr=pos['direction']*(last['c']-pos['entry'])/pos['dist'] if last else 0
        open_mark={**pos,'mark':last['c'] if last else pos['entry'],'r':rr}
    return trades,open_mark


def pnl_scenario(trades,start_equity,max_notional_mult,fee_bps=10,slip_bps=5):
    eq=start_equity; peak=eq; max_dd=0; rows=[]; wins=[]; losses=[]
    fee=fee_bps/10000; slip=slip_bps/10000
    for t in trades:
        d=t['direction']; raw_entry=t['entry']; entry=raw_entry*(1+d*slip)
        # Re-anchor intended price-distance exits to the adverse entry fill.
        dist=t['dist']; trigger=entry + d*(REWARD_R*dist if t['r']>0 else -dist)
        exit_fill=trigger*(1-d*slip)
        risk=.01*eq
        stop_trigger=entry-d*dist; stop_fill=stop_trigger*(1-d*slip)
        per_unit=abs(entry-stop_fill)+(entry+stop_fill)*fee
        qty=min(risk/per_unit,(eq*max_notional_mult)/entry)
        net=d*(exit_fill-entry)*qty-(entry+exit_fill)*fee*qty
        before=eq; eq+=net; peak=max(peak,eq); max_dd=max(max_dd,(peak-eq)/peak)
        nr=net/risk if risk>0 else 0
        row={**t,'equity_before':before,'equity_after':eq,'net_pnl':net,'net_r':nr,'qty':qty,'notional':qty*entry}
        rows.append(row); (wins if net>0 else losses).append(net)
    total_profit=sum(x for x in (r['net_pnl'] for r in rows) if x>0); total_loss=-sum(x for x in (r['net_pnl'] for r in rows) if x<0)
    return {'start':start_equity,'end':eq,'return_pct':100*(eq/start_equity-1),'max_dd_pct':100*max_dd,
            'profit_factor':total_profit/total_loss if total_loss else None,
            'avg_win':sum(wins)/len(wins) if wins else 0,'avg_loss':-sum(losses)/len(losses) if losses else 0,
            'payoff':(sum(wins)/len(wins))/(-sum(losses)/len(losses)) if wins and losses else None,'rows':rows}


def stats(trades):
    wins=[t for t in trades if t['r']>0]; losses=[t for t in trades if t['r']<0]
    gross_pf=(sum(t['r'] for t in wins)/-sum(t['r'] for t in losses)) if losses else None
    return {'trades':len(trades),'wins':len(wins),'losses':len(losses),'win_rate_pct':100*len(wins)/len(trades) if trades else 0,
            'gross_expectancy_r':sum(t['r'] for t in trades)/len(trades) if trades else 0,'gross_profit_factor':gross_pf,
            'gross_payoff':(sum(t['r'] for t in wins)/len(wins))/(-sum(t['r'] for t in losses)/len(losses)) if wins and losses else None,
            'avg_hold_min':sum((t['exit_t']-t['entry_t'])/60000 for t in trades)/len(trades) if trades else 0}


def breakdown(trades,keyfn):
    groups=defaultdict(list)
    for t in trades: groups[keyfn(t)].append(t)
    return {str(k):stats(v) for k,v in sorted(groups.items(),key=lambda x:str(x[0]))}


def main():
    five=fetch_5m(); q=aggregate(five,STEP15); h=aggregate(five,STEP1H)
    print(f'Resampled: 15m={len(q)} 1H={len(h)}',flush=True)
    signals=build_signals(five,q,h)
    print(f'Eligible signal bars={len(signals)}',flush=True)
    trades,open_mark=simulate(five,signals)
    base=stats(trades)
    by_level=breakdown(trades,lambda t:t['level'])
    by_side=breakdown(trades,lambda t:t['side'])
    by_month=breakdown(trades,lambda t:datetime.fromtimestamp(t['entry_t']/1000,timezone.utc).strftime('%Y-%m'))
    s5=pnl_scenario(trades,1000,5)
    s1=pnl_scenario(trades,1000,1)
    report={
      'period':{'start_utc':'2026-06-10T00:00:00Z','end_utc':'2026-09-10T10:45:00Z','warmup_start_utc':'2026-04-01T00:00:00Z'},
      'source':'OKX public history-candles BTC-USDT-SWAP 5m; 15m and 1H resampled from 5m',
      'rules':{'score_max':19,'threshold':8,'bands':'8-10 ordinary / 11-14 strong / 15-19 high-confluence','stop':'1.0x 15m ATR','target':'1.5R','cooldown_min':30,'single_position':True,'same_bar_sl_tp':'SL first (conservative)'},
      'execution_assumptions':{'entry':'next 5m open after signal','FOK_fill':'assumed filled','funding':'not included','network_faults':'not included','daily_loss_and_manual_rearm':'not included','gross_stats':'no fees/slippage','net_scenarios':'10 bps fee per side + 5 bps adverse slippage per side, 1% equity risk target'},
      'market':{'five_min_candles':len(five),'fifteen_min_candles':len(q),'hour_candles':len(h),'start_price':next(r['o'] for r in five if r['t']>=START_MS),'end_price':next(r['c'] for r in reversed(five) if r['t']<END_MS)},
      'strategy':base,'by_level':by_level,'by_side':by_side,'by_month':by_month,
      'net_5x_risk_capacity':{k:v for k,v in s5.items() if k!='rows'},
      'net_1x_notional_cap':{k:v for k,v in s1.items() if k!='rows'},
      'open_position_at_end':open_mark,
    }
    with open('backtest_report.json','w') as f: json.dump(report,f,ensure_ascii=False,indent=2)
    with open('backtest_trades.csv','w',newline='') as f:
        fields=['signal_t','entry_t','exit_t','side','score','level','entry','sl','tp','exit','r','reason','dist']
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for t in trades: w.writerow({k:t.get(k) for k in fields})
    with open('backtest_equity_5x.csv','w',newline='') as f:
        fields=['entry_t','exit_t','side','score','level','net_pnl','net_r','equity_before','equity_after','notional']
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for t in s5['rows']: w.writerow({k:t.get(k) for k in fields})
    print('===REPORT_JSON===')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()

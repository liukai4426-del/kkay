import bisect
import csv
import json
import math
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core import indicators
from strategy import _setup, signal

INST='BTC-USDT-SWAP'
START_MS=int(datetime(2026,3,10,15,25,tzinfo=timezone.utc).timestamp()*1000)
END_MS=int(datetime(2026,9,10,15,25,tzinfo=timezone.utc).timestamp()*1000)
WARMUP_MS=int(datetime(2026,1,1,0,0,tzinfo=timezone.utc).timestamp()*1000)
STEP5=300_000
STEP15=900_000
STEP1H=3_600_000
STEP4H=14_400_000
THRESHOLD=4.0
STOP_ATR=1.0
REWARD_R=1.5
COOLDOWN_MS=30*60*1000
START_EQUITY=1000.0
MAX_LEVERAGE=10.0
RISK_FRACTION=0.01
# OKX regular-user standard futures schedule as of 2026-09-10.
ENTRY_MAKER_BPS=2.0   # 0.0200%
EXIT_TAKER_BPS=5.0    # 0.0500%
EXIT_SLIP_BPS=5.0     # conservative market-exit slippage budget, not an OKX fee
COST_MULTIPLE_MIN=2.0
STREAK_LIMIT=3
WINDOW=1500
CN=ZoneInfo('Asia/Shanghai')


def okx_get(path, params, retries=7):
    url='https://www.okx.com'+path+'?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'kaytrade-v132-6m-10x-backtest/1.0'})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req,timeout=25) as r:
                payload=json.load(r)
            if payload.get('code')!='0':
                raise RuntimeError(payload)
            return payload['data']
        except Exception as exc:
            if attempt+1==retries:
                raise
            print(f'OKX retry {attempt+1}/{retries}: {exc}',flush=True)
            time.sleep(min(6,0.6*(2**attempt)))


def fetch_5m():
    rows={}; cursor=END_MS+STEP5; calls=0
    while cursor>WARMUP_MS:
        batch=okx_get('/api/v5/market/history-candles',{
            'instId':INST,'bar':'5m','after':str(cursor),'limit':'100'})
        calls+=1
        if not batch:
            break
        oldest=cursor
        for r in batch:
            ts=int(r[0]); oldest=min(oldest,ts)
            if WARMUP_MS<=ts<END_MS and len(r)>=9 and str(r[8])=='1':
                rows[ts]={'t':ts,'o':float(r[1]),'h':float(r[2]),'l':float(r[3]),'c':float(r[4]),'v':float(r[5])}
        if oldest>=cursor:
            break
        cursor=oldest
        if calls%100==0:
            print(f'OKX history calls={calls}, candles={len(rows)}',flush=True)
        time.sleep(.09)
    out=[rows[k] for k in sorted(rows)]
    gaps=[(a['t'],b['t']) for a,b in zip(out,out[1:]) if b['t']-a['t']!=STEP5]
    if gaps:
        raise RuntimeError(f'5m gaps: {gaps[:5]} total={len(gaps)}')
    print(f'Fetched {len(out)} closed 5m candles in {calls} calls',flush=True)
    return out


def aggregate(rows,step):
    need=step//STEP5; groups=defaultdict(list)
    for r in rows:
        groups[(r['t']//step)*step].append(r)
    out=[]
    for t in sorted(groups):
        g=sorted(groups[t],key=lambda x:x['t'])
        if len(g)!=need or any(b['t']-a['t']!=STEP5 for a,b in zip(g,g[1:])):
            continue
        out.append({'t':t,'o':g[0]['o'],'h':max(x['h'] for x in g),'l':min(x['l'] for x in g),'c':g[-1]['c'],'v':sum(x['v'] for x in g)})
    return out


def exact_signals(five,q,h,four):
    setup_ok={}
    for i15 in range(220,len(q)):
        qw=q[max(0,i15-WINDOW+1):i15+1]
        m=indicators(qw)
        setup_ok[i15]=(_setup(qw,m,True)[0]>=.5,_setup(qw,m,False)[0]>=.5)
        if i15 and i15%4000==0:
            print(f'15m setup prefilter {i15}/{len(q)}',flush=True)

    qends=[r['t']+STEP15 for r in q]
    hends=[r['t']+STEP1H for r in h]
    fourends=[r['t']+STEP4H for r in four]
    out={}; full_checked=0; skipped=0
    for i5,r in enumerate(five):
        end=r['t']+STEP5
        if end<START_MS or end>END_MS:
            continue
        i15=bisect.bisect_right(qends,end)-1
        i1=bisect.bisect_right(hends,end)-1
        i4=bisect.bisect_right(fourends,end)-1
        if i5<1000 or i15<1000 or i1<1000 or i4<220:
            continue
        if not any(setup_ok.get(i15,(False,False))):
            skipped+=1; continue
        fw=five[max(0,i5-WINDOW+1):i5+1]
        qw=q[max(0,i15-WINDOW+1):i15+1]
        hw=h[max(0,i1-WINDOW+1):i1+1]
        fourw=four[max(0,i4-WINDOW+1):i4+1]
        value=signal(hw,qw,fw,THRESHOLD,STOP_ATR,four=fourw)
        full_checked+=1
        if value['side']!='观望':
            sc=value['scores'][value['side']]
            out[r['t']]={
                'side':'long' if value['side']=='做多' else 'short',
                'side_cn':value['side'],'score':float(sc['total']),'level':sc['level'],
                'atr15':float(value['m']['atr']),'atr1h':float(value['h']['atr']),
                'signal_close':float(r['c']),'front_r':sc['structure']['front_r'],
                'strong_opposite':bool(sc['confirmations']['strong_opposite']),
                'rsi_resonance':bool(sc['confirmations']['rsi_resonance']),
                'ema_support':sc['confirmations']['ema_support'],
                'structure_4h':float(sc['layers']['structure_4h']),
                'structure_1h15m':float(sc['layers']['structure']),
                'setup':float(sc['layers']['setup']),'trigger':float(sc['layers']['trigger']),
            }
        if full_checked and full_checked%2500==0:
            print(f'Full V1.3.2 evaluations={full_checked}, eligible={len(out)}',flush=True)
    print(f'Prefilter skipped={skipped}; full evaluations={full_checked}; eligible bars={len(out)}',flush=True)
    return out


def cn_day(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).astimezone(CN).strftime('%Y-%m-%d')


def cost_multiple(price,atr15,entry_bps=ENTRY_MAKER_BPS,exit_bps=EXIT_TAKER_BPS,slip_bps=EXIT_SLIP_BPS):
    total_bps=entry_bps+exit_bps+slip_bps
    cost=price*(total_bps/10000)
    return atr15*STOP_ATR*REWARD_R/cost if cost>0 else math.inf


def simulate(five,signals,max_leverage=MAX_LEVERAGE,entry_bps=ENTRY_MAKER_BPS,exit_bps=EXIT_TAKER_BPS,slip_bps=EXIT_SLIP_BPS):
    trades=[]; pos=None; pending=None; cooldown_until=0
    equity=START_EQUITY; peak=equity; max_dd=0.0
    submitted=filled=canceled=cost_blocked=streak_blocked=0
    streak=0; streak_day=''; streak_stop_days=set(); same_entry_bar_exits=0
    leverage_capped=0; leverage_samples=[]
    entry_fee=entry_bps/10000; exit_fee=exit_bps/10000; slip=slip_bps/10000

    def close_position(p,t,trigger,reason,gross_r):
        nonlocal equity,peak,max_dd,streak,same_entry_bar_exits
        d=p['d']; exit_fill=trigger*(1-d*slip)
        net=d*(exit_fill-p['entry'])*p['qty'] - p['entry']*p['qty']*entry_fee - exit_fill*p['qty']*exit_fee
        equity+=net; peak=max(peak,equity); max_dd=max(max_dd,(peak-equity)/peak)
        trade={**p,'exit_t':t+STEP5,'exit':exit_fill,'trigger_price':trigger,'r':gross_r,'exit_reason':reason,'net_pnl':net,'equity_after':equity}
        trades.append(trade)
        streak=streak+1 if net<0 else 0
        return trade

    for r in five:
        t=r['t']
        if t<START_MS: continue
        if t>=END_MS: break
        day=cn_day(t)
        if day!=streak_day:
            streak_day=day; streak=0

        if pending and t==pending['entry_bar_t']:
            d=1 if pending['side']=='long' else -1
            touched=(r['l']<=pending['limit']) if d==1 else (r['h']>=pending['limit'])
            if touched:
                filled+=1
                entry=pending['limit']; dist=pending['atr15']*STOP_ATR
                sl=entry-d*dist; tp=entry+d*dist*REWARD_R
                risk_budget=RISK_FRACTION*equity
                # Risk sizing includes price-to-stop loss, maker entry fee, taker exit fee, and adverse exit slippage.
                stop_fill=sl*(1-d*slip)
                loss_per_btc=abs(stop_fill-entry) + entry*entry_fee + stop_fill*exit_fee
                risk_qty=risk_budget/loss_per_btc
                leverage_qty=(equity*max_leverage)/entry
                qty=min(risk_qty,leverage_qty)
                capped=leverage_qty < risk_qty
                if capped: leverage_capped+=1
                eff_lev=entry*qty/equity if equity>0 else 0.0
                leverage_samples.append(eff_lev)
                pos={**pending,'entry':entry,'entry_t':t,'dist':dist,'sl':sl,'tp':tp,'d':d,'qty':qty,
                     'equity_before':equity,'effective_leverage':eff_lev,'leverage_capped':capped}
                pending=None
                hit_sl=(r['l']<=sl) if d==1 else (r['h']>=sl)
                hit_tp=(r['h']>=tp) if d==1 else (r['l']<=tp)
                if hit_sl or hit_tp:
                    same_entry_bar_exits+=1
                    reason='SL' if hit_sl else 'TP'; trigger=sl if hit_sl else tp
                    gross_r=-1.0 if hit_sl else REWARD_R
                    close_position(pos,t,trigger,reason,gross_r)
                    if streak>=STREAK_LIMIT: streak_stop_days.add(day)
                    cooldown_until=t+STEP5+COOLDOWN_MS; pos=None
            else:
                canceled+=1; pending=None
        elif pending and t>pending['entry_bar_t']:
            canceled+=1; pending=None

        if pos:
            d=pos['d']; hit_sl=(r['l']<=pos['sl']) if d==1 else (r['h']>=pos['sl'])
            hit_tp=(r['h']>=pos['tp']) if d==1 else (r['l']<=pos['tp'])
            if hit_sl or hit_tp:
                reason='SL' if hit_sl else 'TP'; trigger=pos['sl'] if hit_sl else pos['tp']
                gross_r=-1.0 if hit_sl else REWARD_R
                close_position(pos,t,trigger,reason,gross_r)
                if streak>=STREAK_LIMIT: streak_stop_days.add(day)
                cooldown_until=t+STEP5+COOLDOWN_MS; pos=None

        if pos or pending or t+STEP5<cooldown_until: continue
        s=signals.get(t)
        if not s: continue
        cm=cost_multiple(s['signal_close'],s['atr15'],entry_bps,exit_bps,slip_bps)
        if cm<COST_MULTIPLE_MIN:
            cost_blocked+=1; continue
        if streak>=STREAK_LIMIT:
            streak_blocked+=1; streak_stop_days.add(day); continue
        submitted+=1
        pending={**s,'signal_t':t+STEP5,'limit':s['signal_close'],'entry_bar_t':t+STEP5,'cost_multiple':cm}

    wins=[x for x in trades if x['net_pnl']>0]; losses=[x for x in trades if x['net_pnl']<0]
    gp=sum(x['net_pnl'] for x in wins); gl=-sum(x['net_pnl'] for x in losses)
    return {
        'trades':trades,'start_equity':START_EQUITY,'end_equity':equity,'return_pct':100*(equity/START_EQUITY-1),
        'max_dd_pct':100*max_dd,'wins':len(wins),'losses':len(losses),'win_rate_pct':100*len(wins)/len(trades) if trades else 0,
        'profit_factor':gp/gl if gl else None,'gross_expectancy_r':sum(x['r'] for x in trades)/len(trades) if trades else 0,
        'submitted_limits':submitted,'filled_limits':filled,'canceled_limits':canceled,'cost_blocked':cost_blocked,
        'streak_blocked':streak_blocked,'streak_stop_days':sorted(streak_stop_days),'same_entry_bar_exits':same_entry_bar_exits,
        'leverage_capped_trades':leverage_capped,
        'avg_effective_leverage':sum(leverage_samples)/len(leverage_samples) if leverage_samples else 0,
        'max_effective_leverage':max(leverage_samples) if leverage_samples else 0,
    }


def summarize(rows,key):
    groups=defaultdict(list)
    for x in rows: groups[str(x[key])].append(x)
    out={}
    for k,v in sorted(groups.items()):
        w=sum(x['net_pnl']>0 for x in v)
        out[k]={'trades':len(v),'wins':w,'losses':len(v)-w,'win_rate_pct':100*w/len(v),
                'net_pnl':sum(x['net_pnl'] for x in v),'gross_r':sum(x['r'] for x in v)}
    return out


def component_summary(trades):
    tests={
        '4H_structure':lambda t:t.get('structure_4h',0)>0,
        '1H_15m_structure':lambda t:t.get('structure_1h15m',0)>0,
        'any_structure':lambda t:t.get('structure_4h',0)>0 or t.get('structure_1h15m',0)>0,
        'RSI_resonance':lambda t:bool(t.get('rsi_resonance')),
        'EMA_dynamic_support':lambda t:bool(t.get('ema_support')),
        'strong_countertrend':lambda t:bool(t.get('strong_opposite')),
    }
    out={}
    for name,pred in tests.items():
        yes=[t for t in trades if pred(t)]; no=[t for t in trades if not pred(t)]
        def pack(v):
            w=sum(x['net_pnl']>0 for x in v)
            return {'trades':len(v),'wins':w,'losses':len(v)-w,'win_rate_pct':100*w/len(v) if v else None,'net_pnl':sum(x['net_pnl'] for x in v)}
        out[name]={'with':pack(yes),'without':pack(no)}
    return out


def main():
    five=fetch_5m(); q=aggregate(five,STEP15); h=aggregate(five,STEP1H); four=aggregate(five,STEP4H)
    print(f'Resampled 15m={len(q)} 1H={len(h)} 4H={len(four)}',flush=True)
    signals=exact_signals(five,q,h,four)
    base=simulate(five,signals,max_leverage=10.0)
    compare_5x=simulate(five,signals,max_leverage=5.0)
    no_slip=simulate(five,signals,max_leverage=10.0,slip_bps=0.0)
    trades=base['trades']
    market_start=next(r['o'] for r in five if r['t']>=START_MS)
    market_end=next(r['c'] for r in reversed(five) if r['t']<END_MS)
    by_month=defaultdict(list)
    for t in trades:
        by_month[datetime.fromtimestamp(t['entry_t']/1000,timezone.utc).strftime('%Y-%m')].append(t)

    report={
        'period':{'start_utc':'2026-03-10T15:25:00Z','end_utc':'2026-09-10T15:25:00Z','warmup_start_utc':'2026-01-01T00:00:00Z'},
        'source':'OKX public BTC-USDT-SWAP closed 5m history-candles; 15m/1H/4H resampled from 5m',
        'strategy':'KAYTRADE V1.3.2 exact strategy.signal; score threshold 4.0/10',
        'market':{'five_min_candles':len(five),'fifteen_min_candles':len(q),'hour_candles':len(h),'four_hour_candles':len(four),
                  'start_price':market_start,'end_price':market_end,'btc_return_pct':100*(market_end/market_start-1)},
        'parameters':{'start_equity_usdt':1000.0,'max_leverage':10.0,'risk_target_pct_equity':1.0,
                      'entry_fee':'OKX regular futures Maker 0.0200%','exit_fee':'OKX regular futures Taker 0.0500%',
                      'exit_slippage_budget_pct':0.05,'score_threshold':4.0,'stop':'1.0x 15m ATR','target':'1.5R',
                      'cost_filter':'TP distance >= 2x (maker entry fee + taker exit fee + 0.05% exit slippage budget)',
                      'loss_streak_guard':'3 losses per Asia/Shanghai natural day'},
        'execution_assumptions':{'entry_limit_proxy':'signal 5m close; filled only if next 5m trades through limit','funding':'excluded',
                                 'partial_fills':'not reconstructable from OHLC; full/no-fill model','liquidation':'not explicitly modeled; ATR stop assumed to execute before liquidation unless OHLC gap semantics intervene',
                                 'same_bar_conflict':'SL-first if both SL and TP touched in same 5m bar'},
        'eligible_signal_bars':len(signals),
        'base_10x':{k:v for k,v in base.items() if k!='trades'},
        'comparison_5x_same_fees':{k:v for k,v in compare_5x.items() if k!='trades'},
        'no_slippage_10x_sensitivity':{k:v for k,v in no_slip.items() if k!='trades'},
        'by_side':summarize(trades,'side'),'by_score':summarize(trades,'score'),
        'by_month':{k:{'trades':len(v),'wins':sum(x['net_pnl']>0 for x in v),'losses':sum(x['net_pnl']<=0 for x in v),
                       'win_rate_pct':100*sum(x['net_pnl']>0 for x in v)/len(v),'net_pnl':sum(x['net_pnl'] for x in v),'gross_r':sum(x['r'] for x in v)}
                    for k,v in sorted(by_month.items())},
        'components':component_summary(trades),
    }
    with open('v132_6m_10x_report.json','w') as f: json.dump(report,f,ensure_ascii=False,indent=2)
    fields=['signal_t','entry_t','exit_t','side','score','level','signal_close','entry','sl','tp','trigger_price','exit','r','exit_reason','net_pnl','equity_before','equity_after','effective_leverage','leverage_capped','cost_multiple','front_r','strong_opposite','rsi_resonance','ema_support','structure_4h','structure_1h15m','setup','trigger']
    with open('v132_6m_10x_trades.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for t in trades: w.writerow({k:t.get(k) for k in fields})
    print('===REPORT_JSON===')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    main()

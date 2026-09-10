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
# Use the exact same market window as the canonical V1.3 report so the strategy delta is apples-to-apples.
START_MS=int(datetime(2026,6,10,0,0,tzinfo=timezone.utc).timestamp()*1000)
END_MS=int(datetime(2026,9,10,11,55,tzinfo=timezone.utc).timestamp()*1000)
WARMUP_MS=int(datetime(2026,4,1,0,0,tzinfo=timezone.utc).timestamp()*1000)
STEP5=300_000
STEP15=900_000
STEP1H=3_600_000
STEP4H=14_400_000
THRESHOLD=4.0
STOP_ATR=1.0
REWARD_R=1.5
COOLDOWN_MS=30*60*1000
FEE_BPS=10.0
EXIT_SLIP_BPS=5.0
COST_MULTIPLE_MIN=2.0
STREAK_LIMIT=3
WINDOW=1500
CN=ZoneInfo('Asia/Shanghai')

V13_BASE={
    'trades':30,'wins':12,'losses':18,'win_rate_pct':40.0,
    'return_pct':-10.8883,'max_dd_pct':11.2935,'profit_factor':0.3616,
    'gross_expectancy_r':0.0,
}


def okx_get(path, params, retries=7):
    url='https://www.okx.com'+path+'?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'kaytrade-v132-backtest/1.0'})
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
        if calls%50==0:
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
    # Exact hard-gate prefilter: V1.3.2 still requires 15m Setup >=0.5.
    setup_ok={}
    for i15 in range(220,len(q)):
        qw=q[max(0,i15-WINDOW+1):i15+1]
        m=indicators(qw)
        setup_ok[i15]=(_setup(qw,m,True)[0]>=.5,_setup(qw,m,False)[0]>=.5)
        if i15 and i15%2500==0:
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
        possible=setup_ok.get(i15,(False,False))
        if not any(possible):
            skipped+=1
            continue
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
                'layers':sc['layers'],'items':sc['items'],'reason':sc['reason'],
                'atr15':float(value['m']['atr']),'atr1h':float(value['h']['atr']),
                'signal_close':float(r['c']),'front_r':sc['structure']['front_r'],
                'warning':sc['structure']['warning'],
                'strong_opposite':sc['confirmations']['strong_opposite'],
                'rsi_resonance':bool(sc['confirmations']['rsi_resonance']),
                'ema_support':sc['confirmations']['ema_support'],
                'structure_4h':float(sc['layers']['structure_4h']),
                'structure_1h15m':float(sc['layers']['structure']),
                'setup':float(sc['layers']['setup']),'trigger':float(sc['layers']['trigger']),
            }
        if full_checked and full_checked%1000==0:
            print(f'Full V1.3.2 evaluations={full_checked}, eligible={len(out)}',flush=True)
    print(f'Prefilter skipped={skipped}; full evaluations={full_checked}; eligible bars={len(out)}',flush=True)
    return out


def cn_day(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).astimezone(CN).strftime('%Y-%m-%d')


def cost_multiple(price,atr15,fee_bps=FEE_BPS,slip_bps=EXIT_SLIP_BPS):
    cost=price*((2*fee_bps+slip_bps)/10000)
    return atr15*STOP_ATR*REWARD_R/cost if cost>0 else math.inf


def simulate(five,signals,score_floor=THRESHOLD,fee_bps=FEE_BPS,slip_bps=EXIT_SLIP_BPS,start_equity=1000.0,max_mult=5.0,apply_streak=True):
    trades=[]; pos=None; pending=None; cooldown_until=0
    equity=start_equity; peak=equity; max_dd=0.0
    submitted=filled=canceled=cost_blocked=streak_blocked=score_blocked=0
    streak_stop_days=set(); same_entry_bar_exits=0
    streak=0; streak_day=''
    fee=fee_bps/10000; slip=slip_bps/10000
    for r in five:
        t=r['t']
        if t<START_MS:
            continue
        if t>=END_MS:
            break
        day=cn_day(t)
        if day!=streak_day:
            streak_day=day; streak=0

        if pending and t==pending['entry_bar_t']:
            d=1 if pending['side']=='long' else -1
            touched=(r['l']<=pending['limit']) if d==1 else (r['h']>=pending['limit'])
            if touched:
                filled+=1
                entry=pending['limit']; dist=pending['atr15']*STOP_ATR
                risk=.01*equity
                sl=entry-d*dist; tp=entry+d*dist*REWARD_R
                per_btc=dist+(entry+sl)*fee+entry*slip
                qty=min(risk/per_btc,(equity*max_mult)/entry)
                pos={**pending,'entry':entry,'entry_t':t,'dist':dist,'sl':sl,'tp':tp,'d':d,'qty':qty,'equity_before':equity}
                pending=None
                hit_sl=(r['l']<=sl) if d==1 else (r['h']>=sl)
                hit_tp=(r['h']>=tp) if d==1 else (r['l']<=tp)
                if hit_sl or hit_tp:
                    same_entry_bar_exits+=1
                    reason='SL' if hit_sl else 'TP'; trigger=sl if hit_sl else tp
                    gross_r=-1.0 if hit_sl else REWARD_R
                    exit_fill=trigger*(1-d*slip)
                    net=d*(exit_fill-entry)*qty-(entry+exit_fill)*fee*qty
                    equity+=net; peak=max(peak,equity); max_dd=max(max_dd,(peak-equity)/peak)
                    trades.append({**pos,'exit_t':t+STEP5,'exit':exit_fill,'trigger_price':trigger,'r':gross_r,'exit_reason':reason,'net_pnl':net,'equity_after':equity})
                    streak=streak+1 if net<0 else 0
                    if apply_streak and streak>=STREAK_LIMIT: streak_stop_days.add(day)
                    cooldown_until=t+STEP5+COOLDOWN_MS; pos=None
            else:
                canceled+=1; pending=None
        elif pending and t>pending['entry_bar_t']:
            canceled+=1; pending=None

        if pos:
            d=pos['d']
            hit_sl=(r['l']<=pos['sl']) if d==1 else (r['h']>=pos['sl'])
            hit_tp=(r['h']>=pos['tp']) if d==1 else (r['l']<=pos['tp'])
            if hit_sl or hit_tp:
                reason='SL' if hit_sl else 'TP'; trigger=pos['sl'] if hit_sl else pos['tp']
                gross_r=-1.0 if hit_sl else REWARD_R
                exit_fill=trigger*(1-d*slip)
                net=d*(exit_fill-pos['entry'])*pos['qty']-(pos['entry']+exit_fill)*fee*pos['qty']
                equity+=net; peak=max(peak,equity); max_dd=max(max_dd,(peak-equity)/peak)
                trades.append({**pos,'exit_t':t+STEP5,'exit':exit_fill,'trigger_price':trigger,'r':gross_r,'exit_reason':reason,'net_pnl':net,'equity_after':equity})
                streak=streak+1 if net<0 else 0
                if apply_streak and streak>=STREAK_LIMIT: streak_stop_days.add(day)
                cooldown_until=t+STEP5+COOLDOWN_MS; pos=None

        if pos or pending or t+STEP5<cooldown_until:
            continue
        s=signals.get(t)
        if not s:
            continue
        if s['score']<score_floor:
            score_blocked+=1; continue
        cm=cost_multiple(s['signal_close'],s['atr15'],fee_bps,slip_bps)
        if cm<COST_MULTIPLE_MIN:
            cost_blocked+=1; continue
        if apply_streak and streak>=STREAK_LIMIT:
            streak_blocked+=1; streak_stop_days.add(day); continue
        submitted+=1
        pending={**s,'signal_t':t+STEP5,'limit':s['signal_close'],'entry_bar_t':t+STEP5,'cost_multiple':cm}

    wins=[t for t in trades if t['net_pnl']>0]; losses=[t for t in trades if t['net_pnl']<0]
    gp=sum(t['net_pnl'] for t in wins); gl=-sum(t['net_pnl'] for t in losses)
    return {
        'trades':trades,'start_equity':start_equity,'end_equity':equity,
        'return_pct':100*(equity/start_equity-1),'max_dd_pct':100*max_dd,
        'net_wins':len(wins),'net_losses':len(losses),
        'net_win_rate_pct':100*len(wins)/len(trades) if trades else 0,
        'net_profit_factor':gp/gl if gl else None,
        'gross_expectancy_r':sum(t['r'] for t in trades)/len(trades) if trades else 0,
        'submitted_limits':submitted,'filled_limits':filled,'canceled_limits':canceled,
        'limit_fill_rate_pct':100*filled/submitted if submitted else 0,
        'cost_blocked':cost_blocked,'streak_blocked':streak_blocked,'score_blocked':score_blocked,
        'streak_stop_days':sorted(streak_stop_days),'same_entry_bar_exits':same_entry_bar_exits,
    }


def summarize_group(rows,key):
    groups=defaultdict(list)
    for r in rows:
        groups[str(r[key])].append(r)
    out={}
    for k,v in sorted(groups.items()):
        wins=[x for x in v if x['net_pnl']>0]
        out[k]={'trades':len(v),'wins':len(wins),'losses':len(v)-len(wins),
                'win_rate_pct':100*len(wins)/len(v),'net_pnl':sum(x['net_pnl'] for x in v),
                'gross_r':sum(x['r'] for x in v)}
    return out


def component_summary(trades):
    tests={
        '4H_structure':lambda t:t.get('structure_4h',0)>0,
        '1H_15m_structure':lambda t:t.get('structure_1h15m',0)>0,
        'any_structure':lambda t:t.get('structure_4h',0)>0 or t.get('structure_1h15m',0)>0,
        'RSI_resonance':lambda t:bool(t.get('rsi_resonance')),
        'EMA_dynamic_support':lambda t:bool(t.get('ema_support')),
        'strong_countertrend':lambda t:bool(t.get('strong_opposite')),
        'setup_1_or_more':lambda t:float(t.get('setup',0))>=1.0,
        'trigger_1_or_more':lambda t:float(t.get('trigger',0))>=1.0,
    }
    out={}
    for name,pred in tests.items():
        yes=[t for t in trades if pred(t)]; no=[t for t in trades if not pred(t)]
        def pack(rows):
            w=sum(t['net_pnl']>0 for t in rows)
            return {'trades':len(rows),'wins':w,'losses':len(rows)-w,
                    'win_rate_pct':100*w/len(rows) if rows else None,
                    'net_pnl':sum(t['net_pnl'] for t in rows)}
        out[name]={'with':pack(yes),'without':pack(no)}
    return out


def main():
    five=fetch_5m(); q=aggregate(five,STEP15); h=aggregate(five,STEP1H); four=aggregate(five,STEP4H)
    print(f'Resampled 15m={len(q)} 1H={len(h)} 4H={len(four)}',flush=True)
    signals=exact_signals(five,q,h,four)

    runs={}
    for threshold in (4.0,4.5,5.0):
        runs[f'{threshold:.1f}']=simulate(five,signals,score_floor=threshold)
    base=runs['4.0']; trades=base['trades']
    low_cost=simulate(five,signals,score_floor=4.0,fee_bps=5,slip_bps=2)
    zero_cost=simulate(five,signals,score_floor=4.0,fee_bps=0,slip_bps=0)

    market_start=next(r['o'] for r in five if r['t']>=START_MS)
    market_end=next(r['c'] for r in reversed(five) if r['t']<END_MS)
    by_month=defaultdict(list)
    for t in trades:
        key=datetime.fromtimestamp(t['entry_t']/1000,timezone.utc).strftime('%Y-%m')
        by_month[key].append(t)

    report={
        'period':{'start_utc':'2026-06-10T00:00:00Z','end_utc':'2026-09-10T11:55:00Z','warmup_start_utc':'2026-04-01T00:00:00Z'},
        'source':'OKX public BTC-USDT-SWAP 5m history-candles; 15m/1H/4H resampled from closed 5m candles',
        'strategy_version':'KAYTRADE V1.3.2 exact strategy.signal from analysis branch based on codex/v1-3-2-strategy',
        'market':{'five_min_candles':len(five),'fifteen_min_candles':len(q),'hour_candles':len(h),'four_hour_candles':len(four),
                  'start_price':market_start,'end_price':market_end,'btc_return_pct':100*(market_end/market_start-1)},
        'rules':{'score_max':10.0,'threshold':4.0,'setup_min':0.5,'trigger_min':0.5,
                 'countertrend':'-1.5 points, no separate 11-point gate','stop':'1.0x 15m ATR','target':'1.5R',
                 'cooldown_min':30,'forward_structure':'<1R block; 1.0-1.3R -1 point',
                 'cost_filter':'TP distance >= 2x budgeted round-trip execution cost','limit_timeout':'one 5m candle',
                 'china_day_loss_streak_stop':3},
        'execution_assumptions':{'entry_limit_proxy':'signal 5m close, filled only if next 5m candle trades through the limit; otherwise canceled',
                 'historical_bid_ask':'not available, spread/chase filter not reconstructed','partial_fills':'not reconstructable from OHLC; modeled full fill or no fill',
                 'same_entry_bar':'SL takes priority if both SL/TP appear in same 5m candle','funding':'excluded',
                 'daily_equity_loss_cap':'excluded because user-local absolute USDT setting is unknown',
                 'net_base':'1000 USDT normalized equity; 1% equity risk target; max 5x notional; 10bps fee each side; 5bps adverse market-exit slippage; limit entry has no slippage',
                 'three_loss_guard':'applied on net PnL, resets at Asia/Shanghai natural-day boundary'},
        'eligible_signal_bars_at_4':len(signals),
        'base_4_0':{k:v for k,v in base.items() if k!='trades'},
        'threshold_sensitivity':{k:{kk:vv for kk,vv in val.items() if kk!='trades'} for k,val in runs.items()},
        'low_cost_sensitivity':{k:v for k,v in low_cost.items() if k!='trades'},
        'zero_cost_sensitivity':{k:v for k,v in zero_cost.items() if k!='trades'},
        'by_side':summarize_group(trades,'side'),'by_level':summarize_group(trades,'level'),'by_score':summarize_group(trades,'score'),
        'by_month':{k:{'trades':len(v),'wins':sum(x['net_pnl']>0 for x in v),'losses':sum(x['net_pnl']<=0 for x in v),
                       'win_rate_pct':100*sum(x['net_pnl']>0 for x in v)/len(v),'net_pnl':sum(x['net_pnl'] for x in v),'gross_r':sum(x['r'] for x in v)}
                    for k,v in sorted(by_month.items())},
        'components':component_summary(trades),
        'v13_canonical_baseline':V13_BASE,
        'delta_vs_v13':{
            'trades':len(trades)-V13_BASE['trades'],
            'win_rate_pp':base['net_win_rate_pct']-V13_BASE['win_rate_pct'],
            'return_pp':base['return_pct']-V13_BASE['return_pct'],
            'max_dd_pp':base['max_dd_pct']-V13_BASE['max_dd_pct'],
            'profit_factor':(base['net_profit_factor']-V13_BASE['profit_factor']) if base['net_profit_factor'] is not None else None,
            'gross_expectancy_r':base['gross_expectancy_r']-V13_BASE['gross_expectancy_r'],
        },
    }

    with open('v132_backtest_report.json','w') as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
    fields=['signal_t','entry_t','exit_t','side','score','level','signal_close','entry','sl','tp','trigger_price','exit','r','exit_reason','net_pnl','equity_before','equity_after','cost_multiple','front_r','strong_opposite','rsi_resonance','ema_support','structure_4h','structure_1h15m','setup','trigger']
    with open('v132_backtest_trades.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for t in trades:
            w.writerow({k:t.get(k) for k in fields})
    print('===REPORT_JSON===')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    main()

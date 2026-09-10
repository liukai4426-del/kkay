import csv
import json
from collections import Counter
from datetime import datetime, timezone

import analysis_v134_1m_100u_10x as b
import analysis_v134_1m_100u_10x_fast as f


def simulate(five,signals,meta):
    equity=b.S.capital; global_peak=equity; max_dd=0.0
    day=''; day_peak=equity; streak=0; cooldown_until=0
    pending=None; pos=None; trades=[]; counts=Counter(); leverage=[]
    maker=b.S.fee_bps/10000; taker=b.S.taker_fee_bps/10000; slip=b.S.slippage_bps/10000

    def update_dd():
        nonlocal global_peak,max_dd
        global_peak=max(global_peak,equity)
        max_dd=max(max_dd,(global_peak-equity)/global_peak if global_peak>0 else 0)

    def exit_fill(trigger,d,qty):
        fill=trigger*(1-d*slip)
        return fill,fill*qty*taker

    def finish(p,t,reason):
        nonlocal equity,streak,cooldown_until,pos
        net=p['gross']-p['entry_fee']-p['exit_fees']
        equity+=net; update_dd()
        p.update(exit_t=t+b.STEP5,exit_reason=reason,net_pnl=net,equity_after=equity)
        trades.append(p.copy()); counts[reason]+=1
        streak=streak+1 if net<0 else 0
        cooldown_until=t+b.STEP5+b.S.cooldown_minutes*60_000
        pos=None

    def close_full(p,t,trigger,reason):
        qty=p['btc']; fill,fee=exit_fill(trigger,p['d'],qty)
        p['gross']+=p['d']*(fill-p['entry'])*qty; p['exit_fees']+=fee
        p['last_exit']=fill; finish(p,t,reason)

    def take_tp1(p,t):
        qty=p['btc']/2; fill,fee=exit_fill(p['tp1'],p['d'],qty)
        p['gross']+=p['d']*(fill-p['entry'])*qty; p['exit_fees']+=fee
        p['tp1_done']=True; p['tp1_fill']=fill; p['tp1_t']=t+b.STEP5

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
        if t<b.START_MS: continue
        if t>=b.END_MS: break
        today=b.cn_day(t)
        if today!=day:
            day=today; day_peak=equity; streak=0
        else:
            day_peak=max(day_peak,equity)

        just_filled=False
        if pending and t==pending['entry_bar_t']:
            d=pending['d']; limit=pending['entry']
            touched=r['l']<=limit if d==1 else r['h']>=limit
            if touched:
                counts['filled_limits']+=1; just_filled=True
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
                if hit_sl:
                    counts['same_entry_bar_exit']+=1; close_full(pos,t,pos['sl'],'SL')
                elif hit_tp2:
                    counts['same_entry_bar_exit']+=1; take_tp1(pos,t); take_tp2_finish(pos,t)
                elif hit_tp1:
                    # TP1 may occur on the entry bar, but the newly activated BE stop cannot
                    # use an earlier low/high from that same OHLC bar. Start BE checks next bar.
                    take_tp1(pos,t)
            else:
                counts['canceled_limits']+=1; pending=None
        elif pending and t>pending['entry_bar_t']:
            counts['canceled_limits']+=1; pending=None

        if pos and not just_filled:
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
                # Once BE existed before this bar, same-bar BE/TP2 ordering is unknown: choose BE first.
                if hit_be: take_be_finish(pos,t)
                elif hit_tp2: take_tp2_finish(pos,t)

        if pos or pending or t+b.STEP5<cooldown_until: continue
        s=signals.get(t)
        if not s: continue
        remaining=b.S.daily_loss-max(0.0,day_peak-equity)
        if remaining<=0: counts['daily_loss_blocked']+=1; continue
        if streak>=b.S.consecutive_losses: counts['streak_blocked']+=1; continue
        ticker={'bidPx':str(s['signal_close']),'askPx':str(s['signal_close'])}
        try:
            plan=b.make_plan(b.S,s['side_cn'],ticker,meta,s['atr15'],equity,remaining,s['position_multiplier'])
        except b.Halt:
            counts['sizing_blocked']+=1; continue
        if float(plan['cost_multiple'])<2:
            counts['cost_blocked']+=1; continue
        counts['submitted_limits']+=1
        d=1 if s['side']=='long' else -1
        pending={**s,'plan':plan,'d':d,'entry':float(plan['px']),'signal_t':t+b.STEP5,'entry_bar_t':t+b.STEP5}

    if pos and last:
        close_full(pos,min(last['t'],b.END_MS-b.STEP5),last['c'],'END_CLOSE')
    if pending: counts['canceled_limits']+=1

    wins=[x for x in trades if x['net_pnl']>0]; losses=[x for x in trades if x['net_pnl']<0]; flats=[x for x in trades if abs(x['net_pnl'])<1e-12]
    gp=sum(x['net_pnl'] for x in wins); gl=-sum(x['net_pnl'] for x in losses)
    return {'trades':trades,'start_equity':b.S.capital,'end_equity':equity,'return_pct':100*(equity/b.S.capital-1),
            'max_dd_pct':100*max_dd,'wins':len(wins),'losses':len(losses),'flats':len(flats),
            'win_rate_pct':100*len(wins)/len(trades) if trades else 0,'profit_factor':gp/gl if gl else None,
            'counts':dict(counts),'avg_effective_leverage':sum(leverage)/len(leverage) if leverage else 0,
            'max_effective_leverage':max(leverage) if leverage else 0,
            'avg_winner_usdt':sum(x['net_pnl'] for x in wins)/len(wins) if wins else 0,
            'avg_loser_usdt':sum(x['net_pnl'] for x in losses)/len(losses) if losses else 0}


def main():
    five=b.fetch_candles('5m',b.WARMUP_MS,b.END_MS,b.STEP5)
    quarter=b.aggregate(five,b.STEP15); hour=b.aggregate(five,b.STEP1H); four=b.aggregate(five,b.STEP4H)
    day=b.fetch_candles('1Dutc',b.DAY_WARMUP_MS,b.END_MS,b.STEPD)
    meta=b.instrument_meta(); signals=f.exact_signals(five,quarter,hour,four,day)
    sim=simulate(five,signals,meta); trades=sim.pop('trades')
    period=[r for r in five if b.START_MS<=r['t']<b.END_MS]
    report={'strategy':'KAYTRADE V1.3.4 exact strategy.signal + corrected staged TP execution model',
      'period':{'start_utc':datetime.fromtimestamp(b.START_MS/1000,timezone.utc).isoformat(),'end_utc':datetime.fromtimestamp(b.END_MS/1000,timezone.utc).isoformat(),'days':30},
      'parameters':{'start_equity_usdt':100,'max_leverage':10,'max_notional_usdt':1000,'base_risk_usdt':1,'risk_pct_config':1,
        'score_threshold':3.5,'position_tiers':'3.5-4.5=1x, 5.0-6.5=1.5x, 7.0-10=2x','stop':'1.0x ATR15',
        'exit':'TP1 1R 50%; remainder BE starting after TP1; TP2 2R 50%','maker_bps':2,'taker_bps':5,'exit_slippage_bps':5,
        'daily_loss_usdt':3,'consecutive_losses':3,'cooldown_minutes':30,'funding':'excluded'},
      'market':{'five_minute_candles':len(period),'btc_start':period[0]['o'],'btc_end':period[-1]['c'],'btc_return_pct':100*(period[-1]['c']/period[0]['o']-1)},
      'signal_bars':len(signals),'result':sim,'exit_outcomes':dict(Counter(t['exit_reason'] for t in trades)),
      'by_side':f.group_stats(trades,'side'),'by_score':f.group_stats(trades,'score'),'by_multiplier':f.group_stats(trades,'position_multiplier'),
      'by_daily_ema':f.group_stats(trades,'daily_ema_name'),
      'notes':['Setup prefilter is lossless because V1.3.4 requires 15m Setup >= 0.5.',
               '10x is a maximum; make_plan reserves 10% available-balance buffer, so effective leverage can be lower.',
               'Entry limit lasts one next 5m bar. Original SL wins pre-TP1 same-bar ambiguity. If TP1 happens on the entry bar, BE checks begin on the next bar, preventing look-back into an earlier low/high.',
               'After BE already exists before a bar, BE wins over TP2 if both are touched in that same later 5m bar.',
               'Funding, latency, partial fills and queue priority are excluded.']}
    with open('v134_1m_100u_10x_report.json','w') as fobj: json.dump(report,fobj,ensure_ascii=False,indent=2)
    fields=sorted({k for t in trades for k in t if k!='plan'})
    with open('v134_1m_100u_10x_trades.csv','w',newline='') as fobj:
        w=csv.DictWriter(fobj,fieldnames=fields); w.writeheader(); w.writerows({k:v for k,v in t.items() if k!='plan'} for t in trades)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()

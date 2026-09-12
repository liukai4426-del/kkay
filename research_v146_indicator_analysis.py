#!/usr/bin/env python3
import csv, json, math, statistics, bisect, os
from collections import defaultdict
from pathlib import Path
import research_v146_backtest as bt

OUT=Path(os.environ.get('BACKTEST_OUT','indicator_analysis_output')); OUT.mkdir(parents=True,exist_ok=True)

POSITIVE_FEATURES=['daily_ema5','daily_ema10','daily_ema20','fav15_structure','setup_boll_touch','setup_volume_boll','setup_kdj','setup_reversal','trigger_ema_reclaim','trigger_kdj','trigger_reversal','trigger_ema_direction','trend5_macd','trend5_boll','trend15_macd','trend15_boll']
NEG_FEATURES=['rsi_penalty','struct1h_penalty','struct4h_penalty','trend1h_penalty','trend4h_penalty','forward_penalty']
ALL_FEATURES=POSITIVE_FEATURES+NEG_FEATURES


def granular(market,sig,side,i1):
    buy=side=='做多'; idx=sig['idx']; i5,i15,i1h,i4h,id1=idx['5m'],idx['15m'],idx['1H'],idx['4H'],idx['1Dutc']
    d=market.data; ind=market.inds; price=sig['price']
    f={k:0 for k in ALL_FEATURES}
    de=bt.daily_ema_score(d['1Dutc'],ind['1Dutc'],id1,price,buy)
    if de==1:f['daily_ema5']=1
    elif de==2:f['daily_ema10']=1
    elif de==3:f['daily_ema20']=1
    row=sig['scores'][side]
    f['fav15_structure']=1 if row['components']['fav15']>0 else 0
    c=d['15m'][i15]; cur=ind['15m'][i15]
    f['setup_boll_touch']=1 if ((c['l']<=cur['lower']) if buy else (c['h']>=cur['upper'])) else 0
    hist=[d['15m'][j]['v'] for j in range(max(0,i15-20),i15)]
    avg=sum(hist)/len(hist) if hist else 0
    ratio=c['v']/avg if avg>0 else 0
    returned=(c['l']<=cur['lower'] and c['c']>cur['lower']) if buy else (c['h']>=cur['upper'] and c['c']<cur['upper'])
    f['setup_volume_boll']=1 if returned and ratio>=1.3 else 0
    f['setup_kdj']=1 if ((cur['j']<=30 and cur['cross_up']) if buy else (cur['j']>=70 and cur['cross_down'])) else 0
    f['setup_reversal']=1 if bt.reversal(d['15m'],i15,buy) else 0
    cur1=ind['1m'][i1]; prev1=ind['1m'][i1-1]; c1=d['1m'][i1]
    f['trigger_ema_reclaim']=1 if bt.ema_reclaim(d['1m'],ind['1m'],i1,buy) else 0
    f['trigger_kdj']=1 if (cur1['cross_up'] if buy else cur1['cross_down']) else 0
    f['trigger_reversal']=1 if bt.reversal(d['1m'],i1,buy) else 0
    f['trigger_ema_direction']=1 if ((c1['c']>cur1['ema20'] and cur1['ema20']>prev1['ema20']) if buy else (c1['c']<cur1['ema20'] and cur1['ema20']<prev1['ema20'])) else 0
    for tf,ii,prefix in [('5m',i5,'trend5'),('15m',i15,'trend15')]:
        ctf=d[tf][ii]; itf=ind[tf][ii]; pitf=ind[tf][ii-1]
        macd=(itf['dif']>itf['dea'] and itf['hist']>0 and itf['hist']>=itf['prev_hist']) if buy else (itf['dif']<itf['dea'] and itf['hist']<0 and itf['hist']<=itf['prev_hist'])
        boll=(ctf['c']>itf['middle'] and itf['middle']>pitf['middle']) if buy else (ctf['c']<itf['middle'] and itf['middle']<pitf['middle'])
        f[prefix+'_macd']=1 if macd else 0; f[prefix+'_boll']=1 if boll else 0
    comp=row['components']
    f['rsi_penalty']=1 if comp['rsi']<0 else 0
    f['struct1h_penalty']=1 if comp['struct1h']<0 else 0
    f['struct4h_penalty']=1 if comp['struct4h']<0 else 0
    f['trend1h_penalty']=1 if comp['trend1h']<0 else 0
    f['trend4h_penalty']=1 if comp['trend4h']<0 else 0
    f['forward_penalty']=1 if comp['front']<0 else 0
    return f

class ISim(bt.Simulator):
    def __init__(self,*a,**kw): super().__init__(*a,**kw); self.feature_by_leg={}
    def submit(self,sig,i1,price):
        before=len(self.legs); side=sig['side']; feats=None
        if side!='观望': feats=granular(self.m,sig,side,i1)
        super().submit(sig,i1,price)
        if len(self.legs)>before:
            self.feature_by_leg[self.legs[-1].leg_id]=feats

def pf(xs):
    gp=sum(x for x in xs if x>0); gl=-sum(x for x in xs if x<0)
    return gp/gl if gl>0 else math.inf

def stats(rows):
    xs=[r['net_pnl'] for r in rows]; n=len(rows)
    return {'n':n,'wins':sum(x>0 for x in xs),'win_rate':100*sum(x>0 for x in xs)/n if n else 0,'tp_rate':100*sum(str(r['exit_reason']).startswith('TP') for r in rows)/n if n else 0,'net_pnl':sum(xs),'avg_pnl':statistics.mean(xs) if xs else 0,'pf':pf(xs)}

def main():
    meta=bt.fetch_instrument(); data={tf:bt.fetch_candles(tf) for tf in ('1m','5m','15m','1H','4H','1Dutc')}; funding=bt.fetch_funding()
    inds={tf:bt.compute_indicators(rows) for tf,rows in data.items()}; ts={tf:[r['t'] for r in rows] for tf,rows in data.items()}
    zones={'15m':bt.ZoneCache(data['15m'],inds['15m'],160),'1H':bt.ZoneCache(data['1H'],inds['1H'],120),'4H':bt.ZoneCache(data['4H'],inds['4H'],180)}
    market=bt.Market(data,inds,ts,zones); sim=ISim(market,meta,funding); d1=data['1m']; start_i=bisect.bisect_left(ts['1m'],bt.START_MS)
    for i in range(start_i,len(d1)):
        bar=d1[i]
        if bar['t']>=bt.END_MS: break
        close=bar['t']+60000; sim.apply_funding_until(bar['t'],bar['o']); sim.fill_pending(bar); sim.process_exits(bar); sim.apply_funding_until(close,bar['c']); sim.record_equity(close,bar['c']); sig=market.score(i); sim.submit(sig,i,bar['c'])
    last=[r for r in d1 if r['t']<bt.END_MS][-1]; sim.finish(last)
    metrics,leg_net,cycles,closed=bt.analyze(sim,d1,funding,meta)
    rows=[]
    for l in closed:
        r={'leg_id':l.leg_id,'cycle_id':l.cycle_id,'side':l.side,'tier':l.tier,'score':l.score,'signal_utc':bt.fmt_dt(l.signal_ms),'exit_reason':l.reason,'net_pnl':leg_net(l),'hold_min':(l.exit_ms-l.fill_ms)/60000}
        r.update(sim.feature_by_leg.get(l.leg_id,{k:0 for k in ALL_FEATURES})); rows.append(r)
    assert len(rows)==509, f'Expected 509 filled legs, got {len(rows)}'
    with (OUT/'V146_4PT_1R2R_OrderIndicators.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    base=stats(rows); effects=[]
    for feat in ALL_FEATURES:
        yes=[r for r in rows if r[feat]]; no=[r for r in rows if not r[feat]]; sy,sn=stats(yes),stats(no)
        effects.append({'indicator':feat,**{f'yes_{k}':v for k,v in sy.items()},'no_win_rate':sn['win_rate'],'win_rate_lift_pp':sy['win_rate']-sn['win_rate'],'no_avg_pnl':sn['avg_pnl'],'avg_pnl_lift':sy['avg_pnl']-sn['avg_pnl']})
    effects.sort(key=lambda x:(x['win_rate_lift_pp'],x['yes_n']),reverse=True)
    with (OUT/'V146_Indicator_Effectiveness.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=effects[0].keys()); w.writeheader(); w.writerows(effects)
    pairs=[]
    for a_i,a in enumerate(ALL_FEATURES):
        for b in ALL_FEATURES[a_i+1:]:
            rr=[r for r in rows if r[a] and r[b]]
            if len(rr)<15: continue
            s=stats(rr); pairs.append({'indicator_a':a,'indicator_b':b,**s,'win_rate_vs_base_pp':s['win_rate']-base['win_rate']})
    pairs.sort(key=lambda x:(x['win_rate'],x['n']),reverse=True)
    with (OUT/'V146_Indicator_Pairs.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=pairs[0].keys()); w.writeheader(); w.writerows(pairs)
    bins=[]
    for sc in sorted(set(r['score'] for r in rows)):
        rr=[r for r in rows if r['score']==sc]; bins.append({'score':sc,**stats(rr)})
    with (OUT/'V146_ScoreBins.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=bins[0].keys()); w.writeheader(); w.writerows(bins)
    best=[x for x in effects if x['yes_n']>=20][:8]; worst=sorted([x for x in effects if x['yes_n']>=20],key=lambda x:x['win_rate_lift_pp'])[:8]
    bestpairs=[x for x in pairs if x['n']>=20][:10]; worstpairs=sorted([x for x in pairs if x['n']>=20],key=lambda x:x['win_rate'])[:10]
    report=['# V1.4.6（≥4分 + 1:2）指标有效性分析','',f"基准：509笔成交，胜率 {base['win_rate']:.2f}%，净PnL {base['net_pnl']:.3f} USDT，PF {base['pf']:.3f}。",'', '## 单指标：相对胜率提升较高','', '|指标|样本|胜率|相对未触发提升|净PnL|PF|','|---|---:|---:|---:|---:|---:|']
    for x in best: report.append(f"|{x['indicator']}|{x['yes_n']}|{x['yes_win_rate']:.2f}%|{x['win_rate_lift_pp']:+.2f}pp|{x['yes_net_pnl']:+.3f}|{x['yes_pf']:.3f}|")
    report += ['', '## 单指标：相对胜率较弱','', '|指标|样本|胜率|相对未触发提升|净PnL|PF|','|---|---:|---:|---:|---:|---:|']
    for x in worst: report.append(f"|{x['indicator']}|{x['yes_n']}|{x['yes_win_rate']:.2f}%|{x['win_rate_lift_pp']:+.2f}pp|{x['yes_net_pnl']:+.3f}|{x['yes_pf']:.3f}|")
    report += ['', '## 较强双指标组合（样本≥20）','', '|组合|样本|胜率|较基准|净PnL|PF|','|---|---:|---:|---:|---:|---:|']
    for x in bestpairs: report.append(f"|{x['indicator_a']} + {x['indicator_b']}|{x['n']}|{x['win_rate']:.2f}%|{x['win_rate_vs_base_pp']:+.2f}pp|{x['net_pnl']:+.3f}|{x['pf']:.3f}|")
    report += ['', '## 较弱双指标组合（样本≥20）','', '|组合|样本|胜率|较基准|净PnL|PF|','|---|---:|---:|---:|---:|---:|']
    for x in worstpairs: report.append(f"|{x['indicator_a']} + {x['indicator_b']}|{x['n']}|{x['win_rate']:.2f}%|{x['win_rate_vs_base_pp']:+.2f}pp|{x['net_pnl']:+.3f}|{x['pf']:.3f}|")
    report += ['', '## 具体分数表现','', '|分数|样本|胜率|净PnL|PF|','|---:|---:|---:|---:|---:|']
    for x in bins: report.append(f"|{x['score']:.1f}|{x['n']}|{x['win_rate']:.2f}%|{x['net_pnl']:+.3f}|{x['pf']:.3f}|")
    report += ['', '> 说明：这是条件关联分析，不等于因果。一个指标可能因为与其他指标共现而显得有效/无效；小样本组合不应直接用于实盘。']
    (OUT/'V146_Indicator_Analysis_Report.md').write_text('\n'.join(report),encoding='utf-8')
    print('\n'.join(report)); print('OUTPUT',OUT.resolve())

if __name__=='__main__': main()

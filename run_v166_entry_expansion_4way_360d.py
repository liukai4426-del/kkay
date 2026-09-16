#!/usr/bin/env python3
"""V1.6.6 research: 360D four single-variable entry-expansion tests.
All variants keep exact V1.6.5 5-minute BOLL opportunity lifetime.
"""
from __future__ import annotations
import csv,json,math,types
from datetime import timedelta
from pathlib import Path
import run_v165_1000d_2000u as base
import v165_model as prod
import v164_model as v164
import v163_model as v163
import v162_model as v162
import v153_model as core

research=base.research
DAYS=360
OUT=Path('backtest_output_v166_entry_expansion_4way_360d')
VARIANTS=('baseline','4h_neutral_allowed','macd_only_opposite_block','threshold_5_5','boll_middle_restored')
ORIG_BOLL=v163.boll_entry_signal
ORIG_APPLY=v163._apply_outer_rules
ORIG_4H=v162._apply_all_4h_gate
ORIG_CORE_BOLL=core.boll_entry_signal
ORIG_PENDING=base._ORIGINAL_PROCESS_PENDING
ORIG_EXIT=base._ORIGINAL_PROCESS_EXIT
ORIG_FINISH=base._ORIGINAL_FINISH


def dedupe(xs):
    out=[]
    for x in xs or []:
        if x not in out: out.append(x)
    return out

def pf(rows):
    gp=sum(max(float(r.get('net_pnl') or 0),0) for r in rows); gl=-sum(min(float(r.get('net_pnl') or 0),0) for r in rows)
    return gp/gl if gl else (math.inf if gp else 0.0)

def stats(rows):
    rows=list(rows); n=len(rows); w=sum(float(r.get('net_pnl') or 0)>0 for r in rows); pnl=sum(float(r.get('net_pnl') or 0) for r in rows)
    return {'trades':n,'wins':w,'losses':n-w,'win_rate_pct':100*w/n if n else 0,'net_pnl':pnl,'profit_factor':pf(rows),'expectancy':pnl/n if n else 0}

def metric(m):
    return {k:m.get(k) for k in ('trades','wins','losses','win_rate_pct','net_pnl','net_return_pct','profit_factor','expectancy','max_drawdown_usdt','max_drawdown_pct','fees','funding_pnl')}

def path_of(row,opp): return prod._path_from(row or {},opportunity=opp)

def unblock(row, needles, threshold):
    c=row.setdefault('confirmations',{}); req=c.setdefault('required',{}); blockers=list(c.get('blockers') or [])
    blockers=[b for b in blockers if not any(n.lower() in str(b).lower() for n in needles)]
    c['blockers']=dedupe(blockers)
    req_ok=all(bool(v) for v in req.values()) if req else True
    row['gate']=bool(req_ok and not blockers)
    row['eligible']=bool(row['gate'] and float(row.get('total') or 0)>=threshold)
    if row['eligible']: row['position_multiplier']=1.0

def reselect(result):
    scores=result.get('scores') or {}; q=[s for s,r in scores.items() if isinstance(r,dict) and r.get('eligible')]
    if len(q)==1: result['side']=q[0]
    elif len(q)==2:
        a,b=q; sa=float(scores[a].get('total') or 0); sb=float(scores[b].get('total') or 0); result['side']=a if sa>sb else b if sb>sa else '观望'
    else: result['side']='观望'
    return result

def macd_opposite(score,side):
    # Formal improvement requires two consecutive directional improvements. Research relaxation:
    # block only when the most recent MACD layer explicitly reports adverse/逆向; neutral/non-improving is allowed.
    c=(score or {}).get('confirmations') or {}
    for key in ('macd5_state','5m_macd_state','macd_state'):
        s=str(c.get(key) or '').lower()
        if s: return any(x in s for x in ('opposite','adverse','逆向','恶化'))
    # No explicit adverse flag => treat failure of strict consecutive-improvement as neutral, not opposite.
    return False

def make_model(variant):
    class Model:
        VERSION='1.6.6-research'; ENTRY_WINDOW_MS=300000; TIME_WINDOW_ENABLED=True
        THRESHOLD=5.5 if variant=='threshold_5_5' else 6.0
        OUTER_PATHS=prod.OUTER_PATHS; MIDDLE_PATH=prod.MIDDLE_PATH; RSI_MIN=prod.RSI_MIN; RSI_MAX=prod.RSI_MAX
        VOLUME_HARD_GATE=prod.VOLUME_HARD_GATE; BOLL_OUTER_SCORE=prod.BOLL_OUTER_SCORE
        _signal_window=staticmethod(prod._signal_window); _finite=staticmethod(prod._finite); _macd_improving=staticmethod(prod._macd_improving); _path_from=staticmethod(prod._path_from)
        @staticmethod
        def evaluate(hour,quarter,five,one,four,opportunity=None,stop_atr=1.0,maker_bps=2.0,taker_bps=5.0,slippage_bps=5.0,now_ms=None,allow_new=True):
            # Ensure exact 5m lifecycle on every call.
            prod.ENTRY_WINDOW_MS=300000; prod.TIME_WINDOW_ENABLED=True; prod._patch_v164_base()
            result,opp,tr=prod.evaluate(hour,quarter,five,one,four,opportunity=opportunity,stop_atr=stop_atr,maker_bps=maker_bps,taker_bps=taker_bps,slippage_bps=slippage_bps,now_ms=now_ms,allow_new=allow_new)
            scores=result.get('scores') or {}
            for side,row in scores.items():
                if not isinstance(row,dict): continue
                p=path_of(row,opp)
                c=row.setdefault('confirmations',{}); req=c.setdefault('required',{})
                if variant=='4h_neutral_allowed' and p in prod.OUTER_PATHS:
                    state=str(c.get('4H_trend_state') or '')
                    if state=='neutral':
                        for k in list(req):
                            if '4h' in k.lower(): req[k]=True
                        unblock(row,['4h','4H'],Model.THRESHOLD)
                elif variant=='macd_only_opposite_block' and p in prod.OUTER_PATHS and not macd_opposite(row,side):
                    for k in list(req):
                        if 'macd' in k.lower(): req[k]=True
                    unblock(row,['macd','MACD'],Model.THRESHOLD)
                elif variant=='threshold_5_5':
                    # only threshold changes; preserve every hard gate
                    if row.get('gate') and not (c.get('blockers') or []): row['eligible']=float(row.get('total') or 0)>=5.5
                elif variant=='boll_middle_restored' and p==prod.MIDDLE_PATH:
                    # Restore inherited V1.6.2 middle semantics: 4H aligned + formal MACD improvement; keep all other hard gates.
                    for k in list(req):
                        if 'middle_path_disabled' in k: req.pop(k,None)
                    unblock(row,['中轨路径已关闭','仅允许上下外轨','仅允许5m BOLL上下外轨'],Model.THRESHOLD)
            reselect(result)
            return result,opp,tr
        @staticmethod
        def execution_checks(plan,opportunity,score):
            ok,diag,blockers=prod.execution_checks(plan,opportunity,score)
            blockers=list(blockers or []); p=path_of(score,opportunity); c=(score or {}).get('confirmations') or {}; state=str(c.get('4H_trend_state') or '')
            if variant=='4h_neutral_allowed' and p in prod.OUTER_PATHS and state=='neutral': blockers=[b for b in blockers if '4H' not in str(b) and '4h' not in str(b)]
            if variant=='macd_only_opposite_block' and p in prod.OUTER_PATHS and not macd_opposite(score,str(opportunity.get('side') or '')): blockers=[b for b in blockers if 'MACD' not in str(b) and 'macd' not in str(b)]
            if variant=='boll_middle_restored' and p==prod.MIDDLE_PATH: blockers=[b for b in blockers if not any(x in str(b) for x in ('中轨路径已关闭','仅允许5m BOLL上下外轨'))]
            return not blockers,dict(diag or {}),dedupe(blockers)
    return Model

def configure():
    end=base.FIXED_END; start=end-timedelta(days=DAYS); sm=int(start.timestamp()*1000); em=int(end.timestamp()*1000)
    for n,v in {'START':start,'END':end,'START_MS':sm,'END_MS':em,'CAPITAL':2000.0,'LEVERAGE':5,'RISK_USDT':20.0,'RISK_PCT':1.0,'DAILY_LOSS':60.0,'COOLDOWN_MINUTES':0,'STOP_ATR':1.0,'REWARD_R':2.0}.items(): setattr(research.base,n,v)
    research.START=start; research.END=end; research.START_MS=sm; research.END_MS=em; research.SPLIT_MS=int((start+(end-start)/2).timestamp()*1000)
    research.CAPITAL=2000.; research.LEVERAGE=5; research.RISK_USDT=20.; research.RISK_PCT=1.; research.DAILY_LOSS=60.; research.FIRST_SIGNAL_NOTIONAL=2000.; research.STOP_ATR=1.; research.REWARD_R=2.; research.COOLDOWN_MS=0
    research.Simulator.process_pending=ORIG_PENDING; research.Simulator.process_exit=ORIG_EXIT; research.Simulator.finish=ORIG_FINISH
    if hasattr(research,'build1544'): research.build1544.PATH_C_ENABLED=False
    return start,end

def submit_variant(self,result,now_ms,mark):
    # Use audited V1.6.5 submit, temporarily exposing the research threshold/path set.
    model=research.model; old_model=base.production; old_thr=base.production.THRESHOLD
    base.production=model; base.production.THRESHOLD=model.THRESHOLD
    try: base._submit_v165(self,result,now_ms,mark)
    finally: base.production=old_model; old_model.THRESHOLD=old_thr

def write_csv(rows,path):
    rows=list(rows)
    if not rows: path.write_text(''); return
    keys=[]
    for r in rows:
        for k in r:
            if k not in keys: keys.append(k)
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)

def main():
    start,end=configure(); OUT.mkdir(exist_ok=True)
    print('V166_ENTRY_EXPANSION_4WAY_360D',start.isoformat(),end.isoformat(),'BOLL_LIFETIME=5m',flush=True)
    meta=research.base.fetch_instrument(); data={tf:research.base.fetch_candles(tf) for tf in ('1m','5m','15m','1H','4H')}; funding=research.base.fetch_funding(); ts={tf:[int(r['t']) for r in rows] for tf,rows in data.items()}; research.cache_patch.prime_one_minute(data['1m'],research.MODEL_WINDOW)
    allrows={}; summaries={}
    for variant in VARIANTS:
        # Middle variant: restore source generator and suppress v163's middle-disable rewrite before v165 patch installs it.
        if variant=='boll_middle_restored':
            v163.boll_entry_signal=ORIG_CORE_BOLL
            def middle_apply(row,p):
                if p==v163.MIDDLE_PATH: return row
                return ORIG_APPLY(row,p)
            v163._apply_outer_rules=middle_apply
        else:
            v163.boll_entry_signal=ORIG_BOLL; v163._apply_outer_rules=ORIG_APPLY
        prod.ENTRY_WINDOW_MS=300000; prod.TIME_WINDOW_ENABLED=True; prod.THRESHOLD=6.0; prod._patch_v164_base()
        model=make_model(variant); research.model=model; research.Simulator.submit=submit_variant
        sim,m=research.run(data,ts,meta,funding,variant='baseline'); rows=list(sim.trades); allrows[variant]=rows; summaries[variant]=metric(dict(m))
        write_csv(rows,OUT/f'trades_{variant}_360d.csv'); (OUT/f'metrics_{variant}_360d.json').write_text(json.dumps(dict(m)|{'variant':variant,'days':360,'boll_lifetime_min':5},ensure_ascii=False,indent=2))
        print('RESULT',variant,json.dumps(summaries[variant],ensure_ascii=False),flush=True)
    b=allrows['baseline']; bids={str(r.get('opportunity_id') or '') for r in b}
    inc={}
    for v in VARIANTS:
        ids={str(r.get('opportunity_id') or '') for r in allrows[v]}; added=[r for r in allrows[v] if str(r.get('opportunity_id') or '') not in bids]; lost=[r for r in b if str(r.get('opportunity_id') or '') not in ids]
        inc[v]={'added_vs_baseline':stats(added),'lost_from_baseline':stats(lost),'shared':len(ids&bids)}; write_csv(added,OUT/f'added_{v}_360d.csv')
    comp={'research':'V1.6.6 entry expansion four single-variable tests 360D','window':{'start':start.isoformat(),'end':end.isoformat()},'boll_signal_lifetime':'5m exact','variants':summaries,'incremental':inc}
    (OUT/'comparison_360d.json').write_text(json.dumps(comp,ensure_ascii=False,indent=2)); print('COMPARISON',json.dumps(comp,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__': main()

import bisect
import csv
import json
from collections import Counter, defaultdict

import analysis_v13_backtest as b
from strategy import signal

# Exact 30 executed entries from the completed base 3-month replay.
TRADES = [
(1781160600000,'short',8,'SL'),(1781199000000,'long',9,'TP'),(1782151500000,'long',8,'SL'),
(1782425700000,'short',8,'TP'),(1782736800000,'short',9,'SL'),(1782757800000,'short',10,'TP'),
(1782871800000,'short',9,'SL'),(1782916200000,'short',8,'SL'),(1783342500000,'long',8,'SL'),
(1783345500000,'long',8,'TP'),(1783388700000,'long',9,'SL'),(1783435800000,'long',9,'TP'),
(1783580700000,'short',10,'SL'),(1783604700000,'long',9,'SL'),(1783913400000,'long',9,'SL'),
(1784044500000,'short',8,'SL'),(1784298900000,'short',8,'TP'),(1784530500000,'long',10,'TP'),
(1787234100000,'long',8,'SL'),(1787319900000,'long',11,'SL'),(1787338800000,'long',8,'TP'),
(1787395200000,'long',8,'SL'),(1787533200000,'long',9,'SL'),(1787537700000,'long',9,'SL'),
(1787548200000,'long',8,'TP'),(1787664900000,'long',9,'SL'),(1787839200000,'long',10,'TP'),
(1787927400000,'long',8,'TP'),(1788134100000,'long',9,'TP'),(1788529800000,'long',9,'SL')]


def main():
    five=b.fetch_5m(); q=b.aggregate(five,b.STEP15); h=b.aggregate(five,b.STEP1H)
    ft=[r['t'] for r in five]; qends=[r['t']+b.STEP15 for r in q]; hends=[r['t']+b.STEP1H for r in h]
    rows=[]
    for signal_t,side_expected,score_expected,outcome in TRADES:
        bar=signal_t-b.STEP5
        i5=bisect.bisect_left(ft,bar)
        assert i5 < len(five) and five[i5]['t']==bar, (signal_t,'missing 5m')
        end=bar+b.STEP5
        i15=bisect.bisect_right(qends,end)-1; i1=bisect.bisect_right(hends,end)-1
        fw=five[max(0,i5-b.WINDOW+1):i5+1]; qw=q[max(0,i15-b.WINDOW+1):i15+1]; hw=h[max(0,i1-b.WINDOW+1):i1+1]
        value=signal(hw,qw,fw,b.THRESHOLD,b.STOP_ATR)
        expected_cn='做多' if side_expected=='long' else '做空'
        assert value['side']==expected_cn, (signal_t,value['side'],expected_cn)
        sc=value['scores'][value['side']]
        assert sc['total']==score_expected, (signal_t,sc['total'],score_expected)
        item_scores={name:score for name,score,_ in sc['items']}
        row={'signal_t':signal_t,'side':side_expected,'score':score_expected,'outcome':outcome,
             'level':sc['level'],'total':sc['total'],'environment':sc['layers']['environment'],
             'structure':sc['layers']['structure'],'setup':sc['layers']['setup'],'trigger':sc['layers']['trigger'],
             'front_penalty':sc['layers']['front_penalty'],'strong_opposite':sc['confirmations']['strong_opposite'],
             'items':item_scores}
        rows.append(row)

    names=[name for name,_,_ in value['scores'][value['side']]['items']]
    summary={}
    for outcome in ('TP','SL'):
        group=[r for r in rows if r['outcome']==outcome]
        counts={}; points={}
        for name in names:
            vals=[r['items'].get(name,0) for r in group]
            counts[name]=sum(v>0 for v in vals)
            points[name]=sum(vals)
        layer_counts={layer:Counter(r[layer] for r in group) for layer in ('environment','structure','setup','trigger','front_penalty')}
        combos=Counter(tuple(name for name in names if r['items'].get(name,0)>0) for r in group)
        summary[outcome]={
            'trades':len(group),'indicator_trigger_counts':counts,'indicator_points_total':points,
            'average_points_per_trade':{name:(points[name]/len(group) if group else 0) for name in names},
            'layer_score_distributions':{k:dict(sorted(v.items())) for k,v in layer_counts.items()},
            'top_positive_combinations':[{'indicators':list(k),'count':v} for k,v in combos.most_common(10)]}
    report={'trades':rows,'summary':summary}
    with open('v13_trade_component_report.json','w') as f: json.dump(report,f,ensure_ascii=False,indent=2)
    with open('v13_trade_components.csv','w',newline='') as f:
        fields=['signal_t','side','score','outcome','environment','structure','setup','trigger','front_penalty']+names
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in rows:
            flat={k:r.get(k) for k in fields if k in r}; flat.update(r['items']); w.writerow(flat)
    print('===COMPONENT_SUMMARY===')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__': main()

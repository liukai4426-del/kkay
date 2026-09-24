"""Account-local closed-round equity statistics; not fill-based realized PnL."""
import json
import math

def summarize(path):
    closed={}
    skipped=0
    if path.exists():
        with path.open() as f:
            for line in f:
                try:
                    record=json.loads(line)
                    if record.get('event') not in ('仓位归零','人工核对平仓'): continue
                    d=record['data']; cid=d['client_id']; pnl=float(d['equity_change'])
                    if not cid or not math.isfinite(pnl): raise ValueError()
                    closed[cid]=dict(time=record['time'],client_id=cid,pnl=pnl,
                                     side=d.get('side','旧记录'),entry=d.get('px','—'),
                                     sz=d.get('sz','—'))
                except (ValueError,KeyError,TypeError): skipped+=1
    rows=sorted(closed.values(),key=lambda row:row['time'])
    total=0.; curve=[0.]
    for row in rows:
        total+=row['pnl']; row['cumulative']=total; curve.append(total)
    wins=sum(row['pnl']>0 for row in rows)
    losses=sum(row['pnl']<0 for row in rows)
    return dict(rows=rows[-200:],count=len(rows),wins=wins,losses=losses,
                breakeven=len(rows)-wins-losses,total=total,
                win_rate=100*wins/len(rows) if rows else None,curve=curve,skipped=skipped)

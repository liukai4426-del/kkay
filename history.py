"""Account-local closed-round equity statistics; not fill-based realized PnL.

V1.7.1 accepts both legacy bare close-event names and version-prefixed
close-event names written by later runtime overlays. PEE/submit events are
intentionally excluded so one trading round is counted only after the
position is confirmed flat.
"""
import json
import math


def _is_closed_round_event(event):
    value = str(event or "").strip()
    return (
        value == "仓位归零"
        or value.endswith("仓位归零")
        or value == "人工核对平仓"
        or value.endswith("人工核对平仓")
    )


def summarize(path):
    closed={}
    skipped=0
    if path.exists():
        with path.open() as f:
            for line in f:
                try:
                    record=json.loads(line)
                    if not _is_closed_round_event(record.get('event')): continue
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

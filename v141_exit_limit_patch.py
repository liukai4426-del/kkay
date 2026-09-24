"""KAYTRADE V1.4.1 TP/SL trigger-limit execution.

All attached take-profit and stop-loss exits use explicit limit prices instead of
OKX's -1 market-execution sentinel. Trigger and limit prices are intentionally
the same. Manual flatten and emergency safety flatten are separate safety paths
and remain unchanged.

If an OKX TP/SL trigger creates a limit child order that remains unfilled, stop
new entries and fault-lock for manual verification rather than pretending the
position has closed or automatically replacing the user's limit exit with a
market TP/SL.
"""
import copy
import math

from v141_history_return_chart_patch import apply as apply_history
apply_history()

import engine
import exchange
import v137_strategy_patch as v137


def _present(value):
    return value not in (None, '')


def _limit_attached_payload(path, body):
    """Normalize any attached TP/SL on /trade/order to trigger-limit execution."""
    if path != '/api/v5/trade/order' or not isinstance(body, dict) or not body.get('attachAlgoOrds'):
        return body
    payload=copy.deepcopy(body)
    items=list(payload.get('attachAlgoOrds') or [])
    tps=[x for x in items if isinstance(x,dict) and _present(x.get('tpTriggerPx'))]
    sls=[x for x in items if isinstance(x,dict) and _present(x.get('slTriggerPx'))]
    if tps and sls:
        tp=tps[-1]; sl=sls[-1]
        tp_trigger=str(tp['tpTriggerPx']); sl_trigger=str(sl['slTriggerPx'])
        payload['attachAlgoOrds']=[{
            'attachAlgoClOrdId':str(tp.get('attachAlgoClOrdId') or sl.get('attachAlgoClOrdId') or ''),
            'tpOrdKind':'condition',
            'tpTriggerPx':tp_trigger,
            'tpOrdPx':tp_trigger,
            'tpTriggerPxType':str(tp.get('tpTriggerPxType') or 'last'),
            'slTriggerPx':sl_trigger,
            'slOrdPx':sl_trigger,
            'slTriggerPxType':str(sl.get('slTriggerPxType') or 'last'),
        }]
        return payload
    # Defensive support for a one-sided attached TP or SL.
    for item in items:
        if not isinstance(item,dict):
            continue
        if _present(item.get('tpTriggerPx')):
            item['tpOrdKind']=str(item.get('tpOrdKind') or 'condition')
            item['tpOrdPx']=str(item['tpTriggerPx'])
        if _present(item.get('slTriggerPx')):
            item['slOrdPx']=str(item['slTriggerPx'])
    payload['attachAlgoOrds']=items
    return payload


def _algos_for_legacy_validator(rows):
    """Present valid explicit-price brackets to the preserved V1.3.7 validator.

    The preserved validator historically expected OKX's -1 market sentinel.
    Only exact trigger==limit pairs are normalized for that validator; any other
    price remains untouched so a mismatched live protection order still halts.
    """
    out=[]
    for row in rows or []:
        item=copy.deepcopy(row)
        tp_trigger=item.get('tpTriggerPx'); tp_order=item.get('tpOrdPx')
        sl_trigger=item.get('slTriggerPx'); sl_order=item.get('slOrdPx')
        try:
            if _present(tp_trigger) and _present(tp_order) and math.isclose(float(tp_trigger),float(tp_order),rel_tol=0,abs_tol=1e-9):
                item['tpOrdPx']='-1'
        except (TypeError,ValueError):
            pass
        try:
            if _present(sl_trigger) and _present(sl_order) and math.isclose(float(sl_trigger),float(sl_order),rel_tol=0,abs_tol=1e-9):
                item['slOrdPx']='-1'
        except (TypeError,ValueError):
            pass
        out.append(item)
    return out


def _matching_leg_for_exit_child(row,p):
    """Return a leg when an OKX source=7 pending order matches our triggered TP/SL."""
    if not isinstance(row,dict) or str(row.get('source') or '')!='7':
        return None
    pos_side=str(p.get('posSide') or '')
    if str(row.get('posSide') or '')!=pos_side or str(row.get('tdMode') or '')!='isolated':
        return None
    expected_side='sell' if pos_side=='long' else 'buy'
    if str(row.get('side') or '')!=expected_side or str(row.get('ordType') or '') not in ('limit','post_only'):
        return None
    try:
        px=float(row.get('px')); sz=float(row.get('sz') or 0)
    except (TypeError,ValueError):
        return None
    if not math.isfinite(px) or px<=0 or not math.isfinite(sz) or sz<=0:
        return None
    for leg in p.get('legs') or []:
        if leg.get('state') not in ('filled','closed'):
            continue
        try:
            leg_sz=float(leg.get('sz') or 0)
            targets=[float(leg.get('tp') or 0),float(leg.get('sl') or 0)]
        except (TypeError,ValueError):
            continue
        if sz<=leg_sz+1e-8 and any(target>0 and math.isclose(px,target,rel_tol=0,abs_tol=1e-8) for target in targets):
            return leg
    return None


def apply():
    if getattr(engine.Engine,'_kaytrade_v141_exit_limit_applied',False):
        return

    previous_reconcile=v137._reconcile_v137

    def post(self,path,body):
        payload=_limit_attached_payload(path,body)
        return self.request('POST',path,payload,True)

    def reconcile(self,p):
        # A triggered stop/take-profit limit that remains pending is deliberately
        # not converted to market. Stop new entries and require account review.
        pending=self.x.orders()
        child=next((row for row in pending if _matching_leg_for_exit_child(row,p)),None)
        if child is not None:
            self.halt('V1.4.1限价止盈/止损已触发但尚未完全成交；保留该限价平仓单，停止新开仓，请核对OKX')
            try:self.emit('position',self.x.positions())
            except Exception:pass
            return

        original_algos=self.x.algos
        had_instance_attr='algos' in getattr(self.x,'__dict__',{})
        old_instance_attr=getattr(self.x,'__dict__',{}).get('algos') if had_instance_attr else None
        def algos():
            return _algos_for_legacy_validator(original_algos())
        self.x.algos=algos
        try:
            return previous_reconcile(self,p)
        finally:
            if had_instance_attr:
                self.x.__dict__['algos']=old_instance_attr
            else:
                self.x.__dict__.pop('algos',None)

    exchange.Exchange.post=post
    v137._reconcile_v137=reconcile
    engine.Engine._kaytrade_v141_exit_limit_applied=True


apply()

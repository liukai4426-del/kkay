"""V1.3.7 unfilled-entry lifecycle correction.

A completely unfilled/canceled limit entry is not a completed trade. Clear the active
cycle without touching last_close or the consecutive-loss counter, matching the proven
V1.3.6 behavior while keeping filled/closed multi-leg cycles unchanged.
"""
from v137_strategy_patch import apply as apply_v137
apply_v137()

import engine
import v137_strategy_patch as v137


def _never_filled(legs):
    rows=list(legs or [])
    if not rows:
        return False
    return all(
        str(leg.get('state') or '') == 'canceled'
        and not leg.get('filled')
        and not leg.get('filled_at')
        and float(leg.get('filled_sz') or 0) <= 0
        for leg in rows if isinstance(leg,dict)
    ) and all(isinstance(leg,dict) for leg in rows)


def apply():
    if getattr(engine.Engine,'_kaytrade_v137_cancel_fix_applied',False):
        return
    original_finalize=v137._finalize_cycle

    def finalize_cycle(self,p):
        legs=p.get('legs') if isinstance(p,dict) else None
        if _never_filled(legs):
            state=self.store.data
            previous_close=state.get('last_close',0)
            state['active']=None
            state['last_close']=previous_close
            self.store.save()
            self.store.record('V1.3.7限价开仓未成交',{
                'side':p.get('side'),
                'legs':len(legs or []),
                'tier_counts':p.get('tier_counts',{}),
            })
            self.emit('log','V1.3.7限价开仓未成交/已撤销；不计为交易，不触发平仓冷却，下一根有效5m信号可重新评估')
            return
        return original_finalize(self,p)

    v137._finalize_cycle=finalize_cycle
    engine.Engine._kaytrade_v137_cancel_fix_applied=True


apply()

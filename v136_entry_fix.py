"""V1.3.6 entry economics and 5m dedupe correction.

This patch intentionally leaves the proven fail-closed order-write path untouched.
It only adapts the legacy engine's fixed 2.0 cost gate to the V1.3.6 expected-cost
model and releases last_bar when a signal was rejected before any order was sent.
"""
import math

import engine
import v136_runtime

EXPECTED_COST_MIN = 1.20
LEGACY_ENGINE_COST_MIN = 2.0
_LAST_PLAN = None


def _engine_gate_multiple(expected_multiple):
    """Map the V1.3.6 expected-cost threshold onto the legacy engine's 2.0 gate."""
    value=float(expected_multiple)
    if not math.isfinite(value) or value < 0:
        return 0.0
    return value * (LEGACY_ENGINE_COST_MIN / EXPECTED_COST_MIN)


def _is_cost_reject(plan):
    if not isinstance(plan,dict):
        return False
    try:
        return float(plan.get('expected_fee_multiple')) < EXPECTED_COST_MIN
    except (TypeError,ValueError):
        return False


def _release_rejected_bar(state,before_last,bar,plan):
    """Release only a bar consumed by the pre-order cost filter.

    A genuine order submission still writes last_bar before POST and remains strictly
    deduplicated. If the bar was already consumed before this cycle, it is preserved.
    """
    if not isinstance(state,dict) or not _is_cost_reject(plan):
        return False
    if state.get('active'):
        return False
    if before_last == bar or state.get('last_bar') != bar:
        return False
    state['last_bar']=before_last
    return True


def apply():
    global _LAST_PLAN
    if getattr(engine.Engine,'_kaytrade_v136_entry_fix_applied',False):
        return

    # v136_runtime must own the live-account/fee model first.
    v136_runtime.apply()
    original_make_plan=engine.make_plan
    original_cycle=engine.Engine.cycle

    def make_plan(*args,**kwargs):
        global _LAST_PLAN
        plan=original_make_plan(*args,**kwargs)
        raw=float(plan.get('expected_fee_multiple',plan.get('cost_multiple',0)) or 0)
        plan['expected_fee_multiple']=raw
        plan['legacy_engine_cost_multiple']=_engine_gate_multiple(raw)
        # engine.py still compares cost_multiple against 2.0. Mapping here makes
        # that old comparison equivalent to EXPECTED_COST_MIN without weakening
        # worst-case sizing or the pre-POST safety record.
        plan['cost_multiple']=plan['legacy_engine_cost_multiple']
        plan['expected_cost_min_multiple']=EXPECTED_COST_MIN
        _LAST_PLAN=plan
        return plan

    engine.make_plan=make_plan

    def cycle(self):
        global _LAST_PLAN
        _LAST_PLAN=None
        state=self.store.data if self.store else None
        before_last=state.get('last_bar') if isinstance(state,dict) else None
        bar=(self.market or {}).get('bar') if isinstance(getattr(self,'market',None),dict) else None
        original_emit=self.emit

        def fixed_emit(kind,data):
            if kind=='log' and isinstance(data,str):
                plan=_LAST_PLAN
                rejected=_is_cost_reject(plan)
                current_bar=(self.market or {}).get('bar') if isinstance(getattr(self,'market',None),dict) else None
                if data.startswith('开仓成本过滤：'):
                    data=data.replace('最低2.00倍',f'最低{EXPECTED_COST_MIN:.2f}倍')
                    if rejected:
                        last_logged=getattr(self,'_v136_cost_reject_log_bar',None)
                        if last_logged==current_bar:
                            return
                        self._v136_cost_reject_log_bar=current_bar
                        data += '；未提交OKX开仓请求，本根5m不会写入下单去重，可随实时价格继续重新评估'
                if '本根5m信号已处理；为防重复下单等待下一根K线' in data and rejected:
                    # This was the legacy engine describing a cost rejection as a
                    # submitted/consumed signal. Suppress the misleading message.
                    return
            return original_emit(kind,data)

        self.emit=fixed_emit
        try:
            result=original_cycle(self)
        finally:
            self.emit=original_emit

        if isinstance(state,dict):
            current_bar=(self.market or {}).get('bar') if isinstance(getattr(self,'market',None),dict) else bar
            if _release_rejected_bar(state,before_last,current_bar,_LAST_PLAN):
                self.store.save()
        return result

    engine.Engine.cycle=cycle
    engine.Engine._kaytrade_v136_entry_fix_applied=True


apply()

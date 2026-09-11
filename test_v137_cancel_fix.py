import unittest

import v137_cancel_fix as fix
import v137_strategy_patch as v137


class FakeStore:
    def __init__(self,p):
        self.data={'active':p,'last_close':123.0,'streak':2}
        self.saved=0; self.records=[]
    def save(self): self.saved+=1
    def record(self,event,data): self.records.append((event,data))


class FakeEngine:
    def __init__(self,p):
        self.store=FakeStore(p); self.logs=[]
    def emit(self,kind,data): self.logs.append((kind,data))


class V137CancelFixTests(unittest.TestCase):
    def test_never_filled_requires_all_canceled_zero_fill(self):
        self.assertTrue(fix._never_filled([{'state':'canceled','filled':False,'filled_sz':0}]))
        self.assertFalse(fix._never_filled([{'state':'filled','filled':True,'filled_sz':1}]))
        self.assertFalse(fix._never_filled([{'state':'canceled','filled':False,'filled_sz':.1}]))
        self.assertFalse(fix._never_filled([]))

    def test_unfilled_cancel_does_not_start_cooldown_or_change_streak(self):
        p={'v137':True,'side':'做多','tier_counts':{'1':1,'2':0,'3':0},'legs':[{'state':'canceled','filled':False,'filled_sz':0}]}
        e=FakeEngine(p)
        before=v137._finalize_cycle
        # v137_cancel_fix.apply() has already replaced this function at import time.
        before(e,p)
        self.assertIsNone(e.store.data['active'])
        self.assertEqual(e.store.data['last_close'],123.0)
        self.assertEqual(e.store.data['streak'],2)
        self.assertEqual(e.store.records[0][0],'V1.3.7限价开仓未成交')
        self.assertTrue(any('不触发平仓冷却' in text for kind,text in e.logs if kind=='log'))


if __name__=='__main__':
    unittest.main()

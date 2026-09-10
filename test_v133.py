import tempfile
import time
import unittest
from dataclasses import replace
from engine import Engine, Settings, Halt, make_plan
import test_engine

class V133ExecutionTests(unittest.TestCase):
    def test_fixed_costs_and_reward(self):
        s=Settings().validate()
        self.assertEqual((s.fee_bps,s.taker_fee_bps,s.slippage_bps,s.reward_r),(2.0,5.0,5.0,2.0))
        for changed in (dict(fee_bps=3),dict(taker_fee_bps=4),dict(slippage_bps=4),dict(reward_r=1.5)):
            with self.assertRaises(Halt): replace(s,**changed).validate()

    def test_plan_is_half_at_1r_half_at_2r(self):
        p=make_plan(Settings(), '做多', test_engine.TICK, test_engine.META, 200, 100, 3)
        entry=float(p['px']); risk=entry-float(p['sl'])
        self.assertAlmostEqual(float(p['tp1'])-entry,risk,delta=.2)
        self.assertAlmostEqual(float(p['tp2'])-entry,2*risk,delta=.2)
        self.assertAlmostEqual(float(p['tp1_sz'])+float(p['tp2_sz']),float(p['sz']))
        self.assertTrue(p['breakeven_after_tp1'])

    def _engine(self):
        folder=tempfile.TemporaryDirectory(); x=test_engine.FakeExchange(); e=Engine(x,folder.name); e.connect(); e.arm(Settings())
        e.market={'side':'做多','bar':int(time.time()//300)*300000-300000,'close':60000,'h':{'atr':2000},'m':{'atr':200},'scores':{'做多':{'gate':True,'total':8,'level':'普通信号','items':[]}}}
        e.market_at=time.time(); e.market_monotonic=time.monotonic(); e.cycle(); return folder,x,e,e.store.data['active']

    def test_order_has_two_split_tp_and_cost_price_sl(self):
        folder,x,e,p=self._engine()
        try:
            body=[b for path,b in x.writes if path.endswith('/order')][-1]
            tps=[a for a in body['attachAlgoOrds'] if a.get('tpTriggerPx')]
            sl=[a for a in body['attachAlgoOrds'] if a.get('slTriggerPx')]
            self.assertEqual(len(tps),2); self.assertEqual(len(sl),1)
            self.assertEqual(sl[0]['amendPxOnTriggerType'],'1')
            self.assertAlmostEqual(sum(float(a['sz']) for a in tps),float(body['sz']))
            self.assertNotEqual(tps[0]['tpTriggerPx'],tps[1]['tpTriggerPx'])
            self.assertNotEqual(tps[0]['tpOrdPx'],tps[1]['tpOrdPx'])
        finally: folder.cleanup()

    def test_tp1_requires_breakeven_sl_verification(self):
        folder,x,e,p=self._engine()
        try:
            x.ord={'state':'filled','accFillSz':p['sz']}
            x.pos=[dict(mgnMode='isolated',posSide=p['posSide'],pos=p['tp2_sz'],avgPx=p['px'])]
            opposite='sell'
            x.protections=[
                dict(algoClOrdId=p['tp2_id'],state='live',posSide=p['posSide'],side=opposite,tdMode='isolated',tpTriggerPx=p['tp2'],sz=p['tp2_sz']),
                dict(algoClOrdId=p['sl_id'],state='live',posSide=p['posSide'],side=opposite,tdMode='isolated',slTriggerPx=p['px'],slOrdPx='-1',amendPxOnTriggerType='1')]
            e.cycle()
            self.assertTrue(e.store.data['active']['tp1_done']); self.assertTrue(e.store.data['active']['breakeven_verified'])
        finally: folder.cleanup()

if __name__=='__main__': unittest.main(verbosity=2)

import unittest

import engine
import v137_strategy_patch as v137


class V137StrategyTests(unittest.TestCase):
    def test_fixed_threshold(self):
        engine.Settings(score_threshold=3.5).validate()
        with self.assertRaises(engine.Halt):
            engine.Settings(score_threshold=4.0).validate()
        with self.assertRaises(engine.Halt):
            engine.Settings(score_threshold=1.0).validate()

    def test_tier_boundaries(self):
        self.assertEqual(v137._tier(3.0)[0],0)
        self.assertEqual(v137._tier(3.5)[:2],(1,1.0))
        self.assertEqual(v137._tier(5.0)[:2],(1,1.0))
        self.assertEqual(v137._tier(5.5)[:2],(2,1.5))
        self.assertEqual(v137._tier(7.0)[:2],(2,1.5))
        self.assertEqual(v137._tier(7.5)[:2],(3,2.0))
        self.assertEqual(v137._tier(10.0)[:2],(3,2.0))

    def test_addon_limits_and_no_downgrade(self):
        active={'tier_counts':{'1':1,'2':0,'3':0},'highest_tier':1,'last_entry_bar':100,'legs':[{}]}
        self.assertFalse(v137._allow_entry(active,1,200)[0])
        self.assertTrue(v137._allow_entry(active,2,200)[0])
        active['tier_counts']['2']=1; active['highest_tier']=2; active['last_entry_bar']=200
        self.assertFalse(v137._allow_entry(active,1,300)[0])
        self.assertTrue(v137._allow_entry(active,3,300)[0])
        active['tier_counts']['3']=2; active['highest_tier']=3
        self.assertFalse(v137._allow_entry(active,3,400)[0])

    def test_full_position_two_r_plan(self):
        s=engine.Settings(capital=1000,max_notional=1000,risk_usdt=10,risk_pct=1,daily_loss=100,score_threshold=3.5)
        ticker={'askPx':'100.01','bidPx':'100.00','last':'100.00'}
        meta={'tickSz':'0.01','ctVal':'1','ctMult':'1','minSz':'0.001','lotSz':'0.001'}
        old=v137.v136_runtime._live_equity_for
        v137.v136_runtime._live_equity_for=lambda available:1000.0
        try:
            plan=v137._v137_make_plan(s,'做多',ticker,meta,1.0,1000.0,100.0,1.0)
        finally:
            v137.v136_runtime._live_equity_for=old
        self.assertEqual(float(plan['sl']),99.0)
        self.assertEqual(float(plan['tp']),102.0)
        self.assertEqual(plan['tp'],plan['tp1'])
        self.assertEqual(plan['tp'],plan['tp2'])
        self.assertFalse(plan['breakeven_after_tp1'])
        self.assertEqual(plan['reward_r'],2.0)
        self.assertGreaterEqual(plan['expected_fee_multiple'],v137.EXPECTED_COST_MIN)

    def test_split_payload_becomes_one_full_bracket(self):
        seen={}
        def original(_self,path,body):
            seen['path']=path; seen['body']=body; return [{'ordId':'1'}]
        wrapped=v137._transform_single_bracket(original)
        body={'clOrdId':'abc','attachAlgoOrds':[
            {'attachAlgoClOrdId':'tp1','tpTriggerPx':'101','tpOrdPx':'100.9','tpTriggerPxType':'last','sz':'1'},
            {'attachAlgoClOrdId':'tp2','tpTriggerPx':'102','tpOrdPx':'101.9','tpTriggerPxType':'last','sz':'1'},
            {'attachAlgoClOrdId':'sl','slTriggerPx':'99','slOrdPx':'-1','slTriggerPxType':'last','amendPxOnTriggerType':'1'}]}
        wrapped(object(),'/api/v5/trade/order',body)
        items=seen['body']['attachAlgoOrds']
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['attachAlgoClOrdId'],'tp2')
        self.assertEqual(items[0]['tpTriggerPx'],'102')
        self.assertEqual(items[0]['tpOrdPx'],'-1')
        self.assertEqual(items[0]['slTriggerPx'],'99')
        self.assertEqual(items[0]['slOrdPx'],'-1')
        self.assertNotIn('sz',items[0])
        self.assertNotIn('amendPxOnTriggerType',items[0])


if __name__=='__main__':
    unittest.main()

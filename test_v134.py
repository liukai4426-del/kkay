import unittest
from unittest.mock import patch

import strategy
from engine import Settings, Halt, make_plan
import test_engine


class V134StrategyTests(unittest.TestCase):
    def test_scale_threshold_and_position_tiers(self):
        self.assertEqual(strategy.SCORE_MAX,10.0)
        self.assertEqual(strategy.NORMAL_THRESHOLD,3.5)
        self.assertEqual(Settings().score_threshold,3.5)
        self.assertEqual(strategy._position_multiplier(3.5),1.0)
        self.assertEqual(strategy._position_multiplier(4.5),1.0)
        self.assertEqual(strategy._position_multiplier(5.0),1.5)
        self.assertEqual(strategy._position_multiplier(6.5),1.5)
        self.assertEqual(strategy._position_multiplier(7.0),2.0)
        self.assertEqual(strategy._position_multiplier(10.0),2.0)
        with self.assertRaises(Halt): Settings(score_threshold=3.0).validate()

    def test_trend_penalty_is_minus_one_only_when_clearly_opposite(self):
        down={'ema20':90,'ema50':100,'ema200':110,'up':False,'down':True}
        score,opposite,_=strategy._trend_penalty(down,{'c':80},True)
        self.assertEqual(score,-1.0); self.assertTrue(opposite)
        score,opposite,_=strategy._trend_penalty(down,{'c':80},False)
        self.assertEqual(score,0.0); self.assertFalse(opposite)

    def test_daily_ema_uses_highest_matching_weight_without_stacking(self):
        rows=[]
        for i in range(240):
            c=100+i*.02
            rows.append({'o':c-.2,'h':c+.5,'l':c-.5,'c':c,'v':100,'t':i})
        d=strategy.indicators(rows)
        ema20=strategy.ema([r['c'] for r in rows],20)[-1]
        score,name,detail=strategy._daily_ema_zone(rows,d,ema20,True)
        self.assertEqual(score,3.0)
        self.assertEqual(name,'EMA20')
        self.assertIn('ema',detail)

    def test_setup_and_trigger_caps_match_new_ten_point_budget(self):
        rows=[{'o':100,'h':101,'l':99,'c':100,'v':100} for _ in range(21)]
        rows[-1]={'o':95,'h':102,'l':89,'c':102,'v':140}
        setup,_,_=strategy._setup(rows,{'lower':90,'upper':110,'j':20,'cross_up':True,'cross_down':False},True)
        self.assertEqual(setup,1.5)
        trigger,_=strategy._trigger([{'o':100,'h':101,'l':99,'c':100},{'o':99,'h':103,'l':98,'c':103}],
                                    {'ema20':101,'cross_up':True,'cross_down':False},{'ema20':100},True)
        self.assertEqual(trigger,1.0)

    def test_score_tier_scales_position_but_respects_risk_and_notional_caps(self):
        s=Settings(capital=1000,max_notional=5000,risk_usdt=10,risk_pct=1,daily_loss=30)
        base=make_plan(s, '做多', test_engine.TICK, test_engine.META, 200, 1000, 30, 1.0)
        high=make_plan(s, '做多', test_engine.TICK, test_engine.META, 200, 1000, 30, 2.0)
        self.assertGreater(float(high['sz']),float(base['sz']))
        self.assertLessEqual(high['estimated_loss'],30+1e-9)
        self.assertEqual(high['position_multiplier'],2.0)

    def test_signal_ten_point_budget_and_double_trend_penalty(self):
        rows=[{'o':100,'h':101,'l':99,'c':100,'v':100,'t':i} for i in range(240)]
        ind={'ema5':100,'ema10':100,'ema20':100,'ema50':100,'ema200':100,'up':False,'down':False,'rsi':50,'atr':10,
             'upper':110,'middle':100,'lower':90,'k':50,'d':50,'j':50,'cross_up':False,'cross_down':False}
        structure_ctx={'score':1.5,'score_1h':1.0,'score_15m':.5,'score_4h':1.5,'penalty':0.0,
                       'blocked':False,'warning':False,'front_r':99.0,'favorable_4h':None,
                       'favorable_1h':None,'favorable_15m':None,'front':None,'overlap':False,
                       'zones_4h':[],'zones_1h':[],'zones_15m':[]}
        with patch('strategy.indicators',return_value=ind), \
             patch('strategy._trend_penalty',side_effect=[(-1.0,True,{}),(-1.0,True,{}),(0.0,False,{}),(0.0,False,{})]), \
             patch('strategy._setup',return_value=(1.5,{'boll':.5,'volume_boll':1.0,'kdj':.5,'reversal':.5},1.4)), \
             patch('strategy._trigger',return_value=(1.0,{'ema_reclaim':.5,'kdj':.5,'reversal':.5,'ema_direction':.5})), \
             patch('strategy._structure_context',return_value=structure_ctx), \
             patch('strategy._daily_ema_zone',return_value=(3.0,'EMA20',{})), \
             patch('strategy._rsi_resonance',return_value=True):
            out=strategy.signal(rows,rows,rows,threshold=3.5,four=rows,day=rows)
        long=out['scores']['做多']
        self.assertEqual(long['raw'],8.0)
        self.assertEqual(long['total'],8.0)
        self.assertEqual(long['position_multiplier'],2.0)
        self.assertEqual(sum(x[2] for x in long['items']),10.0)


if __name__=='__main__':
    unittest.main(verbosity=2)

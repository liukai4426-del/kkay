import unittest
from unittest.mock import patch

import strategy
from engine import Settings, Halt


class V132ScoreTests(unittest.TestCase):
    def test_score_scale_and_levels(self):
        self.assertEqual(strategy.SCORE_MAX,10.0)
        self.assertEqual(strategy.NORMAL_THRESHOLD,4.0)
        self.assertEqual(strategy._level(3.5),'未达开仓线')
        self.assertEqual(strategy._level(4.0),'普通信号')
        self.assertEqual(strategy._level(5.5),'较强信号')
        self.assertEqual(strategy._level(7.0),'强信号')
        self.assertEqual(strategy._level(8.5),'高共振信号')

    def test_threshold_accepts_half_steps(self):
        self.assertEqual(Settings().score_threshold,4.0)
        self.assertEqual(Settings(score_threshold=4.5).validate().score_threshold,4.5)
        with self.assertRaises(Halt): Settings(score_threshold=4.25).validate()
        with self.assertRaises(Halt): Settings(score_threshold=3.5).validate()
        with self.assertRaises(Halt): Settings(score_threshold=10.5).validate()

    def test_environment_half_point_weights_and_countertrend(self):
        aligned={'ema20':110,'ema50':100,'ema200':90,'up':True,'down':False}
        score,strong,_=strategy._environment(aligned,{'c':120},True)
        self.assertEqual(score,1.5); self.assertFalse(strong)
        opposite={'ema20':90,'ema50':100,'ema200':110,'up':False,'down':True}
        score,strong,_=strategy._environment(opposite,{'c':80},True)
        self.assertEqual(score,-1.5); self.assertTrue(strong)

    def test_rsi_only_scores_on_5m_15m_resonance(self):
        self.assertTrue(strategy._rsi_resonance({'rsi':29},{'rsi':28},True))
        self.assertFalse(strategy._rsi_resonance({'rsi':29},{'rsi':40},True))
        self.assertTrue(strategy._rsi_resonance({'rsi':72},{'rsi':75},False))
        self.assertFalse(strategy._rsi_resonance({'rsi':72},{'rsi':60},False))

    def test_setup_detailed_weights_cap_at_two(self):
        rows=[{'o':100,'h':101,'l':99,'c':100,'v':100} for _ in range(21)]
        rows[-2]={'o':100,'h':101,'l':99,'c':100,'v':100}
        rows[-1]={'o':95,'h':102,'l':89,'c':102,'v':140}
        current={'lower':90,'upper':110,'j':20,'cross_up':True,'cross_down':False}
        total,parts,ratio=strategy._setup(rows,current,True)
        self.assertEqual(parts['boll'],.5)
        self.assertEqual(parts['volume_boll'],1.0)
        self.assertEqual(parts['kdj'],.5)
        self.assertEqual(parts['reversal'],.5)
        self.assertEqual(total,2.0)
        self.assertGreaterEqual(ratio,1.3)

    def test_trigger_detailed_weights_cap_at_one_point_five(self):
        rows=[{'o':100,'h':101,'l':99,'c':100},{'o':99,'h':103,'l':98,'c':103}]
        current={'ema20':101,'cross_up':True,'cross_down':False}
        previous={'ema20':100}
        total,parts=strategy._trigger(rows,current,previous,True)
        self.assertEqual(sum(parts.values()),2.0)
        self.assertEqual(total,1.5)

    def test_4h_structure_proximity_adds_one_point_five(self):
        hour=[{'c':100}]; quarter=[{'c':100}]; four=[{'c':100}]
        h={'atr':100}; m={'atr':50}; q={'atr':200}
        four_support={'kind':'support','price':100,'tests':3,'age':2,'recency':.9,'strong':True}
        with patch('strategy._zones',side_effect=[[],[],[four_support]]):
            ctx=strategy._structure_context(hour,quarter,h,m,True,1.0,four,q)
        self.assertEqual(ctx['score_4h'],1.5)
        self.assertEqual(ctx['score'],0.0)
        self.assertFalse(ctx['blocked'])

    def test_1h_and_15m_structure_are_independent(self):
        hour=[{'c':100}]; quarter=[{'c':100}]
        h={'atr':100}; m={'atr':50}
        hs={'kind':'support','price':100,'tests':2,'age':2,'recency':.9,'strong':False}
        ms={'kind':'support','price':100,'tests':2,'age':2,'recency':.9,'strong':False}
        with patch('strategy._zones',side_effect=[[hs],[ms]]):
            ctx=strategy._structure_context(hour,quarter,h,m,True,1.0)
        self.assertEqual(ctx['score_1h'],1.0)
        self.assertEqual(ctx['score_15m'],.5)
        self.assertEqual(ctx['score'],1.5)

    def test_ema_dynamic_support_scores_once(self):
        hour=[{'c':100}]
        h={'atr':10,'ema20':100,'ema50':99}
        ph={'ema20':99.5,'ema50':98.5}
        score,name=strategy._ema_dynamic_support(hour,h,ph,100.5,True)
        self.assertEqual(score,.5)
        self.assertIn(name,('EMA20','EMA50'))

    def test_countertrend_has_no_special_eleven_point_gate(self):
        rows=[{'o':100,'h':101,'l':99,'c':100,'v':100,'t':i} for i in range(210)]
        ind={'ema20':100,'ema50':100,'ema200':100,'up':False,'down':False,'rsi':50,'atr':10,
             'upper':110,'middle':100,'lower':90,'k':50,'d':50,'j':50,'cross_up':False,'cross_down':False}
        structure_ctx={'score':1.5,'score_1h':1.0,'score_15m':.5,'score_4h':1.5,'penalty':0.0,
                       'blocked':False,'warning':False,'front_r':99.0,'favorable_4h':None,
                       'favorable_1h':None,'favorable_15m':None,'front':None,'overlap':False,
                       'zones_4h':[],'zones_1h':[],'zones_15m':[]}
        env_calls=[(-1.5,True,{}),(1.5,False,{})]
        with patch('strategy.indicators',return_value=ind), \
             patch('strategy._environment',side_effect=env_calls), \
             patch('strategy._setup',return_value=(2.0,{'boll':.5,'volume_boll':1.0,'kdj':.5,'reversal':.5},1.4)), \
             patch('strategy._trigger',return_value=(1.5,{'ema_reclaim':.5,'kdj':.5,'reversal':.5,'ema_direction':.5})), \
             patch('strategy._structure_context',return_value=structure_ctx), \
             patch('strategy._ema_dynamic_support',return_value=(.5,'EMA20')), \
             patch('strategy._rsi_resonance',return_value=True):
            out=strategy.signal(rows,rows,rows,threshold=4.0,four=rows)
        long=out['scores']['做多']
        self.assertTrue(long['confirmations']['strong_opposite'])
        self.assertEqual(long['required'],4.0)
        self.assertGreaterEqual(long['total'],4.0)
        self.assertTrue(long['eligible'])


if __name__=='__main__':
    unittest.main(verbosity=2)

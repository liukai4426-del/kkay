import copy
import unittest

import v147_strategy_patch as v147


class V147StrategyTests(unittest.TestCase):
    def setUp(self):
        self.hour=[{'c':120.0}]
        self.four=[{'c':130.0}]
        self.result={
            'h':{'ema200':100.0,'ema20':110.0,'ema50':105.0,'up':True,'down':False},
            'q':{'ema200':100.0,'ema20':115.0,'ema50':108.0,'up':True,'down':False},
        }
        self.row={
            'layers':{
                'daily_ema':3.0,
                'structure_15m':0.5,
                'setup':0.5,
                'trigger':1.0,
                'trend_5m':2.0,
                'trend_15m':2.0,
                'rsi_penalty':0.0,
                'structure_penalty_1h':0.0,
                'structure_penalty_4h':0.0,
                'trend_penalty_1h':0.0,
                'trend_penalty_4h':0.0,
                'front_penalty':-1.0,
            },
            'confirmations':{
                'daily_ema':'EMA20',
                'daily_ema_detail':{'distance_atr':0.1},
                'trigger':{'ema_reclaim':0.0,'kdj':0.5,'reversal':0.0,'ema_direction':0.5},
                'trend_5m':{'macd':True,'boll':True},
            },
            'structure':{'front_r':1.5,'penalty':-1.0,'warning':True,'blocked':False},
        }

    def rewrite(self,row=None,hour=None,four=None,result=None):
        target=copy.deepcopy(row or self.row)
        return v147._rewrite_row(target,'做多',hour or self.hour,four or self.four,result or copy.deepcopy(self.result))

    def test_ema_direction_does_not_activate_trigger(self):
        row=copy.deepcopy(self.row)
        row['confirmations']['trigger']={'ema_reclaim':0.0,'kdj':0.0,'reversal':0.0,'ema_direction':0.5}
        rewritten=self.rewrite(row=row)
        self.assertEqual(rewritten['layers']['trigger'],0.0)
        self.assertFalse(rewritten['gate'])
        self.assertIn('有效Trigger缺失',rewritten['reason'])

    def test_valid_trigger_and_5m_macd_are_required(self):
        rewritten=self.rewrite()
        self.assertEqual(rewritten['layers']['trigger'],0.5)
        self.assertTrue(rewritten['confirmations']['5m_macd_required'])
        self.assertTrue(rewritten['gate'])
        self.assertEqual(rewritten['layers']['trend_1h'],2.0)
        self.assertEqual(rewritten['layers']['trend_4h'],1.0)
        self.assertEqual(rewritten['total'],8.5)

        row=copy.deepcopy(self.row)
        row['confirmations']['trend_5m']['macd']=False
        blocked=self.rewrite(row=row)
        self.assertFalse(blocked['gate'])
        self.assertIn('5m MACD未与开仓方向确认',blocked['reason'])

    def test_forward_space_below_1_3r_is_hard_block(self):
        row=copy.deepcopy(self.row)
        row['structure']['front_r']=1.29
        blocked=self.rewrite(row=row)
        self.assertFalse(blocked['gate'])
        self.assertTrue(blocked['structure']['blocked'])
        self.assertEqual(blocked['structure']['penalty'],0.0)
        self.assertFalse(blocked['structure']['warning'])

        row['structure']['front_r']=1.30
        allowed=self.rewrite(row=row)
        self.assertTrue(allowed['gate'])
        self.assertFalse(allowed['structure']['blocked'])
        self.assertEqual(allowed['structure']['penalty'],0.0)
        self.assertFalse(allowed['structure']['warning'])

    def test_1h_countertrend_is_hard_block(self):
        result=copy.deepcopy(self.result)
        result['h']={'ema200':100.0,'ema20':90.0,'ema50':95.0,'up':False,'down':True}
        hour=[{'c':80.0}]
        blocked=self.rewrite(hour=hour,result=result)
        self.assertFalse(blocked['gate'])
        self.assertTrue(blocked['confirmations']['1H_countertrend'])
        self.assertEqual(blocked['layers']['trend_1h'],0.0)
        self.assertIn('1H逆趋势',blocked['reason'])

    def test_4h_aligned_plus_one_and_opposite_keeps_minus_one(self):
        aligned=self.rewrite()
        self.assertEqual(aligned['layers']['trend_4h'],1.0)

        result=copy.deepcopy(self.result)
        result['q']={'ema200':100.0,'ema20':90.0,'ema50':95.0,'up':False,'down':True}
        four=[{'c':80.0}]
        opposite=self.rewrite(four=four,result=result)
        self.assertEqual(opposite['layers']['trend_4h'],-1.0)
        self.assertTrue(opposite['confirmations']['4H_countertrend'])
        self.assertTrue(opposite['gate'])

    def test_all_1d_scoring_metadata_is_removed(self):
        rewritten=self.rewrite()
        self.assertNotIn('daily_ema',rewritten['layers'])
        self.assertNotIn('daily_ema',rewritten['confirmations'])
        self.assertNotIn('daily_ema_detail',rewritten['confirmations'])
        labels=[item[0] for item in rewritten['items']]
        self.assertFalse(any('1D' in label for label in labels))


if __name__=='__main__':
    unittest.main()

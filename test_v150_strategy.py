import unittest

import engine
import v137_strategy_patch as v137
import v140_score_dialog_patch as v140
import v147_strategy_patch as v147
import v150_strategy_patch as v150


class V150StrategyTests(unittest.TestCase):
    def test_fixed_threshold_is_six_everywhere(self):
        self.assertEqual(v150.V150_THRESHOLD,6.0)
        self.assertEqual(v137.V137_THRESHOLD,6.0)
        self.assertEqual(v140.V140_THRESHOLD,6.0)
        self.assertEqual(v147.V147_THRESHOLD,6.0)
        self.assertEqual(engine.Settings().validate().score_threshold,6.0)
        with self.assertRaises(engine.Halt):
            engine.Settings(score_threshold=4.0).validate()

    def test_only_one_opening_signal_tier_exists(self):
        self.assertEqual(v150._tier(5.5),(0,0.0,'未达开仓线'))
        for score in (6.0,6.5,7.5,8.0,9.5,10.0):
            self.assertEqual(v150._tier(score),(1,1.0,'开仓信号'))
        self.assertEqual(v150._tier_limit(1),1)
        self.assertEqual(v150._tier_limit(2),0)
        self.assertEqual(v150._tier_limit(3),0)

    def test_existing_position_cannot_add_any_tier(self):
        bar=123456
        active={'highest_tier':1,'tier_counts':{'1':1,'2':0,'3':0},
                'last_entry_bar':bar-1,'legs':[{'state':'filled'}]}
        allowed,_=v137._allow_entry(active,1,bar)
        self.assertFalse(allowed)
        for tier in (2,3):
            allowed,_=v137._allow_entry(active,tier,bar)
            self.assertFalse(allowed)

    def test_settings_only_size_by_opening_signal(self):
        s=engine.Settings(first_signal_notional=500.0,second_signal_notional=9999.0,third_signal_notional=9999.0)
        self.assertEqual(s.validate().first_signal_notional,500.0)
        self.assertEqual(v150._tier_limit(2),0)
        self.assertEqual(v150._tier_limit(3),0)

    def test_run_log_time_is_restored(self):
        clock,text=v150._timestamp_parts('2026-09-13 04:31:22 自动交易启动')
        self.assertEqual(clock,'04:31:22')
        self.assertEqual(text,'自动交易启动')
        clock,text=v150._timestamp_parts('普通日志')
        self.assertEqual(clock,'')
        self.assertEqual(text,'普通日志')


if __name__=='__main__':
    unittest.main()

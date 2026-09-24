import unittest

import engine
import v137_strategy_patch as v137
import v141_dialog_signal_positions_patch as v141


class V141DialogSignalPositionTests(unittest.TestCase):
    def settings(self,**updates):
        values=dict(
            capital=1000,
            max_notional=450,
            max_initial_notional=100,
            first_signal_notional=100,
            second_signal_notional=150,
            third_signal_notional=200,
            leverage=5,
            risk_usdt=10,
            risk_pct=1,
            daily_loss=30,
            consecutive_losses=3,
            cooldown_minutes=30,
            stop_atr=1.0,
            reward_r=2.0,
            score_threshold=4.0,
            fee_bps=2.0,
            taker_fee_bps=5.0,
            slippage_bps=5.0,
        )
        values.update(updates)
        return v141.SettingsV141(**values)

    def test_three_signal_caps_validate(self):
        s=self.settings().validate()
        self.assertEqual(v141._signal_cap(s,1),100)
        self.assertEqual(v141._signal_cap(s,2),150)
        self.assertEqual(v141._signal_cap(s,3),200)

    def test_each_tier_is_limited_to_one_entry(self):
        self.assertEqual(v141._tier_limit(1),1)
        self.assertEqual(v141._tier_limit(2),1)
        self.assertEqual(v141._tier_limit(3),1)
        self.assertEqual(v137._tier_limit(3),1)
        active={'tier_counts':{'1':1,'2':1,'3':1},'highest_tier':3,'last_entry_bar':100,'legs':[{},{}]}
        ok,_=v137._allow_entry(active,3,101)
        self.assertFalse(ok)

    def test_complete_cycle_cap_must_fit_capital_times_leverage(self):
        self.settings(capital=100,leverage=5,first_signal_notional=100,second_signal_notional=150,third_signal_notional=250).validate()
        with self.assertRaises(engine.Halt):
            self.settings(capital=100,leverage=5,first_signal_notional=200,second_signal_notional=200,third_signal_notional=101).validate()

    def test_fixed_threshold_remains_four(self):
        self.settings(score_threshold=4.0).validate()
        with self.assertRaises(engine.Halt):
            self.settings(score_threshold=4.5).validate()

    def test_invalid_tier_cap_is_rejected(self):
        with self.assertRaises(engine.Halt):
            v141._signal_cap(self.settings(),0)


if __name__=='__main__':
    unittest.main()

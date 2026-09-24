import unittest

import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v139_position_patch as v139


class V139PositionTests(unittest.TestCase):
    def settings(self,**updates):
        values=dict(
            capital=1000,
            max_notional=1000,
            max_initial_notional=300,
            leverage=5,
            risk_usdt=10,
            risk_pct=1,
            daily_loss=30,
            consecutive_losses=3,
            cooldown_minutes=30,
            stop_atr=1.0,
            reward_r=2.0,
            score_threshold=4.5,
            fee_bps=2.0,
            taker_fee_bps=5.0,
            slippage_bps=5.0,
        )
        values.update(updates)
        return v139.SettingsV139(**values)

    def test_fixed_threshold_is_45(self):
        self.assertEqual(v138.V138_THRESHOLD,4.5)
        self.assertIs(self.settings().validate().__class__,v139.SettingsV139)
        with self.assertRaises(engine.Halt):
            self.settings(score_threshold=3.5).validate()
        with self.assertRaises(engine.Halt):
            self.settings(score_threshold=5.0).validate()

    def test_tier_boundaries_keep_higher_tiers_unchanged(self):
        self.assertEqual(v137._tier(4.0)[0],0)
        self.assertEqual(v137._tier(4.5)[:2],(1,1.0))
        self.assertEqual(v137._tier(5.0)[:2],(1,1.0))
        self.assertEqual(v137._tier(5.5)[:2],(2,1.5))
        self.assertEqual(v137._tier(7.0)[:2],(2,1.5))
        self.assertEqual(v137._tier(7.5)[:2],(3,2.0))
        self.assertEqual(v137._tier(10.0)[:2],(3,2.0))

    def test_initial_and_total_notional_are_separate(self):
        s=self.settings(max_initial_notional=250,max_notional=900).validate()
        self.assertEqual(v139._initial_cap(s),250)
        self.assertEqual(s.max_notional,900)

    def test_initial_cap_cannot_exceed_total_cap(self):
        with self.assertRaises(engine.Halt):
            self.settings(max_initial_notional=1001,max_notional=1000).validate()

    def test_total_cap_still_obeys_capital_times_leverage(self):
        with self.assertRaises(engine.Halt):
            self.settings(capital=100,max_notional=501,max_initial_notional=100,leverage=5).validate()

    def test_leverage_still_supports_1_to_50(self):
        self.settings(leverage=1).validate()
        self.settings(leverage=50).validate()
        with self.assertRaises(engine.Halt):
            self.settings(leverage=51).validate()

    def test_translation_updates_v138_threshold_text(self):
        text='V1.3.8 固定开仓门槛3.5；3.5–5.0一级最多1次'
        translated=v139._translate_v139(text)
        self.assertIn('V1.3.9',translated)
        self.assertIn('固定开仓门槛4.5',translated)
        self.assertIn('4.5–5.0一级最多1次',translated)


if __name__=='__main__':
    unittest.main()

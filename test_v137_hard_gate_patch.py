import inspect
import unittest

import engine
import v137_strategy_patch as v137
import v137_hard_gate_patch as patch


class V137HardGateTests(unittest.TestCase):
    def test_fixed_threshold_and_tiers(self):
        self.assertEqual(patch.V137_THRESHOLD, 4.0)
        self.assertEqual(v137.V137_THRESHOLD, 4.0)
        self.assertEqual(v137._tier(3.5)[0], 0)
        self.assertEqual(v137._tier(4.0)[0], 1)
        self.assertEqual(v137._tier(5.0)[0], 1)
        self.assertEqual(v137._tier(5.5)[0], 2)
        self.assertEqual(v137._tier(7.0)[0], 2)
        self.assertEqual(v137._tier(7.5)[0], 3)
        self.assertEqual(v137._tier_limit(1), 1)
        self.assertEqual(v137._tier_limit(2), 1)
        self.assertEqual(v137._tier_limit(3), 1)

    def test_no_lower_tier_backfill_and_one_per_1m(self):
        active = {'tier_counts': {'1': 0, '2': 1, '3': 0}, 'highest_tier': 2, 'last_entry_bar': 123, 'legs': [{}]}
        self.assertFalse(v137._allow_entry(active, 1, 124)[0])
        self.assertFalse(v137._allow_entry(active, 2, 124)[0])
        self.assertFalse(v137._allow_entry(active, 3, 123)[0])
        self.assertTrue(v137._allow_entry(active, 3, 124)[0])

    def test_layered_hard_gates_are_wired(self):
        src = inspect.getsource(patch.strict_signal)
        self.assertIn('trigger_valid and macd5 and not trend1_opposite and space_ok', src)
        self.assertIn('front_r >= FRONT_MIN_R', src)
        self.assertIn("trend1_score = 2.0 if trend1_aligned else 0.0", src)
        self.assertIn("trend4_score = 1.0 if trend4_aligned else (-1.0 if trend4_opposite else 0.0)", src)
        self.assertNotIn('daily_ema', src)
        self.assertNotIn('1D', src)

    def test_1m_trigger_excludes_ema_direction(self):
        src = inspect.getsource(patch._one_minute_trigger)
        self.assertIn('ema_reclaim or kdj or reversal', src)
        self.assertIn("'ema_direction': False", src)

    def test_execution_uses_1m_and_60_second_limit(self):
        refresh = inspect.getsource(patch._refresh_market)
        initial = inspect.getsource(patch._submit_initial)
        cycle = inspect.getsource(patch._cycle)
        prepare = inspect.getsource(patch._prepare_order)
        self.assertIn("self.x.candles('1m')", refresh)
        self.assertNotIn("candles('1Dutc')", refresh)
        self.assertIn('expires=time.time()+60', initial)
        self.assertIn("state['last_bar'] = market['bar']", initial)
        self.assertIn("state.get('last_bar') == market.get('bar')", cycle)
        self.assertIn(".3 * float(market['h']['atr'])", prepare)

    def test_settings_accept_only_four_point_threshold(self):
        s = engine.Settings(score_threshold=4.0)
        s.validate()
        with self.assertRaises(engine.Halt):
            engine.Settings(score_threshold=3.5).validate()


if __name__ == '__main__':
    unittest.main()

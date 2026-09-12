import inspect
import unittest

import engine
import v138_hard_gate_final as final
import v138_strategy_patch as v138
import v138_user_update as update


class V138FinalHardGateTests(unittest.TestCase):
    def test_two_signal_bands_preserved(self):
        self.assertEqual(update._two_tier(5.5)[0], 0)
        self.assertEqual(update._two_tier(6.0), (1, 1.0, '开仓信号'))
        self.assertEqual(update._two_tier(7.5), (1, 1.0, '开仓信号'))
        self.assertEqual(update._two_tier(8.0), (2, 2.0, '强信号'))
        self.assertEqual(update._two_tier(10.0), (2, 2.0, '强信号'))
        self.assertEqual(v138.V138_THRESHOLD, 6.0)

    def test_final_runtime_uses_strict_signal(self):
        self.assertIs(v138.v138_signal, final.strict_signal)
        self.assertIs(engine.Engine.refresh_market, final._refresh_market)

    def test_final_source_has_required_hard_gates(self):
        source = inspect.getsource(final.strict_signal)
        self.assertIn('trigger_valid and macd5 and not trend1_opposite and space_ok', source)
        self.assertIn('front_r >= FRONT_MIN_R', source)
        self.assertIn("trend1_score = 2.0 if trend1_aligned else 0.0", source)
        self.assertIn("trend4_score = 1.0 if trend4_aligned else (-1.0 if trend4_opposite else 0.0)", source)
        self.assertNotIn('daily_ema', source)

    def test_market_refresh_does_not_fetch_daily(self):
        source = inspect.getsource(final._refresh_market)
        self.assertNotIn("candles('1Dutc')", source)
        for tf in ("candles('4H')", "candles('1H')", "candles('15m')", "candles('5m')", "candles('1m')"):
            self.assertIn(tf, source)

    def test_confirmed_risk_rules_still_active(self):
        self.assertEqual(update.LOSS_PAUSE_SECONDS, 6 * 60 * 60)
        self.assertEqual(update._entry_notional_cap(1000, 1, False), 1000)
        self.assertEqual(update._entry_notional_cap(1000, 2, False), 2000)


if __name__ == '__main__':
    unittest.main()

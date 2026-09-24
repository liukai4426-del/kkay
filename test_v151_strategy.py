import unittest

import engine
import v150_strategy_patch as v150
import v151_strategy_patch as v151


class V151StrategyTests(unittest.TestCase):
    def _row(self, total=6.0, adverse4=0.0, front=None):
        return {
            'total': total,
            'layers': {
                'trigger': 1.0,
                'structure_penalty_4h': adverse4,
            },
            'confirmations': {
                'valid_1m_trigger': True,
                '5m_macd_required': True,
                '1H_trend_state': 'aligned',
            },
            'structure': {'front': front},
            'items': [
                ('4H 不利支撑/阻力结构', adverse4, 0),
                ('前方结构空间：<1.3R硬过滤', 0.0, 0),
            ],
        }

    def test_v150_threshold_and_single_signal_are_preserved(self):
        self.assertEqual(v150.V150_THRESHOLD, 6.0)
        self.assertEqual(v150._tier(5.5), (0, 0.0, '未达开仓线'))
        self.assertEqual(v150._tier(6.0), (1, 1.0, '开仓信号'))
        self.assertEqual(v150._tier(10.0), (1, 1.0, '开仓信号'))

    def test_front_space_uses_1h_atr(self):
        quarter = [{'c': 100.0}]
        result = {'h': {'atr': 10.0}}
        row = self._row(front={'price': 112.0})
        self.assertAlmostEqual(v151._front_r_1h(row, result, quarter, 1.0), 1.2)
        row = self._row(front={'price': 115.0})
        self.assertAlmostEqual(v151._front_r_1h(row, result, quarter, 1.0), 1.5)

    def test_4h_adverse_structure_is_hard_gate(self):
        row = self._row(total=7.0, adverse4=-1.5)
        result = {'h': {'atr': 100.0}}
        v151._rewrite_row_v151(row, result, [{'c': 1000.0}], 1.0)
        self.assertFalse(row['gate'])
        self.assertFalse(row['eligible'])
        self.assertTrue(row['confirmations']['4H_adverse_structure_block'])
        self.assertIn('4H', row['reason'])

    def test_clean_signal_stays_one_x_opening_signal(self):
        row = self._row(total=8.5, adverse4=0.0)
        result = {'h': {'atr': 100.0}}
        v151._rewrite_row_v151(row, result, [{'c': 1000.0}], 1.0)
        self.assertTrue(row['gate'])
        self.assertTrue(row['eligible'])
        self.assertEqual(row['signal_tier'], 1)
        self.assertEqual(row['position_multiplier'], 1.0)
        self.assertEqual(row['level'], '开仓信号 · 1×仓位')

    def test_market_proxy_uses_1h_atr_without_mutating_15m(self):
        market = {'h': {'atr': 250.0}, 'm': {'atr': 80.0}}
        proxy, atr = v151._market_with_1h_atr(market)
        self.assertEqual(atr, 250.0)
        self.assertEqual(proxy['m']['atr'], 250.0)
        self.assertEqual(market['m']['atr'], 80.0)
        with self.assertRaises(engine.Halt):
            v151._market_with_1h_atr({'h': {'atr': 0.0}, 'm': {'atr': 80.0}})

    def test_loss_pause_is_exactly_one_hour(self):
        self.assertEqual(v151.LOSS_PAUSE_SECONDS, 3600)
        self.assertEqual(v151._pause_remaining({'streak_pause_until': 4600.0}, 1000.0), 3600.0)
        self.assertEqual(v151._pause_remaining({'streak_pause_until': 900.0}, 1000.0), 0.0)


if __name__ == '__main__':
    unittest.main()

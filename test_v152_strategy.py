import math
import unittest

import v151_strategy_patch as v151
import v152_backtest_core as bt
import v152_model as model
import v152_strategy_patch as runtime


def candles(count=230, start=100.0, step=0.2, ms=60_000):
    rows = []
    for i in range(count):
        close = start + i * step
        open_ = close - step * 0.4
        rows.append({
            't': i * ms,
            'o': float(open_), 'h': float(max(open_, close) + 0.4),
            'l': float(min(open_, close) - 0.4), 'c': float(close),
            'v': float(100 + i % 7),
        })
    return rows


class V152ModelTests(unittest.TestCase):
    def test_final_constants(self):
        self.assertEqual(model.VERSION, '1.5.2')
        self.assertEqual(model.THRESHOLD, 6.0)
        self.assertEqual(model.FRONT_MIN_R, 1.5)
        self.assertEqual(model.OPPORTUNITY_TTL_MS, 30 * 60 * 1000)
        self.assertEqual(model.OVERLAP_ATR_TOL, 0.25)
        self.assertEqual(model.STOP_BUFFER_ATR, 0.10)
        self.assertEqual(model.COST_MAX_R, 0.30)
        self.assertEqual(v151.LOSS_PAUSE_SECONDS, 3600)

    def test_strict_direction_filter(self):
        up_h = candles(start=100, step=1.0, ms=3_600_000)
        up_m = candles(start=100, step=0.3, ms=900_000)
        self.assertEqual(model.trend_direction(up_h, up_m), '做多')

        down_h = candles(start=1000, step=-1.0, ms=3_600_000)
        down_m = candles(start=1000, step=-0.3, ms=900_000)
        self.assertEqual(model.trend_direction(down_h, down_m), '做空')

        # 1H long but 15m EMA order short -> must wait, not score around it.
        self.assertEqual(model.trend_direction(up_h, down_m), '观望')

    def _opp(self, front_price=None):
        return {
            'id': 'pb-test', 'side': '做多', 'zone_low': 90.0, 'zone_high': 110.0,
            'atr15': 4.0, 'extreme': 95.0,
            'front_structure': None if front_price is None else {'price': front_price},
        }

    def _score(self):
        return {'confirmations': {'required': {
            'direction_pullback': True, 'entry_near_zone': True,
            'five_confirm': True, 'one_trigger': True,
        }}}

    def _plan(self, entry=100.0, stop_distance=10.0, worst_cost=2.0):
        return {'px': str(entry), 'stop_distance': stop_distance, 'btc': 1.0,
                'worst_roundtrip_cost': worst_cost}

    def test_front_space_15r_and_unknown_are_distinct(self):
        ok, diag, blockers = model.execution_checks(self._plan(), self._opp(115.0), self._score())
        self.assertTrue(ok)
        self.assertAlmostEqual(diag['front_r'], 1.5)
        self.assertEqual(diag['front_space_status'], '充足')

        ok, diag, blockers = model.execution_checks(self._plan(), self._opp(114.0), self._score())
        self.assertFalse(ok)
        self.assertAlmostEqual(diag['front_r'], 1.4)
        self.assertTrue(any('1.5R' in x for x in blockers))

        ok, diag, blockers = model.execution_checks(self._plan(), self._opp(None), self._score())
        self.assertTrue(ok)
        self.assertTrue(math.isinf(diag['front_r']))
        self.assertEqual(diag['front_space_status'], '空间未知')
        self.assertFalse(any('前方空间' in x for x in blockers))

    def test_actual_limit_stop_buffer_and_cost_are_hard_gates(self):
        ok, _, blockers = model.execution_checks(self._plan(entry=112.0), self._opp(None), self._score())
        self.assertFalse(ok)
        self.assertTrue(any('偏离固定回调区' in x for x in blockers))

        # SL=96 is above long pullback extreme 95 minus 0.4 buffer => insufficient.
        ok, _, blockers = model.execution_checks(self._plan(entry=100.0, stop_distance=4.0), self._opp(None), self._score())
        self.assertFalse(ok)
        self.assertTrue(any('结构缓冲' in x for x in blockers))

        ok, diag, blockers = model.execution_checks(self._plan(entry=100.0, stop_distance=10.0, worst_cost=3.1), self._opp(None), self._score())
        self.assertFalse(ok)
        self.assertGreater(diag['cost_r'], 0.30)
        self.assertTrue(any('0.30R' in x for x in blockers))

    def test_rearm_requires_price_to_leave_frozen_zone(self):
        rearm = {'side': '做多', 'zone_low': 90.0, 'zone_high': 110.0}
        self.assertFalse(runtime._rearm_ready(rearm, [{'c': 100.0}]))
        self.assertTrue(runtime._rearm_ready(rearm, [{'c': 111.0}]))
        self.assertTrue(runtime._rearm_ready(rearm, [{'c': 89.0}]))

    def test_tp_sl_payload_is_one_full_position_market_bracket(self):
        body = {
            'attachAlgoOrds': [
                {'attachAlgoClOrdId': 't1', 'tpTriggerPx': '110', 'tpOrdPx': '109.9', 'sz': '1'},
                {'attachAlgoClOrdId': 't2', 'tpTriggerPx': '120', 'tpOrdPx': '119.9', 'sz': '1'},
                {'attachAlgoClOrdId': 'sl', 'slTriggerPx': '90', 'slOrdPx': '89.9'},
            ]
        }
        out = runtime._market_exit_payload('/api/v5/trade/order', body)
        self.assertEqual(len(out['attachAlgoOrds']), 1)
        bracket = out['attachAlgoOrds'][0]
        self.assertEqual(bracket['tpTriggerPx'], '120')
        self.assertEqual(bracket['tpOrdPx'], '-1')
        self.assertEqual(bracket['slTriggerPx'], '90')
        self.assertEqual(bracket['slOrdPx'], '-1')


class V152BacktestTests(unittest.TestCase):
    def test_limit_fill_bar_cannot_use_prefill_extremes_for_exit(self):
        pending = bt.PendingEntry('pb1', '做多', 100.0, 90.0, 120.0, 1.0, 7.0,
                                  submitted_bar_t=60_000, expires_ms=600_000)
        fill_bar = {'t': 120_000, 'o': 105.0, 'h': 125.0, 'l': 85.0, 'c': 101.0}
        pos = bt.fill_entry(pending, fill_bar)
        self.assertIsNotNone(pos)
        # Same OHLC contains both TP and SL but their timing relative to limit fill is unknowable.
        self.assertIsNone(bt.exit_on_bar(pos, fill_bar))
        next_bar = {'t': 180_000, 'o': 100.0, 'h': 105.0, 'l': 89.0, 'c': 92.0}
        result = bt.exit_on_bar(pos, next_bar)
        self.assertEqual(result['reason'], 'SL')

    def test_three_net_losses_pause_exactly_one_hour_then_reset(self):
        state = bt.RiskState()
        state.close_trade(1_000, -1.0)
        state.close_trade(2_000, -0.1)
        until = state.close_trade(3_000, -2.0)
        self.assertEqual(state.consecutive_losses, 3)
        self.assertEqual(until, 3_000 + 3_600_000)
        self.assertIn('暂停1小时', state.entry_block_reason(3_000 + 3_599_999))
        state.refresh(3_000 + 3_600_000)
        self.assertEqual(state.consecutive_losses, 0)
        self.assertEqual(state.loss_pause_until_ms, 0)
        self.assertEqual(state.max_consecutive_losses, 3)

    def test_candle_and_funding_integrity_checks(self):
        rows = [{'t': 0}, {'t': 60_000}, {'t': 120_000}]
        self.assertTrue(bt.validate_candle_continuity(rows, 60_000, '1m'))
        with self.assertRaises(ValueError):
            bt.validate_candle_continuity([{'t': 0}, {'t': 120_000}], 60_000, '1m')

        funding = [{'t': 0}, {'t': 8 * 3_600_000}, {'t': 16 * 3_600_000}]
        self.assertTrue(bt.validate_funding_coverage(funding, 0, 16 * 3_600_000))
        with self.assertRaises(ValueError):
            bt.validate_funding_coverage([{'t': 1}, {'t': 20 * 3_600_000}], 0, 20 * 3_600_000)


if __name__ == '__main__':
    unittest.main()

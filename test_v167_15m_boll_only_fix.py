import unittest

import v153_model as signal_core
import v163_model as old5
import v165_model as model
import v167_15m_boll_only_fix as fix


def candles(step_ms, count=220):
    rows = []
    for i in range(count):
        close = 100.0 if i % 2 == 0 else 101.0
        rows.append({
            't': i * step_ms,
            'o': close,
            'h': close + 0.2,
            'l': close - 0.2,
            'c': close,
            'v': 100.0,
        })
    return rows


class V16715mBollOnlyTests(unittest.TestCase):
    def test_5m_upper_touch_cannot_create_short_when_15m_did_not_touch(self):
        quarter = candles(15 * 60 * 1000)
        five = candles(5 * 60 * 1000)
        five[-1]['h'] = 1000.0
        self.assertIsNotNone(old5.boll_entry_signal(five, '做空'))
        self.assertIsNone(fix._boll_entry_signal_15m_only(quarter, five, '做空'))

    def test_5m_lower_touch_cannot_create_long_when_15m_did_not_touch(self):
        quarter = candles(15 * 60 * 1000)
        five = candles(5 * 60 * 1000)
        five[-1]['l'] = 1.0
        self.assertIsNotNone(old5.boll_entry_signal(five, '做多'))
        self.assertIsNone(fix._boll_entry_signal_15m_only(quarter, five, '做多'))

    def test_short_requires_15m_upper_and_long_requires_15m_lower(self):
        quarter = candles(15 * 60 * 1000)
        five = candles(5 * 60 * 1000)
        quarter[-1]['h'] = 1000.0
        short = fix._boll_entry_signal_15m_only(quarter, five, '做空')
        self.assertIsNotNone(short)
        self.assertEqual(short['path'], 'upper_band')
        self.assertEqual(short['boll_timeframe'], '15m')
        self.assertGreaterEqual(short['boll15_signal_high'], short['upper'])

        quarter = candles(15 * 60 * 1000)
        quarter[-1]['l'] = 1.0
        long = fix._boll_entry_signal_15m_only(quarter, five, '做多')
        self.assertIsNotNone(long)
        self.assertEqual(long['path'], 'lower_band')
        self.assertEqual(long['boll_timeframe'], '15m')
        self.assertLessEqual(long['boll15_signal_low'], long['lower'])

    def test_strict_evidence_rejects_wrong_side_path(self):
        good_short = {
            'side': '做空', 'signal_path': 'upper_band', 'boll_timeframe': '15m',
            'trigger_detail': {'boll15_signal_high': 110, 'boll15_signal_low': 95, 'boll15_upper': 105, 'boll15_lower': 90},
        }
        self.assertTrue(fix._valid_15m_evidence(good_short))
        bad = dict(good_short)
        bad['signal_path'] = 'lower_band'
        self.assertFalse(fix._valid_15m_evidence(bad))

    def test_same_15m_bar_is_kept_after_window_to_block_recreation(self):
        quarter = candles(15 * 60 * 1000)
        bar_t = quarter[-1]['t']
        opp = {
            'side': '做空', 'signal_path': 'upper_band', 'boll_timeframe': '15m',
            'signal_bar_t': bar_t,
            'trigger_detail': {'boll15_signal_high': 110, 'boll15_signal_low': 95, 'boll15_upper': 105, 'boll15_lower': 90},
        }
        self.assertIs(fix._prepare_incoming(opp, quarter), opp)
        newer = list(quarter) + [{**quarter[-1], 't': bar_t + 15 * 60 * 1000}]
        self.assertIsNone(fix._prepare_incoming(opp, newer))

    def test_active_signal_source_is_pinned_to_15m_adapter(self):
        fix._pin_15m_only()
        self.assertIs(signal_core.boll_entry_signal, fix._signal_adapter_15m_only)
        self.assertEqual(model.BOLL_TRIGGER_TIMEFRAME, '15m')
        self.assertFalse(model.FIVE_MINUTE_BOLL_ENABLED)

    def test_runtime_copy_has_no_5m_boll_signal_semantic(self):
        text = fix._rewrite_runtime_text_15m_only('上一5m BOLL信号周期已经结束；允许等待新的5m BOLL信号')
        self.assertNotIn('5m BOLL', text)
        self.assertIn('15m BOLL', text)
        self.assertIn('5m执行窗口', text)


if __name__ == '__main__':
    unittest.main()

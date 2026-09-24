import unittest

import v165_model as model
import v164_model as v164
import v167_15m_boll_only_fix as b1671
import v168_update_patch as v168


def trend_rows(step_ms, direction, count=240):
    rows = []
    for i in range(count):
        base = 200.0 + (i * 0.5 if direction == 'up' else -i * 0.5)
        rows.append({
            't': i * step_ms,
            'o': base - 0.1,
            'h': base + 0.4,
            'l': base - 0.4,
            'c': base,
            'v': 100.0,
        })
    return rows


def neutral_rows(step_ms, count=240):
    rows = []
    for i in range(count):
        base = 100.0 + (0.2 if i % 2 else -0.2)
        rows.append({'t': i * step_ms, 'o': base, 'h': base + 0.3, 'l': base - 0.3, 'c': base, 'v': 100.0})
    return rows


def valid_short_opp(signal_close_ms=10_000_000):
    return {
        'id': 'v168-test-short',
        'side': '做空',
        'signal_path': 'upper_band',
        'boll_timeframe': '15m',
        'signal_close_ms': signal_close_ms,
        'created_ms': signal_close_ms,
        'signal_bar_t': signal_close_ms - 15 * 60 * 1000,
        'boll15_signal_high': 110.0,
        'boll15_signal_low': 95.0,
        'trigger_detail': {
            'boll_timeframe': '15m',
            'boll15_signal_high': 110.0,
            'boll15_signal_low': 95.0,
            'boll15_upper': 105.0,
            'boll15_lower': 90.0,
        },
    }


def base_row(opp=None):
    return {
        'total': 0.0,
        'raw': 0.0,
        'gate': True if opp else False,
        'eligible': False,
        'required': 6.0,
        'position_multiplier': 0.0,
        'level': '等待',
        'reason': '评分 0/10',
        'items': [
            ('15m顺势回调区共振', 2.0 if opp else 0.0, 2.0),
            ('入场位置接近15m回调区', 1.0 if opp else 0.0, 1.0),
            ('15m回调区 × 水平结构共振', 1.0 if opp else 0.0, 1.0),
            ('15m BOLL外轨信号（有效至下一根5m收盘）', 2.5 if opp else 0.0, 2.5),
            ('5m MACD动能改善（+1）', 1.0 if opp else 0.0, 1.0),
            ('4H同向（外轨 Hard Gate）', 0.0, 1.0),
            ('15m EMA50顺交易方向推进', 1.0 if opp else 0.0, 1.0),
        ],
        'layers': ({
            'direction_pullback': 2.0,
            'entry_near_zone': 1.0,
            'structure_overlap': 1.0,
            'boll_entry': 2.5,
            'macd_improving': 1.0,
            'trend4h': -1.0,
            'ema50_15m_move': 1.0,
        } if opp else {}),
        'confirmations': {
            'required': {'boll_entry_signal': bool(opp)},
            'blockers': [],
        },
        'structure': {},
        'opportunity': dict(opp) if opp else None,
    }


class V168Tests(unittest.TestCase):
    def setUp(self):
        v168._pin_v168_runtime()

    def test_identity_and_constants(self):
        self.assertEqual(v168.VERSION, '1.6.8')
        self.assertEqual(v168.BUILD, '1680')
        self.assertEqual(model.VERSION, '1.6.8')
        self.assertEqual(model.BUILD, '1680')
        self.assertEqual(model.ENTRY_WINDOW_MS, 900000)
        self.assertEqual(v164.ENTRY_WINDOW_MS, 900000)
        self.assertEqual(model.BOLL_OUTER_SCORE, 2.0)
        self.assertEqual(v164.BOLL_OUTER_SCORE, 2.0)
        self.assertEqual(model.BOLL_TRIGGER_TIMEFRAME, '15m')
        self.assertFalse(model.FIVE_MINUTE_BOLL_ENABLED)
        self.assertIs(model.boll_entry_signal, b1671._signal_adapter_15m_only)

    def test_signal_window_is_full_15_minutes(self):
        opp = valid_short_opp(1_000_000)
        self.assertTrue(v168._signal_window_15m(opp, 1_000_000)[0])
        self.assertTrue(v168._signal_window_15m(opp, 1_899_999)[0])
        opened, _age, remaining, end = v168._signal_window_15m(opp, 1_900_000)
        self.assertFalse(opened)
        self.assertEqual(remaining, 0)
        self.assertEqual(end, 1_900_000)
        norm = v168._normalize_opportunity_15m(opp)
        self.assertEqual(norm['expires_ms'], 1_900_000)
        self.assertTrue(norm['signal_valid_until_next_15m_close'])
        self.assertNotIn('signal_valid_until_next_5m_close', norm)

    def test_1h_ema9_26_trend_score(self):
        up = trend_rows(60 * 60 * 1000, 'up')
        down = trend_rows(60 * 60 * 1000, 'down')
        e9, e26 = v168._ema9_26(up)
        self.assertGreater(e9, e26)
        self.assertTrue(v168._ema_trend_ok('做多', e9, e26))
        self.assertFalse(v168._ema_trend_ok('做空', e9, e26))
        e9, e26 = v168._ema9_26(down)
        self.assertLess(e9, e26)
        self.assertTrue(v168._ema_trend_ok('做空', e9, e26))

    def test_realtime_4h_and_ema_scores_exist_without_boll(self):
        hour = trend_rows(60 * 60 * 1000, 'down')
        four = trend_rows(4 * 60 * 60 * 1000, 'down')
        result = {
            'direction': '做空',
            'side': '观望',
            'scores': {'做多': base_row(), '做空': base_row()},
        }
        out = v168._decorate_scores(result, None, hour, four, 10_000_000)
        short = out['scores']['做空']
        self.assertEqual(short['layers']['trend4h'], 1.0)
        self.assertEqual(short['layers']['ema9_26_1h'], 1.0)
        self.assertEqual(short['total'], 2.0)
        self.assertFalse(short['gate'])
        self.assertFalse(short['eligible'])
        labels = {x[0]: x for x in short['items']}
        self.assertEqual(labels['4H同向（Hard Gate，+1）'][1], 1.0)
        self.assertEqual(labels['1H EMA9/26趋势'][1], 1.0)
        self.assertEqual(labels['15m BOLL外轨信号（有效至下一根15m收盘）'][1], 0.0)

    def test_boll_is_two_points_and_4h_aligned_is_mandatory(self):
        hour = trend_rows(60 * 60 * 1000, 'down')
        four = trend_rows(4 * 60 * 60 * 1000, 'down')
        opp = valid_short_opp(10_000_000)
        result = {
            'direction': '做空',
            'side': '观望',
            'scores': {'做多': base_row(), '做空': base_row(opp)},
        }
        out = v168._decorate_scores(result, opp, hour, four, 10_100_000)
        short = out['scores']['做空']
        self.assertEqual(short['layers']['boll_entry'], 2.0)
        self.assertEqual(short['layers']['trend4h'], 1.0)
        self.assertEqual(short['layers']['ema9_26_1h'], 1.0)
        self.assertEqual(short['total'], 10.0)
        self.assertTrue(short['confirmations']['required']['outer_4h_aligned'])
        self.assertTrue(short['confirmations']['required']['signal_window_15m'])
        self.assertTrue(short['eligible'])
        self.assertEqual(out['side'], '做空')

    def test_non_aligned_4h_blocks_even_with_high_score(self):
        hour = trend_rows(60 * 60 * 1000, 'down')
        four = neutral_rows(4 * 60 * 60 * 1000)
        opp = valid_short_opp(10_000_000)
        result = {
            'direction': '做空',
            'side': '观望',
            'scores': {'做多': base_row(), '做空': base_row(opp)},
        }
        out = v168._decorate_scores(result, opp, hour, four, 10_100_000)
        short = out['scores']['做空']
        self.assertEqual(short['layers']['trend4h'], 0.0)
        self.assertFalse(short['confirmations']['required']['outer_4h_aligned'])
        self.assertFalse(short['gate'])
        self.assertFalse(short['eligible'])
        self.assertIn('4H非同向', ' '.join(short['confirmations']['blockers']))

    def test_runtime_text_and_status_ui_are_v168(self):
        text = v168._rewrite_runtime_text_v168(
            'V1.6.7 Build1671：15m BOLL外轨2.5分；15m BOLL触发后的5分钟执行窗口'
        )
        self.assertIn('1.6.8', text)
        self.assertIn('Build1680', text)
        self.assertIn('外轨2分', text)
        self.assertIn('下一根15m收盘', text)
        self.assertNotIn('5分钟执行窗口', text)
        items = dict(v168.ui161._STATUS_ITEMS)
        self.assertEqual(items['signal_window'], '15m BOLL信号有效')
        self.assertEqual(items['outer_4h'], '4H同向 Hard Gate')
        self.assertEqual(items['ema1h'], '1H EMA9/26趋势')


if __name__ == '__main__':
    unittest.main()

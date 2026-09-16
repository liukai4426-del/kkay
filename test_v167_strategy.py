from types import SimpleNamespace
import unittest

import app
import v161_ui_status_patch as ui161
import v165_model as model
import v167_strategy_patch as v167


class V167StrategyTests(unittest.TestCase):
    def _row(self, *, adverse=False, raw=6.5, kdj=0.5, macd=1.0):
        # Keep all non-MACD/KDJ gates green.  raw is recomputed by the patch;
        # these layers intentionally model a real outer-band candidate.
        layers = {
            'direction_pullback': 1.0,
            'entry_near_zone': 1.0,
            'structure_overlap': 0.0,
            'boll_entry': 2.5,
            'macd_improving': macd,
            'trend4h': 1.0,
            'ema50_15m_move': 0.0,
            'kdj5_signal': kdj,
        }
        return {
            'raw': raw,
            'total': raw,
            'gate': False,
            'eligible': False,
            'required': 6.0,
            'position_multiplier': 0.0,
            'level': '等待外轨RSI/MACD共振',
            'reason': f'评分 {raw:g}/10｜禁止：V1.6.3：外轨开仓要求5m MACD柱连续向交易方向改善',
            'layers': layers,
            'items': [
                ('15m顺势回调区共振', 1.0, 2.0),
                ('入场位置接近15m回调区', 1.0, 1.0),
                ('15m BOLL外轨信号', 2.5, 2.5),
                ('5m 外轨MACD连续改善（必须）', macd, 1.0),
                ('4H同向（外轨 Hard Gate）', 1.0, 1.0),
                ('5m KDJ动能交叉', kdj, 0.5),
            ],
            'confirmations': {
                'required': {
                    'boll_entry_signal': True,
                    'signal_window_5m': True,
                    'volume_below_1_2x': True,
                    'outer_4h_aligned': True,
                    'outer_rsi_30_70': True,
                    'outer_macd_improving': False,
                },
                'blockers': ['V1.6.3：外轨开仓要求5m MACD柱连续向交易方向改善'],
                '5m_macd_adverse': adverse,
                'rsi5': 50.0,
                '4H_trend_state': 'aligned',
            },
            'opportunity': {'signal_path': 'lower_band'},
        }

    def test_macd_hard_gate_is_adverse_only(self):
        self.assertTrue(v167._macd_allowed({'confirmations': {'5m_macd_adverse': False}}))
        self.assertFalse(v167._macd_allowed({'confirmations': {'5m_macd_adverse': True}}))
        # Mere non-improvement is not inferred to be adverse.
        self.assertTrue(v167._macd_allowed({'confirmations': {}}))

    def test_kdj_score_removed_but_macd_momentum_score_retained(self):
        row = self._row(adverse=False)
        v167._apply_v167_row(row, opportunity=row['opportunity'])
        self.assertNotIn('kdj5_signal', row['layers'])
        self.assertFalse(any('KDJ' in str(item[0]) for item in row['items']))
        macd = next(item for item in row['items'] if 'MACD' in str(item[0]))
        self.assertEqual(macd[1], 1.0)
        self.assertEqual(macd[2], 1.0)
        self.assertIn('动能', macd[0])
        self.assertEqual(row['total'], 5.5)
        self.assertFalse(row['eligible'])

    def test_non_improving_non_adverse_can_pass_hard_gate(self):
        row = self._row(adverse=False, macd=0.0, kdj=0.5)
        # Independent EMA confirmation keeps this candidate above the 6.0 line
        # even though MACD is neutral/non-improving and KDJ no longer scores.
        row['layers']['ema50_15m_move'] = 1.0
        v167._apply_v167_row(row, opportunity=row['opportunity'])
        self.assertEqual(row['total'], 6.5)
        self.assertTrue(row['confirmations']['required']['outer_macd_non_adverse'])
        self.assertNotIn('outer_macd_improving', row['confirmations']['required'])
        self.assertTrue(row['gate'])
        self.assertTrue(row['eligible'])
        self.assertEqual(row['position_multiplier'], 1.0)

    def test_explicit_macd_adverse_still_blocks(self):
        row = self._row(adverse=True)
        row['layers']['ema50_15m_move'] = 1.0
        v167._apply_v167_row(row, opportunity=row['opportunity'])
        self.assertFalse(row['confirmations']['required']['outer_macd_non_adverse'])
        self.assertFalse(row['gate'])
        self.assertFalse(row['eligible'])
        self.assertTrue(any('MACD明确逆向' in str(x) for x in row['confirmations']['blockers']))

    def test_production_window_outer_only_and_threshold_preserved(self):
        self.assertEqual(model.THRESHOLD, 6.0)
        self.assertEqual(model.ENTRY_WINDOW_MS, 300000)
        self.assertTrue(model.TIME_WINDOW_ENABLED)
        self.assertEqual(model.BOLL_TRIGGER_TIMEFRAME, '15m')
        self.assertEqual(tuple(model.OUTER_PATHS), ('lower_band', 'upper_band'))

    def test_ui_status_and_authorization_are_synchronized(self):
        labels = dict(ui161._STATUS_ITEMS)
        self.assertIn('rsi_gate', labels)
        self.assertEqual(labels['macd_clean'], 'MACD非逆向')
        self.assertNotIn('middle_macd', labels)
        owner = SimpleNamespace(engine=SimpleNamespace(x=SimpleNamespace(demo=True)))
        settings = SimpleNamespace(
            leverage=5, risk_usdt=2.0, capital=100.0, risk_pct=1.0, max_notional=50.0,
        )
        text = v167._authorization_text_v167(owner, settings)
        self.assertIn('MACD动能改善的+1评分保留', text)
        self.assertIn('KDJ +0.5评分', text)
        self.assertIn('KDJ不作为Hard Gate', text)
        self.assertIn('中轨不作为开仓触发', text)

    def test_patch_flags_and_visible_version(self):
        self.assertTrue(getattr(model, '_kaytrade_v167_strategy_applied', False))
        self.assertTrue(getattr(app.App, '_kaytrade_v167_strategy_applied', False))
        self.assertEqual(v167.VERSION, '1.6.7')
        self.assertEqual(v167.BUILD, '1670')


if __name__ == '__main__':
    unittest.main()

import re
import unittest

import v153_model as signal_core
import v165_model as model
import v167_15m_boll_only_fix as b1671
import v168_update_patch as v168
import v168_build1681_patch as b1681


class V168Build1681Tests(unittest.TestCase):
    def test_strategy_semantics_unchanged(self):
        self.assertEqual(b1681.VERSION, '1.6.8')
        self.assertEqual(b1681.BUILD, '1681')
        self.assertEqual(v168.VERSION, '1.6.8')
        self.assertEqual(v168.BUILD, '1681')
        self.assertEqual(model.VERSION, '1.6.8')
        self.assertEqual(model.BUILD, '1681')
        self.assertEqual(model.THRESHOLD, 6.0)
        self.assertEqual(model.ENTRY_WINDOW_MS, 900000)
        self.assertEqual(model.BOLL_OUTER_SCORE, 2.0)
        self.assertEqual(model.BOLL_TRIGGER_TIMEFRAME, '15m')
        self.assertFalse(model.FIVE_MINUTE_BOLL_ENABLED)
        self.assertIs(signal_core.boll_entry_signal, b1671._signal_adapter_15m_only)

    def test_exact_legacy_log_is_normalized(self):
        raw = (
            'V1.5.4未开仓：BOLL信号执行窗口·第5个1m；计划市价参考过远；'
            '1H ATR止损仍在5m回调极值结构缓冲内侧；V1.6.2要求4H同向：'
            '5m BOLL中轨/外轨在4H中性或逆向时禁止开仓；'
            'V1.6.6 Volume Hard Gate：5m信号K量能 1.89× ≥ 1.20×，禁止开仓'
        )
        out = b1681._sanitize_runtime_text(raw)
        self.assertNotIn('V1.5.4', out)
        self.assertNotIn('V1.6.2', out)
        self.assertNotIn('V1.6.6', out)
        # A literal substring test is wrong here because "15m BOLL" contains
        # the characters "5m BOLL". Reject only a standalone 5m token.
        self.assertIsNone(re.search(r'(?<!1)5m BOLL', out))
        self.assertNotIn('中轨', out)
        self.assertNotIn('计划市价参考', out)
        self.assertNotIn('第5个1m', out)
        self.assertIn('V1.6.8', out)
        self.assertIn('15m BOLL外轨', out)
        self.assertIn('4H Hard Gate', out)
        self.assertIn('计划限价', out)
        # Legitimate current 5m confirmations must remain 5m.
        self.assertIn('5m信号K量能 1.89×', out)
        self.assertIn('5m回调极值', out)

    def test_algo_untrusted_state_is_clear_sync_notice(self):
        raw = (
            '只读查询暂时失败 /ws/v5/business:orders-algo：'
            'Algo实时状态暂未完成可信同步；仅跳过本周期新开仓，自动交易保持开启，'
            '后台继续REST+WebSocket重同步；下一周期自动重新读取最新状态'
        )
        out = b1681._sanitize_runtime_text(raw)
        self.assertTrue(out.startswith('Algo状态同步中：'))
        self.assertNotIn('只读查询暂时失败', out)
        self.assertIn('仅跳过本周期新开仓', out)
        self.assertIn('自动交易保持开启', out)
        self.assertIn('REST+WebSocket重同步', out)
        self.assertIn('Final Entry Guard', out)

    def test_real_read_failure_is_not_hidden(self):
        raw = '只读查询暂时失败 /api/v5/account/balance：HTTPS连接超时'
        out = b1681._sanitize_runtime_text(raw)
        self.assertIn('只读查询暂时失败', out)
        self.assertIn('/api/v5/account/balance', out)
        self.assertIn('HTTPS连接超时', out)

    def test_current_5m_confirmation_words_are_preserved(self):
        raw = '5m RSI30-70；5m MACD明确逆向禁止；5m Volume 1.10× < 1.20×'
        out = b1681._sanitize_runtime_text(raw)
        self.assertEqual(out, raw)


if __name__ == '__main__':
    unittest.main()

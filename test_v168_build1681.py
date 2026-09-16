import unittest
from unittest.mock import patch

import app
import v153_model as signal_core
import v165_model as model
import v165_update_patch as runtime
import v167_15m_boll_only_fix as b1671
import v168_build1681_patch as b1681


class V168Build1681Tests(unittest.TestCase):
    def test_identity_and_strategy_are_unchanged(self):
        self.assertEqual(b1681.VERSION, '1.6.8')
        self.assertEqual(b1681.BUILD, '1681')
        self.assertEqual(model.VERSION, '1.6.8')
        self.assertEqual(model.BUILD, '1681')
        self.assertEqual(model.THRESHOLD, 6.0)
        self.assertEqual(model.ENTRY_WINDOW_MS, 900000)
        self.assertEqual(model.BOLL_OUTER_SCORE, 2.0)
        self.assertEqual(model.BOLL_TRIGGER_TIMEFRAME, '15m')
        self.assertFalse(model.FIVE_MINUTE_BOLL_ENABLED)
        self.assertIs(model.boll_entry_signal, b1671._signal_adapter_15m_only)
        self.assertTrue(getattr(model, '_kaytrade_v168_build1681_applied', False))
        self.assertTrue(getattr(app.App, '_kaytrade_v168_build1681_applied', False))

    def test_exact_live_legacy_log_is_fully_normalized(self):
        legacy = (
            'V1.5.4未开仓；BOLL信号执行窗口 · 第5个1m｜计划市价参考 100000；'
            'V1.6.2要求4H同向：5m BOLL中轨/外轨在4H中性或逆向时禁止开仓；'
            'V1.6.6 Volume Hard Gate：5m信号K量能 1.89× ≥ 1.20×，禁止开仓'
        )
        out = b1681._rewrite_runtime_text_v1681(legacy)
        self.assertIn('V1.6.8', out)
        self.assertIn('15m BOLL外轨', out)
        self.assertIn('第5分钟', out)
        self.assertIn('计划限价', out)
        self.assertIn('5m信号K量能 1.89×', out)
        self.assertNotIn('V1.5.4', out)
        self.assertNotIn('V1.6.2', out)
        self.assertNotIn('V1.6.6', out)
        self.assertNotIn('5m BOLL', out)
        self.assertNotIn('中轨', out)
        self.assertNotIn('市价参考', out)
        self.assertNotIn('第5个1m', out)

    def test_algo_untrusted_copy_is_sync_state_not_query_failure(self):
        raw = (
            '只读查询暂时失败 /ws/v5/business:orders-algo：'
            'Algo实时状态暂未完成可信同步；仅跳过本周期新开仓，自动交易保持开启，'
            '后台继续REST+WebSocket重同步'
        )
        out = b1681._rewrite_runtime_text_v1681(raw)
        self.assertIn('Algo状态同步中', out)
        self.assertIn('自动交易保持开启', out)
        self.assertIn('Final Entry Guard', out)
        self.assertNotIn('只读查询暂时失败', out)

    def test_app_emit_downgrades_expected_algo_sync_alarm_and_exposes_detail(self):
        calls = []
        original = b1681._PREVIOUS_APP_EMIT
        dummy = type('Dummy', (), {})()
        dummy.engine = type('Engine', (), {'x': object()})()

        def capture(_self, kind, data):
            calls.append((kind, data))

        b1681._PREVIOUS_APP_EMIT = capture
        try:
            with patch.object(
                b1681.a167,
                '_algo_state_status',
                return_value={
                    'trusted': False,
                    'ever_ready': True,
                    'ws_connected': False,
                    'last_error': 'WebSocketTimeoutException: reconnecting',
                },
            ):
                b1681._app_emit_v1681(
                    dummy,
                    'alarm',
                    '只读查询暂时失败 /ws/v5/business:orders-algo：Algo实时状态暂未完成可信同步',
                )
        finally:
            b1681._PREVIOUS_APP_EMIT = original

        self.assertEqual(calls[0][0], 'log')
        self.assertIn('Algo状态同步中', calls[0][1])
        self.assertIn('WebSocketTimeoutException', calls[0][1])
        self.assertNotIn('只读查询暂时失败', calls[0][1])

    def test_recursive_market_payload_sanitizer_catches_reason_and_blockers(self):
        payload = {
            'why': 'V1.5.4未开仓：5m BOLL中轨/外轨',
            'scores': {
                '做多': {
                    'reason': 'BOLL信号执行窗口 · 第3个1m；计划市价参考 1',
                    'confirmations': {
                        'blockers': ['V1.6.2要求4H同向：5m BOLL中轨/外轨在4H中性或逆向时禁止开仓']
                    },
                }
            },
        }
        b1681._sanitize_payload(payload)
        text = str(payload)
        self.assertNotIn('V1.5.4', text)
        self.assertNotIn('V1.6.2', text)
        self.assertNotIn('5m BOLL', text)
        self.assertNotIn('中轨', text)
        self.assertNotIn('市价参考', text)
        self.assertIn('15m BOLL外轨', text)
        self.assertIn('计划限价', text)

    def test_runtime_facade_points_to_build1681_rewriters(self):
        self.assertIs(runtime._rewrite_runtime_text, b1681._rewrite_runtime_text_v1681)
        self.assertIs(runtime._pre_submit_guard, b1681._pre_submit_guard_v1681)
        self.assertIs(model.evaluate, b1681._evaluate_v1681)
        self.assertIs(model.execution_checks, b1681._execution_checks_v1681)
        self.assertIs(signal_core.boll_entry_signal, b1671._signal_adapter_15m_only)


if __name__ == '__main__':
    unittest.main()

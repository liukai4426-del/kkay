import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app
import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v153_model as v153
import v154_model as model
import v154_limit_log_fix as limit_fix
import v154_record_card_boll_fix as final_fix


class V154ExecutionTests(unittest.TestCase):
    def test_boll_opportunity_has_no_wall_clock_expiry(self):
        self.assertEqual(model.VERSION, '1.5.4')
        self.assertEqual(model.THRESHOLD, 6.0)
        self.assertEqual(model.ENTRY_WINDOW_MS, 0)
        self.assertFalse(model.TIME_WINDOW_ENABLED)
        self.assertEqual(v153.ENTRY_WINDOW_MS, 0)
        opp={'signal_close_ms':1_000_000,'expires_ms':0}
        self.assertEqual(model._window_state(999_999,opp)[0],False)
        self.assertEqual(model._window_state(1_000_000,opp)[:2],(True,0))
        self.assertTrue(model._window_state(1_060_000,opp)[0])
        self.assertTrue(model._window_state(9_000_000,opp)[0])

    def test_limit_plan_uses_maker_expected_entry_cost(self):
        settings=SimpleNamespace(
            stop_atr=1.0, slippage_bps=5.0, taker_fee_bps=5.0, fee_bps=2.0,
            capital=10_000.0, risk_usdt=100.0, risk_pct=1.0, max_notional=5_000.0,
            leverage=5.0, reward_r=2.0, consecutive_losses=3, daily_loss=500.0,
        )
        settings.validate=lambda:settings
        ticker={'askPx':'100.04','bidPx':'100.00'}
        meta={'tickSz':'0.01','ctVal':'0.01','ctMult':'1','minSz':'0.01','lotSz':'0.01'}
        with patch.object(limit_fix.v136_runtime,'_live_equity_for',return_value=10_000.0), \
             patch.object(limit_fix.v136_entry_fix,'_engine_gate_multiple',return_value=1.0):
            plan=limit_fix._v154_limit_make_plan(settings,'做多',ticker,meta,10.0,10_000.0,500.0,1.0)
        self.assertEqual(float(plan['px']),100.0)
        self.assertEqual(plan['entry_order_type'],'limit')
        self.assertFalse(plan['market_entry'])
        self.assertEqual(plan['entry_fee_budget_bps'],2.0)
        self.assertEqual(plan['entry_slippage_budget_bps'],0.0)
        self.assertGreater(plan['worst_roundtrip_cost'],plan['expected_roundtrip_cost'])

    def test_runtime_final_submit_is_limit_without_boll_minute_gate(self):
        self.assertIs(v138._submit_initial,final_fix._submit_initial_limit_persistent)
        source=inspect.getsource(final_fix._submit_initial_limit_persistent)
        self.assertIn('"ordType": "limit"',source)
        self.assertIn('"px": plan["px"]',source)
        self.assertNotIn('_inside_first_minute',source)
        self.assertNotIn('_window_timing',source)
        self.assertIn('submitted_at + 60.0',source)
        self.assertIn('boll_time_window_enabled=False',source)
        self.assertIn('禁止重复提交',source)
        self.assertTrue(engine.Engine._kaytrade_v154_record_boll_fix_applied)

    def test_score_items_are_marked_required_or_auxiliary(self):
        row={'items':[
            ('15m正确方向回调区共振（辅助）',2.0,2.0),
            ('5m BOLL中轨+RSI / 外轨触发（必须）',2.0,2.0),
            ('5m MACD柱连续两根改善',1.0,1.0),
        ]}
        model._tag_score_items(row)
        labels=[x[0] for x in row['items']]
        self.assertIn('【辅助评分】',labels[0])
        self.assertIn('【开仓必要】',labels[1])
        self.assertIn('【辅助评分】',labels[2])
        self.assertNotIn('（辅助）',labels[0])
        self.assertNotIn('（必须）',labels[1])

    def test_live_wrappers_use_v154_model_and_limit_plan(self):
        import v154_runtime_patch as runtime
        self.assertIs(runtime.v152_runtime.model,model)
        self.assertIs(runtime.v153_runtime.model,model)
        self.assertIs(runtime.v153_bt.model,model)
        self.assertEqual(runtime.v153_runtime.VERSION,'1.5.4')
        self.assertEqual(runtime.v152_runtime.V152_VERSION,'1.5.4')
        self.assertIs(v137._v137_make_plan,limit_fix._v154_limit_make_plan)
        self.assertIs(app.App.render_logs, __import__('v146_ui_log_polish_patch')._render_logs)


if __name__=='__main__':
    unittest.main()

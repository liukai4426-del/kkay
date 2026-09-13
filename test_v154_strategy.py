import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v153_model as v153
import v154_model as model
import v154_runtime_patch as runtime


class V154ExecutionTests(unittest.TestCase):
    def test_one_minute_window_is_exact(self):
        self.assertEqual(model.VERSION, '1.5.4')
        self.assertEqual(model.THRESHOLD, 6.0)
        self.assertEqual(model.ENTRY_WINDOW_MS, 60_000)
        self.assertEqual(v153.ENTRY_WINDOW_MS, 60_000)
        opp={'signal_close_ms':1_000_000,'expires_ms':1_060_000}
        self.assertEqual(model._window_state(1_000_000,opp)[:2],(True,1))
        self.assertTrue(model._window_state(1_059_999,opp)[0])
        self.assertFalse(model._window_state(1_060_000,opp)[0])

    def test_market_plan_uses_taker_entry_and_market_side_price(self):
        settings=SimpleNamespace(
            stop_atr=1.0, slippage_bps=5.0, taker_fee_bps=5.0, fee_bps=2.0,
            capital=10_000.0, risk_usdt=100.0, risk_pct=1.0, max_notional=5_000.0,
            leverage=5.0, reward_r=2.0, consecutive_losses=3, daily_loss=500.0,
        )
        settings.validate=lambda:settings
        ticker={'askPx':'100.10','bidPx':'100.00'}
        meta={'tickSz':'0.01','ctVal':'0.01','ctMult':'1','minSz':'0.01','lotSz':'0.01'}
        with patch.object(runtime.v136_runtime,'_live_equity_for',return_value=10_000.0), \
             patch.object(runtime.v136_entry_fix,'_engine_gate_multiple',return_value=1.0):
            plan=runtime._v154_make_plan(settings,'做多',ticker,meta,10.0,10_000.0,500.0,1.0)
        self.assertEqual(float(plan['px']),100.10)
        self.assertEqual(plan['entry_order_type'],'market')
        self.assertTrue(plan['market_entry'])
        self.assertEqual(plan['entry_fee_budget_bps'],5.0)
        self.assertEqual(plan['entry_slippage_budget_bps'],5.0)
        self.assertGreater(plan['worst_roundtrip_cost'],plan['expected_roundtrip_cost'])

    def test_runtime_replaces_initial_submit_with_market_path(self):
        self.assertIs(v138._submit_initial,runtime._submit_initial_market)
        source=inspect.getsource(runtime._submit_initial_market)
        self.assertIn('"ordType": "market"',source)
        self.assertNotIn('"ordType": "limit"',source)
        self.assertIn('_inside_first_minute',source)
        self.assertIn('禁止重复提交',source)
        self.assertTrue(getattr(runtime.engine.Engine,'_kaytrade_v154_applied',False))

    def test_live_wrappers_use_v154_model(self):
        self.assertIs(runtime.v152_runtime.model,model)
        self.assertIs(runtime.v153_runtime.model,model)
        self.assertIs(runtime.v153_bt.model,model)
        self.assertEqual(runtime.v153_runtime.VERSION,'1.5.4')
        self.assertEqual(runtime.v152_runtime.V152_VERSION,'1.5.4')
        self.assertIs(v137._v137_make_plan,runtime._v154_make_plan)


if __name__=='__main__':
    unittest.main()

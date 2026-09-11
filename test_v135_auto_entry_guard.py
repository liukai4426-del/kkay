"""Regression tests for V1.3.5 auto-entry skip/stop/fault classification."""
import tempfile
import time
import unittest
from unittest.mock import patch

from v135_auto_entry_guard import apply
apply()

from engine import Engine, Settings, Halt, Store
from exchange import APIError, NetworkError
from test_engine import FakeExchange, TICK
import app


class V135AutoEntryGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.x = FakeExchange()
        self.events = []

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self, arm=True):
        e = Engine(self.x, self.tmp.name, lambda k, d: self.events.append((k, d)))
        e.connect()
        if arm:
            e.arm(Settings())
            e.startup_buffer_until = 0.0
        return e

    def market(self, e, side='做多', score=8):
        bar = int(e.market_now() // 300) * 300000 - 300000
        e.market = {
            'side': side, 'bar': bar, 'close': 60000,
            'h': {'atr': 2000}, 'm': {'atr': 200},
            'scores': {side: {'gate': True, 'total': score,
                              'position_multiplier': 2.0 if score >= 7 else 1.0,
                              'level': '测试信号'}},
        }
        e.market_at = time.time()
        e.market_monotonic = time.monotonic()
        return bar

    def test_empty_manual_flatten_is_noop_not_fault(self):
        e = self.engine()
        self.assertIsNone(e.store.data['active'])
        self.assertFalse(e.flatten())
        self.assertEqual(e.store.data['halt'], '')
        self.assertTrue(any(k == 'log' and '无需平仓' in str(v) for k, v in self.events))

    def test_ui_flatten_submits_one_action_without_queued_stop(self):
        ui = app.App.__new__(app.App)
        ui.engine = type('Stub', (), {'enabled': True})()
        submitted = []
        ui.update_trade_button = lambda: None
        ui.submit = lambda kind, data=None: submitted.append((kind, data))
        # If App.flatten regresses to self.stop(), fail immediately.
        ui.stop = lambda: self.fail('flatten must not enqueue a separate stop action')
        with patch.object(app.messagebox, 'askyesno', return_value=True):
            ui.flatten()
        self.assertFalse(ui.engine.enabled)
        self.assertEqual(submitted, [('flatten', None)])

    def test_packaged_entry_loads_complete_fault_classifier(self):
        from pathlib import Path
        desktop = Path('desktop.py').read_text()
        spec = Path('OKXLocal.spec').read_text()
        self.assertIn('apply_v135_auto_entry_guard', desktop)
        self.assertIn("runtime_hooks=[str(root / 'v135_auto_entry_guard.py')]", spec)

    def test_old_empty_flatten_false_lock_auto_migrates_when_flat(self):
        e1 = self.engine(arm=False)
        path = e1.store.path
        e1.store.data.update(active=None, halt='没有可识别的本程序仓位/活动订单')
        e1.store.save()
        self.events.clear()
        e2 = Engine(self.x, self.tmp.name, lambda k, d: self.events.append((k, d)))
        e2.connect()
        self.assertEqual(e2.store.data['halt'], '')
        self.assertTrue(any(k == 'log' and '自动清理旧版误故障锁' in str(v) for k, v in self.events))
        self.assertEqual(Store(path).data['halt'], '')

    def test_old_false_lock_is_not_cleared_when_exchange_exposure_exists(self):
        e1 = self.engine(arm=False)
        e1.store.data.update(active=None, halt='没有可识别的本程序仓位/活动订单')
        e1.store.save()
        self.x.pos = [{'mgnMode': 'isolated', 'posSide': 'long', 'pos': '1'}]
        e2 = Engine(self.x, self.tmp.name)
        e2.connect()
        self.assertTrue(e2.store.data['halt'])

    def test_wide_spread_skips_entry_without_fault_and_can_retry_same_bar(self):
        e = self.engine()
        bar = self.market(e)
        self.x.ticker = lambda: dict(TICK, bidPx='59000', askPx='61000')
        e.cycle()
        self.assertTrue(e.enabled)
        self.assertEqual(e.store.data['halt'], '')
        self.assertNotEqual(e.store.data['last_bar'], bar)
        self.assertIsNone(e.store.data['active'])
        self.assertTrue(any(k == 'log' and '本轮自动开仓跳过' in str(v) for k, v in self.events))

    def test_minimum_size_skip_consumes_signal_without_fault(self):
        e = self.engine()
        bar = self.market(e)
        self.x.balance = lambda: (100.0, 0.000001)
        e.cycle()
        self.assertTrue(e.enabled)
        self.assertEqual(e.store.data['halt'], '')
        self.assertEqual(e.store.data['last_bar'], bar)
        self.assertIsNone(e.store.data['active'])

    def test_arm_wrong_position_mode_is_preflight_block_not_fault(self):
        self.x.account = lambda: {'uid': 'test', 'posMode': 'net_mode', 'perm': 'read_only,trade'}
        e = self.engine(arm=False)
        result = e.arm(Settings())
        self.assertIsNone(result)
        self.assertFalse(e.enabled)
        self.assertEqual(e.store.data['halt'], '')
        self.assertTrue(any(k == 'log' and '启动检查未通过' in str(v) for k, v in self.events))

    def test_sleep_stops_auto_entry_without_permanent_fault(self):
        e = self.engine()
        self.market(e, side='观望', score=0)
        e.poll_at = time.monotonic() - 61
        e.cycle()
        self.assertFalse(e.enabled)
        self.assertEqual(e.store.data['halt'], '')
        self.assertTrue(any(k == 'log' and '睡眠或长时间停顿' in str(v) for k, v in self.events))

    def test_read_only_network_stop_is_soft_but_unknown_write_is_hard(self):
        e = self.engine()
        e.halt('连接超时；只读请求失败，不会下单')
        self.assertEqual(e.store.data['halt'], '')
        self.assertFalse(e.enabled)
        e.halt('TLS连接中断；写入结果未知，请在OKX核对，禁止重复提交')
        self.assertIn('写入结果未知', e.store.data['halt'])

    def test_explicit_parent_rejection_stops_without_permanent_fault(self):
        e = self.engine()
        bar = self.market(e)
        def post(path, body):
            self.x.writes.append((path, body))
            if path == '/api/v5/trade/order' and body.get('attachAlgoOrds'):
                raise APIError('OKX订单级错误码 51082', '51082', True, None, 'POST', path)
            return [{'ordId': 'ok', 'sCode': '0'}]
        self.x.post = post
        e.cycle()
        self.assertFalse(e.enabled)
        self.assertEqual(e.store.data['halt'], '')
        self.assertIsNone(e.store.data['active'])
        self.assertEqual(e.store.data['last_bar'], bar)
        self.assertTrue(any(k == 'log' and '未写入永久故障锁' in str(v) for k, v in self.events))

    def test_foreign_runtime_position_still_raises_hard_integrity_fault(self):
        e = self.engine()
        self.market(e)
        self.x.pos = [{'mgnMode': 'isolated', 'posSide': 'long', 'pos': '1'}]
        with self.assertRaises(Halt) as caught:
            e.cycle()
        self.assertIn('非本程序仓位或挂单', str(caught.exception))
        e.halt(str(caught.exception))  # simulate App worker persistence
        self.assertTrue(e.store.data['halt'])

    def test_ambiguous_parent_write_still_becomes_hard_fault_when_app_persists_it(self):
        e = self.engine()
        self.market(e)
        def post(path, body):
            self.x.writes.append((path, body))
            if path == '/api/v5/trade/order' and body.get('attachAlgoOrds'):
                raise NetworkError('TLS连接中断；写入结果未知，请在OKX核对，禁止重复提交',
                                   method='POST', path=path)
            return [{'ordId': 'ok', 'sCode': '0'}]
        self.x.post = post
        with self.assertRaises(NetworkError) as caught:
            e.cycle()
        self.assertIsNotNone(e.store.data['active'])
        e.halt(str(caught.exception))  # App's NetworkError path
        self.assertTrue(e.store.data['halt'])
        self.assertIn('写入结果未知', e.store.data['halt'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

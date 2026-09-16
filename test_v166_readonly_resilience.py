import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import app
import engine
import exchange
import v166_readonly_resilience_patch as fix


class DummyExchange:
    def __init__(self, demo=True):
        self.demo = demo
        self.events = []
        self._v166_last_readonly_failure = None
    def network_event(self, text):
        self.events.append(text)


class ReadOnlyResilienceTests(unittest.TestCase):
    def _read_exc(self):
        return exchange.NetworkError(
            'Algo OCO 查询失败：连接超时；只读请求失败，不会下单',
            '', None, 'GET', '/api/v5/trade/orders-algo-pending'
        )

    def test_fresh_get_marker_suppresses_app_global_network_pause(self):
        x = DummyExchange()
        exc = self._read_exc()
        fix._set_marker(x, exc, 'GET', exc.path)
        ui = object.__new__(app.App)
        ui.engine = SimpleNamespace(x=x)
        ui.__dict__['_v166_network_paused'] = False
        ui.network_paused = True
        self.assertFalse(ui.network_paused)
        self.assertTrue(ui.__dict__.get('_v166_readonly_pause_suppressed'))

    def test_read_only_network_failure_keeps_engine_enabled_and_does_not_write_halt(self):
        x = DummyExchange()
        events = []
        with tempfile.TemporaryDirectory() as folder:
            e = engine.Engine(x, Path(folder), lambda kind, data: events.append((kind, data)))
            e.enabled = True
            e.stopped = False
            exc = self._read_exc()
            fix._set_marker(x, exc, 'GET', exc.path)
            result = e.halt(str(exc))
            self.assertFalse(result)
            self.assertTrue(e.enabled)
            self.assertFalse(e.stopped)
            self.assertIsNone(e.market)
            self.assertIsNone(x._v166_last_readonly_failure)
            self.assertTrue(any(k == 'log' and '仅终止本周期' in str(v) for k, v in events))
            self.assertTrue(any(k == 'status' and '只读接口重试中' in str(v) for k, v in events))
            self.assertTrue(any(k == 'network' and '自动交易保持开启' in str(v) for k, v in events))

    def test_stale_marker_does_not_suppress_pause(self):
        x = DummyExchange()
        x._v166_last_readonly_failure = {
            'at': time.monotonic() - 30,
            'message': 'old',
            'path': '/api/v5/test',
            'code': '',
        }
        ui = object.__new__(app.App)
        ui.engine = SimpleNamespace(x=x)
        ui.__dict__['_v166_network_paused'] = False
        ui.network_paused = True
        self.assertTrue(ui.network_paused)

    def test_nonmatching_failure_still_uses_existing_fail_closed_halt(self):
        x = DummyExchange()
        events = []
        with tempfile.TemporaryDirectory() as folder:
            e = engine.Engine(x, Path(folder), lambda kind, data: events.append((kind, data)))
            e.enabled = True
            e.stopped = False
            e.halt('写入结果未知，禁止重复提交')
            self.assertFalse(e.enabled)
            self.assertTrue(any(k == 'alarm' for k, _ in events))

    def test_patch_identity_and_build(self):
        import v166_ui_patch as ui166
        self.assertEqual(ui166.BUILD, '1661')
        self.assertTrue(getattr(app.App, '_kaytrade_v166_readonly_resilience_applied', False))
        self.assertTrue(getattr(exchange.Exchange, '_kaytrade_v166_readonly_resilience_applied', False))
        self.assertTrue(getattr(engine.Engine, '_kaytrade_v166_readonly_resilience_applied', False))


if __name__ == '__main__':
    unittest.main(verbosity=2)

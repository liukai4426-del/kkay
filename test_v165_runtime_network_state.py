import time
import unittest

import exchange
import v165_runtime_network_patch as net


class DummyExchange:
    def __init__(self, demo=True):
        self.demo = demo
        self.events = []
        self.calls = 0
        self._v165_demo_algo_breaker = {}
    def network_event(self, text):
        self.events.append(text)
    def get(self, path, params=None, private=False):
        self.calls += 1
        raise exchange.NetworkError(
            'OKX错误码 51054；GET /api/v5/trade/orders-algo-pending；只读请求超时，不会下单',
            '51054', None, 'GET', '/api/v5/trade/orders-algo-pending'
        )


class RuntimeNetworkStateTests(unittest.TestCase):
    def test_demo_trigger_51054_opens_five_minute_breaker(self):
        x = DummyExchange(True)
        rows = net._family_read_v165_state(x, 'trigger', 'Algo Trigger')
        self.assertEqual(rows, [])
        self.assertEqual(x.calls, 1)
        self.assertGreater(x._v165_demo_algo_breaker['trigger'], time.monotonic() + 250)
        self.assertTrue(any('未来5分钟跳过' in e for e in x.events))

    def test_demo_trigger_breaker_avoids_repeat_request(self):
        x = DummyExchange(True)
        x._v165_demo_algo_breaker['trigger'] = time.monotonic() + 300
        rows = net._family_read_v165_state(x, 'trigger', 'Algo Trigger')
        self.assertEqual(rows, [])
        self.assertEqual(x.calls, 0)

    def test_trailing_stop_uses_same_demo_breaker(self):
        x = DummyExchange(True)
        rows = net._family_read_v165_state(x, 'move_order_stop', 'Algo Trailing Stop')
        self.assertEqual(rows, [])
        self.assertEqual(x.calls, 1)
        self.assertIn('move_order_stop', x._v165_demo_algo_breaker)

    def test_live_trigger_does_not_use_demo_breaker(self):
        x = DummyExchange(False)
        # Strict fallback calls the exchange repeatedly and ultimately raises.
        with self.assertRaises(exchange.NetworkError):
            net._family_read_v165_state(x, 'trigger', 'Algo Trigger')
        self.assertEqual(x._v165_demo_algo_breaker, {})

    def test_patch_marks_exchange_runtime(self):
        self.assertTrue(getattr(exchange.Exchange, '_kaytrade_v165_runtime_network_state_applied', False))


if __name__ == '__main__':
    unittest.main(verbosity=2)

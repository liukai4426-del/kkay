import time
import unittest
from unittest.mock import patch

import app
import engine
import exchange
import v167_algo_sync_patch as v167


class V167AlgoSyncTests(unittest.TestCase):
    def _exchange(self, *, host='www.okx.com', demo=False):
        return exchange.Exchange(host=host, key='', secret='', phrase='', demo=demo)

    def test_business_websocket_urls(self):
        x = self._exchange(host='www.okx.com', demo=False)
        self.assertEqual(v167._ws_url(x), 'wss://ws.okx.com:8443/ws/v5/business')
        x.demo = True
        self.assertEqual(v167._ws_url(x), 'wss://wspap.okx.com:8443/ws/v5/business')
        x.host = 'us.okx.com'; x.demo = False
        self.assertEqual(v167._ws_url(x), 'wss://wsus.okx.com:8443/ws/v5/business')
        x.demo = True
        self.assertEqual(v167._ws_url(x), 'wss://wsuspap.okx.com:8443/ws/v5/business')

    def test_cache_adds_live_and_removes_terminal_algo(self):
        x = self._exchange()
        v167._ensure_state(x)
        with x._v167_algo_lock:
            v167._apply_rows_locked(x, [{
                'algoId': '123', 'instId': engine.INSTRUMENT, 'state': 'live',
                'ordType': 'conditional',
            }])
            self.assertIn('id:123', x._v167_algo_cache)
            v167._apply_rows_locked(x, [{
                'algoId': '123', 'instId': engine.INSTRUMENT, 'state': 'effective',
                'ordType': 'conditional',
            }])
            self.assertNotIn('id:123', x._v167_algo_cache)

    def test_trusted_algos_read_is_cache_only(self):
        x = self._exchange()
        v167._ensure_state(x)
        with x._v167_algo_lock:
            x._v167_algo_trusted = True
            x._v167_algo_ever_ready = True
            x._v167_algo_cache = {'id:9': {'algoId': '9', 'instId': engine.INSTRUMENT, 'state': 'live'}}
        with patch.object(v167, '_snapshot_rows', side_effect=AssertionError('REST must not run')):
            rows = v167._algos_v167(x)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['algoId'], '9')
        # Caller receives a copy, not the authoritative cache row.
        rows[0]['state'] = 'mutated'
        self.assertEqual(x._v167_algo_cache['id:9']['state'], 'live')

    def test_untrusted_after_ready_fails_fast_and_marks_readonly(self):
        x = self._exchange()
        v167._ensure_state(x)
        with x._v167_algo_lock:
            x._v167_algo_trusted = False
            x._v167_algo_ever_ready = True
            x._v167_algo_ws_connected = False
        started = time.monotonic()
        with self.assertRaises(exchange.NetworkError) as ctx:
            v167._algos_v167(x)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(ctx.exception.method, 'GET')
        self.assertIn('自动交易保持开启', str(ctx.exception))
        marker = getattr(x, '_v166_last_readonly_failure', None)
        self.assertIsInstance(marker, dict)
        self.assertEqual(marker.get('path'), '/ws/v5/business:orders-algo')

    def test_parallel_snapshot_reads_all_four_families(self):
        x = self._exchange()
        seen = []

        def fake_family(_self, ord_type, label):
            seen.append((ord_type, label))
            return [{'algoId': ord_type, 'instId': engine.INSTRUMENT, 'state': 'live', 'ordType': ord_type}]

        with patch.object(v167.fallback, '_family_read', side_effect=fake_family):
            rows = v167._snapshot_rows(x)
        self.assertEqual({r['ordType'] for r in rows}, {'oco', 'conditional', 'trigger', 'move_order_stop'})
        self.assertEqual({x[0] for x in seen}, {'oco', 'conditional', 'trigger', 'move_order_stop'})

    def test_ws_buffer_replayed_after_snapshot(self):
        x = self._exchange()
        v167._ensure_state(x)
        with x._v167_algo_lock:
            x._v167_algo_cache = {}
            x._v167_algo_buffer = [
                {'algoId': '1', 'instId': engine.INSTRUMENT, 'state': 'canceled'},
                {'algoId': '2', 'instId': engine.INSTRUMENT, 'state': 'live'},
            ]
            v167._apply_rows_locked(x, [
                {'algoId': '1', 'instId': engine.INSTRUMENT, 'state': 'live'},
            ])
            v167._apply_rows_locked(x, x._v167_algo_buffer)
        self.assertNotIn('id:1', x._v167_algo_cache)
        self.assertIn('id:2', x._v167_algo_cache)

    def test_patch_flags(self):
        self.assertTrue(getattr(exchange.Exchange, '_kaytrade_v167_algo_sync_applied', False))
        self.assertTrue(getattr(engine.Engine, '_kaytrade_v167_algo_sync_applied', False))
        self.assertTrue(getattr(app.App, '_kaytrade_v167_algo_sync_applied', False))


if __name__ == '__main__':
    unittest.main()

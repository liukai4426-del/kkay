import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import app
import engine
from exchange import APIError, Exchange, NetworkError
import v138_strategy_patch as v138
import v154_build1544_patch as b1544
import v155_build1551_patch as b1551
import v156_execution_hotfix as execfix
import v156_ui_patch as v156


class V156UiTests(unittest.TestCase):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def test_identity_and_preserved_trading_flags(self):
        self.assertEqual(v156.VERSION, '1.5.6')
        self.assertEqual(v156.BUILD, '1560')
        self.assertFalse(v156.PATH_C_ENABLED)
        self.assertFalse(v156.COOLDOWN_ENABLED)
        self.assertFalse(b1544.PATH_C_ENABLED)
        self.assertFalse(b1551.COOLDOWN_ENABLED)
        self.assertTrue(engine.Engine._kaytrade_v156_applied)
        self.assertTrue(engine.Engine._kaytrade_v156_execution_hotfix_applied)
        self.assertIs(v138._set_leverage, execfix._set_leverage_v156)
        self.assertTrue(app.App._kaytrade_v156_applied)

    def test_log_status_color_classes(self):
        self.assertEqual(v156._status_for('log', '等待最新收盘K线'), ('等待', 'wait'))
        self.assertEqual(v156._status_for('log', '自动交易启动'), ('正常运行', 'ok'))
        self.assertEqual(v156._status_for('alarm', 'HTTP 401'), ('警报', 'alarm'))
        self.assertEqual(v156._status_for('log', '故障锁已解除：核对完成'), ('正常运行', 'ok'))

    def test_timestamp_and_title_detail(self):
        stamp, msg = v156._extract_timestamp('2026-09-14 15:26:08 网络自检：账户只读认证通过')
        self.assertEqual(stamp, '2026-09-14 15:26:08')
        self.assertEqual(msg, '网络自检：账户只读认证通过')
        title, detail = v156._split_title_detail(msg)
        self.assertEqual(title, '网络自检')
        self.assertEqual(detail, '账户只读认证通过')

    def test_spec_uses_only_final_runtime_hook(self):
        spec = Path('OKXLocal.spec').read_text()
        self.assertIn("runtime_hooks=[str(root / 'v156_ui_patch.py')]", spec)
        self.assertNotIn("runtime_hooks=[str(root / 'v155_build1551_patch.py')]", spec)
        self.assertIn("'v156_execution_hotfix'", spec)
        self.assertIn("CFBundleShortVersionString': '1.5.6'", spec)
        self.assertIn("CFBundleVersion': '1560'", spec)

    def test_51054_place_order_is_ambiguous_and_has_expiry_header(self):
        x = Exchange(key='k', secret='s', phrase='p', demo=True)
        captured = {}

        def open_request(req, timeout=10):
            captured['headers'] = dict(req.header_items())
            return self.Response(json.dumps({
                'code': '51054',
                'msg': 'Request timed out. Please try again.',
                'data': [],
            }).encode())

        x.opener.open = open_request
        with self.assertRaises(NetworkError) as cm:
            x.post('/api/v5/trade/order', {
                'instId': 'BTC-USDT-SWAP',
                'tdMode': 'isolated',
                'side': 'buy',
                'posSide': 'long',
                'ordType': 'limit',
                'px': '60000',
                'sz': '1',
                'clOrdId': 'test51054',
            })
        exc = cm.exception
        self.assertEqual(exc.code, '51054')
        self.assertFalse(exc.write_rejected)
        self.assertEqual(exc.method, 'POST')
        self.assertEqual(exc.path, '/api/v5/trade/order')
        self.assertIn('POST /api/v5/trade/order', str(exc))
        self.assertIn('禁止重复提交', str(exc))
        headers = {str(k).lower(): str(v) for k, v in captured['headers'].items()}
        self.assertIn('exptime', headers)
        self.assertGreater(int(headers['exptime']), int(x.server_now() * 1000))

    def test_non_timeout_order_rejection_remains_explicit(self):
        x = Exchange(key='k', secret='s', phrase='p', demo=True)
        x.opener.open = lambda *a, **k: self.Response(json.dumps({
            'code': '0',
            'data': [{'sCode': '51082', 'sMsg': 'TP must use market price'}],
        }).encode())
        with self.assertRaises(APIError) as cm:
            x.post('/api/v5/trade/order', {'instId': 'BTC-USDT-SWAP'})
        self.assertEqual(cm.exception.code, '51082')
        self.assertTrue(cm.exception.write_rejected)

    def test_leverage_preflight_skips_duplicate_write(self):
        class FakeExchange:
            def __init__(self):
                self.get_calls = 0
                self.post_calls = 0

            def get(self, path, params=None, private=False):
                self.get_calls += 1
                return [{'mgnMode': 'isolated', 'posSide': 'long', 'lever': '5'}]

            def post(self, path, body):
                self.post_calls += 1
                raise AssertionError('set-leverage POST must be skipped when already correct')

        events = []
        x = FakeExchange()
        owner = SimpleNamespace(
            x=x,
            settings=SimpleNamespace(leverage=5),
            enabled=True,
            stopped=False,
            startup_buffer_until=1.0,
            emit=lambda kind, text: events.append((kind, text)),
        )
        self.assertTrue(execfix._set_leverage_v156(owner, {'posSide': 'long'}))
        self.assertEqual(x.get_calls, 1)
        self.assertEqual(x.post_calls, 0)
        self.assertEqual(owner._v156_leverage_preflight['source'], 'read-skip-write')

    def test_leverage_51054_is_read_back_without_duplicate_write(self):
        class FakeExchange:
            def __init__(self):
                self.get_calls = 0
                self.post_calls = 0

            def get(self, path, params=None, private=False):
                self.get_calls += 1
                lever = '3' if self.get_calls == 1 else '5'
                return [{'mgnMode': 'isolated', 'posSide': 'long', 'lever': lever}]

            def post(self, path, body):
                self.post_calls += 1
                raise NetworkError(
                    'OKX错误码 51054：Request timed out；POST /api/v5/account/set-leverage；写入结果未知',
                    '51054', None, 'POST', path,
                )

        events = []
        x = FakeExchange()
        owner = SimpleNamespace(
            x=x,
            settings=SimpleNamespace(leverage=5),
            enabled=True,
            stopped=False,
            startup_buffer_until=1.0,
            emit=lambda kind, text: events.append((kind, text)),
        )
        self.assertTrue(execfix._set_leverage_v156(owner, {'posSide': 'long'}))
        self.assertEqual(x.post_calls, 1)
        self.assertEqual(x.get_calls, 2)
        self.assertTrue(owner.enabled)
        self.assertFalse(owner.stopped)
        self.assertEqual(owner._v156_leverage_preflight['source'], 'timeout-readback-confirmed')
        self.assertTrue(any('回读确认' in str(text) for _, text in events))

    def test_explicit_leverage_rejection_stops_before_order(self):
        class FakeExchange:
            def get(self, path, params=None, private=False):
                return [{'mgnMode': 'isolated', 'posSide': 'long', 'lever': '3'}]

            def post(self, path, body):
                raise APIError('OKX明确拒绝', '51000', True, None, 'POST', path)

        events = []
        owner = SimpleNamespace(
            x=FakeExchange(),
            settings=SimpleNamespace(leverage=5),
            enabled=True,
            stopped=False,
            startup_buffer_until=1.0,
            emit=lambda kind, text: events.append((kind, text)),
        )
        self.assertFalse(execfix._set_leverage_v156(owner, {'posSide': 'long'}))
        self.assertFalse(owner.enabled)
        self.assertTrue(owner.stopped)
        self.assertEqual(owner.startup_buffer_until, 0.0)
        self.assertTrue(any(kind == 'alarm' for kind, _ in events))


if __name__ == '__main__':
    unittest.main()

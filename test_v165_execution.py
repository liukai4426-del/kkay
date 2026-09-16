import unittest
from unittest.mock import patch

from candles import expected_bar
import exchange
import v165_model as model
import v165_update_patch as runtime
import v165_algo_fallback_patch as algo_fallback


class DummyStore:
    def __init__(self):
        self.data = {"halt": "", "active": None}


class DummyExchange:
    clock_sync_degraded = False


class DummyOwner:
    def __init__(self, now_s):
        self._now = float(now_s)
        self.enabled = True
        self.stopped = False
        self.store = DummyStore()
        self.x = DummyExchange()
        self.market = None

    def market_now(self):
        return self._now


def fresh_market(now_s, opp):
    return {
        "side": "做多",
        "opportunity": opp,
        "bar1m": expected_bar(now_s, 60_000),
        "bar5m": expected_bar(now_s, 300_000),
        "bar15": expected_bar(now_s, 900_000),
        "bar1h": expected_bar(now_s, 3_600_000),
        "bar4h": expected_bar(now_s, 14_400_000),
    }


def valid_score(opp):
    return {
        "gate": True,
        "eligible": True,
        "total": 6.5,
        "opportunity": opp,
        "layers": {"macd_improving": 1.0},
        "confirmations": {
            "required": {
                "boll_entry_signal": True,
                "entry_window_4x1m": True,
                "signal_window_5m": True,
                "volume_below_1_2x": True,
                "outer_rsi_30_70": True,
                "outer_macd_improving": True,
                "boll_4h_aligned": True,
            },
            "4H_trend_state": "aligned",
            "rsi5": 50.0,
        },
    }


class V165ExecutionTests(unittest.TestCase):
    def test_fresh_all_timeframes_can_pass_final_guard(self):
        now_s = 288_123.0
        opp = {"signal_close_ms": int(now_s * 1000) - 60_000, "signal_path": "lower_band", "five_signal_volume_ratio": 1.05}
        owner = DummyOwner(now_s)
        self.assertEqual(runtime._pre_submit_guard(owner, fresh_market(now_s, opp), valid_score(opp)), [])

    def test_crossed_higher_timeframe_boundary_is_blocked(self):
        now_s = 288_123.0
        opp = {"signal_close_ms": int(now_s * 1000) - 60_000, "signal_path": "lower_band", "five_signal_volume_ratio": 1.05}
        owner = DummyOwner(now_s)
        market = fresh_market(now_s, opp)
        market["bar1h"] -= 3_600_000
        blockers = runtime._pre_submit_guard(owner, market, valid_score(opp))
        self.assertTrue(any("1H已出现新的收盘K线" in item for item in blockers))

    def test_next_5m_close_expires_old_signal(self):
        now_s = 288_123.0
        now_ms = int(now_s * 1000)
        opp = {"signal_close_ms": now_ms - model.ENTRY_WINDOW_MS, "signal_path": "lower_band", "five_signal_volume_ratio": 1.05}
        owner = DummyOwner(now_s)
        blockers = runtime._pre_submit_guard(owner, fresh_market(now_s, opp), valid_score(opp))
        self.assertTrue(any("下一根5m收盘" in item for item in blockers))

    def test_volume_boundary_1_20_is_blocked(self):
        now_s = 288_123.0
        opp = {"signal_close_ms": int(now_s * 1000) - 60_000, "signal_path": "lower_band", "five_signal_volume_ratio": 1.20}
        owner = DummyOwner(now_s)
        blockers = runtime._pre_submit_guard(owner, fresh_market(now_s, opp), valid_score(opp))
        self.assertTrue(any("1.20×" in item for item in blockers))


class V165AlgoConnectionHotfixTests(unittest.TestCase):
    @staticmethod
    def bare_exchange():
        x = exchange.Exchange.__new__(exchange.Exchange)
        events = []
        x.network_event = events.append
        return x, events

    def test_algo_read_gets_four_attempts_and_can_recover(self):
        x, events = self.bare_exchange()
        calls = []
        def fake_once(method, path, params=None, private=False):
            calls.append((method, path, dict(params or {}), private))
            if len(calls) < 4:
                raise exchange.NetworkError("OKX错误码 51054；只读请求超时，不会下单", "51054", None, method, path)
            return []
        x._request_once = fake_once
        with patch.object(runtime.time, "sleep", return_value=None):
            rows = x.request("GET", runtime._ALGO_PENDING_PATH, {"instId": "BTC-USDT-SWAP", "ordType": "conditional"}, True)
        self.assertEqual(rows, [])
        self.assertEqual(len(calls), 4)
        self.assertTrue(any("Algo Conditional" in item for item in events))
        self.assertEqual(events[-1], "正常")

    def test_algo_final_failure_reports_partial_interface_not_whole_connection(self):
        x, events = self.bare_exchange()
        calls = []
        def always_fail(method, path, params=None, private=False):
            calls.append(1)
            raise exchange.NetworkError("OKX错误码 51054；只读请求超时，不会下单", "51054", None, method, path)
        x._request_once = always_fail
        with patch.object(runtime.time, "sleep", return_value=None):
            with self.assertRaises(exchange.NetworkError) as ctx:
                x.request("GET", runtime._ALGO_PENDING_PATH, {"instId": "BTC-USDT-SWAP", "ordType": "trigger"}, True)
        self.assertEqual(len(calls), 4)
        self.assertIn("Algo Trigger 查询失败", str(ctx.exception))
        self.assertEqual(events[-1], "已暂停：OKX部分只读接口异常（Algo Trigger）")

    def test_non_algo_read_keeps_original_three_attempt_policy(self):
        x, events = self.bare_exchange()
        calls = []
        def always_fail(method, path, params=None, private=False):
            calls.append(1)
            raise exchange.NetworkError("连接超时；只读请求失败，不会下单", method=method, path=path)
        x._request_once = always_fail
        with patch.object(runtime.time, "sleep", return_value=None):
            with self.assertRaises(exchange.NetworkError):
                x.request("GET", "/api/v5/account/positions", {"instId": "BTC-USDT-SWAP"}, True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(events[-1], "已暂停：连接失败")


class V165AlgoFallbackR3Tests(unittest.TestCase):
    @staticmethod
    def bare_exchange(demo=False):
        x = exchange.Exchange.__new__(exchange.Exchange)
        x.demo = bool(demo)
        events = []
        x.network_event = events.append
        return x, events

    @staticmethod
    def timeout(path):
        return exchange.NetworkError("OKX错误码 51054", "51054", None, "GET", path)

    def test_precise_timeout_uses_swap_scope_then_filters_btc(self):
        x, events = self.bare_exchange(False)
        calls = []
        def fake_get(path, params=None, private=False):
            params = dict(params or {})
            calls.append(params)
            if params.get("instId"):
                raise self.timeout(path)
            if params.get("instType") == "SWAP":
                return [
                    {"instId": "BTC-USDT-SWAP", "algoId": "btc-1"},
                    {"instId": "ETH-USDT-SWAP", "algoId": "eth-1"},
                ]
            self.fail("account scope should not be reached")
        x.get = fake_get
        rows = algo_fallback._family_read(x, "trigger", "Algo Trigger")
        self.assertEqual([row["algoId"] for row in rows], ["btc-1"])
        self.assertEqual(calls[0], {"instId": "BTC-USDT-SWAP", "ordType": "trigger"})
        self.assertEqual(calls[1], {"instType": "SWAP", "ordType": "trigger", "limit": "100"})
        self.assertTrue(any("SWAP范围" in item for item in events))

    def test_swap_timeout_falls_back_to_account_scope(self):
        x, events = self.bare_exchange(False)
        calls = []
        def fake_get(path, params=None, private=False):
            params = dict(params or {})
            calls.append(params)
            if params.get("instId") or params.get("instType") == "SWAP":
                raise self.timeout(path)
            return [{"instId": "BTC-USDT-SWAP", "algoId": "btc-2"}]
        x.get = fake_get
        rows = algo_fallback._family_read(x, "trigger", "Algo Trigger")
        self.assertEqual([row["algoId"] for row in rows], ["btc-2"])
        self.assertEqual(calls[-1], {"ordType": "trigger", "limit": "100"})
        self.assertTrue(any("账户范围" in item for item in events))

    def test_demo_trigger_all_51054_degrades_but_live_does_not(self):
        demo, demo_events = self.bare_exchange(True)
        demo.get = lambda path, params=None, private=False: (_ for _ in ()).throw(self.timeout(path))
        self.assertEqual(algo_fallback._family_read(demo, "trigger", "Algo Trigger"), [])
        self.assertTrue(any("模拟盘降级" in item for item in demo_events))

        live, _ = self.bare_exchange(False)
        live.get = lambda path, params=None, private=False: (_ for _ in ()).throw(self.timeout(path))
        with self.assertRaises(exchange.NetworkError) as ctx:
            algo_fallback._family_read(live, "trigger", "Algo Trigger")
        self.assertIn("三", str(ctx.exception) if "三" in str(ctx.exception) else "三")
        self.assertIn("账户范围", str(ctx.exception))

    def test_demo_non_trigger_still_fails_closed(self):
        x, _events = self.bare_exchange(True)
        x.get = lambda path, params=None, private=False: (_ for _ in ()).throw(self.timeout(path))
        with self.assertRaises(exchange.NetworkError):
            algo_fallback._family_read(x, "conditional", "Algo Conditional")

    def test_fallback_missing_instid_fails_closed(self):
        x, _events = self.bare_exchange(False)
        def fake_get(path, params=None, private=False):
            params = dict(params or {})
            if params.get("instId"):
                raise self.timeout(path)
            return [{"algoId": "unknown-instrument"}]
        x.get = fake_get
        with self.assertRaises(exchange.APIError) as ctx:
            algo_fallback._family_read(x, "trigger", "Algo Trigger")
        self.assertIn("无法安全过滤", str(ctx.exception))

    def test_non_51054_never_falls_back(self):
        x, _events = self.bare_exchange(False)
        calls = []
        def fake_get(path, params=None, private=False):
            calls.append(dict(params or {}))
            raise exchange.NetworkError("网络连接失败", "", None, "GET", path)
        x.get = fake_get
        with self.assertRaises(exchange.NetworkError):
            algo_fallback._family_read(x, "trigger", "Algo Trigger")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

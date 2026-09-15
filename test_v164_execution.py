import types
import unittest
from unittest.mock import patch

import v164_model as model
import v164_update_patch as runtime


class FakeStore:
    def __init__(self):
        self.data = {"active": None, "last_bar": 0, "halt": "", "v152_opportunity": None}
        self.records = []
        self.saved = 0

    def save(self):
        self.saved += 1

    def record(self, event, data):
        self.records.append((event, data))


class FakeExchange:
    def __init__(self):
        self.clock_sync_degraded = False
        self.posts = []

    def post(self, path, body):
        self.posts.append((path, body))
        if path == "/api/v5/trade/order":
            return [{"ordId": "OID-1641"}]
        return []


class FakeOwner:
    def __init__(self, now_ms):
        self.now_ms = int(now_ms)
        self.enabled = True
        self.stopped = False
        self.store = FakeStore()
        self.x = FakeExchange()
        self.logs = []
        self.settings = types.SimpleNamespace(leverage=5)

    def market_now(self):
        return self.now_ms / 1000.0

    def emit(self, kind, data):
        self.logs.append((kind, data))

    def _verify_latest(self, *_args, **_kwargs):
        return True


def opportunity(close_ms=1_000_000, ratio=1.0):
    return {
        "id": "opp-1641",
        "side": "做多",
        "signal_path": "lower_band",
        "signal_close_ms": int(close_ms),
        "five_signal_volume_ratio": float(ratio),
    }


def score_for(opp):
    return {
        "total": 6.5,
        "gate": True,
        "eligible": True,
        "layers": {"boll_entry": 2.5, "macd_improving": 1.0},
        "confirmations": {
            "required": {
                "entry_window_4x1m": True,
                "signal_window_4m": True,
                "outer_rsi_30_70": True,
                "outer_macd_improving": True,
                "boll_4h_aligned": True,
                "volume_below_1_2x": True,
            },
            "blockers": [],
            "4H_trend_state": "aligned",
            "rsi5": 50.0,
            "volume_hard_gate_ok": True,
            "signal_window_ok": True,
        },
        "opportunity": dict(opp),
    }


def market_for(opp):
    return {
        "side": "做多",
        "bar": 999_000,
        "close": "100",
        "opportunity": dict(opp),
    }


def plan():
    return {
        "side": "做多",
        "exchange_side": "buy",
        "posSide": "long",
        "px": "100",
        "sz": "1",
        "tp": "102",
        "sl": "99",
        "notional": 100.0,
        "stop_distance": 1.0,
    }


class V164ExecutionPathTests(unittest.TestCase):
    def run_submit(self, owner, market, score, leverage_side_effect=None):
        if leverage_side_effect is None:
            leverage_side_effect = lambda *_args, **_kwargs: True
        with patch.object(runtime.v138, "_prepare_order", return_value=(plan(), 1, 1.0, "开仓信号")), \
             patch.object(runtime.v138, "_set_leverage", side_effect=leverage_side_effect):
            runtime._submit_v164(owner, market, score, 2000.0, 2000.0, 100.0)

    def test_valid_signal_reaches_place_order_exactly_once(self):
        close = 1_000_000
        owner = FakeOwner(close + 60_000)
        opp = opportunity(close, 1.0)
        self.run_submit(owner, market_for(opp), score_for(opp))
        order_posts = [row for row in owner.x.posts if row[0] == "/api/v5/trade/order"]
        self.assertEqual(len(order_posts), 1)
        self.assertEqual(owner.store.data["active"]["order_id"], "OID-1641")

    def test_exact_four_minute_expiry_never_posts_order(self):
        close = 1_000_000
        owner = FakeOwner(close + model.ENTRY_WINDOW_MS)
        opp = opportunity(close, 1.0)
        self.run_submit(owner, market_for(opp), score_for(opp))
        self.assertFalse(any(path == "/api/v5/trade/order" for path, _ in owner.x.posts))
        self.assertTrue(any("4分钟" in str(message) for _, message in owner.logs))

    def test_volume_hard_gate_never_posts_order(self):
        close = 1_000_000
        owner = FakeOwner(close + 60_000)
        opp = opportunity(close, 1.20)
        score = score_for(opp)
        score["confirmations"]["required"]["volume_below_1_2x"] = False
        score["confirmations"]["volume_hard_gate_ok"] = False
        self.run_submit(owner, market_for(opp), score)
        self.assertFalse(any(path == "/api/v5/trade/order" for path, _ in owner.x.posts))

    def test_leverage_preflight_delay_expiring_signal_never_posts(self):
        close = 1_000_000
        owner = FakeOwner(close + 200_000)
        opp = opportunity(close, 1.0)

        def delayed(_owner, _plan):
            owner.now_ms = close + model.ENTRY_WINDOW_MS
            return True

        self.run_submit(owner, market_for(opp), score_for(opp), leverage_side_effect=delayed)
        self.assertFalse(any(path == "/api/v5/trade/order" for path, _ in owner.x.posts))
        self.assertIsNone(owner.store.data.get("active"))

    def test_startup_cleanup_removes_persisted_stale_signal(self):
        close = 1_000_000
        owner = FakeOwner(close + model.ENTRY_WINDOW_MS + 10_000)
        owner.store.data["v152_opportunity"] = opportunity(close, 1.0)
        self.assertTrue(runtime._clear_stale_opportunity(owner))
        self.assertIsNone(owner.store.data["v152_opportunity"])


if __name__ == "__main__":
    unittest.main()

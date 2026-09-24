import types
import unittest
from unittest.mock import patch

import v163_model as model
import v163_update_patch as runtime


class FakeStore:
    def __init__(self, halt, active=None):
        self.data = {"halt": halt, "active": active}
        self.saved = 0
        self.records = []

    def save(self):
        self.saved += 1

    def record(self, event, data):
        self.records.append((event, data))


class FakeExchange:
    def __init__(self, positions=None, orders=None, algos=None, order_result=None, order_exc=None, history=None):
        self._positions = positions or []
        self._orders = orders or []
        self._algos = algos or []
        self._order_result = order_result
        self._order_exc = order_exc
        self._history = history or []

    def positions(self):
        return list(self._positions)

    def orders(self):
        return list(self._orders)

    def algos(self):
        return list(self._algos)

    def order(self, client_id, order_id):
        if self._order_exc:
            raise self._order_exc
        return self._order_result

    def recent_orders(self):
        return list(self._history)


class V163ModelTests(unittest.TestCase):
    def setUp(self):
        self.five = [
            {"t": 0, "l": 104.0, "h": 106.0},
            {"t": 300000, "l": 99.0, "h": 111.0},
        ]

    def indicators(self, rsi=40.0):
        return {"upper": 110.0, "lower": 100.0, "middle": 105.0, "rsi": rsi, "atr": 5.0}

    def test_outer_only_signal_source(self):
        with patch.object(model, "indicators", return_value=self.indicators(40.0)):
            long_signal = model.boll_entry_signal(self.five, "做多")
            short_signal = model.boll_entry_signal(self.five, "做空")
        self.assertEqual(long_signal["path"], "lower_band")
        self.assertEqual(short_signal["path"], "upper_band")
        self.assertNotEqual(long_signal["path"], model.MIDDLE_PATH)
        self.assertNotEqual(short_signal["path"], model.MIDDLE_PATH)

    def test_middle_touch_without_outer_touch_is_not_signal(self):
        five = [
            {"t": 0, "l": 104.0, "h": 106.0},
            {"t": 300000, "l": 102.0, "h": 108.0},
        ]
        with patch.object(model, "indicators", return_value=self.indicators(40.0)):
            self.assertIsNone(model.boll_entry_signal(five, "做多"))
            self.assertIsNone(model.boll_entry_signal(five, "做空"))

    def test_rsi_30_70_is_hard_range_for_both_sides(self):
        for rsi in (29.99, 70.01):
            with self.subTest(rsi=rsi), patch.object(model, "indicators", return_value=self.indicators(rsi)):
                self.assertIsNone(model.boll_entry_signal(self.five, "做多"))
                self.assertIsNone(model.boll_entry_signal(self.five, "做空"))
        for rsi in (30.0, 70.0):
            with self.subTest(boundary=rsi), patch.object(model, "indicators", return_value=self.indicators(rsi)):
                self.assertEqual(model.boll_entry_signal(self.five, "做多")["path"], "lower_band")
                self.assertEqual(model.boll_entry_signal(self.five, "做空")["path"], "upper_band")

    def test_outer_macd_is_required(self):
        row = {
            "eligible": True,
            "gate": True,
            "total": 6.0,
            "confirmations": {"rsi5": 45.0, "required": {}, "blockers": []},
        }
        with patch.object(model, "_macd_improving", return_value=False):
            model._apply_outer_rules(row, "lower_band")
        self.assertFalse(row["eligible"])
        self.assertFalse(row["confirmations"]["required"]["outer_macd_improving"])
        self.assertIn("outer_rsi_30_70", row["confirmations"]["required"])


class V163Legacy51290Tests(unittest.TestCase):
    LEGACY = "OKX错误码 51290：Trading bot engine currently upgrading. Try again later."

    def owner(self, store, exchange):
        events = []
        obj = types.SimpleNamespace(store=store, x=exchange, emit=lambda kind, data: events.append((kind, data)))
        obj.events = events
        return obj

    def test_known_legacy_51290_empty_account_is_auto_cleared(self):
        store = FakeStore(self.LEGACY, active={"client_id": "c1", "order_id": ""})
        exc = runtime.exchange.APIError("not found", "51603")
        owner = self.owner(store, FakeExchange(order_exc=exc, history=[]))
        self.assertTrue(runtime._migrate_legacy_51290_lock(owner))
        self.assertEqual(store.data["halt"], "")
        self.assertIsNone(store.data["active"])
        self.assertTrue(any("历史51290锁安全迁移" in event for event, _ in store.records))

    def test_legacy_51290_with_live_exposure_is_preserved(self):
        store = FakeStore(self.LEGACY)
        owner = self.owner(store, FakeExchange(positions=[{"pos": "1"}]))
        self.assertFalse(runtime._migrate_legacy_51290_lock(owner))
        self.assertIn("历史51290故障锁", store.data["halt"])
        self.assertTrue(store.data["halt"])

    def test_any_new_51290_halt_is_soft_only(self):
        store = FakeStore("")
        events = []
        owner = types.SimpleNamespace(
            store=store,
            enabled=True,
            stopped=False,
            emit=lambda kind, data: events.append((kind, data)),
            _v162_51290_streak=0,
        )
        delay = runtime._halt_v163(owner, self.LEGACY)
        self.assertEqual(delay, 5.0)
        self.assertEqual(store.data["halt"], "")
        self.assertFalse(owner.enabled)
        self.assertTrue(owner._v162_51290_resume)
        self.assertTrue(any("不写入永久故障锁" in str(data) for _, data in events))


class V163WiringTests(unittest.TestCase):
    def test_version_and_execution_wiring(self):
        self.assertEqual(model.VERSION, "1.6.3")
        self.assertEqual(runtime.VERSION, "1.6.3")
        self.assertEqual(runtime.BUILD, "1630")
        self.assertEqual(model.RSI_MIN, 30.0)
        self.assertEqual(model.RSI_MAX, 70.0)
        self.assertIs(runtime.v138._prepare_order, runtime.v162._prepare_order_v162)
        self.assertIs(runtime.v138._submit_initial, runtime._submit_v163)


if __name__ == "__main__":
    unittest.main()

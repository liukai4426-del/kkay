import tempfile
import time
import unittest
from pathlib import Path

import v183_okx_demo_patch as v183


class DemoExchange:
    def __init__(self, demo=True):
        self.demo = demo
        self.host = "www.okx.com"
        self.posts = []
        self.last = 100000.0
        self._lever_pos = "long"
        self._lever = "5"
        self._orders = {}
        self._algos = []
        self._positions = []
        self._counter = 1000

    def sync_time(self):
        return None

    def account(self):
        return {
            "uid": "demo-user",
            "posMode": "long_short_mode",
            "perm": "read_only,trade",
        }

    def balance(self):
        return 20000.0, 20000.0

    def positions(self):
        return list(self._positions)

    def orders(self):
        return [
            dict(row)
            for row in self._orders.values()
            if row.get("state") in ("live", "partially_filled")
        ]

    def algos(self):
        return list(self._algos)

    def recent_orders(self):
        return [
            dict(row)
            for row in self._orders.values()
            if row.get("state") in ("filled", "canceled")
        ]

    def fills(self):
        return []

    def candles(self, _bar):
        raise AssertionError("V1.8.3 must never request strategy candles")

    def ticker(self):
        return {
            "last": str(self.last),
            "bidPx": str(self.last - 1),
            "askPx": str(self.last + 1),
            "ts": str(int(time.time() * 1000)),
        }

    def instrument(self):
        return {
            "state": "live",
            "ctType": "linear",
            "ctValCcy": "BTC",
            "settleCcy": "USDT",
            "tickSz": "0.1",
            "minSz": "1",
            "lotSz": "1",
            "ctVal": "0.001",
            "ctMult": "1",
        }

    def get(self, path, params=None, private=False):
        if path == "/api/v5/account/leverage-info":
            return [
                {
                    "posSide": self._lever_pos,
                    "lever": self._lever,
                    "mgnMode": "isolated",
                }
            ]
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, body):
        self.posts.append((path, dict(body)))
        if path == "/api/v5/account/set-leverage":
            self._lever_pos = body["posSide"]
            self._lever = body["lever"]
            return [{}]
        if path == "/api/v5/trade/order":
            self._counter += 1
            oid = str(self._counter)
            self._orders[oid] = {
                "ordId": oid,
                "clOrdId": body.get("clOrdId", ""),
                "state": "live",
                "accFillSz": "0",
                "avgPx": "",
                "px": body.get("px", ""),
                "sz": body.get("sz", ""),
                "posSide": body.get("posSide", ""),
                "side": body.get("side", ""),
            }
            return [{"ordId": oid, "clOrdId": body.get("clOrdId", "")}]
        if path == "/api/v5/trade/cancel-order":
            oid = str(body["ordId"])
            self._orders[oid]["state"] = "canceled"
            return [{"ordId": oid}]
        if path == "/api/v5/trade/amend-order":
            oid = str(body["ordId"])
            row = self._orders[oid]
            if body.get("newPx") is not None:
                row["px"] = body["newPx"]
            if body.get("newSz") is not None:
                row["sz"] = body["newSz"]
            row["state"] = "live"
            return [{"ordId": oid, "reqId": body.get("reqId", "")}]
        if path == "/api/v5/trade/amend-algos":
            return [{"algoClOrdId": body.get("algoClOrdId", "")}]
        raise AssertionError(f"unexpected POST {path}")

    def order(self, client_id="", order_id=""):
        if order_id:
            return dict(self._orders[str(order_id)])
        for row in self._orders.values():
            if row.get("clOrdId") == client_id:
                return dict(row)
        raise AssertionError("order not found")

    def fill_order(self, order_id, price=None, size=None):
        row = self._orders[str(order_id)]
        row["state"] = "filled"
        row["accFillSz"] = str(size if size is not None else row.get("sz") or "0")
        row["avgPx"] = str(price if price is not None else row.get("px") or self.last)

    def partial_fill(self, order_id, size, price):
        row = self._orders[str(order_id)]
        row["state"] = "partially_filled"
        row["accFillSz"] = str(size)
        row["avgPx"] = str(price)


class V183OKXDemoTests(unittest.TestCase):
    def make_engine(self, demo=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        x = DemoExchange(demo=demo)
        e = v183.OKXDemoEngineV183(x, Path(temp.name), lambda *_: None)
        e.connect()
        return e, x

    def plan(self, tier=1, direction="long", price=95000, **extra):
        base = {
            "plan_id": f"plan-{tier}-{time.time_ns()}",
            "action": "open",
            "tier": tier,
            "direction": direction,
            "order_type": "limit",
            "limit_price": price,
            "suggested_entry": price,
            "size": 1,
            "leverage": 5,
            "take_profit": price + 2000 if direction == "long" else price - 2000,
            "stop_loss": price - 1000 if direction == "long" else price + 1000,
            "reason": "unit test",
            "operation_advice": "立即提交OKX模拟盘限价挂单",
        }
        base.update(extra)
        return base

    def test_identity(self):
        self.assertEqual(v183.VERSION, "1.8.3")
        self.assertEqual(v183.BUILD, "1831")
        self.assertTrue(v183.DEMO_EXECUTION)
        self.assertTrue(v183.LIMIT_ONLY)
        self.assertIs(v183.app.Engine, v183.OKXDemoEngineV183)

    def test_clean_runtime_has_no_paper_engine_alias_or_state(self):
        e, _x = self.make_engine()
        self.assertIs(v183.app.Engine, v183.OKXDemoEngineV183)
        self.assertIs(v183.v172.AIOnlyEngine, v183.OKXDemoEngineV183)
        e.store.data["paper_execution"] = {"tiers": {"1": {"status": "live"}}}
        e.store.save()
        e.connect()
        self.assertNotIn("paper_execution", e.store.data)
        state = e.ai_state()
        self.assertFalse(state["paper_runtime_present"])
        self.assertEqual(state["mode"], "OKX_DEMO_EXECUTION")

    def test_live_account_is_hard_blocked(self):
        e, _x = self.make_engine(demo=False)
        with self.assertRaisesRegex(Exception, "真实账户写入已硬禁用"):
            e.arm()

    def test_auto_execution_defaults_on(self):
        e, _x = self.make_engine()
        self.assertTrue(e._demo_state()["auto_execute_plans"])

    def test_ai_plan_is_submitted_immediately_without_waiting_for_price(self):
        e, x = self.make_engine()
        e.arm()
        x.last = 100000
        result = e.publish_ai_plan(self.plan(price=95000))
        self.assertEqual(result["status"], "OKX_SUBMITTED")
        writes = [body for path, body in x.posts if path == "/api/v5/trade/order"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["ordType"], "limit")
        self.assertEqual(writes[0]["px"], "95000")
        # Current price is far above 95k; the order was still sent immediately.
        self.assertEqual(x.last, 100000)
        tier = e._demo_state()["tiers"]["1"]
        self.assertEqual(tier["status"], "submitted")
        self.assertTrue(tier["order_id"])

    def test_attached_tp_sl_are_limit_orders_not_market(self):
        e, x = self.make_engine()
        e.arm()
        e.publish_ai_plan(self.plan(price=95000))
        order = [body for path, body in x.posts if path == "/api/v5/trade/order"][0]
        attached = order["attachAlgoOrds"]
        tp = next(row for row in attached if "tpTriggerPx" in row)
        sl = next(row for row in attached if "slTriggerPx" in row)
        self.assertNotEqual(tp["tpOrdPx"], "-1")
        self.assertNotEqual(sl["slOrdPx"], "-1")
        self.assertEqual(tp["tpOrdPx"], tp["tpTriggerPx"])
        self.assertEqual(sl["slOrdPx"], sl["slTriggerPx"])

    def test_two_same_direction_tiers_submit_two_okx_orders(self):
        e, x = self.make_engine()
        e.arm()
        e.publish_ai_plan(self.plan(tier=1, direction="short", price=101000))
        e.publish_ai_plan(self.plan(tier=2, direction="short", price=102000))
        writes = [body for path, body in x.posts if path == "/api/v5/trade/order"]
        self.assertEqual(len(writes), 2)
        self.assertEqual({body["px"] for body in writes}, {"101000", "102000"})
        self.assertTrue(e._demo_state()["tiers"]["1"]["order_id"])
        self.assertTrue(e._demo_state()["tiers"]["2"]["order_id"])

    def test_opposite_tier_is_rejected(self):
        e, _x = self.make_engine()
        e.arm()
        e.publish_ai_plan(self.plan(tier=1, direction="long", price=95000))
        result = e.publish_ai_plan(self.plan(tier=2, direction="short", price=105000))
        self.assertEqual(result["status"], "AUTO_REJECTED")
        self.assertIn("同方向", result["error"])

    def test_exchange_fill_not_local_price_marks_filled(self):
        e, x = self.make_engine()
        e.arm()
        e.publish_ai_plan(self.plan(price=95000))
        tier = e._demo_state()["tiers"]["1"]
        oid = tier["order_id"]

        # Even if ticker crosses, KAYTRADE does not locally fill the order.
        x.last = 94000
        e.cycle()
        self.assertEqual(tier["status"], "live")
        self.assertEqual(tier["filled_size"], 0.0)

        # Only the OKX order state changes the fill state.
        x.fill_order(oid, price=94950, size=1)
        e.cycle()
        self.assertEqual(tier["status"], "filled")
        self.assertEqual(tier["filled_size"], 1.0)
        self.assertEqual(tier["entry_price"], 94950.0)

    def test_60_minute_expiry_submits_cancel_to_okx(self):
        e, x = self.make_engine()
        e.arm()
        e.publish_ai_plan(self.plan(price=95000))
        tier = e._demo_state()["tiers"]["1"]
        tier["cancel_deadline"] = time.time() - 1
        e.store.save()
        e.cycle()
        cancels = [body for path, body in x.posts if path == "/api/v5/trade/cancel-order"]
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0]["ordId"], tier["order_id"])
        self.assertEqual(tier["order_state"], "cancel_requested")

    def test_changed_unfilled_ai_plan_uses_okx_amend_not_second_entry(self):
        e, x = self.make_engine()
        e.arm()
        first = self.plan(tier=1, direction="long", price=95000, plan_id="same-tier")
        e.publish_ai_plan(first)
        second = dict(first)
        second["limit_price"] = 95500
        second["suggested_entry"] = 95500
        second["take_profit"] = 97500
        second["stop_loss"] = 94500
        second["plan_id"] = "same-tier-new"
        result = e.publish_ai_plan(second)
        entries = [body for path, body in x.posts if path == "/api/v5/trade/order"]
        amends = [body for path, body in x.posts if path == "/api/v5/trade/amend-order"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(amends), 1)
        self.assertEqual(amends[0]["newPx"], "95500")
        self.assertEqual(result["status"], "OKX_SUBMITTED")

    def test_auto_execution_off_does_not_write_okx(self):
        e, x = self.make_engine()
        e.arm()
        e.set_auto_execute_plans(False)
        before = len([1 for path, _ in x.posts if path == "/api/v5/trade/order"])
        result = e.publish_ai_plan(self.plan(price=95000))
        after = len([1 for path, _ in x.posts if path == "/api/v5/trade/order"])
        self.assertEqual(result["auto_execution"], "OFF")
        self.assertEqual(before, after)

    def test_market_entry_is_rejected(self):
        e, _x = self.make_engine()
        e.arm()
        proposal = self.plan(price=95000)
        proposal["order_type"] = "market"
        result = e.publish_ai_plan(proposal)
        self.assertEqual(result["status"], "AUTO_REJECTED")
        self.assertIn("LIMIT ONLY", result["error"])

    def test_state_reports_exchange_backed_demo_mode(self):
        e, _x = self.make_engine()
        e.arm()
        state = e.ai_state()
        self.assertEqual(state["mode"], "OKX_DEMO_EXECUTION")
        self.assertTrue(state["demo_exchange_writes"])
        self.assertFalse(state["live_ai_writes"])
        self.assertTrue(state["limit_only"])
        self.assertNotIn("paper_execution", state)


if __name__ == "__main__":
    unittest.main()

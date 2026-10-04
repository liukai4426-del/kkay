import tempfile
import time
import unittest
from pathlib import Path

from test_v172_ai_only import FakeExchange
import v180_ai_only_patch as v180


class V180AIOnlyTests(unittest.TestCase):
    def make_engine(self, demo=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        x = FakeExchange(demo=demo)
        e = v180.AIOnlyEngineV180(x, Path(temp.name), lambda *_: None)
        e.connect()
        return e, x

    def test_identity(self):
        self.assertEqual(v180.VERSION, "1.8.0")
        self.assertEqual(v180.BUILD, "1801")
        self.assertTrue(v180.AI_ONLY)
        self.assertIs(v180.app.Engine, v180.AIOnlyEngineV180)
        self.assertEqual(v180.AI_MAX_LEVERAGE, 20)
        self.assertEqual(str(v180.AI_MAX_NOTIONAL_USDT), "3500")
        self.assertEqual(str(v180.AI_MAX_ESTIMATED_STOP_LOSS_USDT), "100")

    def test_market_entry_uses_market_order(self):
        e, x = self.make_engine()
        e.arm()
        result = e.submit_ai_trade(
            {
                "proposal_id": "v180-market-1",
                "action": "open",
                "direction": "long",
                "order_type": "market",
                "size": 1,
                "leverage": 5,
                "take_profit": 102000,
                "stop_loss": 99000,
                "reason": "market test",
            }
        )
        self.assertEqual(result["order_type"], "market")
        order = [body for path, body in x.posts if path == "/api/v5/trade/order"][0]
        self.assertEqual(order["ordType"], "market")
        self.assertNotIn("px", order)

    def test_limit_entry_uses_limit_price(self):
        e, x = self.make_engine()
        e.arm()
        result = e.submit_ai_trade(
            {
                "proposal_id": "v180-limit-1",
                "action": "open",
                "direction": "long",
                "order_type": "limit",
                "limit_price": 99500,
                "size": 1,
                "leverage": 5,
                "take_profit": 102000,
                "stop_loss": 98500,
                "reason": "limit test",
            }
        )
        self.assertEqual(result["order_type"], "limit")
        self.assertEqual(result["limit_price"], "99500")
        order = [body for path, body in x.posts if path == "/api/v5/trade/order"][0]
        self.assertEqual(order["ordType"], "limit")
        self.assertEqual(order["px"], "99500")
        self.assertEqual(e.store.data["active"]["order_type"], "limit")
        self.assertEqual(e.store.data["active"]["requested_entry"], "99500")

    def test_limit_requires_price(self):
        e, _x = self.make_engine()
        e.arm()
        with self.assertRaisesRegex(Exception, "limit_price"):
            e._validate_open(
                {
                    "action": "open",
                    "direction": "long",
                    "order_type": "limit",
                    "size": 1,
                    "leverage": 5,
                    "take_profit": 102000,
                    "stop_loss": 98500,
                }
            )

    def test_limit_price_must_follow_tick(self):
        e, _x = self.make_engine()
        e.arm()
        with self.assertRaisesRegex(Exception, "tickSz"):
            e._validate_open(
                {
                    "action": "open",
                    "direction": "long",
                    "order_type": "limit",
                    "limit_price": 99500.05,
                    "size": 1,
                    "leverage": 5,
                    "take_profit": 102000,
                    "stop_loss": 98500,
                }
            )

    def test_live_limit_order_does_not_use_market_timeout(self):
        e, x = self.make_engine()
        e.arm()
        e.submit_ai_trade(
            {
                "proposal_id": "v180-limit-live",
                "action": "open",
                "direction": "short",
                "order_type": "limit",
                "limit_price": 100500,
                "size": 1,
                "leverage": 5,
                "take_profit": 99000,
                "stop_loss": 101500,
                "reason": "pending limit",
            }
        )
        active = e.store.data["active"]
        active["submitted"] = time.time() - 3600
        e.store.save()
        x._parent = {
            "state": "live",
            "accFillSz": "0",
            "ordId": active["order_id"],
            "clOrdId": active["client_id"],
        }
        e.reconcile()
        self.assertEqual(e.store.data["active"]["order_state"], "live")
        self.assertFalse(e.store.data["active"]["filled"])

    def test_plan_records_order_type_and_limit_price(self):
        e, _x = self.make_engine()
        result = e.publish_ai_plan(
            {
                "tier": 2,
                "direction": "long",
                "order_type": "limit",
                "limit_price": 99200,
                "take_profit": 102000,
                "stop_loss": 98200,
                "size": 2,
                "leverage": 10,
                "reason": "tier two",
                "operation_advice": "等待价格回落到限价位",
            }
        )
        plan = result["plan"]
        self.assertEqual(plan["order_type"], "limit")
        self.assertEqual(plan["limit_price"], 99200)
        self.assertEqual(plan["suggested_entry"], 99200)

    def test_state_advertises_both_entry_types(self):
        e, _x = self.make_engine()
        state = e.ai_state()
        self.assertEqual(state["version"], "1.8.0")
        self.assertEqual(state["supported_entry_order_types"], ["market", "limit"])


if __name__ == "__main__":
    unittest.main()

import tempfile
import time
import unittest
from pathlib import Path

from test_v172_ai_only import FakeExchange
import v181_paper_patch as v181


class MutableFakeExchange(FakeExchange):
    def __init__(self, demo=True):
        super().__init__(demo=demo)
        self.last = 100000.0

    def ticker(self):
        return {
            "last": str(self.last),
            "bidPx": str(self.last - 1),
            "askPx": str(self.last + 1),
            "ts": "1",
        }


class V181PaperTests(unittest.TestCase):
    def make_engine(self, demo=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        x = MutableFakeExchange(demo=demo)
        e = v181.PaperEngineV181(x, Path(temp.name), lambda *_: None)
        e.connect()
        e.arm()
        x.posts.clear()
        return e, x

    def market_open(self, e, tier=1, size=2, direction="long", **extra):
        payload = {
            "proposal_id": f"market-{tier}-{time.time_ns()}",
            "action": "open",
            "tier": tier,
            "direction": direction,
            "order_type": "market",
            "size": size,
            "leverage": 5,
            "take_profit": 102000 if direction == "long" else 98000,
            "stop_loss": 98000 if direction == "long" else 102000,
            "reason": "test",
        }
        payload.update(extra)
        return e.submit_ai_trade(payload)

    def limit_open(self, e, tier, price, direction="long", **extra):
        payload = {
            "proposal_id": f"limit-{tier}-{time.time_ns()}",
            "action": "open",
            "tier": tier,
            "direction": direction,
            "order_type": "limit",
            "limit_price": price,
            "size": 1,
            "leverage": 5,
            "take_profit": 103000 if direction == "long" else 97000,
            "stop_loss": 97000 if direction == "long" else 103000,
            "reason": "test",
        }
        payload.update(extra)
        return e.submit_ai_trade(payload)

    def test_identity_and_paper_only(self):
        self.assertEqual(v181.VERSION, "1.8.1")
        self.assertEqual(v181.BUILD, "1810")
        self.assertTrue(v181.PAPER_ONLY)
        self.assertIs(v181.app.Engine, v181.PaperEngineV181)

    def test_market_open_never_writes_exchange(self):
        e, x = self.make_engine()
        result = self.market_open(e, tier=1, size=2)
        self.assertEqual(result["status"], "FILLED")
        self.assertEqual(x.posts, [])
        tier = e.store.data["paper_execution"]["tiers"]["1"]
        self.assertEqual(tier["filled_size"], 2.0)
        self.assertEqual(tier["entry_price"], 100000.0)

    def test_two_same_direction_tiers_can_be_live_together(self):
        e, x = self.make_engine()
        self.limit_open(e, 1, 99000, direction="long")
        self.limit_open(e, 2, 98000, direction="long")
        tiers = e.store.data["paper_execution"]["tiers"]
        self.assertEqual(tiers["1"]["status"], "live")
        self.assertEqual(tiers["2"]["status"], "live")
        self.assertEqual(x.posts, [])

    def test_opposite_direction_second_tier_is_rejected(self):
        e, _x = self.make_engine()
        self.limit_open(e, 1, 99000, direction="long")
        with self.assertRaisesRegex(Exception, "仅允许同方向"):
            self.limit_open(e, 2, 101000, direction="short")

    def test_60_minute_auto_cancel(self):
        e, x = self.make_engine()
        self.limit_open(e, 1, 99000, direction="long")
        tier = e.store.data["paper_execution"]["tiers"]["1"]
        tier["cancel_deadline"] = time.time() - 1
        e.store.save()
        x.last = 100000
        e.cycle()
        self.assertEqual(tier["status"], "canceled")
        self.assertEqual(tier["order_state"], "auto_canceled_60m")
        self.assertEqual(x.posts, [])

    def test_amend_entry_keeps_original_deadline(self):
        e, _x = self.make_engine()
        self.limit_open(e, 1, 99000)
        tier = e.store.data["paper_execution"]["tiers"]["1"]
        deadline = tier["cancel_deadline"]
        result = e.amend_paper_entry({"tier": 1, "new_price": 98500, "new_size": 2})
        self.assertEqual(result["limit_price"], 98500.0)
        self.assertEqual(result["size"], 2.0)
        self.assertEqual(tier["cancel_deadline"], deadline)

    def test_amend_protection_to_limit_and_fill_after_trigger(self):
        e, x = self.make_engine()
        self.market_open(e, tier=1, size=1)
        e.amend_paper_protection(
            {
                "tier": 1,
                "take_profit": 101000,
                "stop_loss": 98000,
                "tp_exit_type": "limit",
                "tp_limit_price": 101100,
                "sl_exit_type": "limit",
                "sl_limit_price": 97900,
            }
        )
        tier = e.store.data["paper_execution"]["tiers"]["1"]
        x.last = 101000
        e.cycle()
        self.assertEqual(tier["status"], "exit_live")
        self.assertEqual(tier["exit_order"]["limit_price"], 101100.0)
        x.last = 101100
        e.cycle()
        self.assertEqual(tier["status"], "closed")
        self.assertEqual(tier["last_exit_reason"], "TP")
        self.assertEqual(x.posts, [])

    def test_market_partial_close_by_size(self):
        e, x = self.make_engine()
        self.market_open(e, tier=1, size=2)
        result = e.paper_close_position(
            {"tier": 1, "size": 1, "order_type": "market", "reason": "减一半"}
        )
        tier = e.store.data["paper_execution"]["tiers"]["1"]
        self.assertEqual(result["status"], "filled")
        self.assertEqual(tier["closed_size"], 1.0)
        self.assertEqual(e._position_remaining(tier), 1.0)
        self.assertEqual(x.posts, [])

    def test_limit_partial_close_waits_then_fills(self):
        e, x = self.make_engine()
        self.market_open(e, tier=1, size=2)
        result = e.paper_close_position(
            {
                "tier": 1,
                "size": 1,
                "order_type": "limit",
                "limit_price": 101000,
                "reason": "限价减仓",
            }
        )
        self.assertEqual(result["status"], "live")
        tier = e.store.data["paper_execution"]["tiers"]["1"]
        self.assertEqual(e._position_remaining(tier), 2.0)
        x.last = 101000
        e.cycle()
        self.assertEqual(e._position_remaining(tier), 1.0)
        self.assertEqual(result["status"], "filled")

    def test_cancel_entry_command(self):
        e, _x = self.make_engine()
        self.limit_open(e, 1, 99000)
        result = e.cancel_paper_entry({"tier": 1})
        self.assertEqual(result["status"], "CANCELED")
        self.assertEqual(e.store.data["paper_execution"]["tiers"]["1"]["status"], "canceled")

    def test_state_advertises_all_v181_capabilities(self):
        e, _x = self.make_engine()
        state = e.ai_state()
        self.assertTrue(state["paper_only"])
        self.assertFalse(state["live_ai_writes"])
        self.assertEqual(state["supported_entry_order_types"], ["market", "limit"])
        self.assertEqual(state["supported_close_order_types"], ["market", "limit"])
        self.assertTrue(state["supports_partial_close"])
        self.assertTrue(state["supports_amend_entry"])
        self.assertTrue(state["supports_cancel_entry"])
        self.assertTrue(state["supports_amend_protection"])
        self.assertTrue(state["supports_two_same_direction_tiers"])
        self.assertEqual(state["entry_auto_cancel_seconds"], 3600)


if __name__ == "__main__":
    unittest.main()

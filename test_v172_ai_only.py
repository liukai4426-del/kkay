import tempfile
import unittest
from pathlib import Path

import v172_ai_only_patch as v172


class FakeExchange:
    def __init__(self, demo=True):
        self.demo = demo
        self.host = "www.okx.com"
        self.posts = []
        self._positions = []
        self._orders = []
        self._algos = []
        self._parent = None
        self._lever_pos = "long"
        self._lever = "5"

    def sync_time(self):
        return None

    def account(self):
        return {"uid": "demo-user", "posMode": "long_short_mode", "perm": "read_only,trade"}

    def balance(self):
        return 1000.0, 1000.0

    def positions(self):
        return list(self._positions)

    def orders(self):
        return list(self._orders)

    def algos(self):
        return list(self._algos)

    def candles(self, _bar):
        raise AssertionError("AI Only must never request strategy candles")

    def ticker(self):
        return {"last": "100000", "bidPx": "99999", "askPx": "100001", "ts": "1"}

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
            return [{"posSide": self._lever_pos, "lever": self._lever, "mgnMode": "isolated"}]
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, body):
        self.posts.append((path, dict(body)))
        if path == "/api/v5/account/set-leverage":
            self._lever_pos = body["posSide"]
            self._lever = body["lever"]
            return [{}]
        if path == "/api/v5/trade/order":
            row = {"ordId": "123456", "clOrdId": body.get("clOrdId", "")}
            self._parent = {
                "state": "filled",
                "accFillSz": body.get("sz", "0"),
                "ordId": row["ordId"],
                "clOrdId": row["clOrdId"],
            }
            return [row]
        raise AssertionError(f"unexpected POST {path}")

    def order(self, client_id="", order_id=""):
        if self._parent is None:
            raise AssertionError("no parent order")
        return dict(self._parent)

    def recent_orders(self):
        return []

    def fills(self):
        return []


class V172AIOnlyTests(unittest.TestCase):
    def make_engine(self, demo=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        x = FakeExchange(demo=demo)
        e = v172.AIOnlyEngine(x, Path(temp.name), lambda *_: None)
        e.connect()
        return e, x

    def test_identity_and_strategy_disabled(self):
        self.assertEqual(v172.VERSION, "1.7.2")
        self.assertEqual(v172.BUILD, "1722")
        self.assertTrue(v172.AI_ONLY)
        self.assertFalse(v172.model.STRATEGY_ENABLED)
        self.assertIs(v172.app.Engine, v172.AIOnlyEngine)

    def test_cycle_never_calls_strategy_candles(self):
        e, _x = self.make_engine()
        e.arm()
        e.cycle()
        self.assertTrue(e.enabled)

    def test_live_ai_writes_are_hard_blocked(self):
        e, _x = self.make_engine(demo=False)
        with self.assertRaisesRegex(Exception, "真实账户写入已硬禁用"):
            e.arm()

    def test_build1720_stop_atr_lock_is_migrated_without_legacy_settings(self):
        e, _x = self.make_engine()
        e.store.data["halt"] = "'dict' object has no attribute 'stop_atr'"
        e.store.save()
        e.arm({"mode": "AI_ONLY"})
        self.assertTrue(e.enabled)
        self.assertEqual(e.store.data.get("halt"), "")
        self.assertIsInstance(e.settings, v172.AIOnlySettings)

    def test_gui_activation_bypasses_legacy_arm_queue(self):
        import inspect
        source = inspect.getsource(v172._app_arm_v172)
        self.assertIn("_start_ai_enable(self)", source)
        self.assertNotIn('self.submit("arm"', source)
        runner = inspect.getsource(v172._run_ai_enable)
        self.assertIn("_ensure_ai_only_engine(owner)", runner)

    def test_ai_open_is_only_new_order_path(self):
        e, x = self.make_engine()
        e.arm()
        result = e.submit_ai_trade(
            {
                "proposal_id": "proposal-001",
                "action": "open",
                "direction": "long",
                "order_type": "market",
                "size": 1,
                "leverage": 5,
                "take_profit": 102000,
                "stop_loss": 99000,
                "reason": "unit test",
            }
        )
        self.assertEqual(result["status"], "EXECUTED")
        writes = [body for path, body in x.posts if path == "/api/v5/trade/order"]
        self.assertEqual(len(writes), 1)
        order = writes[0]
        self.assertEqual(order["ordType"], "market")
        self.assertEqual(order["posSide"], "long")
        self.assertEqual(len(order["attachAlgoOrds"]), 2)
        self.assertTrue(any("tpTriggerPx" in row for row in order["attachAlgoOrds"]))
        self.assertTrue(any("slTriggerPx" in row for row in order["attachAlgoOrds"]))
        self.assertTrue(e.store.data["active"]["ai_only"])

    def test_duplicate_proposal_is_idempotent(self):
        e, x = self.make_engine()
        e.arm()
        proposal = {
            "proposal_id": "same-request",
            "action": "open",
            "direction": "short",
            "size": 1,
            "take_profit": 98000,
            "stop_loss": 101000,
            "reason": "idempotency",
        }
        first = e.submit_ai_trade(proposal)
        second = e.submit_ai_trade(proposal)
        self.assertFalse(first.get("duplicate", False))
        self.assertTrue(second["duplicate"])
        writes = [1 for path, _body in x.posts if path == "/api/v5/trade/order"]
        self.assertEqual(len(writes), 1)


if __name__ == "__main__":
    unittest.main()

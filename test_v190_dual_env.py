import tempfile
import unittest
from pathlib import Path

from test_v183_okx_demo import DemoExchange
import v190_dual_env_patch as v190


class V190DualEnvironmentTests(unittest.TestCase):
    def make_engine(self, folder, demo):
        x = DemoExchange(demo=demo)
        e = v190.OKXDualExecutionEngineV190(x, Path(folder), lambda *_: None)
        e.connect()
        return e, x

    def plan(self, price=95000, direction="long", tier=1):
        return {
            "plan_id": f"v190-{direction}-{tier}-{price}",
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
            "reason": "v190 unit test",
            "operation_advice": "program submits exchange LIMIT order",
        }

    def test_demo_and_live_use_different_store_files(self):
        with tempfile.TemporaryDirectory() as folder:
            demo, _ = self.make_engine(folder, True)
            live, _ = self.make_engine(folder, False)
            self.assertNotEqual(demo.store.path, live.store.path)
            self.assertIn("okx_demo_execution", demo.store.data)
            self.assertIn("okx_live_execution", live.store.data)

    def test_demo_defaults_auto_on_live_defaults_auto_off(self):
        with tempfile.TemporaryDirectory() as folder:
            demo, _ = self.make_engine(folder, True)
            live, _ = self.make_engine(folder, False)
            self.assertTrue(demo._demo_state()["auto_execute_plans"])
            self.assertFalse(live._demo_state()["auto_execute_plans"])

    def test_live_requires_explicit_session_authorization(self):
        with tempfile.TemporaryDirectory() as folder:
            live, _ = self.make_engine(folder, False)
            with self.assertRaisesRegex(Exception, "LIVE写权限未授权"):
                live.arm()
            live.authorize_live_session("LIVE")
            live.arm()
            self.assertTrue(live.enabled)
            self.assertFalse(live._demo_state()["auto_execute_plans"])

    def test_live_plan_does_not_write_until_auto_execution_is_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            live, x = self.make_engine(folder, False)
            live.authorize_live_session("LIVE")
            live.arm()
            result = live.publish_ai_plan(self.plan())
            self.assertEqual(result["auto_execution"], "OFF")
            self.assertEqual(
                len([1 for path, _ in x.posts if path == "/api/v5/trade/order"]), 0
            )
            live.set_auto_execute_plans(True)
            result = live.publish_ai_plan(self.plan(price=95100))
            self.assertEqual(result["status"], "OKX_SUBMITTED")
            self.assertEqual(result["environment"], "OKX_LIVE")
            writes = [body for path, body in x.posts if path == "/api/v5/trade/order"]
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0]["ordType"], "limit")
            attached = writes[0]["attachAlgoOrds"]
            self.assertTrue(all(str(row.get("tpOrdPx", row.get("slOrdPx", ""))) != "-1" for row in attached))
            tier = live._demo_state()["tiers"]["1"]
            self.assertFalse(tier["okx_demo"])
            self.assertEqual(tier["environment"], "OKX_LIVE")

    def test_live_stop_forces_auto_execution_off(self):
        with tempfile.TemporaryDirectory() as folder:
            live, _ = self.make_engine(folder, False)
            live.authorize_live_session("LIVE")
            live.arm()
            live.set_auto_execute_plans(True)
            self.assertTrue(live._demo_state()["auto_execute_plans"])
            live.stop()
            self.assertFalse(live._demo_state()["auto_execute_plans"])

    def test_reconnect_live_resets_auto_execution_off(self):
        with tempfile.TemporaryDirectory() as folder:
            live, _ = self.make_engine(folder, False)
            live.authorize_live_session("LIVE")
            live.arm()
            live.set_auto_execute_plans(True)
            self.assertTrue(live._demo_state()["auto_execute_plans"])
            live2, _ = self.make_engine(folder, False)
            self.assertFalse(live2._demo_state()["auto_execute_plans"])
            self.assertFalse(live2._live_session_authorized)

    def test_ai_cannot_choose_or_override_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            demo, _ = self.make_engine(folder, True)
            demo.arm()
            payload = self.plan()
            payload["environment"] = "OKX_LIVE"
            with self.assertRaisesRegex(Exception, "不得指定 Demo/Live"):
                demo.publish_ai_plan(payload)
            self.assertTrue(demo.x.demo)

    def test_demo_behavior_stays_immediate_limit_submission(self):
        with tempfile.TemporaryDirectory() as folder:
            demo, x = self.make_engine(folder, True)
            demo.arm()
            x.last = 100000
            result = demo.publish_ai_plan(self.plan(price=95000))
            self.assertEqual(result["status"], "OKX_SUBMITTED")
            self.assertEqual(result["environment"], "OKX_DEMO")
            writes = [body for path, body in x.posts if path == "/api/v5/trade/order"]
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0]["px"], "95000")

    def test_state_reports_current_environment_only(self):
        with tempfile.TemporaryDirectory() as folder:
            live, _ = self.make_engine(folder, False)
            state = live.ai_state()
            self.assertEqual(state["version"], "1.9.0")
            self.assertEqual(state["build"], "1900")
            self.assertEqual(state["mode"], "OKX_LIVE_EXECUTION")
            self.assertEqual(state["active_environment"], "LIVE")
            self.assertFalse(state["auto_execute_plans"])
            self.assertFalse(state["demo_exchange_writes"])
            self.assertFalse(state["live_ai_writes"])
            self.assertTrue(state["limit_only"])


if __name__ == "__main__":
    unittest.main()

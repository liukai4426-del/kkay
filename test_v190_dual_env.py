import tempfile
import threading
import unittest
from pathlib import Path

import v190_dual_env_patch as v190
from test_v183_okx_demo import DemoExchange


class V190DualEnvironmentTests(unittest.TestCase):
    def make_demo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        x = DemoExchange(demo=True)
        e = v190.DemoEngineV190(
            x, Path(temp.name) / "demo", lambda *_: None
        )
        e.connect()
        return e, x, Path(temp.name)

    def make_live(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        raw = DemoExchange(demo=False)
        x = v190.LiveReadOnlyExchange(raw)
        e = v190.LiveReviewEngineV190(
            x, Path(temp.name) / "live", lambda *_: None
        )
        e.connect()
        return e, raw, Path(temp.name)

    def plan(self, environment, tier=1, direction="long", price=95000):
        return {
            "environment": environment,
            "plan_id": f"{environment}-plan-{tier}",
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
            "tp_limit_price": -1,
            "sl_limit_price": -1,
            "reason": "dual env unit test",
            "operation_advice": "test",
        }

    def test_identity_and_modes(self):
        self.assertEqual(v190.VERSION, "1.9.0")
        self.assertEqual(v190.BUILD, "1900")
        self.assertEqual(v190.ENV_META["demo"]["mode"], "OKX_DEMO_EXECUTION")
        self.assertEqual(v190.ENV_META["live"]["mode"], "OKX_LIVE_REVIEW")
        self.assertFalse(v190.PAPER_RUNTIME_PRESENT)

    def test_demo_keeps_immediate_auto_execution(self):
        e, x, _ = self.make_demo()
        e.arm()
        e.set_auto_execute_plans(True)
        result = e.publish_ai_plan(self.plan("demo", price=95000))
        self.assertEqual(result["environment"], "demo")
        self.assertEqual(result["mode"], "OKX_DEMO_EXECUTION")
        writes = [body for path, body in x.posts if path == "/api/v5/trade/order"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["ordType"], "limit")
        self.assertEqual(writes[0]["px"], "95000")
        attached = writes[0]["attachAlgoOrds"]
        self.assertTrue(all(str(row.get("tpOrdPx", row.get("slOrdPx", "1"))) != "-1" for row in attached))

    def test_demo_rejects_live_environment_payload(self):
        e, _x, _ = self.make_demo()
        e.arm()
        with self.assertRaisesRegex(Exception, "Environment Match Gate"):
            e.publish_ai_plan(self.plan("live"))

    def test_live_channel_off_records_recommendation_only(self):
        e, raw, _ = self.make_live()
        result = e.publish_ai_plan(self.plan("live"))
        self.assertEqual(result["status"], "RECOMMENDED_ONLY")
        self.assertFalse(result["live_ai_writes"])
        self.assertTrue(result["manual_execution_required"])
        self.assertEqual(raw.posts, [])

    def test_live_channel_on_stages_manual_execution_only(self):
        e, raw, _ = self.make_live()
        e.set_review_enabled(True)
        result = e.publish_ai_plan(self.plan("live"))
        self.assertEqual(result["status"], "READY_FOR_MANUAL_EXECUTION")
        self.assertTrue(e.enabled)
        self.assertEqual(raw.posts, [])
        state = e.ai_state()
        self.assertEqual(state["mode"], "OKX_LIVE_REVIEW")
        self.assertFalse(state["live_ai_writes"])
        self.assertTrue(state["manual_execution_required"])
        self.assertTrue(state["review_state"]["channel_enabled"])

    def test_live_state_poll_and_cycle_can_persist_concurrently(self):
        e, _raw, _ = self.make_live()
        e.set_review_enabled(True)
        errors=[]
        barrier=threading.Barrier(2)

        def poll_state():
            try:
                barrier.wait()
                for _ in range(40):
                    e.ai_state()
            except Exception as exc:
                errors.append(exc)

        def run_cycle():
            try:
                barrier.wait()
                for _ in range(40):
                    e.cycle()
            except Exception as exc:
                errors.append(exc)

        threads=[threading.Thread(target=poll_state),threading.Thread(target=run_cycle)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors,[])
        self.assertTrue(e.store.path.exists())
        self.assertEqual(list(e.store.path.parent.glob('.*.tmp')),[])

    def test_live_submit_trade_is_blocked(self):
        e, raw, _ = self.make_live()
        e.set_review_enabled(True)
        with self.assertRaisesRegex(Exception, "不提供自动实盘写入"):
            e.submit_ai_trade(self.plan("live"))
        self.assertEqual(raw.posts, [])

    def test_live_readonly_exchange_blocks_post(self):
        raw = DemoExchange(demo=False)
        x = v190.LiveReadOnlyExchange(raw)
        with self.assertRaisesRegex(Exception, "禁止自动写入真实账户"):
            x.post("/api/v5/trade/order", {"ordType": "limit"})
        self.assertEqual(raw.posts, [])

    def test_live_rejects_demo_environment_payload(self):
        e, raw, _ = self.make_live()
        e.set_review_enabled(True)
        plan = self.plan("demo")
        with self.assertRaisesRegex(Exception, "environment=live"):
            e.publish_ai_plan(plan)
        self.assertEqual(raw.posts, [])

    def test_live_minus_one_protection_is_normalized_but_never_sent(self):
        e, raw, _ = self.make_live()
        e.set_review_enabled(True)
        result = e.publish_ai_plan(self.plan("live"))
        plan = result["plan"]
        self.assertEqual(plan["tp_limit_price"], plan["take_profit"])
        self.assertEqual(plan["sl_limit_price"], plan["stop_loss"])
        self.assertGreater(plan["tp_limit_price"], 0)
        self.assertGreater(plan["sl_limit_price"], 0)
        self.assertEqual(raw.posts, [])

    def test_live_two_tiers_must_share_direction(self):
        e, raw, _ = self.make_live()
        e.set_review_enabled(True)
        first = e.publish_ai_plan(self.plan("live", tier=1, direction="long", price=95000))
        self.assertEqual(first["status"], "READY_FOR_MANUAL_EXECUTION")
        with self.assertRaisesRegex(Exception, "同方向"):
            e.publish_ai_plan(self.plan("live", tier=2, direction="short", price=105000))
        self.assertEqual(raw.posts, [])

    def test_demo_and_live_store_paths_are_isolated(self):
        demo_temp = tempfile.TemporaryDirectory()
        live_temp = tempfile.TemporaryDirectory()
        self.addCleanup(demo_temp.cleanup)
        self.addCleanup(live_temp.cleanup)
        demo = v190.DemoEngineV190(
            DemoExchange(demo=True), Path(demo_temp.name) / "demo", lambda *_: None
        )
        live = v190.LiveReviewEngineV190(
            v190.LiveReadOnlyExchange(DemoExchange(demo=False)),
            Path(live_temp.name) / "live",
            lambda *_: None,
        )
        demo.connect()
        live.connect()
        self.assertNotEqual(demo.store.path, live.store.path)
        self.assertIn("demo", str(demo.store.path))
        self.assertIn("live", str(live.store.path))

    def test_bridge_tokens_and_descriptor_names_are_isolated(self):
        class Owner:
            def __init__(self, folder):
                self.folder = folder
                self._v190_engines = {"demo": None, "live": None}
            def emit(self, *_args):
                pass

        with tempfile.TemporaryDirectory() as temp:
            owner = Owner(Path(temp))
            demo = v190.DualBridgeServer(owner, "demo")
            live = v190.DualBridgeServer(owner, "live")
            self.assertNotEqual(demo.token, live.token)
            self.assertNotEqual(demo.path, live.path)
            self.assertTrue(str(demo.path).endswith("ai_bridge_demo.json"))
            self.assertTrue(str(live.path).endswith("ai_bridge_live.json"))


if __name__ == "__main__":
    unittest.main()

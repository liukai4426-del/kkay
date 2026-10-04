import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from exchange import APIError, _assert_limit_only_write
import v190_dual_env_patch as v191
from test_v183_okx_demo import DemoExchange


class V191LiveExecutionTests(unittest.TestCase):
    def plan(self, environment, tier=1, direction="long", price=95000):
        return {
            "environment": environment,
            "plan_id": f"{environment}-v191-{tier}",
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
            "tp_exit_type": "market",
            "sl_exit_type": "market",
            "tp_limit_price": -1,
            "sl_limit_price": -1,
            "reason": "v191 execution regression",
            "operation_advice": "test",
        }

    def make_demo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        raw = DemoExchange(demo=True)
        engine = v191.DemoEngineV190(
            raw, Path(temp.name) / "demo", lambda *_: None
        )
        engine.connect()
        return engine, raw, Path(temp.name)

    def make_live(self, exchange=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        raw = exchange or DemoExchange(demo=False)
        guarded = v191.LiveReadOnlyExchange(raw)
        engine = v191.LiveReviewEngineV190(
            guarded, Path(temp.name) / "live", lambda *_: None
        )
        engine.connect()
        return engine, guarded, raw, Path(temp.name)

    def order_writes(self, raw):
        return [body for path, body in raw.posts if path == "/api/v5/trade/order"]

    def test_version_and_modes(self):
        self.assertEqual(v191.VERSION, "1.9.1")
        self.assertEqual(v191.BUILD, "1911")
        self.assertEqual(v191.ENV_META["demo"]["mode"], "OKX_DEMO_EXECUTION")
        self.assertEqual(v191.ENV_META["live"]["mode"], "OKX_LIVE_EXECUTION")
        self.assertFalse(v191.LIMIT_ONLY)
        self.assertFalse(v191.PAPER_RUNTIME_PRESENT)

    def test_transport_allows_trigger_market_protection_but_not_market_parent(self):
        body = {
            "instId": "BTC-USDT-SWAP",
            "ordType": "limit",
            "px": "95000",
            "attachAlgoOrds": [
                {"tpTriggerPx": "97000", "tpOrdPx": "-1"},
                {"slTriggerPx": "94000", "slOrdPx": "-1"},
            ],
        }
        _assert_limit_only_write("/api/v5/trade/order", body)
        with self.assertRaisesRegex(APIError, "主订单"):
            _assert_limit_only_write(
                "/api/v5/trade/order",
                {"instId": "BTC-USDT-SWAP", "ordType": "market", "sz": "1"},
            )

    def test_demo_entry_is_limit_with_trigger_market_tp_sl(self):
        engine, raw, _ = self.make_demo()
        engine.arm()
        engine.set_auto_execute_plans(True)
        result = engine.publish_ai_plan(self.plan("demo"))
        self.assertIn(result["status"], ("OKX_SUBMITTED", "SUBMITTED_TO_OKX"))
        writes = self.order_writes(raw)
        self.assertEqual(len(writes), 1)
        body = writes[0]
        self.assertEqual(body["ordType"], "limit")
        attached = body["attachAlgoOrds"]
        self.assertEqual(attached[0]["tpOrdPx"], "-1")
        self.assertEqual(attached[0]["tpOrdKind"], "condition")
        self.assertEqual(attached[1]["slOrdPx"], "-1")

    def test_live_connect_is_read_only_until_explicit_arm(self):
        engine, guarded, raw, _ = self.make_live()
        self.assertFalse(engine.enabled)
        self.assertFalse(guarded.writes_enabled)
        self.assertEqual(raw.posts, [])
        with self.assertRaisesRegex(APIError, "LIVE写入锁关闭"):
            guarded.post(
                "/api/v5/trade/order",
                {"instId": "BTC-USDT-SWAP", "ordType": "limit", "px": "95000", "sz": "1"},
            )

    def test_live_arm_then_plan_submits_real_mode_limit_with_market_protection(self):
        engine, guarded, raw, _ = self.make_live()
        engine.arm()
        self.assertTrue(engine.enabled)
        self.assertTrue(guarded.writes_enabled)
        result = engine.publish_ai_plan(self.plan("live"))
        self.assertEqual(result["environment"], "live")
        self.assertEqual(result["mode"], "OKX_LIVE_EXECUTION")
        self.assertTrue(result["live_ai_writes"])
        self.assertFalse(result["manual_execution_required"])
        writes = self.order_writes(raw)
        self.assertEqual(len(writes), 1)
        body = writes[0]
        self.assertEqual(body["ordType"], "limit")
        self.assertEqual(body["attachAlgoOrds"][0]["tpOrdPx"], "-1")
        self.assertEqual(body["attachAlgoOrds"][1]["slOrdPx"], "-1")

    def test_live_stop_closes_write_gate(self):
        engine, guarded, raw, _ = self.make_live()
        engine.arm()
        engine.stop()
        self.assertFalse(engine.enabled)
        self.assertFalse(guarded.writes_enabled)
        before = len(raw.posts)
        result = engine.publish_ai_plan(self.plan("live"))
        self.assertEqual(len(raw.posts), before)
        self.assertIn(result["status"], ("RECOMMENDED", "AUTO_BLOCKED"))

    def test_live_rejects_demo_payload(self):
        engine, _guarded, raw, _ = self.make_live()
        engine.arm()
        with self.assertRaisesRegex(Exception, "Environment Match Gate"):
            engine.publish_ai_plan(self.plan("demo"))
        self.assertEqual(self.order_writes(raw), [])

    def test_live_activation_check_rejects_foreign_position_and_keeps_gate_closed(self):
        engine, guarded, raw, _ = self.make_live()
        raw._positions = [{"posSide": "long", "pos": "1", "mgnMode": "isolated"}]
        with self.assertRaisesRegex(Exception, "非KAYTRADE管理"):
            engine.activation_check()
        self.assertFalse(engine.enabled)
        self.assertFalse(guarded.writes_enabled)
        self.assertEqual(self.order_writes(raw), [])

    def test_live_toggle_surfaces_activation_error_instead_of_silent_noop(self):
        engine, guarded, raw, _ = self.make_live()
        raw._positions = [{"posSide": "long", "pos": "1", "mgnMode": "isolated"}]

        class Owner:
            _v190_selected = "live"
            root = None

            def __init__(self):
                self._v190_engines = {"demo": None, "live": engine}
                self.events = []

            def emit(self, kind, data):
                self.events.append((kind, data))

            def update_trade_button(self):
                return None

        owner = Owner()
        with (
            patch.object(v191.app.simpledialog, "askstring", return_value="LIVE AUTO"),
            patch.object(v191.app.messagebox, "showerror") as showerror,
            patch.object(v191, "_update_environment_card", lambda _owner: None),
            patch.object(v191, "_refresh_v190_dashboard", lambda _owner: None),
        ):
            v191._toggle_selected(owner)

        showerror.assert_called_once()
        message = "\n".join(str(x) for x in showerror.call_args.args)
        self.assertIn("LIVE AI自动执行开启失败", message)
        self.assertIn("非KAYTRADE管理", message)
        self.assertFalse(engine.enabled)
        self.assertFalse(guarded.writes_enabled)
        self.assertTrue(
            any(kind == "alarm" and "LIVE开启失败" in str(data) for kind, data in owner.events)
        )

    def test_live_requires_trade_permission_and_rejects_withdraw(self):
        class UnsafeExchange(DemoExchange):
            def account(self):
                data = super().account()
                data["perm"] = "read_only,trade,withdraw"
                return data

        engine, guarded, raw, _ = self.make_live(UnsafeExchange(demo=False))
        with self.assertRaisesRegex(Exception, "不得有提币权限"):
            engine.arm()
        self.assertFalse(guarded.writes_enabled)
        self.assertEqual(raw.posts, [])

    def test_demo_and_live_state_are_isolated(self):
        demo, _demo_raw, demo_root = self.make_demo()
        live, _guarded, _live_raw, live_root = self.make_live()
        self.assertIn("demo", str(demo.store.path))
        self.assertIn("live", str(live.store.path))
        self.assertNotEqual(demo.store.path, live.store.path)
        self.assertIn("okx_demo_execution", demo.store.data)
        self.assertIn("okx_live_execution", live.store.data)
        self.assertNotIn("okx_demo_execution", live.store.data)
        self.assertNotEqual(demo_root, live_root)

    def test_live_state_reports_write_gate_and_market_protection(self):
        engine, guarded, _raw, _ = self.make_live()
        state = engine.ai_state()
        self.assertFalse(state["ai_enabled"])
        self.assertTrue(state["live_ai_writes"])
        self.assertFalse(state["live_write_enabled"])
        self.assertTrue(state["limit_entry_only"])
        self.assertTrue(state["market_protection"])
        self.assertEqual(
            state["supported_protection_exit_order_types"], ["trigger_market"]
        )
        engine.arm()
        state = engine.ai_state()
        self.assertTrue(state["ai_enabled"])
        self.assertTrue(state["live_write_enabled"])
        self.assertTrue(guarded.writes_enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)

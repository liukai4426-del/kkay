import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from engine import Store
from history import summarize
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

    def test_store_save_is_atomic_under_eight_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "concurrent-state.json"
            store = Store(path)
            workers = 8
            rounds = 50
            barrier = threading.Barrier(workers)
            errors = []

            def writer():
                for _ in range(rounds):
                    barrier.wait()
                    try:
                        store.save()
                    except Exception as exc:
                        errors.append(exc)

            threads = [threading.Thread(target=writer) for _ in range(workers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            self.assertTrue(path.exists())
            self.assertIsInstance(json.loads(path.read_text()), dict)
            leftovers = [item for item in Path(folder).iterdir() if item.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_store_save_handles_concurrent_nested_dict_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mutating-state.json"
            store = Store(path)
            store.data["runtime"] = {}
            workers = 8
            rounds = 120
            barrier = threading.Barrier(workers)
            errors = []

            def writer(worker_id):
                try:
                    barrier.wait()
                    for i in range(rounds):
                        key = f"{worker_id}-{i}"
                        store.data["runtime"][key] = i
                        if i >= 3:
                            store.data["runtime"].pop(f"{worker_id}-{i-3}", None)
                        store.save()
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(worker_id,)) for worker_id in range(workers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            self.assertIsInstance(json.loads(path.read_text()), dict)
            self.assertFalse(any("dictionary changed size during iteration" in str(exc) for exc in errors))

    def test_ai_state_returns_detached_execution_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            live, _ = self.make_engine(folder, False)
            state = live.ai_state()
            state["execution_state"]["events"].append({"event": "local-copy-only"})
            current = live._demo_state()
            self.assertFalse(any(row.get("event") == "local-copy-only" for row in current["events"]))

    def test_current_environment_emits_live_balance_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            events = []
            x = DemoExchange(demo=True)
            e = v190.OKXDualExecutionEngineV190(
                x, Path(folder), lambda kind, data: events.append((kind, data))
            )
            e.connect()
            events.clear()
            e._v200_balance_emit_at = 0.0
            e.cycle()

            balances = [data for kind, data in events if kind == "balance"]
            self.assertEqual(len(balances), 1)
            self.assertEqual(balances[0]["equity"], 20000.0)
            self.assertEqual(balances[0]["available"], 20000.0)

            e.cycle()
            balances = [data for kind, data in events if kind == "balance"]
            self.assertEqual(len(balances), 1)

    def test_live_closed_round_writes_history_and_clears_managed_position(self):
        with tempfile.TemporaryDirectory() as folder:
            live, x = self.make_engine(folder, False)
            funds = {"equity": 20000.0, "available": 20000.0}
            x.balance = lambda: (funds["equity"], funds["available"])

            live.authorize_live_session("LIVE")
            live.arm()
            live.set_auto_execute_plans(True)
            result = live.publish_ai_plan(self.plan(price=95100))
            self.assertEqual(result["status"], "OKX_SUBMITTED")

            tier = live._demo_state()["tiers"]["1"]
            self.assertEqual(tier["equity_before"], 20000.0)
            x.fill_order(tier["order_id"], price=95100, size=1)
            x._positions = [{"posSide": "long", "pos": "1"}]
            live.cycle()

            tier = live._demo_state()["tiers"]["1"]
            self.assertIsInstance(live._demo_state()["history_round"], dict)
            tier["filled_at"] = time.time() - 10

            funds["equity"] = 20012.5
            funds["available"] = 20012.5
            x._positions = []
            live.cycle()

            summary = summarize(live.store.path.with_suffix(".history.jsonl"))
            self.assertEqual(summary["count"], 1)
            self.assertAlmostEqual(summary["total"], 12.5, places=6)
            self.assertEqual(summary["rows"][0]["side"], "做多")
            self.assertIsNone(live._demo_state()["history_round"])
            self.assertEqual(live._demo_state()["tiers"]["1"]["status"], "closed")
            self.assertEqual(live._demo_state()["tiers"]["1"]["position_size"], 0.0)

    def test_legacy_filled_tiers_reconcile_closed_when_okx_is_flat(self):
        with tempfile.TemporaryDirectory() as folder:
            demo, x = self.make_engine(folder, True)
            state = demo._demo_state()
            state["tiers"] = {
                "1": {
                    "tier": 1,
                    "direction": "short",
                    "side": "做空",
                    "size": 1.15,
                    "filled_size": 1.15,
                    "closed_size": 0.0,
                    "status": "filled",
                    "accepted_at": time.time() - 3600,
                    "client_order_id": "legacy-short-1",
                    "proposal_id": "legacy-short-plan-1",
                },
                "2": {
                    "tier": 2,
                    "direction": "short",
                    "side": "做空",
                    "size": 1.15,
                    "filled_size": 1.15,
                    "closed_size": 0.0,
                    "status": "filled",
                    "accepted_at": time.time() - 3500,
                    "client_order_id": "legacy-short-2",
                    "proposal_id": "legacy-short-plan-2",
                },
            }
            demo.store.save()

            self.assertEqual(x.positions(), [])
            self.assertEqual(x.orders(), [])
            self.assertEqual(x.algos(), [])

            demo._sync_v200_history_round()

            for item in demo._demo_state()["tiers"].values():
                self.assertEqual(item["closed_size"], 1.15)
                self.assertEqual(item["position_size"], 0.0)
                self.assertEqual(item["status"], "closed")
                self.assertTrue(item["legacy_flat_reconciled"])

            summary = summarize(demo.store.path.with_suffix(".history.jsonl"))
            self.assertEqual(summary["count"], 0)

            demo.arm()
            result = demo.publish_ai_plan(self.plan(price=96000, tier=1))
            self.assertEqual(result["status"], "OKX_SUBMITTED")

    def test_legacy_filled_tier_is_not_closed_while_okx_position_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            demo, x = self.make_engine(folder, True)
            state = demo._demo_state()
            state["tiers"]["1"] = {
                "tier": 1,
                "direction": "short",
                "side": "做空",
                "size": 1.15,
                "filled_size": 1.15,
                "closed_size": 0.0,
                "status": "filled",
                "accepted_at": time.time() - 3600,
                "client_order_id": "legacy-short-live",
            }
            demo.store.save()
            x._positions = [{"posSide": "short", "pos": "1.15"}]

            demo._sync_v200_history_round()

            item = demo._demo_state()["tiers"]["1"]
            self.assertEqual(item["closed_size"], 0.0)
            self.assertEqual(item["status"], "filled")
            self.assertNotIn("legacy_flat_reconciled", item)

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
            self.assertEqual(state["version"], "2.0.0")
            self.assertEqual(state["build"], "2000")
            self.assertEqual(state["mode"], "OKX_LIVE_EXECUTION")
            self.assertEqual(state["active_environment"], "LIVE")
            self.assertFalse(state["auto_execute_plans"])
            self.assertFalse(state["demo_exchange_writes"])
            self.assertFalse(state["live_ai_writes"])
            self.assertTrue(state["limit_only"])


if __name__ == "__main__":
    unittest.main()

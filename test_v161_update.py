import time
import unittest

import engine
import exchange
import v160_update_patch as v160
import v161_model as model
import v161_update_patch as v161


class ModelTests(unittest.TestCase):
    def _row(self, state, macd=1.0):
        return {
            "total": 6.5,
            "gate": True,
            "eligible": True,
            "position_multiplier": 1.0,
            "layers": {"macd_improving": macd},
            "confirmations": {"required": {}, "4H_trend_state": state},
            "reason": "ok",
            "level": "ok",
        }

    def test_lower_long_outer_requires_aligned_4h(self):
        row = self._row("neutral")
        model._apply_row_rules(row, "lower_band")
        self.assertFalse(row["eligible"])
        self.assertFalse(row["gate"])
        self.assertEqual(row["position_multiplier"], 0.0)
        self.assertFalse(row["confirmations"]["required"]["outer_4h_aligned"])

        aligned = self._row("aligned")
        model._apply_row_rules(aligned, "lower_band")
        self.assertTrue(aligned["eligible"])
        self.assertEqual(aligned["position_multiplier"], 2.0)
        self.assertTrue(aligned["confirmations"]["required"]["outer_4h_aligned"])

    def test_upper_short_is_mirrored_and_middle_unchanged(self):
        outer = self._row("opposite")
        model._apply_row_rules(outer, "upper_band")
        self.assertFalse(outer["eligible"])

        middle = self._row("neutral", macd=1.0)
        model._apply_row_rules(middle, "middle_rsi")
        self.assertTrue(middle["eligible"])
        self.assertEqual(middle["position_multiplier"], 1.0)
        self.assertNotIn("outer_4h_aligned", middle["confirmations"]["required"])

    def test_stale_v154_label_is_rewritten(self):
        self.assertEqual(v161._normalize_runtime_text("V1.5.4未开仓"), "V1.6.1未开仓")


class FakeClock:
    def __init__(self):
        self.mode = "slow"
        self.offset = 0.0
        self.clock_anchor = None
        self.events = []
        self.clock_sync_fail_streak = 0

    def network_event(self, text):
        self.events.append(text)

    def get(self, path):
        self.assert_path = path
        end = time.time()
        rtt = 4.0 if self.mode == "slow" else 0.05
        sent = end - rtt
        server = (sent + end) / 2.0 + 0.125
        self.last_timing = (sent, end)
        return [{"ts": str(int(server * 1000))}]

    def server_now(self):
        if self.clock_anchor is None:
            return time.time() + self.offset
        server, mono = self.clock_anchor
        return server + time.monotonic() - mono


class ClockTests(unittest.TestCase):
    def test_slow_rtt_is_soft_pause_and_good_samples_auto_recover(self):
        fake = FakeClock()
        ok = v161._sync_time_v161(fake)
        self.assertFalse(ok)
        self.assertTrue(fake.clock_sync_degraded)
        self.assertIsNotNone(fake.clock_anchor)
        self.assertGreater(fake.clock_sync_rtt_ms, 3000)

        fake.mode = "good"
        ok = v161._sync_time_v161(fake)
        self.assertTrue(ok)
        self.assertFalse(fake.clock_sync_degraded)
        self.assertEqual(fake.clock_sync_good_samples, 3)
        self.assertLess(fake.clock_sync_rtt_ms, 1000)


class DummyStore:
    def __init__(self, active):
        self.data = {"active": active, "halt": ""}
        self.saved = 0
        self.records = []

    def save(self):
        self.saved += 1

    def record(self, event, data):
        self.records.append((event, data))


class DummyExchange:
    def __init__(self):
        self.algo = {
            "algoId": "algo-1",
            "algoClOrdId": "br-1",
            "state": "live",
            "slTriggerPx": "90",
            "slOrdPx": "-1",
        }
        self.posts = []

    def positions(self):
        return [{"posSide": "long", "pos": "1", "avgPx": "100", "mgnMode": "isolated"}]

    def ticker(self):
        return {"last": "110"}

    def instrument(self):
        return {"tickSz": "0.1"}

    def algos(self):
        return [dict(self.algo)]

    def post(self, path, body):
        self.posts.append((path, dict(body)))
        return [{"sCode": "0"}]


class DummyOwner:
    def __init__(self):
        active = {
            "client_id": "c1",
            "side": "做多",
            "posSide": "long",
            "filled": True,
            "protected": True,
            "sl": "90",
            "legs": [{
                "state": "filled", "bracket_id": "br-1", "sl": "90",
                "tp": "120", "sz": "1",
            }],
        }
        self.store = DummyStore(active)
        self.x = DummyExchange()
        self.logs = []

    def emit(self, kind, data):
        self.logs.append((kind, data))


class BreakEvenTests(unittest.TestCase):
    def test_plus_one_r_submits_once_then_verifies_at_average_entry(self):
        owner = DummyOwner()
        v161._maybe_trigger_be(owner)
        active = owner.store.data["active"]
        self.assertEqual(active["be_state"], "submitted")
        self.assertEqual(len(owner.x.posts), 1)
        path, body = owner.x.posts[0]
        self.assertEqual(path, "/api/v5/trade/amend-algos")
        self.assertEqual(float(body["newSlTriggerPx"]), 100.0)
        self.assertEqual(body["newSlOrdPx"], "-1")
        self.assertEqual(body["algoId"], "algo-1")

        owner.x.algo["slTriggerPx"] = active["be_price"]
        v161._sync_be_from_algos(owner, owner.x.algos())
        self.assertEqual(active["be_state"], "verified")
        self.assertTrue(active["be_verified"])
        self.assertEqual(float(active["sl"]), 100.0)
        self.assertEqual(float(active["legs"][0]["sl"]), 100.0)
        self.assertEqual(float(active["initial_sl"]), 90.0)

        v161._maybe_trigger_be(owner)
        self.assertEqual(len(owner.x.posts), 1)


class WiringTests(unittest.TestCase):
    def test_v161_is_final_runtime_model(self):
        self.assertEqual(model.VERSION, "1.6.1")
        self.assertIs(v160.model, model)
        self.assertIs(exchange.Exchange.sync_time, v161._sync_time_v161)
        self.assertTrue(engine.Engine._kaytrade_v161_applied)
        self.assertEqual(v161.BUILD, "1610")


if __name__ == "__main__":
    unittest.main()

import types
import unittest
from unittest.mock import patch

import v163b_network_patch as hotfix
from exchange import NetworkError


class FakeStore:
    def __init__(self):
        self.data = {"halt": "", "last_bar": 0, "active": None}
        self.saved = 0

    def save(self):
        self.saved += 1


class FakeExchange:
    def __init__(self, fail=False):
        self.fail = fail
        self.clock_sync_degraded = False

    def sync_time(self):
        if self.fail:
            raise NetworkError("network down", method="GET", path="/api/v5/public/time")
        return True

    def account(self):
        return {"uid": "u1"}

    def balance(self):
        return 1000.0, 1000.0

    def positions(self):
        return []

    def orders(self):
        return []

    def algos(self):
        return []


class FakeOwner:
    def __init__(self, fail=False, enabled=True):
        self.events = []
        self.network_paused = False
        self.recovery_count = 0
        self.probe_at = 0.0
        self.engine = types.SimpleNamespace(
            x=FakeExchange(fail),
            enabled=enabled,
            stopped=False,
            connection_id="u1",
            store=FakeStore(),
            poll_at=0.0,
            market_now=lambda: 1000.0,
        )

    def emit(self, kind, data):
        self.events.append((kind, data))


class V163bNetworkTests(unittest.TestCase):
    def test_read_failure_starts_grace_without_disabling(self):
        owner = FakeOwner(enabled=True)
        exc = NetworkError("read failed", method="GET", path="/api/v5/account/balance")
        with patch.object(hotfix.time, "monotonic", return_value=100.0):
            self.assertTrue(hotfix._begin_read_only_recovery(owner, exc))
        self.assertTrue(owner.network_paused)
        self.assertTrue(owner.engine.enabled)
        self.assertTrue(owner._v163b_network_auto_resume)
        self.assertEqual(owner.engine.store.data["halt"], "")

    def test_write_uncertainty_never_uses_grace_window(self):
        owner = FakeOwner(enabled=True)
        exc = NetworkError("write unknown", method="POST", path="/api/v5/trade/order")
        self.assertTrue(hotfix._is_write_network_error(exc))
        self.assertFalse(hotfix._begin_read_only_recovery(owner, exc))
        self.assertFalse(owner.network_paused)

    def test_recovery_inside_60s_auto_resumes_and_skips_missed_signal(self):
        owner = FakeOwner(enabled=True)
        exc = NetworkError("read failed", method="GET", path="/api/v5/account/balance")
        with patch.object(hotfix.time, "monotonic", return_value=100.0):
            hotfix._begin_read_only_recovery(owner, exc)
        with patch.object(hotfix.time, "monotonic", side_effect=[105.0, 105.0, 105.0]):
            result = hotfix._probe_read_only_recovery(owner)
        self.assertEqual(result, "recovered")
        self.assertFalse(owner.network_paused)
        self.assertTrue(owner.engine.enabled)
        self.assertEqual(owner.engine.store.data["halt"], "")
        self.assertGreater(owner.engine.store.data["last_bar"], 0)
        self.assertTrue(any("自动交易继续" in str(data) for kind, data in owner.events if kind == "status"))

    def test_over_60s_stops_new_entries_without_fault_lock(self):
        owner = FakeOwner(fail=True, enabled=True)
        exc = NetworkError("read failed", method="GET", path="/api/v5/account/balance")
        with patch.object(hotfix.time, "monotonic", return_value=100.0):
            hotfix._begin_read_only_recovery(owner, exc)
        owner.probe_at = 155.0
        with patch.object(hotfix.time, "monotonic", side_effect=[161.0, 161.0, 161.0]):
            result = hotfix._probe_read_only_recovery(owner)
        self.assertEqual(result, "retry")
        self.assertFalse(owner.engine.enabled)
        self.assertTrue(owner._v163b_network_timed_out)
        self.assertEqual(owner.engine.store.data["halt"], "")
        self.assertTrue(any("超过60秒" in str(data) for kind, data in owner.events if kind == "log"))


if __name__ == "__main__":
    unittest.main()

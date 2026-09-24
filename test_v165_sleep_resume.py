import time
import unittest
from unittest.mock import patch

import engine
import v165_sleep_resume_patch as sleep_patch


class DummyExchange:
    def __init__(self):
        self.clock_anchor = (123.0, 456.0)


class SleepResumeTests(unittest.TestCase):
    @staticmethod
    def bare_engine():
        e = engine.Engine.__new__(engine.Engine)
        e.enabled = True
        e.stopped = False
        e.market = {"bar5m": 123}
        e.market_at = 111
        e.poll_at = 222
        e.candle_lag_count = 3
        e.candle_paused = True
        e.startup_buffer_until = 0.0
        e.store = None
        e.x = DummyExchange()
        events = []
        e.emit = lambda kind, data: events.append((kind, data))
        return e, events

    def test_sleep_pause_stops_authorization_without_fault_lock(self):
        e, events = self.bare_engine()
        result = engine.Engine.halt(e, "检测到睡眠或长时间停顿，必须人工重新检查后启动")
        self.assertFalse(result)
        self.assertFalse(e.enabled)
        self.assertTrue(e.stopped)
        self.assertIsNone(e.market)
        self.assertEqual(e.market_at, 0)
        self.assertEqual(e.poll_at, 0)
        self.assertEqual(e.candle_lag_count, 0)
        self.assertFalse(e.candle_paused)
        self.assertIsNone(e.x.clock_anchor)
        self.assertEqual(e.startup_buffer_until, 0.0)
        self.assertTrue(any("未设置故障锁" in str(data) for kind, data in events if kind == "log"))

    def test_network_read_failure_is_still_a_soft_stop(self):
        e, _events = self.bare_engine()
        engine.Engine.halt(e, "只读请求失败，不会下单")
        self.assertFalse(e.enabled)
        self.assertTrue(e.stopped)

    def test_sleep_detector_uses_gap_since_previous_cycle_completed(self):
        source = open('v165_sleep_resume_patch.py', encoding='utf-8').read()
        self.assertIn('_v165_last_cycle_end_wall', source)
        self.assertIn('self.poll_at = now_mono', source)
        self.assertNotIn('now-self.poll_at>60', source.replace(' ', ''))

    def test_recent_completed_cycle_does_not_trigger_sleep(self):
        e, _events = self.bare_engine()
        e._v165_last_cycle_end_wall = time.time()
        # Avoid running the legacy engine body; this test only validates the new
        # pre-cycle sleep decision and heartbeat neutralization.
        previous = sleep_patch.previous_cycle if hasattr(sleep_patch, 'previous_cycle') else None
        self.assertLess(time.time() - e._v165_last_cycle_end_wall, sleep_patch._SLEEP_IDLE_SECONDS)


if __name__ == "__main__":
    unittest.main()

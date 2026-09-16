import time
import unittest

import engine
import v165_sleep_resume_patch  # applies runtime patch


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

    def test_sleep_pause_keeps_authorization_but_discards_stale_state(self):
        e, events = self.bare_engine()
        before = time.monotonic()
        result = engine.Engine.halt(e, "检测到睡眠或长时间停顿，必须人工重新检查后启动")
        self.assertFalse(result)
        self.assertTrue(e.enabled)
        self.assertFalse(e.stopped)
        self.assertIsNone(e.market)
        self.assertEqual(e.market_at, 0)
        self.assertEqual(e.poll_at, 0)
        self.assertEqual(e.candle_lag_count, 0)
        self.assertFalse(e.candle_paused)
        self.assertIsNone(e.x.clock_anchor)
        self.assertGreaterEqual(e.startup_buffer_until, before + 4.5)
        self.assertTrue(any("保持自动交易授权" in str(data) for kind, data in events if kind == "log"))

    def test_network_read_failure_is_still_a_soft_stop(self):
        e, _events = self.bare_engine()
        engine.Engine.halt(e, "只读请求失败，不会下单")
        self.assertFalse(e.enabled)
        self.assertTrue(e.stopped)


if __name__ == "__main__":
    unittest.main()

import unittest

import exchange
import v165_algo_fallback_patch as algo


class DemoAlgoDegradeTests(unittest.TestCase):
    @staticmethod
    def timeout(path):
        return exchange.NetworkError("OKX错误码 51054", "51054", None, "GET", path)

    @staticmethod
    def bare(demo):
        x = exchange.Exchange.__new__(exchange.Exchange)
        x.demo = bool(demo)
        events = []
        x.network_event = events.append
        return x, events

    def make_all_51054(self, x):
        x.get = lambda path, params=None, private=False: (_ for _ in ()).throw(self.timeout(path))

    def test_demo_trigger_can_degrade(self):
        x, events = self.bare(True)
        self.make_all_51054(x)
        self.assertEqual(algo._family_read(x, "trigger", "Algo Trigger"), [])
        self.assertTrue(any("模拟盘降级" in e and "Algo Trigger" in e for e in events))

    def test_demo_trailing_can_degrade(self):
        x, events = self.bare(True)
        self.make_all_51054(x)
        self.assertEqual(algo._family_read(x, "move_order_stop", "Algo Trailing Stop"), [])
        self.assertTrue(any("模拟盘降级" in e and "Algo Trailing Stop" in e for e in events))

    def test_demo_conditional_stays_fail_closed(self):
        x, _ = self.bare(True)
        self.make_all_51054(x)
        with self.assertRaises(exchange.NetworkError):
            algo._family_read(x, "conditional", "Algo Conditional")

    def test_live_trailing_stays_fail_closed(self):
        x, _ = self.bare(False)
        self.make_all_51054(x)
        with self.assertRaises(exchange.NetworkError):
            algo._family_read(x, "move_order_stop", "Algo Trailing Stop")


if __name__ == "__main__":
    unittest.main()

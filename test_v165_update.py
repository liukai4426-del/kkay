import unittest

import v165_model as model


class V165ModelTests(unittest.TestCase):
    def test_identity_and_fixed_strategy_values(self):
        self.assertEqual(model.VERSION, "1.6.5")
        self.assertEqual(model.ENTRY_WINDOW_MS, 300000)
        self.assertTrue(model.TIME_WINDOW_ENABLED)
        self.assertEqual(model.THRESHOLD, 6.0)
        self.assertEqual(model.BOLL_OUTER_SCORE, 2.5)
        self.assertEqual(model.VOLUME_HARD_GATE, 1.20)

    def test_signal_is_valid_until_next_5m_close(self):
        opp = {"signal_close_ms": 1_000_000}
        opened, age, remaining, end = model._signal_window(opp, 1_299_999)
        self.assertTrue(opened)
        self.assertEqual(end, 1_300_000)
        self.assertEqual(remaining, 1)
        opened, age, remaining, end = model._signal_window(opp, 1_300_000)
        self.assertFalse(opened)
        self.assertEqual(remaining, 0)

    def test_opportunity_expiry_is_exactly_five_minutes(self):
        opp = model._normalize_opportunity_5m({"signal_close_ms": 9_000_000})
        self.assertEqual(opp["expires_ms"], 9_300_000)
        self.assertTrue(opp["signal_valid_until_next_5m_close"])

    def test_visible_rewrite_contains_no_four_minute_semantics(self):
        text = model._rewrite_text("V1.6.4外轨信号超过4分钟执行窗口；4分钟有效")
        self.assertIn("V1.6.5", text)
        self.assertNotIn("4分钟", text)
        self.assertIn("下一根5m收盘", text)


if __name__ == "__main__":
    unittest.main()

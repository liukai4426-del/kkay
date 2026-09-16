import unittest

import v165_model as model
import v165_15m_outer_patch as outer15


def candles(step_ms, count=201):
    rows = []
    for i in range(count):
        close = 100.0 if i % 2 == 0 else 101.0
        rows.append({
            "t": i * step_ms,
            "o": close,
            "h": close + 0.2,
            "l": close - 0.2,
            "c": close,
            "v": 100.0,
        })
    return rows


class V165ModelTests(unittest.TestCase):
    def test_identity_and_fixed_strategy_values(self):
        self.assertEqual(model.VERSION, "1.6.5")
        self.assertEqual(model.ENTRY_WINDOW_MS, 300000)
        self.assertTrue(model.TIME_WINDOW_ENABLED)
        self.assertEqual(model.THRESHOLD, 6.0)
        self.assertEqual(model.BOLL_OUTER_SCORE, 2.5)
        self.assertEqual(model.VOLUME_HARD_GATE, 1.20)
        self.assertEqual(model.BOLL_TRIGGER_TIMEFRAME, "15m")
        self.assertTrue(model._kaytrade_v165_15m_outer_applied)

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
        text = model._rewrite_text("V1.6.4的5m BOLL外轨信号超过4分钟执行窗口；4分钟有效")
        self.assertIn("V1.6.5", text)
        self.assertNotIn("4分钟", text)
        self.assertIn("15m BOLL外轨", text)
        self.assertIn("下一根5m收盘", text)

    def test_outer_trigger_uses_closed_15m_boll_but_keeps_5m_confirmation_snapshot(self):
        quarter = candles(15 * 60 * 1000)
        five = candles(5 * 60 * 1000)
        # Force only the latest 15m candle to touch deeply below its lower band.
        # The latest 5m candle remains near its ordinary range, so this is a
        # genuine 15m BOLL trigger rather than a renamed 5m trigger.
        quarter[-1]["l"] = 90.0
        signal = outer15.boll_entry_signal_15m(quarter, five, "做多")
        self.assertIsNotNone(signal)
        self.assertEqual(signal["path"], "lower_band")
        self.assertEqual(signal["boll_timeframe"], "15m")
        self.assertEqual(signal["bar_t"], quarter[-1]["t"])
        self.assertEqual(signal["signal_close_ms"], quarter[-1]["t"] + 15 * 60 * 1000)
        self.assertGreaterEqual(signal["rsi5"], model.RSI_MIN)
        self.assertLessEqual(signal["rsi5"], model.RSI_MAX)
        self.assertEqual(signal["bar_low"], five[-1]["l"])
        self.assertEqual(signal["bar_high"], five[-1]["h"])

    def test_5m_confirmation_failure_still_blocks_15m_outer_trigger(self):
        quarter = candles(15 * 60 * 1000)
        five = candles(5 * 60 * 1000)
        quarter[-1]["l"] = 90.0
        # Make 5m RSI strongly one-sided and outside 30-70 while the 15m outer
        # touch remains present.
        for i, row in enumerate(five):
            row["c"] = 100.0 + i
            row["o"] = row["c"]
            row["h"] = row["c"] + 0.2
            row["l"] = row["c"] - 0.2
        self.assertIsNone(outer15.boll_entry_signal_15m(quarter, five, "做多"))


if __name__ == "__main__":
    unittest.main()

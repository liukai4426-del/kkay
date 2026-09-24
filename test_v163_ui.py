import unittest

import v156_ui_patch as ui156
import v161_ui_status_patch as ui161
import v163_ui_patch as ui


class V163UITests(unittest.TestCase):
    def test_version_subtitle_is_current(self):
        self.assertEqual(ui.VERSION, "1.6.3")
        self.assertEqual(ui.BUILD, "1630")
        self.assertEqual(ui.VERSION_SUBTITLE, "BTC / USDT   ·   V1.6.3")
        self.assertIn("1.6.3", ui.WINDOW_TITLE)

    def test_run_log_scrollbar_uses_max_round_drawer(self):
        self.assertIs(ui156.CleanScrollbar._draw, ui._draw_max_round)

    def test_opening_status_labels_match_outer_only_model(self):
        labels = dict(ui161._STATUS_ITEMS)
        self.assertEqual(labels["boll"], "外轨BOLL")
        self.assertEqual(labels["middle_macd"], "外轨MACD")
        self.assertEqual(labels["macd_clean"], "RSI 30–70")
        self.assertNotIn("中轨", " ".join(labels.values()))


if __name__ == "__main__":
    unittest.main()

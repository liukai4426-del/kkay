import inspect
import unittest

import app
import v161_ui_status_patch as ui161
import v165_model as model
import v165_15m_outer_patch as outer15
import v166_ui_patch as ui166


class V166UITests(unittest.TestCase):
    def test_version_and_status_labels(self):
        self.assertEqual(ui166.VERSION, "1.6.6")
        self.assertEqual(ui166.BUILD, "1660")
        labels = dict(ui161._STATUS_ITEMS)
        self.assertEqual(labels["boll"], "15m BOLL外轨触发")
        self.assertEqual(labels["signal_window"], "5m执行窗口")
        self.assertNotIn("funds", labels)
        self.assertTrue(getattr(app.App, "_kaytrade_v166_ui_applied", False))

    def test_15m_rewrite_is_idempotent(self):
        once = ui166._rewrite_text_v166("5m BOLL外轨信号（有效至下一根5m收盘）")
        twice = ui166._rewrite_text_v166(once)
        self.assertIn("15m BOLL外轨信号", once)
        self.assertEqual(once, twice)
        self.assertNotIn("115m", once)
        self.assertNotIn("115m", twice)
        self.assertEqual(ui166._sanitize_display("115m BOLL外轨信号"), "15m BOLL外轨信号")
        self.assertIs(model._rewrite_text, ui166._rewrite_text_v166)
        self.assertIs(outer15._rewrite_text_15m, ui166._rewrite_text_v166)

    def test_run_log_renderer_is_inline_and_wrap_aligned(self):
        source = inspect.getsource(ui166._render_logs_v166) + inspect.getsource(ui166._insert_log_row)
        self.assertIn("\\t", source)
        self.assertIn("v166_detail_inline", source)
        self.assertIn("v166_row", source)
        self.assertGreater(ui166.LOG_CONTENT_TAB_PX, 260)
        self.assertIs(app.App.render_logs, ui166._render_logs_v166)

    def test_patch_is_presentation_only(self):
        source = inspect.getsource(ui166)
        for forbidden in ("/api/v5/trade/order", "_submit_initial =", "stop_atr =", "THRESHOLD = 5.5"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(model.THRESHOLD, 6.0)
        self.assertEqual(model.BOLL_TRIGGER_TIMEFRAME, "15m")


if __name__ == "__main__":
    unittest.main()

import inspect
import unittest

import app
import visual
import v154_runtime_patch as runtime


class V154SmoothScrollTests(unittest.TestCase):
    def test_hidden_scrollbar_does_not_redraw_when_unmapped(self):
        source=inspect.getsource(runtime._efficient_scrollbar_set)
        self.assertIn('winfo_ismapped',source)
        self.assertIn('if mapped',source)
        self.assertIs(visual.WideScrollbar.set,runtime._efficient_scrollbar_set)

    def test_scroll_events_are_coalesced(self):
        queue_source=inspect.getsource(runtime._queue_scroll)
        bind_source=inspect.getsource(runtime._bind_smooth_scroll)
        wheel_source=inspect.getsource(runtime._wheel_amount)
        self.assertIn('SCROLL_FLUSH_MS',queue_source)
        self.assertIn('root.after',queue_source)
        self.assertIn('pending',queue_source)
        self.assertIn('unbind_all',bind_source)
        self.assertIn('<MouseWheel>',bind_source)
        self.assertIn('<Shift-MouseWheel>',bind_source)
        self.assertIn('visual.ScoreTable',bind_source)
        self.assertIn('tk.Text',bind_source)
        self.assertIn('delta / 3.0',wheel_source)

    def test_run_log_keeps_reader_position(self):
        source=inspect.getsource(runtime._render_logs_v154)
        self.assertIn('at_bottom',source)
        self.assertIn('yview_moveto(top)',source)
        self.assertIn('incremental',source)
        self.assertIn('self.log.insert',source)
        self.assertIs(app.App.render_logs,runtime._render_logs_v154)

    def test_v154_ui_hook_is_applied(self):
        self.assertTrue(getattr(app.App,'_kaytrade_v154_smooth_scroll_applied',False))
        init_source=inspect.getsource(app.App.__init__)
        self.assertIn('KAYTRADE 1.5.4',init_source)
        self.assertIn('_bind_smooth_scroll',init_source)


if __name__=='__main__':
    unittest.main()

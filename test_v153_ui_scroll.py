import inspect
import unittest

import app
import engine
import v153_runtime_patch as runtime


class V153HiddenScrollTests(unittest.TestCase):
    def test_runtime_hook_is_applied(self):
        self.assertTrue(getattr(engine.Engine,'_kaytrade_v153_applied',False))
        self.assertTrue(getattr(app.App,'_kaytrade_v153_hidden_scrollbars_applied',False))

    def test_visible_scrollbar_controls_are_hidden_but_wheel_remains(self):
        hide=inspect.getsource(runtime._hide_scrollbars)
        bind=inspect.getsource(runtime._bind_hidden_scroll)
        self.assertIn('ttk.Scrollbar',hide)
        self.assertIn('visual.WideScrollbar',hide)
        self.assertIn('pack_forget',hide)
        self.assertIn('grid_remove',hide)
        self.assertIn('<MouseWheel>',bind)
        self.assertIn('yview_scroll',bind)
        self.assertIn('<Shift-MouseWheel>',bind)
        self.assertIn('xview_scroll',bind)

    def test_ui_copy_states_v153_and_no_one_minute_confirmation(self):
        source=inspect.getsource(runtime.apply)
        rewrite=inspect.getsource(runtime._rewrite_text)
        self.assertIn('KAYTRADE 1.5.3',source)
        self.assertIn('1m无EMA/突破/KDJ等确认',source)
        self.assertIn('单一1×仓位，不进行第二次加仓',source)
        self.assertIn('4根1m',rewrite)


if __name__=='__main__':
    unittest.main()

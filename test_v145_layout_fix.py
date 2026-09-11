import inspect
import unittest

import v145_layout_fix_patch as v145


class V145LayoutFixTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(v145.V145_VERSION,'1.4.5')

    def test_main_page_uses_full_relative_width(self):
        source=inspect.getsource(v145)
        self.assertIn("relwidth=1.0",source)
        self.assertIn("relheight=1.0",source)
        self.assertIn("_v145_main_layout",source)
        self.assertIn("selected.lift()",source)

    def test_connection_page_is_rebuilt_as_expanding_card(self):
        source=inspect.getsource(v145._rebuild_connection_page)
        self.assertIn("surface.pack(fill='both',expand=True",source)
        self.assertIn("body.grid_columnconfigure(1,weight=1)",source)
        self.assertIn("sticky='ew'",source)
        self.assertIn("测试连接",source)
        self.assertIn("网络自检",source)

    def test_width_fix_is_scoped_to_main_tabs(self):
        source=inspect.getsource(v145)
        self.assertIn("Nested score/indicator tabs keep the previous compact layout",source)
        self.assertIn("return previous_tabs_select(self,page)",source)

    def test_strategy_is_inherited_not_rewritten(self):
        source=inspect.getsource(v145)
        self.assertIn("from v144_ui_polish_patch import apply as apply_v144",source)
        for token in ('score_threshold=','tpTriggerPx','slTriggerPx','first_signal_notional='):
            self.assertNotIn(token,source)


if __name__=='__main__':
    unittest.main()

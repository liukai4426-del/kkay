import inspect
import unittest

import v144_ui_polish_patch as v144


class V144UiPolishTests(unittest.TestCase):
    def test_capsule_radius_is_maximal_for_button_height(self):
        self.assertEqual(v144._capsule_radius(42),20.0)
        self.assertEqual(v144._capsule_radius(38),18.0)

    def test_all_scrollbars_share_one_width(self):
        self.assertEqual(v144.SCROLL_WIDTH,20)

    def test_connection_fields_target_panel_background(self):
        self.assertEqual(v144.ACCOUNT_FIELDS,{'账户官方域名','环境','API Key','Secret Key','Passphrase'})

    def test_patch_contains_requested_text_cleanup_and_antialias(self):
        source=inspect.getsource(v144)
        for token in ('Image.Resampling.LANCZOS','AA_SCALE=4','网络自检','测试连接',"self.signal.set('')",'_v144_overview_uniform'):
            self.assertIn(token,source)

    def test_strategy_patch_is_inherited_not_replaced(self):
        source=inspect.getsource(v144)
        self.assertIn('from v143_visual_polish_patch import apply as apply_v143_visual',source)
        self.assertNotIn('score_threshold=',source)
        self.assertNotIn('tpTriggerPx',source)
        self.assertNotIn('slTriggerPx',source)


if __name__=='__main__':
    unittest.main()

import unittest
from pathlib import Path

import app
import engine
import v156_build1561_patch as b1561
import v156_execution_hotfix as execfix


class V156Build1561Tests(unittest.TestCase):
    def test_identity_and_preserved_flags(self):
        self.assertEqual(b1561.VERSION, '1.5.6')
        self.assertEqual(b1561.BUILD, '1561')
        self.assertFalse(b1561.PATH_C_ENABLED)
        self.assertFalse(b1561.COOLDOWN_ENABLED)
        self.assertFalse(b1561.CONSECUTIVE_LOSS_PAUSE_ENABLED)
        self.assertTrue(engine.Engine._kaytrade_v1561_applied)
        self.assertTrue(engine.Engine._kaytrade_v156_execution_hotfix_applied)
        self.assertTrue(app.App._kaytrade_v1561_applied)
        self.assertTrue(callable(execfix._set_leverage_v156))

    def test_log_order_helper_is_newest_first_and_capped(self):
        rows = [('log', str(i)) for i in range(510)]
        ordered = b1561._ordered_rows(rows)
        self.assertEqual(len(ordered), 500)
        self.assertEqual(ordered[0][1], '509')
        self.assertEqual(ordered[-1][1], '10')

    def test_fixed_loss_control_markers_cover_value_row_and_note(self):
        markers = b1561.FIXED_LOSS_MARKERS
        self.assertTrue(any('连续亏损停开次数' in x for x in markers))
        self.assertIn('3（固定）', markers)
        self.assertTrue(any('除连续亏损停开次数外' in x for x in markers))

    def test_spec_preserves_build1561_but_promotes_only_final_release_hook(self):
        spec = Path('OKXLocal.spec').read_text()
        self.assertIn("'v156_build1561_patch'", spec)
        self.assertNotIn("runtime_hooks=[str(root / 'v156_ui_patch.py')]", spec)
        if "CFBundleShortVersionString': '1.5.6'" in spec:
            self.assertIn("runtime_hooks=[str(root / 'v156_build1561_patch.py')]", spec)
            self.assertIn("CFBundleVersion': '1561'", spec)
        elif "CFBundleShortVersionString': '1.6.0'" in spec:
            self.assertIn("runtime_hooks=[str(root / 'v160_update_patch.py')]", spec)
            self.assertNotIn("runtime_hooks=[str(root / 'v156_build1561_patch.py')]", spec)
            self.assertIn("CFBundleVersion': '1600'", spec)
        else:
            self.assertIn("runtime_hooks=[str(root / 'v161_update_patch.py')]", spec)
            self.assertNotIn("runtime_hooks=[str(root / 'v156_build1561_patch.py')]", spec)
            self.assertIn("CFBundleShortVersionString': '1.6.1'", spec)
            self.assertIn("CFBundleVersion': '1610'", spec)


if __name__ == '__main__':
    unittest.main()

import unittest
from pathlib import Path

import app
import engine
import v154_build1544_patch as b1544
import v155_build1551_patch as b1551
import v156_ui_patch as v156


class V156UiTests(unittest.TestCase):
    def test_identity_and_preserved_trading_flags(self):
        self.assertEqual(v156.VERSION, '1.5.6')
        self.assertEqual(v156.BUILD, '1560')
        self.assertFalse(v156.PATH_C_ENABLED)
        self.assertFalse(v156.COOLDOWN_ENABLED)
        self.assertFalse(b1544.PATH_C_ENABLED)
        self.assertFalse(b1551.COOLDOWN_ENABLED)
        self.assertTrue(engine.Engine._kaytrade_v156_applied)
        self.assertTrue(app.App._kaytrade_v156_applied)

    def test_log_status_color_classes(self):
        self.assertEqual(v156._status_for('log', '等待最新收盘K线'), ('等待', 'wait'))
        self.assertEqual(v156._status_for('log', '自动交易启动'), ('正常运行', 'ok'))
        self.assertEqual(v156._status_for('alarm', 'HTTP 401'), ('警报', 'alarm'))
        self.assertEqual(v156._status_for('log', '故障锁已解除：核对完成'), ('正常运行', 'ok'))

    def test_timestamp_and_title_detail(self):
        stamp, msg = v156._extract_timestamp('2026-09-14 15:26:08 网络自检：账户只读认证通过')
        self.assertEqual(stamp, '2026-09-14 15:26:08')
        self.assertEqual(msg, '网络自检：账户只读认证通过')
        title, detail = v156._split_title_detail(msg)
        self.assertEqual(title, '网络自检')
        self.assertEqual(detail, '账户只读认证通过')

    def test_spec_uses_only_final_runtime_hook(self):
        spec = Path('OKXLocal.spec').read_text()
        self.assertIn("runtime_hooks=[str(root / 'v156_ui_patch.py')]", spec)
        self.assertNotIn("runtime_hooks=[str(root / 'v155_build1551_patch.py')]", spec)
        self.assertIn("CFBundleShortVersionString': '1.5.6'", spec)
        self.assertIn("CFBundleVersion': '1560'", spec)


if __name__ == '__main__':
    unittest.main()

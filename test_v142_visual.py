import unittest
from pathlib import Path

import app
import v142_visual_patch as v142


class Visual142Tests(unittest.TestCase):
    def test_button_colors_match_score_colors(self):
        accent=app.RoundedButton.PALETTES['accent']
        danger=app.RoundedButton.PALETTES['danger']
        self.assertEqual(accent[0],app.GREEN)
        self.assertEqual(danger[0],app.RED)

    def test_button_radius_is_more_rounded(self):
        self.assertGreaterEqual(v142._button_radius(42,14),19)
        self.assertGreaterEqual(v142._button_radius(38,13),17)
        self.assertLess(v142._button_radius(42,14),21)

    def test_status_color_rules(self):
        self.assertEqual(v142._status_kind('默认停止 · 未连接',False,False),'yellow')
        self.assertEqual(v142._status_kind('已停止新开仓 / 继续核对持仓',True,False),'green')
        self.assertEqual(v142._status_kind('全自动运行 / 模拟盘',True,False),'green')
        self.assertEqual(v142._status_kind('故障锁定：停止新开仓',True,True),'red')

    def test_selected_logo_asset_exists(self):
        path=v142._logo_path()
        self.assertTrue(path.exists(),path)
        self.assertGreater(path.stat().st_size,5000)
        self.assertEqual(path.suffix.lower(),'.png')


if __name__=='__main__':
    unittest.main()

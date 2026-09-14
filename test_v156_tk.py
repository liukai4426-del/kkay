import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import v156_ui_patch as v156


class V156TkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kaytrade-v156-')
        self.root = tk.Tk()
        self.worker_patch = patch.object(app.App, 'worker', lambda self: None)
        self.worker_patch.start()
        self.ui = app.App(self.root, Path(self.temp.name))
        self.root.update()

    def tearDown(self):
        try:
            self.ui.finished.set()
        except Exception:
            pass
        try:
            self.ui.thread.join(timeout=1)
        except Exception:
            pass
        try:
            self.ui.lock.close()
        except Exception:
            pass
        self.worker_patch.stop()
        try:
            self.root.destroy()
        except Exception:
            pass
        self.temp.cleanup()

    def _visible_texts(self):
        out = []
        for widget in v156._walk(self.root):
            try:
                if widget.winfo_ismapped():
                    text = str(widget.cget('text') or '')
                    if text:
                        out.append(text)
            except Exception:
                pass
        return out

    def test_compact_log_card_and_clean_scrollbar(self):
        self.assertTrue(self.ui._v156_ready)
        self.assertTrue(self.ui._v156_log_ready)
        self.assertIn('Build 1560', self.root.title())
        self.assertEqual(int(float(self.ui._v156_log_surface.cget('height'))), v156.LOG_CARD_HEIGHT)
        self.assertLessEqual(v156.LOG_CARD_HEIGHT, 145)
        bar = self.ui._v156_log_scrollbar
        self.assertEqual(int(bar.cget('borderwidth')), 0)
        self.assertEqual(int(bar.cget('highlightthickness')), 0)
        self.assertEqual(str(bar.cget('bg')), app.PANEL)

    def test_second_signal_and_cooldown_rows_are_physically_absent(self):
        self.assertNotIn('second_signal_notional', self.ui.fields)
        self.assertNotIn('cooldown_minutes', self.ui.fields)
        self.assertTrue(self.ui._v156_second_signal_control_removed)
        self.assertTrue(self.ui._v156_cooldown_control_removed)
        texts = self._visible_texts()
        self.assertFalse(any('第二信号仓位' in x for x in texts))
        self.assertFalse(any('平仓后冷却' in x or '冷却时间' in x or '平仓冷却' in x for x in texts))

    def test_log_renders_date_status_title_detail_and_three_state_colors(self):
        self.ui.log_lines = [
            ('log', '2026-09-14 15:26:08 等待最新收盘K线：暂不新开仓'),
            ('log', '2026-09-14 15:27:10 自动交易启动：完整条件开始监控'),
            ('alarm', '2026-09-14 15:28:11 警报：HTTP 401 / OKX 50123'),
        ]
        self.ui.render_logs()
        self.root.update_idletasks()
        text = self.ui._v156_log_text
        content = text.get('1.0', 'end')
        self.assertIn('2026-09-14 15:26:08', content)
        self.assertIn('等待', content)
        self.assertIn('正常运行', content)
        self.assertIn('警报', content)
        self.assertIn('等待最新收盘K线', content)
        self.assertIn('暂不新开仓', content)
        self.assertEqual(text.tag_cget('wait', 'foreground'), v156.YELLOW)
        self.assertEqual(text.tag_cget('ok', 'foreground'), v156.GREEN)
        self.assertEqual(text.tag_cget('alarm', 'foreground'), v156.RED)


if __name__ == '__main__':
    unittest.main()

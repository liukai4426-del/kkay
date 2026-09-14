import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import v155_update_patch as v155
import v155_build1551_patch as b1551


class Build1551TkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kaytrade-v1551-')
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

    def test_packaged_startup_shape_has_no_recursive_overlay_and_no_cooldown_ui(self):
        self.assertTrue(self.ui._v1551_ready)
        self.assertIn('Build 1551', self.root.title())
        self.assertNotIn('cooldown_minutes', self.ui.fields)
        self.assertTrue(self.ui._v1551_cooldown_control_removed)
        self.assertIs(getattr(self.ui.render_logs, '__func__', None), v155._render_logs_v155)
        visible_labels = []
        for widget in b1551._walk(self.root):
            try:
                if widget.winfo_ismapped():
                    visible_labels.append(str(widget.cget('text') or ''))
            except Exception:
                pass
        self.assertFalse(any('平仓后冷却' in x or '冷却时间' in x for x in visible_labels))
        self.assertTrue(self.ui._v155_log_scroll_ready)


if __name__ == '__main__':
    unittest.main()

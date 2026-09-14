import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
import v155_update_patch as v155


class V155TkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kaytrade-v155-')
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
        self.root.destroy()
        self.temp.cleanup()

    def state(self, field):
        entry = v155._entry_for(self.ui, field)
        self.assertIsNotNone(entry, field)
        return str(entry.entry.cget('state'))

    def test_connected_idle_settings_are_editable_and_fixed_fields_are_readonly(self):
        self.ui.engine = SimpleNamespace(enabled=False)
        v155._apply_editable_state(self.ui)
        for field in ('first_signal_notional', 'risk_usdt', 'risk_pct', 'daily_loss', 'leverage', 'cooldown_minutes'):
            self.assertEqual(self.state(field), 'normal', field)
        self.assertEqual(self.state('capital'), 'disabled')
        self.assertEqual(self.state('score_threshold'), 'disabled')
        self.assertEqual(self.state('stop_atr'), 'disabled')

        self.ui.engine.enabled = True
        v155._apply_editable_state(self.ui)
        self.assertEqual(self.state('first_signal_notional'), 'disabled')
        self.assertEqual(self.state('leverage'), 'disabled')

        self.ui.engine.enabled = False
        v155._apply_editable_state(self.ui)
        self.assertEqual(self.state('first_signal_notional'), 'normal')
        self.assertEqual(self.state('leverage'), 'normal')

    def test_margin_and_leverage_sync_capital_and_second_row_is_removed(self):
        self.ui.fields['first_signal_notional'].set('250')
        self.ui.fields['leverage'].set('4')
        self.root.update()
        self.assertEqual(float(self.ui.fields['capital'].get()), 1000.0)
        settings = self.ui.settings()
        self.assertEqual(settings.first_signal_notional, 250.0)
        self.assertEqual(settings.second_signal_notional, 750.0)
        self.assertEqual(settings.max_notional, 1000.0)
        self.assertNotIn('second_signal_notional', self.ui.fields)
        self.assertTrue(self.ui._v155_second_signal_removed)

    def test_run_log_shows_timestamp_scrolls_and_preserves_history_position(self):
        self.ui.log_lines = [
            ('log', f'2026-09-14 13:{i//60:02d}:{i%60:02d} 测试运行记录 {i}')
            for i in range(90)
        ]
        self.ui.render_logs()
        self.root.update()
        text = self.ui._v155_log_text
        self.assertIn('2026-09-14 13:', text.get('1.0', 'end'))
        self.assertTrue(self.ui._v155_log_scroll_ready)

        text.yview_moveto(0.0)
        self.root.update()
        before = text.yview()[0]
        v155._on_log_wheel(self.ui, SimpleNamespace(delta=-120))
        self.root.update()
        after = text.yview()[0]
        self.assertGreater(after, before)

        text.yview_moveto(0.30)
        self.root.update()
        held = text.yview()[0]
        self.ui.log_lines.append(('log', '2026-09-14 13:59:59 新日志不应把阅读者强制拉到底部'))
        self.ui.render_logs()
        self.root.update()
        self.assertLess(abs(text.yview()[0] - held), 0.08)
        self.assertLess(text.yview()[1], 0.99)


if __name__ == '__main__':
    unittest.main()

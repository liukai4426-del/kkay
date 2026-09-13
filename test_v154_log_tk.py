import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import v154_limit_log_fix  # noqa: F401 - applies the repair overlay


@unittest.skipUnless(sys.platform == "darwin", "real Tk visibility smoke runs on the Intel Mac job")
class V154MacLogVisibilityTests(unittest.TestCase):
    def test_persisted_and_live_logs_are_visible_in_real_text_widget(self):
        import tkinter as tk

        with tempfile.TemporaryDirectory(prefix="kaytrade-v154-log-") as folder:
            root_path = Path(folder)
            (root_path / "events.log").write_text(
                "2026-09-14 02:00:00 修复版历史日志可见性测试\n",
                encoding="utf-8",
            )
            root = tk.Tk()
            ui = None
            try:
                with patch.object(app.App, "worker", lambda self: None), patch.object(app.messagebox, "showerror", side_effect=AssertionError):
                    ui = app.App(root, root_path)
                    root.update_idletasks()
                    root.update()
                    text = ui.log.get("1.0", "end")
                    self.assertIn("修复版历史日志可见性测试", text)
                    self.assertTrue(ui.log.winfo_ismapped())
                    self.assertGreater(ui.log.winfo_height(), 20)

                    ui.emit("log", "修复版当前会话实时日志测试")
                    ui.drain()
                    root.update_idletasks()
                    root.update()
                    text = ui.log.get("1.0", "end")
                    self.assertIn("修复版当前会话实时日志测试", text)
                    persisted = (root_path / "events.log").read_text(encoding="utf-8")
                    self.assertIn("修复版当前会话实时日志测试", persisted)
            finally:
                if ui is not None:
                    try:
                        ui.finished.set()
                        ui.thread.join(timeout=2)
                        ui.lock.close()
                    except Exception:
                        pass
                root.destroy()


if __name__ == "__main__":
    unittest.main()

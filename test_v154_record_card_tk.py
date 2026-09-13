import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import visual
import v154_record_card_boll_fix  # noqa: F401 - applies final V1.5.4 UI/runtime repair


def _walk(root):
    try:
        children=root.winfo_children()
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk(child)


def _texts(root):
    out=[]
    for widget in _walk(root):
        try:
            text=str(widget.cget('text') or '')
        except Exception:
            continue
        if text:
            out.append(text)
    return out


@unittest.skipUnless(sys.platform == 'darwin', 'real Tk visibility smoke runs on Intel Mac job')
class V154RecordCardVisibilityTests(unittest.TestCase):
    def test_original_record_card_is_only_visible_log_ui(self):
        import tkinter as tk

        with tempfile.TemporaryDirectory(prefix='kaytrade-v154-record-') as folder:
            root_path=Path(folder)
            # Historical replay was added by the prior repair.  The restored
            # original card must not import it into the current session UI.
            (root_path/'events.log').write_text(
                '2026-09-14 02:00:00 旧历史日志不应注入运行记录卡片\n',
                encoding='utf-8',
            )
            root=tk.Tk(); ui=None
            try:
                with patch.object(app.App,'worker',lambda self:None), \
                     patch.object(app.messagebox,'showerror',side_effect=AssertionError):
                    ui=app.App(root,root_path)
                    root.update_idletasks(); root.update()

                    self.assertTrue(getattr(ui,'_v154_original_record_card_restored',False))
                    self.assertTrue(getattr(ui,'_v154_added_text_log_removed',False))
                    self.assertTrue(ui._v146_log_surface.winfo_ismapped())
                    self.assertFalse(ui.log.winfo_ismapped())
                    self.assertNotIn('旧历史日志不应注入运行记录卡片',' '.join(_texts(ui._v146_log_rows)))

                    ui.emit('log','当前会话运行记录卡片测试')
                    ui.drain(); root.update_idletasks(); root.update()
                    rendered=' '.join(_texts(ui._v146_log_rows))
                    self.assertIn('当前会话运行记录卡片测试',rendered)
                    self.assertIn('运行记录',_texts(ui._v146_log_surface))

                    # Score-detail header tells the user the role labels are explicit.
                    self.assertIn('必要/辅助',ui.score_table.heading('#0')['text'])

                    # The fixed execution-cost Card must blend into PANEL rather
                    # than expose the rectangular BG/black canvas gutter.
                    fixed_cards=[w for w in _walk(root) if isinstance(w,visual.Card) and getattr(w,'_v154_fixed_cost_gutter_removed',False)]
                    self.assertEqual(len(fixed_cards),1)
                    self.assertEqual(fixed_cards[0].cget('bg'),app.PANEL)
            finally:
                if ui is not None:
                    try:
                        ui.finished.set(); ui.thread.join(timeout=2); ui.lock.close()
                    except Exception:
                        pass
                root.destroy()


if __name__=='__main__':
    unittest.main()

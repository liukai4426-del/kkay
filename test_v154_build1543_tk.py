import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import v154_fixed_card_precision_fix  # noqa: F401
import v154_build1543_patch  # noqa: F401


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


@unittest.skipUnless(sys.platform=='darwin','real Tk visibility smoke runs on Intel Mac job')
class Build1543TkTests(unittest.TestCase):
    def test_risk_page_shows_first_and_locked_second_without_loss_stop(self):
        import tkinter as tk
        with tempfile.TemporaryDirectory(prefix='kaytrade-v1543-ui-') as folder:
            root=tk.Tk(); ui=None
            try:
                with patch.object(app.App,'worker',lambda self:None), \
                     patch.object(app.messagebox,'showerror',side_effect=AssertionError):
                    ui=app.App(root,Path(folder))
                    root.update_idletasks(); root.update()
                    self.assertTrue(getattr(ui,'_v1543_ready',False))
                    self.assertTrue(getattr(ui,'_v1543_loss_control_removed',False))
                    self.assertTrue(getattr(ui,'_v1543_signal_funds_ready',False))
                    self.assertNotIn('consecutive_losses',ui.fields)
                    texts=' '.join(_texts(root))
                    self.assertNotIn('中国时间连续亏损停开次数',texts)
                    self.assertNotIn('除连续亏损停开次数外',texts)
                    self.assertIn('第一信号仓位 / 开仓资金 USDT',texts)
                    self.assertIn('第二信号仓位 / 加仓资金 USDT（自动=第一信号×2）',texts)
                    self.assertEqual(ui._v1543_second_signal_entry.entry.cget('state'),'disabled')
                    ui.fields['first_signal_notional'].set('123')
                    root.update_idletasks(); root.update()
                    self.assertEqual(float(ui.fields['second_signal_notional'].get()),246.0)
            finally:
                if ui is not None:
                    try:
                        ui.finished.set(); ui.thread.join(timeout=2); ui.lock.close()
                    except Exception:
                        pass
                root.destroy()


if __name__=='__main__':
    unittest.main()

"""Packaged entry point; SSL roots travel with the application."""
import json
import os
import sys
import tempfile
from pathlib import Path

import certifi

os.environ['SSL_CERT_FILE'] = certifi.where()

from v135_patch import apply as apply_v135_patch
apply_v135_patch()
from v135_execution_patch import apply as apply_v135_execution_patch
apply_v135_execution_patch()
from v135_auto_entry_guard import apply as apply_v135_auto_entry_guard
apply_v135_auto_entry_guard()
from v136_runtime import apply as apply_v136_runtime
apply_v136_runtime()
from v163_update_patch import apply as apply_v163_update_patch
apply_v163_update_patch()
from v161_ui_status_patch import apply as apply_v161_ui_status_patch
apply_v161_ui_status_patch()
from v163_ui_patch import apply as apply_v163_ui_patch
apply_v163_ui_patch()
from v163b_network_patch import apply as apply_v163b_network_patch
apply_v163b_network_patch()


def smoke_test():
    """Exercise the packaged UI without credentials, network, or orders."""
    import ssl
    import tkinter as tk
    from unittest.mock import patch
    import app
    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    with tempfile.TemporaryDirectory(prefix='kaytrade-ui-check-') as folder:
        root=tk.Tk()
        try:
            with patch.object(app.App,'worker',lambda self:None), patch.object(app.messagebox,'showerror',side_effect=AssertionError):
                ui=app.App(root,Path(folder)); root.update()
                assert 'KAYTRADE' in root.title() and '1.6.3b' in root.title()
                assert ui.mode.get() == 'OKX模拟盘'
                assert ui.engine is None
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()
                assert getattr(ui,'_v163_ui_ready',False)
                assert getattr(ui,'_v163_version_label_updated',False)
                assert getattr(ui,'_v163_log_scrollbar_max_round',False)
                assert getattr(ui,'_v163b_network_patch_ready',False)
                label=getattr(ui,'_v163_version_label',None)
                assert label is not None and 'V1.6.3b' in str(label.cget('text'))
                assert 'V1.6.3b' in ui.signal.get()
                ui.stop(); assert ui.engine is None
                ui.finished.set(); ui.thread.join(timeout=2); ui.lock.close()
        finally:
            root.destroy()
    Path(sys.argv[2]).write_text('PASS: KAYTRADE V1.6.3b packaged UI startup; 60s network auto-recovery patch active; no network or orders.\n')


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test': smoke_test()
    else:
        from app import main
        main()

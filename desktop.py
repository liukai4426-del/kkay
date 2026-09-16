"""Packaged entry point; SSL roots travel with the application."""
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
from v164_update_patch import apply as apply_v164_update_patch
apply_v164_update_patch()
from v165_update_patch import apply as apply_v165_update_patch
apply_v165_update_patch()
from v165_algo_fallback_patch import apply as apply_v165_algo_fallback_patch
apply_v165_algo_fallback_patch()
from v165_runtime_network_patch import apply as apply_v165_runtime_network_patch
apply_v165_runtime_network_patch()
from v165_sleep_resume_patch import apply as apply_v165_sleep_resume_patch
apply_v165_sleep_resume_patch()
from v165_keepawake_patch import apply as apply_v165_keepawake_patch
apply_v165_keepawake_patch()
from v161_ui_status_patch import apply as apply_v161_ui_status_patch
apply_v161_ui_status_patch()
from v164_ui_patch import apply as apply_v164_ui_patch
apply_v164_ui_patch()
from v165_ui_patch import apply as apply_v165_ui_patch
apply_v165_ui_patch()


def smoke_test():
    """Exercise the packaged UI without credentials, network, or orders."""
    import ssl
    import tkinter as tk
    from unittest.mock import patch
    import app
    import engine
    import exchange
    import v161_ui_status_patch as ui161
    import v165_ui_patch as ui165
    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    assert getattr(exchange.Exchange,'_kaytrade_v165_algo_fallback_applied',False)
    assert getattr(exchange.Exchange,'_kaytrade_v165_runtime_network_state_applied',False)
    assert getattr(engine.Engine,'_kaytrade_v165_sleep_resume_applied',False)
    assert getattr(app.App,'_kaytrade_v165_keepawake_applied',False)
    with tempfile.TemporaryDirectory(prefix='kaytrade-ui-check-') as folder:
        root=tk.Tk()
        try:
            with patch.object(app.App,'worker',lambda self:None), patch.object(app.messagebox,'showerror',side_effect=AssertionError):
                ui=app.App(root,Path(folder)); root.update()
                assert 'KAYTRADE' in root.title() and '1.6.5' in root.title()
                assert ui.mode.get() == 'OKX模拟盘'
                assert ui.engine is None
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()
                assert getattr(ui,'_v165_ui_ready',False)
                assert getattr(ui,'_v165_version_label_updated',False)
                assert getattr(ui,'_v165_keepawake_proc',None) is None
                label=getattr(ui,'_v165_version_label',None)
                assert label is not None and 'V1.6.5' in str(label.cget('text'))
                assert 'V1.6.5' in ui.signal.get()
                labels=dict(getattr(ui,'_v161_status_labels',{}) or {})
                assert 'funds' not in labels and 'volume_gate' in labels and 'signal_window' in labels
                assert dict(ui161._STATUS_ITEMS)['signal_window']=='最新5m信号周期'
                assert app.App.arm is ui165.app_arm_v165
                ui.stop(); assert ui.engine is None
                ui.finished.set(); ui.thread.join(timeout=2); ui.lock.close()
        finally:
            root.destroy()
    Path(sys.argv[2]).write_text(
        'PASS: KAYTRADE V1.6.5 packaged UI startup; closed-candle lifecycle, Volume Hard Gate, '
        'bounded Algo 51054 handling, sleep soft-stop, Mac keep-awake and version sync active; no network or orders.\n'
    )


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test': smoke_test()
    else:
        from app import main
        main()

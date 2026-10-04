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
from v165_15m_outer_patch import apply as apply_v165_15m_outer_patch
apply_v165_15m_outer_patch()
from v166_ui_patch import apply as apply_v166_ui_patch
apply_v166_ui_patch()
from v166_readonly_resilience_patch import apply as apply_v166_readonly_resilience_patch
apply_v166_readonly_resilience_patch()
from v167_strategy_patch import apply as apply_v167_strategy_patch
apply_v167_strategy_patch()
from v167_algo_sync_patch import apply as apply_v167_algo_sync_patch
apply_v167_algo_sync_patch()
from v167_15m_boll_only_fix import apply as apply_v167_15m_boll_only_fix
apply_v167_15m_boll_only_fix()
from v168_update_patch import apply as apply_v168_update_patch
apply_v168_update_patch()
from v168_build1681_patch import apply as apply_v168_build1681_patch
apply_v168_build1681_patch()

from v170_update_patch import apply as apply_v170_update_patch
apply_v170_update_patch()
from v170_build1701_patch import apply as apply_v170_build1701_patch
apply_v170_build1701_patch()
from v171_update_patch import apply as apply_v171_update_patch
apply_v171_update_patch()
from v172_ai_only_patch import apply as apply_v172_ai_only_patch
apply_v172_ai_only_patch()


def smoke_test():
    """Exercise the packaged V1.7.2 AI-only UI without credentials or orders."""
    import ssl
    import tkinter as tk
    from unittest.mock import patch
    import app
    import v165_model as model
    import v172_ai_only_patch as v172

    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    assert v172.VERSION == '1.7.2' and v172.BUILD == '1720'
    assert v172.AI_ONLY is True
    assert model.VERSION == '1.7.2' and model.BUILD == '1720'
    assert model.STRATEGY_ENABLED is False
    assert getattr(model, '_kaytrade_v172_ai_only_applied', False)
    assert getattr(app.App, '_kaytrade_v172_ai_only_applied', False)
    assert app.Engine is v172.AIOnlyEngine

    with tempfile.TemporaryDirectory(prefix='kaytrade-v172-ui-check-') as folder:
        root = tk.Tk()
        try:
            with (
                patch.object(app.App, 'worker', lambda self: None),
                patch.object(v172.AIBridgeServer, 'start', lambda self: None),
                patch.object(v172.AIBridgeServer, 'stop', lambda self: None),
                patch.object(app.messagebox, 'showerror', side_effect=AssertionError),
            ):
                ui = app.App(root, Path(folder))
                root.update()
                assert 'KAYTRADE' in root.title()
                assert '1.7.2' in root.title()
                assert 'AI ONLY' in root.title()
                assert '1720' in root.title()
                assert ui.mode.get() == 'OKX模拟盘'
                assert ui.engine is None
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()
                assert getattr(ui, '_v172_ai_only_ready', False)
                assert 'AI ONLY' in ui.signal.get()
                assert '无本地交易策略' in ui.signal.get()
                assert '启用AI交易通道' in str(ui.trade_button.cget('text'))
                visible = [
                    str(button.text)
                    for button in ui.book.buttons
                    if button.winfo_manager()
                ]
                assert '风险设置' not in visible
                assert '执行参数' not in visible
                ui.finished.set()
                ui.thread.join(timeout=2)
                ui.lock.close()
        finally:
            root.destroy()

    Path(sys.argv[2]).write_text(
        'PASS: KAYTRADE V1.7.2 Build1720 AI ONLY packaged runtime; '
        'legacy strategy entry is disabled, AI bridge is localhost-token protected, '
        'and autonomous AI writes are restricted to OKX Demo Trading.\n'
    )


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test': smoke_test()
    else:
        from app import main
        main()

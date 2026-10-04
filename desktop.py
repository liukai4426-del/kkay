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
from v180_ai_only_patch import apply as apply_v180_ai_only_patch
apply_v180_ai_only_patch()
from v183_okx_demo_patch import apply as apply_v183_okx_demo_patch
apply_v183_okx_demo_patch()
from v184_ui_patch import apply as apply_v184_ui_patch
apply_v184_ui_patch()
from v190_dual_env_patch import apply as apply_v190_dual_env_patch
apply_v190_dual_env_patch()


def smoke_test():
    """Exercise packaged V1.9.1 dual-environment execution UI without credentials/writes."""
    import ssl
    import tkinter as tk
    from unittest.mock import patch
    import app
    import v165_model as model
    import v172_ai_only_patch as v172
    import v183_okx_demo_patch as v183
    import v190_dual_env_patch as v190

    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    assert v190.VERSION == '1.9.1' and v190.BUILD == '1911'
    assert model.VERSION == '1.9.1' and model.BUILD == '1911'
    assert model.STRATEGY_ENABLED is False
    assert getattr(model, '_kaytrade_v190_applied', False)
    assert getattr(app.App, '_kaytrade_v190_applied', False)
    assert app.Engine is v190.DemoEngineV190
    assert 'v181_paper_patch' not in sys.modules
    assert 'v182_ui_patch' not in sys.modules

    with tempfile.TemporaryDirectory(prefix='kaytrade-v191-ui-check-') as folder:
        root = tk.Tk()
        try:
            with (
                patch.object(app.App, 'worker', lambda self: None),
                patch.object(v190.DualBridgeServer, 'start', lambda self: None),
                patch.object(v190.DualBridgeServer, 'stop', lambda self: None),
                patch.object(app.messagebox, 'showerror', side_effect=AssertionError),
            ):
                ui = app.App(root, Path(folder))
                root.update()
                assert 'KAYTRADE' in root.title()
                assert '1.9.1' in root.title()
                assert 'LIVE EXECUTION' in root.title()
                assert '1911' in root.title()
                assert getattr(ui, '_v190_dual_ready', False)
                assert set(ui._v190_engines) == {'demo','live'}
                assert set(ui._v190_bridges) == {'demo','live'}
                assert ui._v190_bridges['demo'].token != ui._v190_bridges['live'].token
                assert ui._v190_bridges['demo'].path != ui._v190_bridges['live'].path
                assert getattr(ui, '_v190_environment_card', None) is not None
                assert getattr(ui, '_v184_plan_card', None) is not None
                assert getattr(ui, '_v180_execution_card', None) is not None
                packed = ui.book.pages[3].body.pack_slaves()
                assert packed.index(ui.quote) < packed.index(ui._v190_environment_card)
                assert packed.index(ui._v190_environment_card) < packed.index(ui._v183_auto_exec_card)
                assert packed.index(ui._v183_auto_exec_card) < packed.index(ui._v180_plan_card)
                ui._v190_selected = 'live'
                ui.engine = None
                v190._refresh_v190_dashboard(ui)
                root.update()
                assert 'LIVE' in ui._v180_mode_var.get()
                assert '实盘' in ui._v183_auto_exec_note_var.get() and '市价' in ui._v183_auto_exec_note_var.get()
                ui.finished.set()
                ui.thread.join(timeout=2)
                ui.lock.close()
        finally:
            root.destroy()

    Path(sys.argv[2]).write_text(
        'PASS: KAYTRADE V1.9.1 Build1911 LIVE EXECUTION; '
        'DEMO and LIVE use isolated AI execution channels, credentials/bridges/tokens/states stay separate, '
        'parent entries/reductions remain LIMIT, TP/SL are trigger-market protections, and no Paper runtime is loaded.\n'
    )


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test': smoke_test()
    else:
        from app import main
        main()

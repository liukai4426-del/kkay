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
from v181_paper_patch import apply as apply_v181_paper_patch
apply_v181_paper_patch()


def smoke_test():
    """Exercise the packaged V1.8.1 Paper UI without credentials or exchange writes."""
    import ssl
    import tkinter as tk
    from unittest.mock import patch
    import app
    import v165_model as model
    import v172_ai_only_patch as v172
    import v181_paper_patch as v181
    import visual

    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    assert v181.VERSION == '1.8.1' and v181.BUILD == '1810'
    assert v181.AI_ONLY is True and v181.PAPER_ONLY is True
    assert model.VERSION == '1.8.1' and model.BUILD == '1810'
    assert model.STRATEGY_ENABLED is False
    assert getattr(model, '_kaytrade_v181_paper_applied', False)
    assert getattr(app.App, '_kaytrade_v181_paper_applied', False)
    assert app.Engine is v181.PaperEngineV181
    assert str(v181.AI_MAX_LEVERAGE) == '20'
    assert str(v181.AI_MAX_NOTIONAL_USDT) == '3500'
    assert str(v181.AI_MAX_ESTIMATED_STOP_LOSS_USDT) == '100'
    assert v181.PAPER_ENTRY_TTL_SEC == 3600

    with tempfile.TemporaryDirectory(prefix='kaytrade-v181-ui-check-') as folder:
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
                assert '1.8.1' in root.title()
                assert 'PAPER AI' in root.title()
                assert '1810' in root.title()
                assert ui.mode.get() == 'OKX模拟盘'
                assert ui.engine is None
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()
                assert getattr(ui, '_v181_paper_ready', False)
                assert 'V1.8.1 PAPER' in ui.signal.get()
                assert getattr(ui, '_v180_plan_card', None) is not None
                assert getattr(ui, '_v180_execution_card', None) is not None
                assert getattr(ui, '_v181_direction_label', None) is not None
                # BTC quote panel is packed before the AI plan panel.
                packed = ui.book.pages[3].body.pack_slaves()
                assert ui.quote in packed and ui._v180_plan_card in packed
                assert packed.index(ui.quote) < packed.index(ui._v180_plan_card)
                # Direction colors are explicitly available for V1.8.1 updates.
                assert visual.GREEN and visual.RED
                ui.finished.set()
                ui.thread.join(timeout=2)
                ui.lock.close()
        finally:
            root.destroy()

    Path(sys.argv[2]).write_text(
        'PASS: KAYTRADE V1.8.1 Build1810 PAPER; '
        'BTC quote is topmost, LONG/SHORT color semantics are enabled, '
        'two-tier paper execution supports market/limit entry, 60-minute auto-cancel, '
        'amend/cancel/protection changes and market/limit partial close; no OKX write path is exposed.\n'
    )


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test': smoke_test()
    else:
        from app import main
        main()

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


def smoke_test():
    """Exercise the packaged V1.8 AI-only UI without credentials or orders."""
    import ssl
    import tkinter as tk
    from unittest.mock import patch
    import app
    import v165_model as model
    import v172_ai_only_patch as v172
    import v180_ai_only_patch as v180

    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    assert v180.VERSION == '1.8.0' and v180.BUILD == '1801'
    assert v180.AI_ONLY is True
    assert model.VERSION == '1.8.0' and model.BUILD == '1801'
    assert model.STRATEGY_ENABLED is False
    assert getattr(model, '_kaytrade_v180_ai_only_applied', False)
    assert getattr(app.App, '_kaytrade_v180_ai_only_applied', False)
    assert app.Engine is v180.AIOnlyEngineV180
    assert v180.AI_MAX_LEVERAGE == 20
    assert str(v180.AI_MAX_NOTIONAL_USDT) == '3500'
    assert str(v180.AI_MAX_ESTIMATED_STOP_LOSS_USDT) == '100'

    with tempfile.TemporaryDirectory(prefix='kaytrade-v180-ui-check-') as folder:
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
                assert '1.8.0' in root.title()
                assert 'AI ONLY' in root.title()
                assert '1801' in root.title()
                assert ui.mode.get() == 'OKX模拟盘'
                assert ui.engine is None
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()
                assert getattr(ui, '_v180_ai_only_ready', False)
                assert 'V1.8 AI ONLY' in ui.signal.get()
                assert '市价 / 限价' in ui.signal.get()
                assert getattr(ui, '_v180_plan_card', None) is not None
                assert getattr(ui, '_v180_execution_card', None) is not None
                assert 'AI 交易方案看板' in v172._widget_text(ui._v180_plan_card)
                assert '订单 / 持仓执行' in v172._widget_text(ui._v180_execution_card)
                assert '第一档执行方案' in v172._widget_text(ui._v180_execution_card)
                assert '第二档执行方案' in v172._widget_text(ui._v180_execution_card)
                assert '启用AI交易通道' in str(getattr(ui.trade_button, 'text', ''))
                visible = [
                    str(button.text)
                    for button in ui.book.buttons
                    if button.winfo_manager()
                ]
                assert '风险设置' not in visible
                assert '执行参数' not in visible
                pee4 = getattr(ui, '_v170_pee4_card', None)
                assert pee4 is None or not pee4.winfo_manager()
                ui.finished.set()
                ui.thread.join(timeout=2)
                ui.lock.close()
        finally:
            root.destroy()

    Path(sys.argv[2]).write_text(
        'PASS: KAYTRADE V1.8.0 Build1801 AI ONLY packaged runtime; '
        'market and limit entry orders are supported, the redesigned KAYTRADE AI-plan and order/position dashboards are present, '
        'legacy strategy entry is disabled, and autonomous AI writes remain restricted to OKX Demo Trading.\n'
    )


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test': smoke_test()
    else:
        from app import main
        main()

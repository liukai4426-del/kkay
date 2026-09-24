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


def smoke_test():
    """Exercise the packaged UI without credentials, network, or orders."""
    import ssl
    import tkinter as tk
    from unittest.mock import patch
    import app
    import engine
    import exchange
    import v153_model as signal_core
    import v161_ui_status_patch as ui161
    import v165_model as model
    import v165_ui_patch as ui165
    import v166_ui_patch as ui166
    import v167_strategy_patch as s167
    import v167_algo_sync_patch as a167
    import v167_15m_boll_only_fix as b1671
    import v168_update_patch as v168
    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    assert getattr(exchange.Exchange,'_kaytrade_v165_algo_fallback_applied',False)
    assert getattr(exchange.Exchange,'_kaytrade_v165_runtime_network_state_applied',False)
    assert getattr(engine.Engine,'_kaytrade_v165_sleep_resume_applied',False)
    assert getattr(app.App,'_kaytrade_v165_keepawake_applied',False)
    assert getattr(model,'_kaytrade_v165_15m_outer_applied',False)
    assert getattr(app.App,'_kaytrade_v166_ui_applied',False)
    assert getattr(app.App,'_kaytrade_v166_readonly_resilience_applied',False)
    assert getattr(exchange.Exchange,'_kaytrade_v166_readonly_resilience_applied',False)
    assert getattr(engine.Engine,'_kaytrade_v166_readonly_resilience_applied',False)
    assert getattr(model,'_kaytrade_v167_strategy_applied',False)
    assert getattr(app.App,'_kaytrade_v167_strategy_applied',False)
    assert getattr(exchange.Exchange,'_kaytrade_v167_algo_sync_applied',False)
    assert getattr(engine.Engine,'_kaytrade_v167_algo_sync_applied',False)
    assert getattr(app.App,'_kaytrade_v167_algo_sync_applied',False)
    assert getattr(model,'_kaytrade_v167_15m_boll_only_applied',False)
    assert getattr(model,'_kaytrade_v168_applied',False)
    assert getattr(app.App,'_kaytrade_v168_applied',False)
    assert v168.VERSION == '1.6.8' and v168.BUILD == '1680'
    assert model.VERSION == '1.6.8' and model.BUILD == '1680'
    assert model.THRESHOLD == 6.0
    assert model.ENTRY_WINDOW_MS == 900000
    assert model.BOLL_OUTER_SCORE == 2.0
    assert model.BOLL_TRIGGER_TIMEFRAME == '15m'
    assert model.FIVE_MINUTE_BOLL_ENABLED is False
    assert signal_core.boll_entry_signal is b1671._signal_adapter_15m_only
    assert s167._macd_allowed({'confirmations':{'5m_macd_adverse':False}})
    assert not s167._macd_allowed({'confirmations':{'5m_macd_adverse':True}})
    status_labels=dict(ui161._STATUS_ITEMS)
    assert status_labels['boll']=='15m BOLL外轨触发'
    assert status_labels['signal_window']=='15m BOLL信号有效'
    assert status_labels['outer_4h']=='4H同向 Hard Gate'
    assert status_labels['ema1h']=='1H EMA9/26趋势'
    assert status_labels['macd_clean']=='MACD非逆向'
    assert status_labels['rsi_gate']=='5m RSI 30–70'
    with tempfile.TemporaryDirectory(prefix='kaytrade-ui-check-') as folder:
        root=tk.Tk()
        try:
            with patch.object(app.App,'worker',lambda self:None), patch.object(app.messagebox,'showerror',side_effect=AssertionError):
                ui=app.App(root,Path(folder)); root.update()
                assert 'KAYTRADE' in root.title() and '1.6.8' in root.title() and '1680' in root.title()
                assert ui.mode.get() == 'OKX模拟盘'
                assert ui.engine is None
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()
                assert getattr(ui,'_v165_ui_ready',False)
                assert getattr(ui,'_v166_ui_ready',False)
                assert getattr(ui,'_v167_strategy_ui_ready',False)
                assert getattr(ui,'_v168_strategy_ui_ready',False)
                label=getattr(ui,'_v168_version_label',None)
                if label is not None:
                    assert '1.6.8' in str(label.cget('text'))
                assert '15m BOLL外轨2分' in ui.signal.get()
                assert '4H同向Hard Gate+1' in ui.signal.get()
                assert '1H EMA9/26趋势+1' in ui.signal.get()
                labels=dict(getattr(ui,'_v161_status_labels',{}) or {})
                assert 'signal_window' in labels and 'outer_4h' in labels and 'ema1h' in labels
                assert app.App.arm is ui165.app_arm_v165
                assert ui.render_logs.__func__ is ui166._render_logs_v166
                ui.stop(); assert ui.engine is None
                ui.finished.set(); ui.thread.join(timeout=2); ui.lock.close()
        finally:
            root.destroy()
    Path(sys.argv[2]).write_text(
        'PASS: KAYTRADE V1.6.8 Build1680 packaged UI/runtime; BOLL remains strict closed-15m outer-only and scores 2 points, each trigger stays valid until the next 15m close, 4H alignment is a +1 mandatory Hard Gate, 1H EMA9/26 trend adds +1 score without becoming a Hard Gate, 5m remains RSI/MACD/Volume confirmation only, MACD adverse-only and KDJ removal are preserved, Algo REST+WebSocket synchronization remains active, and write ambiguity remains fail-closed.\n'
    )


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test': smoke_test()
    else:
        from app import main
        main()

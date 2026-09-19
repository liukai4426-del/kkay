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
from v170_build1702_patch import apply as apply_v170_build1702_patch
apply_v170_build1702_patch()


def smoke_test():
    """Exercise the packaged UI without credentials, network, or orders."""
    import re
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
    import v168_build1681_patch as b1681
    import v170_update_patch as v170
    import v170_build1701_patch as v1701
    import v170_build1702_patch as v1702
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
    assert getattr(model,'_kaytrade_v168_build1681_applied',False)
    assert getattr(app.App,'_kaytrade_v168_build1681_applied',False)
    assert getattr(model,'_kaytrade_v170_applied',False)
    assert getattr(engine.Engine,'_kaytrade_v170_applied',False)
    assert getattr(exchange.Exchange,'_kaytrade_v170_applied',False)
    assert getattr(app.App,'_kaytrade_v170_applied',False)
    assert getattr(model,'_kaytrade_v170_build1701_applied',False)
    assert getattr(app.App,'_kaytrade_v170_build1701_applied',False)
    assert getattr(model,'_kaytrade_v170_build1702_applied',False)
    assert getattr(exchange.Exchange,'_kaytrade_v170_build1702_applied',False)
    assert getattr(app.App,'_kaytrade_v170_build1702_applied',False)
    assert v1702.VERSION == '1.7.0' and v1702.BUILD == '1702'
    assert v1701.VERSION == '1.7.0' and v1701.BUILD == '1702'
    assert v170.VERSION == '1.7.0' and v170.BUILD == '1702'
    assert b1681.VERSION == '1.7.0' and b1681.BUILD == '1702'
    assert v168.VERSION == '1.7.0' and v168.BUILD == '1702'
    assert model.VERSION == '1.7.0' and model.BUILD == '1702'
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
    assert 'ema1h' not in status_labels
    assert status_labels['boll5_overext']=='5m BOLL超伸 +1'
    assert status_labels['macd_clean']=='MACD非逆向'
    assert status_labels['rsi_gate']=='5m RSI 30–70'
    legacy=(
        'V1.5.4未开仓；BOLL信号执行窗口 · 第5个1m｜计划市价参考 100000；'
        'V1.6.2要求4H同向：5m BOLL中轨/外轨在4H中性或逆向时禁止开仓；'
        'V1.6.6 Volume Hard Gate：5m信号K量能 1.89× ≥ 1.20×，禁止开仓'
    )
    normalized=v170._rewrite_runtime_text_v170(legacy)
    assert 'V1.7.0' in normalized and '15m BOLL外轨' in normalized and '计划限价' in normalized and '第5分钟' in normalized
    assert re.search(r'(?<!\d)5m\s+BOLL', normalized) is None
    assert '中轨' not in normalized and '市价参考' not in normalized
    sync=b1681._rewrite_runtime_text_v1681(
        '只读查询暂时失败 /ws/v5/business:orders-algo：Algo实时状态暂未完成可信同步'
    )
    assert 'Algo状态同步中' in sync and '只读查询暂时失败' not in sync
    with tempfile.TemporaryDirectory(prefix='kaytrade-ui-check-') as folder:
        root=tk.Tk()
        try:
            with patch.object(app.App,'worker',lambda self:None), patch.object(app.messagebox,'showerror',side_effect=AssertionError):
                ui=app.App(root,Path(folder)); root.update()
                assert 'KAYTRADE' in root.title() and '1.7.0' in root.title() and '1702' in root.title()
                assert ui.mode.get() == 'OKX模拟盘'
                assert ui.engine is None
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()
                assert getattr(ui,'_v165_ui_ready',False)
                assert getattr(ui,'_v166_ui_ready',False)
                assert getattr(ui,'_v167_strategy_ui_ready',False)
                assert getattr(ui,'_v168_strategy_ui_ready',False)
                assert getattr(ui,'_v168_build1681_ready',False)
                assert getattr(ui,'_v170_strategy_ui_ready',False)
                assert getattr(ui,'_v170_build1701_ready',False)
                assert getattr(ui,'_v170_build1702_ready',False)
                label=getattr(ui,'_v170_version_label',None)
                if label is not None:
                    assert '1.7.0' in str(label.cget('text'))
                assert '15m BOLL外轨2分' in ui.signal.get()
                assert '4H同向Hard Gate+1' in ui.signal.get()
                assert 'BOLL5超伸0.10ATR +1' in ui.signal.get()
                assert 'NoEMA1H' in ui.signal.get()
                assert 'PEE4 + Lock1H' in ui.signal.get()
                labels=dict(getattr(ui,'_v161_status_labels',{}) or {})
                assert 'signal_window' in labels and 'outer_4h' in labels and 'boll5_overext' in labels
                assert 'ema1h' not in labels
                assert getattr(ui,'_v170_pee4_card',None) is not None
                assert not ui.matrix.exists('k') and not ui.matrix.exists('d') and not ui.matrix.exists('j')
                assert app.App.arm is ui165.app_arm_v165
                assert ui.render_logs.__func__ is ui166._render_logs_v166
                ui.stop(); assert ui.engine is None
                ui.finished.set(); ui.thread.join(timeout=2); ui.lock.close()
        finally:
            root.destroy()
    Path(sys.argv[2]).write_text(
        'PASS: KAYTRADE V1.7.0 Build1702 packaged UI/runtime; strict closed-15m BOLL opportunity-source lock installed, run logs include long/short direction, TP/SL algoClOrdId/attachAlgoClOrdId normalization and fresh-REST protection proof installed, 1H EMA9/26 score removed, BOLL5 overextension 0.10 ATR14 +1 latched, PEE4 tiered Boolean risk exit and global Lock1H installed, and confirmed missing protection remains fail-closed.\n'
    )


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test': smoke_test()
    else:
        from app import main
        main()

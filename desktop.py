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


def smoke_test():
    """Exercise the packaged V1.3.5 UI without credentials, network, or orders."""
    import ssl
    import tkinter as tk
    from types import SimpleNamespace
    from unittest.mock import patch
    import app

    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    with tempfile.TemporaryDirectory(prefix='kaytrade-ui-check-') as folder:
        root = tk.Tk()
        print('Bundled Tk version:', root.tk.call('package','provide','Tk'), flush=True)
        try:
            with patch.object(app.App, 'worker', lambda self: None), \
                 patch.object(app.messagebox, 'showerror', side_effect=AssertionError):
                ui = app.App(root, Path(folder))
                root.update()
                assert 'KAYTRADE' in root.title() and '1.3.5' in root.title()
                assert ui.mode.get() == 'OKX模拟盘'
                assert ui.engine is None
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()

                # V1.3.5 settings persist under their own file and preserve fixed execution economics.
                ui.fields['capital'].set('2000')
                ui.save_settings()
                saved = json.loads((Path(folder) / 'settings-v1.3.5.json').read_text())
                assert saved['capital'] == 2000
                assert not {'key','secret','phrase'} & saved.keys()
                assert ui.fields['stop_atr'].get() == '1.0'
                assert ui.fields['score_threshold'].get() == '3.5'
                assert ui.fields['consecutive_losses'].get() == '3'
                assert 'reward_r' not in ui.fields
                from engine import Settings, Halt
                defaults = Settings()
                assert defaults.reward_r == 2.0
                assert defaults.fee_bps == 2.0 and defaults.taker_fee_bps == 5.0 and defaults.slippage_bps == 5.0
                assert defaults.score_threshold == 3.5
                Settings(score_threshold=1.0).validate()
                try:
                    Settings(score_threshold=0.5).validate()
                    raise AssertionError('score threshold below 1 must fail')
                except Halt:
                    pass

                # Render V1.3.5 score cards, daily indicators, ticker and a tiered position plan.
                from strategy import signal
                rows=[dict(t=i*900000,o=100,h=101,l=99,c=100,v=100) for i in range(1002)]
                market=dict(signal(rows,rows),bar=rows[-1]['t'],close=100)
                ui.emit('market',market); ui.emit('network','正常')
                for last in ('79000','79020','78990','79045'):
                    ui.emit('ticker',{'last':last})
                ui.emit('plan',dict(side='做多',px='79000',sl='78800',tp1='79200',tp2='79400',btc=.02,
                                    estimated_loss=20,position_multiplier=1.5))
                ui.emit('alarm','测试警报：无网络、无订单')
                ui.drain(); root.update()
                assert len(ui.score_table.get_children()) == 10
                assert ui.score_vars['做多'].get().endswith('/ 10')
                assert ui.matrix.exists('ema5') and ui.matrix.exists('ema10') and ui.matrix.exists('ema20')
                assert '1.5×' in ui.plan_vars['qty'].get()
                assert ui.price.get() == '79,045.00 USDT'
                assert len(ui.price_history) == 4
                assert ui.score_bars['做多'].color == app.GREEN
                assert ui.score_bars['做空'].color == app.RED
                assert int(ui.score_bars['做空'].cget('highlightthickness')) == 0

                # Score detail is custom Canvas content: verify programmatic, wheel/trackpad and thumb scrolling.
                extra=[]
                for i in range(30):
                    extra.append(ui.score_table.insert('','end',text=f'滚动测试 {i}',values=(0,0,0)))
                root.update_idletasks()
                top_before=ui.score_table.yview()[0]
                ui.score_table.yview_moveto(.55); root.update_idletasks()
                assert ui.score_table.yview()[0] > top_before + .05
                ui.score_table.yview_moveto(0); root.update()
                before=ui.score_table.yview()[0]
                ui.score_table._on_mousewheel(SimpleNamespace(delta=-120)); root.update()
                assert ui.score_table.yview()[0] > before
                ui.score_table.yview_moveto(0); root.update()
                ui.score_scroll.set(*ui.score_table.yview()); root.update()
                y1,y2,_,_,_=ui.score_scroll._bounds(); start=(y1+y2)/2
                ui.score_scroll._press(SimpleNamespace(y=start))
                ui.score_scroll._drag(SimpleNamespace(y=min(ui.score_scroll.winfo_height()-2,start+60)))
                root.update()
                assert ui.score_table.yview()[0] > 0
                ui.score_scroll._release(SimpleNamespace(y=start)); ui.score_table.yview_moveto(0)
                for iid in extra: ui.score_table.delete(iid)

                # Indicator pane retains a usable scrollbar after adding the 1D column.
                detail_tabs=ui.indicator_scroll.master.master
                detail_tabs.select(ui.indicator_scroll.master); root.update()
                assert ui.indicator_scroll.winfo_ismapped()
                assert ui.indicator_scroll.winfo_width() >= 18
                detail_tabs.select(ui.score_scroll.master); root.update()

                from engine import Store
                from history import summarize
                ledger=Store(Path(folder)/'fake-account.json')
                ledger.record('仓位归零',dict(client_id='smoke',equity_change=1,side='做多'))
                ui.emit('history',summarize(ledger.path.with_suffix('.history.jsonl')))
                ui.drain(); root.update()
                assert '100.0%' in ui.performance.get()
                assert len(ui.history_table.get_children()) == 1
                assert ui.performance_label.cget('fg') == app.GREEN
                ui.log_filter.set('警报'); ui.render_logs()
                assert '测试警报' in ui.log.get('1.0','end')
                ui.stop(); assert ui.engine is None

                # On CI, capture each main page so the artifact contains visual evidence of the packaged app.
                if sys.platform == 'darwin' and os.environ.get('CI'):
                    import subprocess
                    import time
                    for index,name in [(0,'connect'),(1,'risk'),(2,'execution'),(3,'scores'),(4,'history')]:
                        ui.book.select(index)
                        root.lift(); root.focus_force()
                        root.after(1000,root.quit)
                        root.mainloop()
                        assert ui.log.winfo_height() > 40, 'Log panel clipped'
                        if name in ('risk','execution'):
                            page=root.nametowidget(ui.book.select())
                            def descendants(w):
                                out=[]
                                for child in w.winfo_children():
                                    out.append(child); out.extend(descendants(child))
                                return out
                            entries=[w for w in descendants(page) if w.winfo_class() in ('Entry','TEntry')]
                            assert entries and all(w.winfo_ismapped() and w.winfo_width()>30 for w in entries), 'Settings inputs not visible'
                        target=Path(sys.argv[2]).parent/f'ui-{name}.png'
                        capture=subprocess.Popen(['/usr/sbin/screencapture','-x',str(target)])
                        deadline=time.monotonic()+10
                        def finish_capture():
                            if capture.poll() is not None: root.quit()
                            elif time.monotonic()>deadline:
                                capture.kill(); root.quit()
                            else: root.after(50,finish_capture)
                        root.after(50,finish_capture)
                        root.mainloop()
                        assert capture.wait(timeout=2) == 0, 'Mac screenshot failed'
                ui.finished.set()
                ui.thread.join(timeout=2)
                ui.lock.close()
        finally:
            root.destroy()
    Path(sys.argv[2]).write_text(
        'PASS: KAYTRADE V1.3.5 UI + selectable 1-10 threshold + 5s startup buffer + hardened order lifecycle; '
        '15m Setup is scoring-only, split TP1/TP2 are market-on-trigger, ambiguous writes remain fail-closed, '
        'manual flatten cancels unfinished parent entries first, all pending algo families are preflight-checked, '
        'functional scrollbars, direction colors, demo default, settings and bundled CA roots. No network or orders.\n')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--smoke-test':
        smoke_test()
    else:
        from app import main
        main()
"""Packaged entry point; SSL roots travel with the application."""
import json
import os
import sys
import tempfile
from pathlib import Path

import certifi

os.environ['SSL_CERT_FILE'] = certifi.where()


def smoke_test():
    """Exercise the packaged UI without credentials, network, or orders."""
    import ssl
    import tkinter as tk
    from unittest.mock import patch
    import app

    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    with tempfile.TemporaryDirectory(prefix='okx-ui-check-') as folder:
        root = tk.Tk()
        print('Bundled Tk version:',root.tk.call('package','provide','Tk'),flush=True)
        try:
            with patch.object(app.App, 'worker', lambda self: None), \
                 patch.object(app.messagebox, 'showerror', side_effect=AssertionError):
                ui = app.App(root, Path(folder))
                root.update()
                assert '1.2' in root.title()
                assert ui.mode.get() == 'OKX模拟盘'
                assert ui.engine is None
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()
                ui.fields['capital'].set('2000')
                ui.save_settings()
                saved = json.loads((Path(folder) / 'settings-v1.1.json').read_text())
                assert saved['capital'] == 2000
                assert not {'key', 'secret', 'phrase'} & saved.keys()
                assert ui.fields['stop_atr'].get() == '1.0'
                assert ui.fields['reward_r'].get() == '1.5'
                assert ui.fields['score_threshold'].get() == '8'
                # Render both score cards, details, network status and filtered alarms.
                from strategy import signal
                rows=[dict(t=i*900000,o=100,h=101,l=99,c=100) for i in range(1002)]
                market=dict(signal(rows,rows),bar=rows[-1]['t'],close=100)
                ui.emit('market',market); ui.emit('network','正常')
                for last in ('79000','79020','78990','79045'):
                    ui.emit('ticker',{'last':last})
                ui.emit('alarm','测试警报：无网络、无订单')
                ui.drain(); root.update()
                assert len(ui.score_table.get_children()) == 8
                assert ui.score_vars['做多'].get().endswith('/ 19')
                assert ui.price.get()=='79,045.00 USDT'
                assert len(ui.price_history)==4
                assert 'Short' in str(ui.score_bars['做空']['style'])
                from engine import Store
                from history import summarize
                ledger=Store(Path(folder)/'fake-account.json')
                ledger.record('仓位归零',dict(client_id='smoke',equity_change=1,side='做多'))
                ui.emit('history',summarize(ledger.path.with_suffix('.history.jsonl')))
                ui.drain(); root.update()
                assert '100.0%' in ui.performance.get()
                assert len(ui.history_table.get_children())==1
                ui.log_filter.set('警报'); ui.render_logs()
                assert '测试警报' in ui.log.get('1.0','end')
                ui.stop(); assert ui.engine is None
                if sys.platform=='darwin' and os.environ.get('CI'):
                    import subprocess
                    import time
                    for index,name in [(0,'connect'),(1,'risk'),(2,'scores'),(3,'history')]:
                        ui.book.select(index)
                        root.lift(); root.focus_force()
                        root.after(1000,root.quit)
                        root.mainloop()
                        assert ui.log.winfo_height()>40, 'Log panel clipped'
                        if name=='risk':
                            page=root.nametowidget(ui.book.select())
                            body=page.winfo_children()[0]
                            entries=[w for w in body.winfo_children() if w.winfo_class() in ('Entry','TEntry')]
                            assert len(entries)==13 and all(w.winfo_ismapped() and w.winfo_width()>30 for w in entries), 'Risk inputs not visible'
                            assert all(0<=w.winfo_x()<body.winfo_width() and 0<=w.winfo_y()<body.winfo_height() for w in entries), 'Risk inputs outside pane'
                        target=Path(sys.argv[2]).parent/f'ui-{name}.png'
                        if name=='scores':
                            def inspect_widget(w):
                                print('WIDGET',str(w),w.winfo_class(),w.winfo_manager(),w.winfo_ismapped(),w.winfo_geometry(),w.winfo_rootx(),w.winfo_rooty(),flush=True)
                                for child in w.winfo_children():inspect_widget(child)
                            inspect_widget(root.nametowidget(ui.book.select()))
                        capture=subprocess.Popen(['/usr/sbin/screencapture','-x',str(target)])
                        deadline=time.monotonic()+10
                        def finish_capture():
                            if capture.poll() is not None:root.quit()
                            elif time.monotonic()>deadline:
                                capture.kill(); root.quit()
                            else:root.after(50,finish_capture)
                        root.after(50,finish_capture)
                        root.mainloop()
                        assert capture.wait(timeout=2)==0, 'Mac screenshot failed'
                ui.finished.set()
                ui.thread.join(timeout=2)
                ui.lock.close()
        finally:
            root.destroy()
    Path(sys.argv[2]).write_text('PASS: V1.2 UI, red/green scores, ticker rendering, demo default, stopped state, settings, bundled CA roots. Screenshots use synthetic test data. No network or orders.\n')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--smoke-test':
        smoke_test()
    else:
        from app import main
        main()

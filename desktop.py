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
        try:
            with patch.object(app.App, 'worker', lambda self: None), \
                 patch.object(app.messagebox, 'showerror', side_effect=AssertionError):
                ui = app.App(root, Path(folder))
                root.update()
                assert ui.mode.get() == 'OKX模拟盘'
                assert ui.engine is None
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()
                ui.fields['capital'].set('2000')
                ui.save_settings()
                saved = json.loads((Path(folder) / 'settings-v1.1.json').read_text())
                assert saved['capital'] == 2000
                assert not {'key', 'secret', 'phrase'} & saved.keys()
                assert ui.fields['stop_atr'].get() == '0.8'
                assert ui.fields['reward_r'].get() == '1.5'
                assert ui.fields['score_threshold'].get() == '7'
                # Render both score cards, details, network status and filtered alarms.
                from strategy import signal
                rows=[dict(t=i*900000,o=100,h=101,l=99,c=100) for i in range(1002)]
                market=dict(signal(rows,rows),bar=rows[-1]['t'],close=100)
                ui.emit('market',market); ui.emit('network','正常')
                ui.emit('alarm','测试警报：无网络、无订单')
                ui.drain(); root.update()
                assert len(ui.score_table.get_children()) == 7
                assert ui.score_vars['做多'].get().endswith('/ 10')
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
                    for index,name in [(0,'connect'),(1,'risk'),(2,'scores'),(3,'history')]:
                        ui.book.select(index); root.update()
                        target=Path(sys.argv[2]).parent/f'ui-{name}.png'
                        subprocess.run(['/usr/sbin/screencapture','-x',str(target)],check=False,timeout=10)
                ui.finished.set()
                ui.thread.join(timeout=2)
                ui.lock.close()
        finally:
            root.destroy()
    Path(sys.argv[2]).write_text('PASS: UI, demo default, stopped state, settings, bundled CA roots. No network or orders.\n')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--smoke-test':
        smoke_test()
    else:
        from app import main
        main()

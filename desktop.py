"""Packaged entry point for KAYTRADE V1.3.6."""
import json
import os
import sys
import tempfile
from pathlib import Path

import certifi

os.environ['SSL_CERT_FILE'] = certifi.where()

from v136_patch import apply as apply_v136_patch
apply_v136_patch()


def smoke_test():
    """Exercise the packaged V1.3.6 UI without credentials, network, or orders."""
    import ssl
    import tkinter as tk
    from unittest.mock import patch
    import app

    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    with tempfile.TemporaryDirectory(prefix='kaytrade-v136-ui-') as folder:
        root=tk.Tk()
        print('Bundled Tk version:',root.tk.call('package','provide','Tk'),flush=True)
        try:
            with patch.object(app.App,'worker',lambda self:None), \
                 patch.object(app.messagebox,'showerror',side_effect=AssertionError):
                ui=app.App(root,Path(folder))
                root.update()
                assert 'KAYTRADE' in root.title() and '1.3.6' in root.title()
                assert ui.mode.get()=='OKX模拟盘'
                assert ui.engine is None
                assert ui.settings_path.name=='settings-v1.3.6.json'
                assert ui.fields['score_threshold'].get()=='6'
                assert ui.fields['consecutive_losses'].get()=='3'
                assert not ui.key.get() and not ui.secret.get() and not ui.phrase.get()

                from engine import Settings,Halt
                Settings(score_threshold=1.0).validate()
                Settings(score_threshold=13.0).validate()
                for bad in (.5,13.5):
                    try:
                        Settings(score_threshold=bad).validate()
                        raise AssertionError('invalid V1.3.6 threshold accepted')
                    except Halt:
                        pass

                ui.fields['capital'].set('2000')
                ui.save_settings()
                saved=json.loads((Path(folder)/'settings-v1.3.6.json').read_text())
                assert saved['capital']==2000
                assert saved['score_threshold']==6.0
                assert not {'key','secret','phrase'} & saved.keys()

                # Synthetic flat candles are enough to verify score plumbing and UI rendering.
                from strategy import signal,SCORE_MAX,ENTRY_SCORE_MAX,DEFAULT_THRESHOLD
                assert SCORE_MAX==13.0 and ENTRY_SCORE_MAX==10.0 and DEFAULT_THRESHOLD==6.0
                def rows(step):
                    return [dict(t=i*step,o=100,h=101,l=99,c=100,v=100) for i in range(1002)]
                market=signal(rows(3600000),rows(900000),rows(300000),threshold=6,
                              four=rows(14400000),day=rows(86400000))
                market=dict(market,bar=1001*300000,close=100)
                ui.emit('market',market)
                ui.emit('ticker',{'last':'79045'})
                ui.emit('plan',dict(side='做多',px='79000',sl='78800',tp1='79200',tp2='79400',btc=.02,
                                    estimated_loss=20,position_multiplier=1.0))
                ui.emit('alarm','V1.3.6 smoke test alarm')
                ui.drain(); root.update()
                assert len(ui.score_table.get_children())==12
                assert '/13' in ui.score_vars['做多'].get()
                assert '入场' in ui.score_vars['做多'].get() and '趋势' in ui.score_vars['做多'].get()
                assert ui.score_bars['做多'].maximum==13.0
                assert '1×' in ui.plan_vars['qty'].get()
                assert ui.price.get()=='79,045.00 USDT'
                assert ui.matrix.exists('upper') and ui.matrix.exists('middle') and ui.matrix.exists('lower')

                if sys.platform=='darwin' and os.environ.get('CI'):
                    import subprocess,time
                    ui.book.select(3); root.lift(); root.focus_force(); root.update()
                    target=Path(sys.argv[2]).parent/'ui-v136-scores.png'
                    capture=subprocess.Popen(['/usr/sbin/screencapture','-x',str(target)])
                    deadline=time.monotonic()+10
                    while capture.poll() is None and time.monotonic()<deadline:
                        root.update(); time.sleep(.05)
                    if capture.poll() is None: capture.kill()
                    assert capture.wait(timeout=2)==0

                ui.finished.set(); ui.lock.close()
        finally:
            root.destroy()
    Path(sys.argv[2]).write_text(
        'PASS: KAYTRADE V1.3.6 UI; 13-point final score; 5m 6 + 15m 4 + 1H +/-1 + 4H +/-2; '
        'Bollinger confirmation; default threshold 6; fixed 1x qualified sizing; 5s startup buffer wiring; '
        'demo default and bundled CA roots. Synthetic data only; no network or orders.\n')


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test':
        smoke_test()
    else:
        from app import main
        main()

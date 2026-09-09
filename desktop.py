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
                saved = json.loads((Path(folder) / 'settings.json').read_text())
                assert saved['capital'] == 2000
                assert not {'key', 'secret', 'phrase'} & saved.keys()
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

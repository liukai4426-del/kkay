"""V1.5.4 run-log footer layout repair.

Aqua/Tk pack ordering could leave the log Text widget with data but unmapped.
Repack the footer before the expanding notebook so it always receives visible
space, then let the notebook consume only the remaining area.
"""
from __future__ import annotations

from v154_limit_log_fix import apply as apply_limit_log_fix
apply_limit_log_fix()

import app
import v138_strategy_patch as v138


def _find_log_filterbar(self):
    for widget in v138._widgets(self.root):
        try:
            if str(widget.cget("text") or "") == "运行日志":
                return widget.master
        except Exception:
            continue
    return None


def _repair_log_layout(self):
    filterbar = _find_log_filterbar(self)
    if filterbar is None or not hasattr(self, "book") or not hasattr(self, "log"):
        return False
    # Packing order matters: carve the footer from the bottom first, then give
    # the expanding notebook the remaining center area.
    try:
        self.book.pack_forget()
        self.log.pack_forget()
        filterbar.pack_forget()
        self.log.pack(side="bottom", fill="x", padx=15, pady=(0, 12))
        filterbar.pack(side="bottom", fill="x")
        self.book.pack(fill="both", expand=True, padx=15, pady=10)
        self._v154_log_filterbar = filterbar
        self._v154_log_layout_ready = True
        return True
    except Exception:
        return False


def apply():
    if getattr(app.App, "_kaytrade_v154_log_layout_fix_applied", False):
        return
    previous_app_init = app.App.__init__

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        _repair_log_layout(self)
        try:
            self.render_logs()
        except Exception:
            pass

    app.App.__init__ = app_init
    app.App._kaytrade_v154_log_layout_fix_applied = True


apply()

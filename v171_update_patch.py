"""KAYTRADE V1.7.1 maintenance overlay.

V1.7.1 keeps the V1.7.0 Build1701 trading strategy unchanged and fixes two
production issues:
- preserve the strict latest-CLOSED-15m BOLL opportunity-source lock so legacy
  5m BOLL code can never create an entry opportunity;
- make the UI/runtime identity V1.7.1 Build1710 while history.py accepts
  version-prefixed confirmed-flat events.

The history parser itself is fixed in history.py. This overlay only promotes
the packaged/runtime identity and keeps the Build1701 safety chain installed.
"""
from __future__ import annotations

import tkinter as tk

from v170_build1701_patch import apply as apply_previous
apply_previous()

import app
import visual
import v165_model as model
import v165_ui_patch as ui165
import v165_update_patch as runtime
import v166_ui_patch as ui166
import v168_update_patch as v168
import v168_build1681_patch as b1681
import v170_update_patch as v170
import v170_build1701_patch as v1701

VERSION = "1.7.1"
BUILD = "1710"

_PREVIOUS_RUNTIME_TEXT = runtime._rewrite_runtime_text
_PREVIOUS_APP_EMIT = app.App.emit
_PREVIOUS_APP_INIT = app.App.__init__


def _rewrite_runtime_text_v171(data):
    text = _PREVIOUS_RUNTIME_TEXT(data)
    if not isinstance(text, str):
        return text
    text = text.replace("V1.7.0", "V1.7.1")
    text = text.replace("Build1701", "Build1710").replace("Build 1701", "Build 1710")
    if text.startswith("自动交易启动；") and "历史收益兼容" not in text:
        text += "；V1.7.1历史收益兼容版本化仓位归零事件，确认平仓后自动计入统计"
    return text


def _authorization_text_v171(owner, settings):
    return v170._authorization_text_v170(owner, settings).replace("V1.7.0", "V1.7.1")


def _show_authorization_dialog_v171(owner, settings):
    result = {"confirmed": False}
    win = tk.Toplevel(owner.root)
    win.title("启动自动交易 · V1.7.1")
    win.configure(bg=visual.BG)
    win.transient(owner.root)
    win.resizable(False, False)
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    outer = tk.Frame(win, bg=visual.BG, padx=18, pady=18)
    outer.pack(fill="both", expand=True)
    card = tk.Frame(outer, bg=visual.PANEL, padx=24, pady=22)
    card.pack(fill="both", expand=True)
    visual.label(card, text="启动自动交易", size=20, bold=True, color=visual.TEXT, bg=visual.PANEL).pack(anchor="w")
    visual.label(card, text="V1.7.1 · 当前正式策略确认", size=11, color=visual.GREEN, bg=visual.PANEL).pack(anchor="w", pady=(5, 16))
    tk.Label(
        card, text=_authorization_text_v171(owner, settings), justify="left", anchor="nw",
        wraplength=620, bg=visual.PANEL, fg=visual.TEXT,
        font=("Helvetica", 11), bd=0, highlightthickness=0,
    ).pack(fill="x", anchor="w")
    buttons = tk.Frame(card, bg=visual.PANEL)
    buttons.pack(fill="x", pady=(22, 0))
    visual.RoundedButton(buttons, text="取消", command=win.destroy, variant="neutral", width=150, height=44, radius=16).pack(side="right")

    def confirm():
        result["confirmed"] = True
        win.destroy()

    visual.RoundedButton(buttons, text="确认", command=confirm, variant="accent", width=150, height=44, radius=16).pack(side="right", padx=(0, 10))
    win.update_idletasks()
    width = 700
    height = max(650, min(830, win.winfo_reqheight()))
    try:
        x = owner.root.winfo_rootx() + max(0, (owner.root.winfo_width() - width) // 2)
        y = owner.root.winfo_rooty() + max(0, (owner.root.winfo_height() - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")
    except Exception:
        win.geometry(f"{width}x{height}")
    win.grab_set()
    win.focus_force()
    owner.root.wait_window(win)
    return bool(result["confirmed"])


def _app_emit_v171(self, kind, data):
    outgoing = _rewrite_runtime_text_v171(data) if isinstance(data, str) else data
    return _PREVIOUS_APP_EMIT(self, kind, outgoing)


def _app_init_v171(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    try:
        self.root.title("KAYTRADE 1.7.1 · BTC 策略控制台 · Build 1710")
    except Exception:
        pass
    try:
        value = str(self.signal.get() or "")
        self.signal.set(value.replace("V1.7.0", "V1.7.1"))
    except Exception:
        pass
    label = getattr(self, "_v170_version_label", None)
    if label is not None:
        try:
            label.configure(text="BTC / USDT   ·   V1.7.1")
        except Exception:
            pass
    self._v171_ready = True


def apply():
    if getattr(model, "_kaytrade_v171_applied", False):
        return

    # Promote identity only; V1.7.0 Build1701 strict-15m strategy/safety stays intact.
    model.VERSION = VERSION
    model.BUILD = BUILD
    runtime.VERSION = VERSION
    runtime.BUILD = BUILD
    v170.VERSION = VERSION
    v170.BUILD = BUILD
    v1701.VERSION = VERSION
    v1701.BUILD = BUILD
    v168.VERSION = VERSION
    v168.BUILD = BUILD
    b1681.VERSION = VERSION
    b1681.BUILD = BUILD
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.7.1 · BTC 策略控制台 · Build 1710"

    runtime._rewrite_runtime_text = _rewrite_runtime_text_v171
    ui165._authorization_text = _authorization_text_v171
    ui165._show_authorization_dialog = _show_authorization_dialog_v171
    app.App.emit = _app_emit_v171
    app.App.__init__ = _app_init_v171

    model._kaytrade_v171_applied = True
    app.App._kaytrade_v171_applied = True


apply()

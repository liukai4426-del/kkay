"""KAYTRADE V1.5.5 Build 1551 hotfix.

Fixes the packaged-app startup recursion by making the V1.5.5 overlay load once
through a single PyInstaller runtime hook, keeps the final scrollable log
renderer bound on each App instance, and disables the inherited post-close
cooldown gate.  The old cooldown setting remains compatibility data only and is
removed from the visible Execution Settings UI.
"""
from __future__ import annotations

import os
import traceback
from pathlib import Path

from v155_update_patch import apply as apply_v155
apply_v155()

import app
import engine
import v155_update_patch as v155
from v156_execution_hotfix import apply as apply_v156_execution_hotfix

# V1.5.6 packages Build1551 as an inherited normal module. Load the execution
# stability hotfix here so the final v156_ui_patch runtime hook still remains
# the single PyInstaller runtime hook and the historical wrapper chain is not
# duplicated.
apply_v156_execution_hotfix()

VERSION = "1.5.5"
BUILD = "1551"
COOLDOWN_ENABLED = False

_PREVIOUS_INIT = app.App.__init__
_PREVIOUS_CYCLE = engine.Engine.cycle


def _walk(root):
    try:
        children = root.winfo_children()
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk(child)


def _remove_cooldown_row(owner):
    """Remove the visible cooldown setting while retaining compatibility data."""
    var = owner.fields.get("cooldown_minutes")
    if var is None:
        owner._v1551_cooldown_control_removed = True
        return True

    entry = None
    label = None
    for widget in _walk(owner.root):
        if isinstance(widget, app.RoundedEntry) and getattr(widget, "variable", None) is var:
            entry = widget
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            text = ""
        if "平仓后冷却" in text or "冷却时间" in text:
            label = widget

    parent = getattr(entry, "master", None) or getattr(label, "master", None)
    row = None
    for widget in (entry, label):
        if widget is None:
            continue
        try:
            info = widget.grid_info()
            if info:
                row = int(info.get("row", -1))
                parent = widget.master
                break
        except Exception:
            pass

    if parent is not None and row is not None and row >= 0:
        for child in list(parent.winfo_children()):
            try:
                info = child.grid_info()
                child_row = int(info.get("row", -99)) if info else -99
            except Exception:
                continue
            if child_row == row:
                try:
                    child.grid_remove()
                except Exception:
                    pass
        for child in list(parent.winfo_children()):
            try:
                info = child.grid_info()
                child_row = int(info.get("row", -1)) if info else -1
                if info and child_row > row:
                    child.grid_configure(row=child_row - 1)
            except Exception:
                pass
    else:
        for widget in (entry, label):
            if widget is not None:
                try:
                    widget.grid_remove()
                except Exception:
                    try:
                        widget.pack_forget()
                    except Exception:
                        pass

    owner.fields.pop("cooldown_minutes", None)
    owner._v1551_cooldown_control_removed = True
    return True


def _call_without_cooldown(owner, previous_cycle):
    """Run inherited cycle with last_close neutralized only for entry gating.

    If the inherited cycle records a genuinely new close time, keep that new
    timestamp for history/diagnostics.  Otherwise restore the previous value.
    """
    store = getattr(owner, "store", None)
    if store is None:
        return previous_cycle(owner)
    state = store.data
    old_last_close = state.get("last_close", 0)
    state["last_close"] = 0
    try:
        result = previous_cycle(owner)
    finally:
        current = state.get("last_close", 0)
        if not current and old_last_close:
            state["last_close"] = old_last_close
    return result


def _probe_target():
    return os.environ.get("KAYTRADE_STARTUP_PROBE", "").strip()


def _write_startup_probe(owner):
    """CI-only packaged-app probe written only after App init fully succeeds."""
    target = _probe_target()
    if not target:
        return
    try:
        Path(target).write_text(
            f"PASS KAYTRADE {VERSION} Build {BUILD} cooldown=off path_c=off\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _write_startup_error():
    target = _probe_target()
    if not target:
        return
    try:
        Path(target + ".error").write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


def apply():
    if getattr(engine.Engine, "_kaytrade_v1551_applied", False):
        return

    def app_init(self, *args, **kwargs):
        try:
            _PREVIOUS_INIT(self, *args, **kwargs)
            _remove_cooldown_row(self)
            # V1.5.4 installs an instance-level renderer during its init chain.
            # Rebind only once, after the whole inherited stack has completed.
            self.render_logs = v155._render_logs_v155.__get__(self, app.App)
            self.render_logs()
            try:
                self.root.title("KAYTRADE 1.5.5 · BTC 策略控制台 · Build 1551")
            except Exception:
                pass
            try:
                text = str(self.signal.get() or "")
                for old in ("平仓后30分钟冷却", "30分钟冷却", "平仓冷却30分钟"):
                    text = text.replace(old, "平仓冷却已关闭")
                self.signal.set(text)
            except Exception:
                pass
            self._v1551_ready = True
            _write_startup_probe(self)
        except Exception:
            _write_startup_error()
            raise

    def cycle(self):
        result = _call_without_cooldown(self, _PREVIOUS_CYCLE)
        if self.store:
            active = self.store.data.get("active")
            if isinstance(active, dict):
                changed = active.get("version") != VERSION or active.get("build") != BUILD
                active["version"] = VERSION
                active["build"] = BUILD
                active["path_c_enabled"] = False
                active["cooldown_enabled"] = False
                if changed:
                    self.store.save()
        return result

    app.App.__init__ = app_init
    engine.Engine.cycle = cycle
    engine.Engine._kaytrade_v1551_applied = True
    app.App._kaytrade_v1551_applied = True


apply()

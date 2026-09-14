"""KAYTRADE V1.5.6 final UI cleanup.

Presentation-only follow-up:
- Run Log is newest-first so fresh events stay at the top and history is below.
- Remove the obsolete fixed consecutive-loss value from Risk Settings.

Trading semantics are unchanged. The consecutive-loss pause remains disabled;
its backend compatibility default may remain in Settings, but there is no
visible/editable control for it.
"""
from __future__ import annotations

import time

from v156_ui_patch import apply as apply_previous
apply_previous()

import app
import engine
import v156_ui_patch as v156

VERSION = "1.5.6"
BUILD = "1560"

_PREVIOUS_INIT = app.App.__init__


def _walk(root):
    try:
        children = root.winfo_children()
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk(child)


def _widget_text(widget):
    try:
        return str(widget.cget("text") or "")
    except Exception:
        return ""


def _remove_row(parent, row):
    if parent is None or row is None or row < 0:
        return 0
    removed = 0
    for child in list(parent.winfo_children()):
        try:
            info = child.grid_info()
            child_row = int(info.get("row", -99)) if info else -99
        except Exception:
            continue
        if child_row == row:
            try:
                child.destroy()
                removed += 1
            except Exception:
                try:
                    child.grid_forget()
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
    return removed


def _remove_fixed_loss_control(owner):
    """Remove the final fixed `3` consecutive-loss Risk row, including its entry."""
    var = owner.fields.get("consecutive_losses")
    targets = set()

    # Prefer the label row. Match both current and inherited wording.
    for widget in _walk(owner.root):
        text = _widget_text(widget)
        if text and (
            "连续亏损停开次数" in text
            or "连续亏损" in text and "固定" in text
            or "consecutive" in text.lower() and "loss" in text.lower()
        ):
            try:
                info = widget.grid_info()
                if info:
                    targets.add((widget.master, int(info.get("row", -1))))
            except Exception:
                pass

    # If the label has already been hidden by an inherited overlay, locate the
    # actual field widget by its Tk variable so the dangling `3` box is removed.
    if var is not None:
        for widget in _walk(owner.root):
            if getattr(widget, "variable", None) is var:
                try:
                    info = widget.grid_info()
                    if info:
                        targets.add((widget.master, int(info.get("row", -1))))
                except Exception:
                    pass
            try:
                inner = getattr(widget, "entry", None)
                if inner is not None and str(inner.cget("textvariable")) == str(var):
                    info = widget.grid_info()
                    if info:
                        targets.add((widget.master, int(info.get("row", -1))))
            except Exception:
                pass

    for parent, row in sorted(targets, key=lambda item: item[1], reverse=True):
        _remove_row(parent, row)

    owner.fields.pop("consecutive_losses", None)
    owner._v156_fixed_loss_control_removed = True


def _render_logs_newest_first(self):
    text = getattr(self, "_v156_log_text", None)
    if text is None:
        return

    try:
        view = text.yview()
        top = float(view[0]) if view else 0.0
        at_top = not view or top <= 0.005
    except Exception:
        top, at_top = 0.0, True

    try:
        text.configure(state="normal")
        text.delete("1.0", "end")
        # log_lines is chronological; render its newest tail in reverse order.
        rows = list(reversed(list(getattr(self, "log_lines", []) or [])[-500:]))
        if not rows:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            text.insert("end", "● 等待  ", "wait")
            text.insert("end", stamp + "   ", "time")
            text.insert("end", "等待运行记录\n", "title")
            text.insert("end", "策略尚未产生新的运行事件。\n", "detail")
        else:
            for index, row in enumerate(rows):
                try:
                    kind, line = row
                except Exception:
                    kind, line = "log", str(row)
                stamp, message = v156._extract_timestamp(line)
                status, tag = v156._status_for(kind, message)
                title, detail = v156._split_title_detail(message)
                text.insert("end", "● " + status + "  ", tag)
                text.insert("end", stamp + "   ", "time")
                text.insert("end", title + "\n", "title")
                if detail:
                    text.insert("end", detail + "\n", "detail")
                if index != len(rows) - 1:
                    text.insert("end", "\n", "detail")
    finally:
        try:
            text.configure(state="disabled")
        except Exception:
            pass

    # New records should remain visible at the top. If the user intentionally
    # scrolled into history, preserve the current scroll fraction instead of
    # snapping them back to the newest event.
    try:
        if at_top:
            text.yview_moveto(0.0)
        else:
            text.yview_moveto(top)
    except Exception:
        pass
    self._v156_log_at_top = at_top


def apply():
    if getattr(app.App, "_kaytrade_v156_log_order_risk_cleanup_applied", False):
        return

    def app_init(self, *args, **kwargs):
        _PREVIOUS_INIT(self, *args, **kwargs)
        _remove_fixed_loss_control(self)
        self.render_logs = _render_logs_newest_first.__get__(self, app.App)
        self.render_logs()
        self._v156_newest_first = True

    app.App.__init__ = app_init
    app.App.render_logs = _render_logs_newest_first
    app.App._kaytrade_v156_log_order_risk_cleanup_applied = True
    engine.Engine._kaytrade_v156_log_order_risk_cleanup_applied = True


apply()

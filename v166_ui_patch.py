"""KAYTRADE V1.6.6 UI polish overlay.

Presentation-only changes on top of the audited V1.6.5 + 15m BOLL runtime:
- disambiguate the opening-status BOLL trigger and execution-window rows;
- make 15m text rewriting idempotent so "15m" can never become "115m";
- render run-log detail inline after the white title, wrapping under the title column;
- widen the time-to-content gap and slightly increase opening-status row spacing;
- bump visible application version to V1.6.6 Build1660.

No score, signal, execution, order, risk or exchange-write semantics are changed.
"""
from __future__ import annotations

import copy
import re
import time
import tkinter as tk

from v165_15m_outer_patch import apply as apply_previous
apply_previous()

import app
import visual
import v156_ui_patch as ui156
import v156_build1561_patch as ui1561
import v161_ui_status_patch as ui161
import v165_model as model
import v165_update_patch as runtime
import v165_ui_patch as ui165
import v165_15m_outer_patch as outer15

VERSION = "1.6.6"
BUILD = "1660"
WINDOW_TITLE = f"KAYTRADE {VERSION} · BTC 策略控制台 · Build {BUILD}"
VERSION_SUBTITLE = f"BTC / USDT   ·   V{VERSION}"

_PREVIOUS_INIT = app.App.__init__
_PREVIOUS_EMIT = app.App.emit
_PREVIOUS_STATUS_RENDER = ui161._render_status
_PREVIOUS_RUNTIME_TEXT_15M = outer15._runtime_text_15m

GREEN = app.GREEN
RED = app.RED
MUTED = app.MUTED
TEXT = "#eef5f7"

# Two fixed tab stops keep status/time/content aligned.  The second stop is
# deliberately a little wider than V1.5.6 so time and content breathe.
LOG_STATUS_TAB_PX = 92
LOG_CONTENT_TAB_PX = 288


def _walk(root):
    try:
        children = root.winfo_children()
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk(child)


def _rewrite_text_v166(text):
    """Idempotent 15m presentation rewrite; never rewrites the '5m' inside '15m'."""
    if not isinstance(text, str):
        return text

    # Use the V1.6.5 lifecycle rewrite as the base, not the old non-idempotent
    # 15m wrapper.  Then convert only standalone 5m BOLL phrases.
    out = outer15._ORIGINAL_MODEL_REWRITE_TEXT(text)
    replacements = (
        ("5m BOLL中轨+RSI / 外轨触发（必须）", "15m BOLL外轨触发（必须）"),
        ("5m BOLL外轨", "15m BOLL外轨"),
        ("5m BOLL回调信号", "15m BOLL外轨触发"),
        ("最新已收盘5m外轨信号", "最新已收盘15m BOLL外轨触发"),
        ("等待新的已收盘5m外轨信号", "等待新的已收盘15m BOLL外轨触发"),
        ("偏离5m BOLL触发位置", "偏离15m BOLL外轨触发位置"),
        ("缺少有效5m BOLL信号状态", "缺少有效15m BOLL外轨触发状态"),
    )
    for old, new in replacements:
        # Negative digit look-behind is the important part: "15m" contains the
        # substring "5m", but is not a standalone 5m timeframe token.
        pattern = r"(?<!\d)" + re.escape(old)
        out = re.sub(pattern, new, out)

    # Defensive cleanup for display strings already corrupted by an older pass.
    out = out.replace("115m BOLL", "15m BOLL").replace("115m外轨", "15m外轨")
    return out


def _runtime_text_v166(data):
    out = _PREVIOUS_RUNTIME_TEXT_15M(data)
    if isinstance(out, str):
        out = out.replace("115m BOLL", "15m BOLL").replace("115m外轨", "15m外轨")
        out = out.replace("V1.6.5", "V1.6.6")
    return out


def _sanitize_display(value):
    """Presentation-only recursive cleanup; never mutates the engine payload."""
    if isinstance(value, str):
        return value.replace("115m BOLL", "15m BOLL").replace("115m外轨", "15m外轨")
    if isinstance(value, list):
        return [_sanitize_display(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_display(item) for item in value)
    if isinstance(value, dict):
        return {key: _sanitize_display(item) for key, item in value.items()}
    return value


def _install_safe_text_rewrite():
    # Result decoration reads the module global at call time, while the V1.6.5
    # model also holds a direct function reference.  Patch both string-only routes.
    outer15._rewrite_text_15m = _rewrite_text_v166
    model._rewrite_text = _rewrite_text_v166
    outer15._runtime_text_15m = _runtime_text_v166
    runtime._rewrite_runtime_text = _runtime_text_v166


def _configure_status_items():
    items = []
    for key, label in ui161._STATUS_ITEMS:
        if key == "boll":
            label = "15m BOLL外轨触发"
        elif key == "signal_window":
            label = "5m执行窗口"
        items.append((key, label))
    ui161._STATUS_ITEMS = tuple(items)


def _render_status_v166(owner):
    _PREVIOUS_STATUS_RENDER(owner)
    labels = getattr(owner, "_v161_status_labels", None)
    snapshot = getattr(owner, "_v161_status_snapshot", {}) or {}
    if not isinstance(labels, dict):
        return

    # BOLL = whether a fresh 15m outer signal exists.
    boll = labels.get("boll")
    if boll is not None:
        value = snapshot.get("boll")
        color = GREEN if value is True else RED if value is False else MUTED
        try:
            boll.configure(fg=color, text="● 15m BOLL外轨触发")
        except Exception:
            pass

    # Signal window = whether that trigger is still executable.  It is a
    # separate state, but no longer repeats the words "15m外轨" in the UI.
    window = labels.get("signal_window")
    if window is not None:
        value = snapshot.get("signal_window")
        color = GREEN if value is True else RED if value is False else MUTED
        text = "5m执行窗口：有效" if value is True else "5m执行窗口：等待信号" if value is False else "5m执行窗口"
        try:
            window.configure(fg=color, text="● " + text)
        except Exception:
            pass


def _space_opening_status(owner):
    labels = getattr(owner, "_v161_status_labels", None)
    if not isinstance(labels, dict):
        return False
    for widget in labels.values():
        try:
            # V1.6.1 used pady=0.  Add a small vertical gap without materially
            # increasing the card footprint.
            widget.grid_configure(pady=(2, 3))
        except Exception:
            pass
    surface = getattr(owner, "_v161_status_surface", None)
    if surface is not None:
        for child in surface.winfo_children():
            try:
                if str(child.cget("text") or "") == "开仓状态":
                    child.pack_configure(pady=(0, 5))
                    break
            except Exception:
                pass
    owner._v166_status_spacing = True
    return True


def _configure_log_tags(owner):
    text = getattr(owner, "_v156_log_text", None)
    if text is None:
        return False
    try:
        text.tag_configure(
            "v166_row",
            tabs=(LOG_STATUS_TAB_PX, LOG_CONTENT_TAB_PX),
            lmargin1=0,
            lmargin2=LOG_CONTENT_TAB_PX,
            spacing1=1,
            spacing3=5,
        )
        text.tag_configure("v166_detail_inline", foreground=MUTED, font=("Helvetica", 9))
        return True
    except Exception:
        return False


def _insert_log_row(text, status, status_tag, stamp, title, detail):
    start = text.index("end-1c")
    text.insert("end", "● " + status + "\t", status_tag)
    text.insert("end", stamp + "\t", "time")
    text.insert("end", title, "title")
    if detail:
        text.insert("end", "  " + detail, "v166_detail_inline")
    text.insert("end", "\n")
    end = text.index("end-1c")
    try:
        text.tag_add("v166_row", start, end)
    except Exception:
        pass


def _render_logs_v166(self):
    """Newest-first compact log with inline detail and title-column wrap."""
    text = getattr(self, "_v156_log_text", None)
    if text is None:
        return

    force_top = bool(getattr(self, "_v1561_force_top", False))
    try:
        view = text.yview()
        at_top = force_top or not view or float(view[0]) <= 0.005
        top = float(view[0]) if view else 0.0
    except Exception:
        at_top, top = True, 0.0

    try:
        text.configure(state="normal")
        text.delete("1.0", "end")
        rows = ui1561._ordered_rows(getattr(self, "log_lines", []) or [])
        if not rows:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            _insert_log_row(text, "等待", "wait", stamp, "等待运行记录", "策略尚未产生新的运行事件。")
        else:
            for row in rows:
                try:
                    kind, line = row
                except Exception:
                    kind, line = "log", str(row)
                stamp, message = ui156._extract_timestamp(line)
                status, tag = ui156._status_for(kind, message)
                title, detail = ui156._split_title_detail(message)
                title = _sanitize_display(title)
                detail = _sanitize_display(detail)
                _insert_log_row(text, status, tag, stamp, title, detail)
    finally:
        try:
            text.configure(state="disabled")
        except Exception:
            pass

    if at_top:
        try:
            text.yview_moveto(0.0)
        except Exception:
            pass
    else:
        try:
            text.yview_moveto(top)
        except Exception:
            pass
    self._v1561_force_top = False
    self._v156_log_at_top = at_top


def _sync_visible_version(owner):
    try:
        owner.root.title(WINDOW_TITLE)
    except Exception:
        pass
    for widget in _walk(owner.root):
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            continue
        if text.startswith("BTC / USDT"):
            try:
                widget.configure(text=VERSION_SUBTITLE)
                owner._v166_version_label = widget
            except Exception:
                pass
    try:
        owner.signal.set(str(owner.signal.get() or "").replace("V1.6.5", "V1.6.6"))
    except Exception:
        pass


def app_emit_v166(self, kind, data):
    payload = data
    if kind in ("market", "plan") and isinstance(data, dict):
        payload = _sanitize_display(copy.deepcopy(data))
    elif kind in ("log", "alarm") and isinstance(data, str):
        payload = _sanitize_display(data).replace("V1.6.5", "V1.6.6")
    return _PREVIOUS_EMIT(self, kind, payload)


def app_init_v166(self, *args, **kwargs):
    _PREVIOUS_INIT(self, *args, **kwargs)
    _sync_visible_version(self)
    _space_opening_status(self)
    _configure_log_tags(self)
    self.render_logs = _render_logs_v166.__get__(self, app.App)
    self.render_logs()
    self._v166_ui_ready = True


def apply():
    if getattr(app.App, "_kaytrade_v166_ui_applied", False):
        return
    _install_safe_text_rewrite()
    _configure_status_items()
    ui161._render_status = _render_status_v166
    app.App.__init__ = app_init_v166
    app.App.emit = app_emit_v166
    app.App.render_logs = _render_logs_v166
    app.App._kaytrade_v166_ui_applied = True


apply()

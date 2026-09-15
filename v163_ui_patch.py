"""KAYTRADE V1.6.3 presentation overlay.

- sync the visible BTC/USDT subtitle with V1.6.3;
- make the Run Log scrollbar thumb maximally rounded (capsule shape);
- update compact opening-status labels for the outer-only RSI/MACD model.
"""
from __future__ import annotations

import tkinter as tk

from v161_ui_status_patch import apply as apply_previous
apply_previous()

import app
import v156_ui_patch as ui156
import v161_ui_status_patch as ui161
import v163_model as model

VERSION = "1.6.3"
BUILD = "1630"
VERSION_SUBTITLE = f"BTC / USDT   ·   V{VERSION}"
WINDOW_TITLE = f"KAYTRADE {VERSION} · BTC 策略控制台 · Build {BUILD}"

_PREVIOUS_INIT = app.App.__init__
_PREVIOUS_STATUS_SNAPSHOT = ui161._status_snapshot

# Make the inherited status surface follow the new strategy semantics before any
# App instance is created. Reuse existing keys to avoid rebuilding the widget API.
ui161.model = model
ui161._STATUS_ITEMS = (
    ("score", "评分≥6"),
    ("direction", "主方向"),
    ("boll", "外轨BOLL"),
    ("middle_macd", "外轨MACD"),
    ("outer_4h", "外轨4H"),
    ("macd_clean", "RSI 30–70"),
    ("entry_drift", "入场未偏离"),
    ("front_space", "前方≥1.5R"),
    ("stop_structure", "止损结构"),
    ("cost", "成本≤0.30R"),
    ("adverse_4h", "4H结构安全"),
    ("daily_risk", "日内风险"),
    ("funds", "可用资金"),
    ("clock", "时钟同步"),
)


def _status_snapshot_v163(owner, data):
    snapshot = dict(_PREVIOUS_STATUS_SNAPSHOT(owner, data) or {})
    if not isinstance(data, dict):
        return snapshot
    side, row = ui161._candidate(data)
    if not isinstance(row, dict):
        snapshot["middle_macd"] = None
        snapshot["macd_clean"] = None
        return snapshot
    confirmations = row.get("confirmations") or {}
    required = confirmations.get("required") or {}
    opportunity = row.get("opportunity") or data.get("opportunity") or {}
    path = model._path_from(row, result=data, opportunity=opportunity)
    if path in model.OUTER_PATHS:
        snapshot["middle_macd"] = bool(required.get("outer_macd_improving"))
        snapshot["macd_clean"] = bool(required.get("outer_rsi_30_70"))
    else:
        snapshot["middle_macd"] = None
        snapshot["macd_clean"] = None
    return snapshot


ui161._status_snapshot = _status_snapshot_v163


def _draw_max_round(self):
    """Draw a maximum-radius capsule thumb with no contrasting track."""
    if not self.winfo_exists():
        return
    self.delete("all")
    y1, y2, _, _, _ = self._bounds()
    w = max(self.bar_width, self.winfo_width())
    x1 = 2.0
    x2 = max(3.0, float(w) - 2.0)
    height = max(1.0, float(y2) - float(y1))
    width = max(1.0, x2 - x1)
    radius = max(0.5, min(width / 2.0, height / 2.0))
    fill = "#465861"

    # Center pieces plus end circles produce a true capsule with the maximum
    # possible radius for the current thumb dimensions.
    if x2 - x1 > 2 * radius:
        self.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline="")
    if y2 - y1 > 2 * radius:
        self.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline="")
    self.create_oval(x1, y1, x1 + 2 * radius, y1 + 2 * radius, fill=fill, outline="")
    self.create_oval(x2 - 2 * radius, y2 - 2 * radius, x2, y2, fill=fill, outline="")
    if width <= 2 * radius and height <= 2 * radius:
        # Degenerate square-ish thumb: one centered oval is sufficient.
        self.delete("all")
        self.create_oval(x1, y1, x2, y2, fill=fill, outline="")


# Patch the class before the inherited App.__init__ creates the Run Log widgets.
ui156.CleanScrollbar._draw = _draw_max_round


def _walk(root):
    try:
        children = root.winfo_children()
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk(child)


def _sync_visible_version(owner):
    updated = False
    target = None
    for widget in _walk(owner.root):
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            continue
        if text.startswith("BTC / USDT"):
            try:
                widget.configure(text=VERSION_SUBTITLE)
                target = widget
                updated = True
            except Exception:
                pass
    owner._v163_version_label = target
    owner._v163_version_label_updated = updated
    return updated


def app_init(self, *args, **kwargs):
    _PREVIOUS_INIT(self, *args, **kwargs)
    self.root.title(WINDOW_TITLE)
    _sync_visible_version(self)
    try:
        self.signal.set("V1.6.3 · 外轨 only / RSI30–70 / 5m MACD改善 / 4H同向")
    except Exception:
        pass
    scrollbar = getattr(self, "_v156_log_scrollbar", None)
    self._v163_log_scrollbar_max_round = isinstance(scrollbar, ui156.CleanScrollbar)
    self._v163_ui_ready = True


def apply():
    if getattr(app.App, "_kaytrade_v163_ui_applied", False):
        return
    app.App.__init__ = app_init
    app.App._kaytrade_v163_ui_applied = True


apply()

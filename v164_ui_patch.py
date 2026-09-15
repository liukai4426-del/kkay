"""KAYTRADE V1.6.4 presentation overlay.

- synchronize visible version with V1.6.4;
- remove Available Funds from the compact opening-status surface;
- show Volume only as an opening hard-gate state;
- render allowed states green, prohibited/failed states red, unknown states gray;
- keep score-detail wording aligned with the outer-only execution model.
"""
from __future__ import annotations

import math
import tkinter as tk

from v163_ui_patch import apply as apply_previous
apply_previous()

import app
import v156_ui_patch as ui156
import v161_ui_status_patch as ui161
import v163_ui_patch as ui163
import v164_model as model

VERSION = "1.6.4"
BUILD = "1640"
VERSION_SUBTITLE = f"BTC / USDT   ·   V{VERSION}"
WINDOW_TITLE = f"KAYTRADE {VERSION} · BTC 策略控制台 · Build {BUILD}"

_PREVIOUS_INIT = app.App.__init__
_PREVIOUS_STATUS_SNAPSHOT = ui161._status_snapshot

GREEN = app.GREEN
RED = app.RED
MUTED = app.MUTED

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
    ("volume_gate", "量能 Hard Gate"),
    ("clock", "时钟同步"),
)


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _status_snapshot_v164(owner, data):
    snapshot = dict(_PREVIOUS_STATUS_SNAPSHOT(owner, data) or {})
    snapshot.pop("funds", None)
    snapshot["volume_gate"] = None
    if not isinstance(data, dict):
        return snapshot

    side, row = ui161._candidate(data)
    if not isinstance(row, dict):
        return snapshot
    confirmations = row.get("confirmations") or {}
    opportunity = row.get("opportunity") or data.get("opportunity") or {}

    if isinstance(opportunity, dict) and opportunity:
        explicit = confirmations.get("volume_hard_gate_ok")
        if explicit is not None:
            snapshot["volume_gate"] = bool(explicit)
        else:
            ratio = _finite(opportunity.get("five_signal_volume_ratio"))
            snapshot["volume_gate"] = None if ratio is None else ratio < model.VOLUME_HARD_GATE
    return snapshot


ui161._status_snapshot = _status_snapshot_v164


def _render_status_v164(owner):
    labels = getattr(owner, "_v161_status_labels", None)
    if not isinstance(labels, dict):
        return
    snapshot = getattr(owner, "_v161_status_snapshot", {}) or {}
    static = dict(ui161._STATUS_ITEMS)
    for key, widget in labels.items():
        value = snapshot.get(key)
        color = GREEN if value is True else RED if value is False else MUTED
        text = static.get(key, key)
        if key == "volume_gate":
            if value is True:
                text = "量能：允许开仓"
            elif value is False:
                text = "量能：禁止开仓"
            else:
                text = "量能 Hard Gate"
        try:
            widget.configure(fg=color, text="● " + text)
        except Exception:
            pass


ui161._render_status = _render_status_v164

# Preserve the V1.6.3 capsule Run Log scrollbar.
ui156.CleanScrollbar._draw = ui163._draw_max_round


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
    owner._v164_version_label = target
    owner._v164_version_label_updated = updated
    return updated


def app_init(self, *args, **kwargs):
    _PREVIOUS_INIT(self, *args, **kwargs)
    self.root.title(WINDOW_TITLE)
    _sync_visible_version(self)
    try:
        self.signal.set(
            "V1.6.4 · 外轨2.5分 / Volume<1.2× Hard Gate / RSI30–70 / 5m MACD改善 / 4H同向"
        )
    except Exception:
        pass
    scrollbar = getattr(self, "_v156_log_scrollbar", None)
    self._v164_log_scrollbar_max_round = isinstance(scrollbar, ui156.CleanScrollbar)
    self._v164_ui_ready = True


def apply():
    if getattr(app.App, "_kaytrade_v164_ui_applied", False):
        return
    app.App.__init__ = app_init
    app.App._kaytrade_v164_ui_applied = True


apply()

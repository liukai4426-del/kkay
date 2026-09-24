"""KAYTRADE V1.5.6 UI overlay on top of V1.5.5 Build1551.

V1.5.6 is presentation-only:
- compact the Run Log card to roughly half the V1.5.5 height;
- render each log as status dot + status label + full date/time + bold title + muted detail;
- waiting is yellow, normal running is green, alarms are red;
- replace the old dark-track scrollbar with a borderless panel-matched scrollbar;
- physically destroy any remaining second-signal and post-close-cooldown UI rows;
- preserve Build1551 trading semantics: Path C OFF and post-close cooldown OFF.
"""
from __future__ import annotations

import os
import re
import time
import traceback
import tkinter as tk
from pathlib import Path

from v155_build1551_patch import apply as apply_previous
apply_previous()

import app
import engine
import v155_build1551_patch as b1551

VERSION = "1.5.6"
BUILD = "1560"
LOG_CARD_HEIGHT = 140
PATH_C_ENABLED = False
COOLDOWN_ENABLED = False

YELLOW = "#f3c969"
GREEN = app.GREEN
RED = app.RED
TEXT = "#eef5f7"
MUTED = app.MUTED

_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(.*)$")

# Build1551 used the same probe environment variable.  Its App.__init__ wrapper
# writes before this final overlay gets a chance to finish.  Disable only the
# earlier success write; its error probe remains useful if inherited startup fails.
b1551._write_startup_probe = lambda owner: None

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


def _widget_text(widget):
    try:
        return str(widget.cget("text") or "")
    except Exception:
        return ""


def _destroy_grid_rows(owner, markers, extra_widgets=()):
    """Destroy matching label/input rows and collapse their parent grid gaps."""
    targets = set()
    for widget in _walk(owner.root):
        text = _widget_text(widget)
        if text and any(marker in text for marker in markers):
            try:
                info = widget.grid_info()
                if info:
                    targets.add((widget.master, int(info.get("row", -1))))
            except Exception:
                pass

    for widget in extra_widgets:
        if widget is None:
            continue
        try:
            info = widget.grid_info()
            if info:
                targets.add((widget.master, int(info.get("row", -1))))
        except Exception:
            pass

    grouped = {}
    for parent, row in targets:
        if parent is not None and row >= 0:
            grouped.setdefault(parent, set()).add(row)

    removed = 0
    for parent, rows in grouped.items():
        # Highest row first means lower row indices remain stable as gaps collapse.
        for row in sorted(rows, reverse=True):
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


def _purge_legacy_controls(owner):
    second = getattr(owner, "_v1543_second_signal_entry", None)
    _destroy_grid_rows(
        owner,
        ("第二信号仓位", "第二信号 /", "第二信号仓位 / 加仓资金"),
        extra_widgets=(second,),
    )
    _destroy_grid_rows(
        owner,
        ("平仓后冷却", "冷却时间", "平仓冷却"),
    )
    owner.fields.pop("second_signal_notional", None)
    owner.fields.pop("cooldown_minutes", None)
    owner._v156_second_signal_control_removed = True
    owner._v156_cooldown_control_removed = True


class CleanScrollbar(tk.Canvas):
    """Borderless scrollbar whose track is exactly the Run Log panel color."""
    def __init__(self, parent, command=None, width=10, **kwargs):
        self.command = command
        self.bar_width = int(width)
        self.first = 0.0
        self.last = 1.0
        self.drag_y = None
        self.drag_first = 0.0
        super().__init__(
            parent,
            width=self.bar_width,
            bg=app.PANEL,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            takefocus=0,
            **kwargs,
        )
        self.bind("<Configure>", lambda event: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def set(self, first, last):
        try:
            self.first = max(0.0, min(1.0, float(first)))
            self.last = max(self.first, min(1.0, float(last)))
        except Exception:
            self.first, self.last = 0.0, 1.0
        self._draw()

    def _bounds(self):
        h = max(1, self.winfo_height())
        margin = 2
        track = max(1, h - 2 * margin)
        span = max(0.0, min(1.0, self.last - self.first))
        thumb = min(track, max(24.0, track * span))
        travel = max(0.0, track - thumb)
        max_first = max(1e-9, 1.0 - span)
        y1 = margin + (travel * (self.first / max_first) if travel else 0.0)
        return y1, y1 + thumb, track, thumb, span

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        # No contrasting trough rectangle: this removes the visible black edge.
        y1, y2, _, _, _ = self._bounds()
        w = max(self.bar_width, self.winfo_width())
        self.create_rectangle(2, y1, max(3, w - 2), y2, fill="#465861", outline="")

    def _press(self, event):
        y1, y2, _, _, _ = self._bounds()
        if y1 <= event.y <= y2:
            self.drag_y = event.y
            self.drag_first = self.first
        elif self.command:
            self.command("scroll", -1 if event.y < y1 else 1, "pages")

    def _drag(self, event):
        if self.drag_y is None or not self.command:
            return
        _, _, track, thumb, span = self._bounds()
        travel = max(1.0, track - thumb)
        max_first = max(0.0, 1.0 - span)
        fraction = max(0.0, min(max_first, self.drag_first + (event.y - self.drag_y) / travel * max_first))
        self.command("moveto", fraction)

    def _release(self, event):
        self.drag_y = None


def _destroy_previous_log(owner):
    for name in ("_v155_log_surface", "_v146_log_surface"):
        widget = getattr(owner, name, None)
        if widget is not None:
            try:
                widget.destroy()
            except Exception:
                try:
                    widget.pack_forget()
                except Exception:
                    pass


def _on_wheel(owner, event):
    text = getattr(owner, "_v156_log_text", None)
    if text is None:
        return
    delta = int(getattr(event, "delta", 0) or 0)
    if delta:
        units = (-1 if delta > 0 else 1) if abs(delta) < 120 else int(-delta / 120)
        text.yview_scroll(units, "units")
        owner._v156_log_user_scrolled = True
        return "break"


def _on_linux_wheel(owner, units):
    text = getattr(owner, "_v156_log_text", None)
    if text is not None:
        text.yview_scroll(int(units), "units")
        owner._v156_log_user_scrolled = True
        return "break"


def _build_compact_log(owner):
    _destroy_previous_log(owner)
    surface = app.Card(owner.root, height=LOG_CARD_HEIGHT)
    try:
        surface.pack(side="bottom", fill="x", padx=15, pady=(0, 8), before=owner.book)
    except Exception:
        surface.pack(side="bottom", fill="x", padx=15, pady=(0, 8))

    body = surface.body
    app.card_label(body, text="运行记录", size=16, bold=True).pack(anchor="w")
    app.card_label(body, text="所有策略循环与安全拦截", color=MUTED, size=9).pack(anchor="w", pady=(2, 4))
    holder = tk.Frame(body, bg=app.PANEL, bd=0, highlightthickness=0)
    holder.pack(fill="both", expand=True)

    text = tk.Text(
        holder,
        height=3,
        wrap="word",
        bg=app.PANEL,
        fg=TEXT,
        font=("Helvetica", 10),
        bd=0,
        borderwidth=0,
        highlightthickness=0,
        relief="flat",
        insertborderwidth=0,
        padx=0,
        pady=1,
        state="disabled",
        cursor="arrow",
    )
    scroll = CleanScrollbar(holder, command=text.yview, width=10)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y", padx=(5, 0))
    text.pack(side="left", fill="both", expand=True)

    text.tag_configure("wait", foreground=YELLOW, font=("Helvetica", 9, "bold"))
    text.tag_configure("ok", foreground=GREEN, font=("Helvetica", 9, "bold"))
    text.tag_configure("alarm", foreground=RED, font=("Helvetica", 9, "bold"))
    text.tag_configure("time", foreground=MUTED, font=("Helvetica", 9))
    text.tag_configure("title", foreground=TEXT, font=("Helvetica", 10, "bold"))
    text.tag_configure("detail", foreground=MUTED, font=("Helvetica", 9), lmargin1=18, lmargin2=18, spacing3=2)

    text.bind("<MouseWheel>", lambda e: _on_wheel(owner, e))
    text.bind("<Button-4>", lambda e: _on_linux_wheel(owner, -1))
    text.bind("<Button-5>", lambda e: _on_linux_wheel(owner, 1))

    owner._v156_log_surface = surface
    owner._v156_log_text = text
    owner._v156_log_scrollbar = scroll
    # Keep old aliases pointing at the final widgets so inherited helpers cannot
    # accidentally target a destroyed V1.5.5 Text/scrollbar.
    owner._v155_log_surface = surface
    owner._v155_log_text = text
    owner._v155_log_scrollbar = scroll
    owner._v156_log_ready = True


def _extract_timestamp(line):
    raw = str(line or "").strip()
    match = _TIMESTAMP.match(raw)
    if match:
        return match.group(1), match.group(2).strip()
    return time.strftime("%Y-%m-%d %H:%M:%S"), raw


def _status_for(kind, message):
    text = str(message or "")
    if kind == "alarm":
        return "警报", "alarm"
    # Recovery/success language wins over words such as 故障/锁 that may appear
    # in a resolved event (e.g. 故障锁已解除).
    if any(x in text for x in ("已解除", "成功", "已通过", "已连接", "已恢复", "恢复核对", "成交", "已保存", "自动交易启动")):
        return "正常运行", "ok"
    if any(x in text for x in ("警报", "异常", "错误", "失败", "故障", "拒绝", "禁止", "风控拦截", "超时", "HTTP 401", "50123")):
        return "警报", "alarm"
    if any(x in text for x in ("等待", "待处理", "尚未", "未连接", "未满足", "加载", "准备", "观察", "锁定", "核对中")):
        return "等待", "wait"
    return "正常运行", "ok"


def _split_title_detail(message):
    msg = str(message or "").strip()
    if msg.startswith("警报："):
        msg = msg[3:].strip()
    for sep in ("：", "；", "。"):
        if sep in msg:
            left, right = msg.split(sep, 1)
            left = left.strip()
            right = right.strip()
            if left and len(left) <= 30 and right:
                return left, right
    if "，" in msg:
        left, right = msg.split("，", 1)
        if left.strip() and len(left.strip()) <= 26 and right.strip():
            return left.strip(), right.strip()
    if len(msg) > 34:
        return msg[:28].rstrip() + "…", msg
    return msg or "运行记录", ""


def _render_logs_v156(self):
    text = getattr(self, "_v156_log_text", None)
    if text is None:
        return
    try:
        view = text.yview()
        at_bottom = not view or float(view[1]) >= 0.995
        top = float(view[0]) if view else 0.0
    except Exception:
        at_bottom, top = True, 0.0

    try:
        text.configure(state="normal")
        text.delete("1.0", "end")
        rows = list(getattr(self, "log_lines", []) or [])[-500:]
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
                stamp, message = _extract_timestamp(line)
                status, tag = _status_for(kind, message)
                title, detail = _split_title_detail(message)
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

    if at_bottom:
        try:
            text.see("end")
        except Exception:
            pass
    else:
        try:
            text.yview_moveto(top)
        except Exception:
            pass
    self._v156_log_at_bottom = at_bottom


def _probe_target():
    return os.environ.get("KAYTRADE_STARTUP_PROBE", "").strip()


def _write_probe():
    target = _probe_target()
    if not target:
        return
    try:
        Path(target).write_text(
            f"PASS KAYTRADE {VERSION} Build {BUILD} compact_log=on legacy_controls=off cooldown=off path_c=off\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _write_probe_error():
    target = _probe_target()
    if not target:
        return
    try:
        Path(target + ".error").write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


def apply():
    if getattr(engine.Engine, "_kaytrade_v156_applied", False):
        return

    def app_init(self, *args, **kwargs):
        try:
            _PREVIOUS_INIT(self, *args, **kwargs)
            _purge_legacy_controls(self)
            _build_compact_log(self)
            self.render_logs = _render_logs_v156.__get__(self, app.App)
            self.render_logs()
            try:
                self.root.title("KAYTRADE 1.5.6 · BTC 策略控制台 · Build 1560")
            except Exception:
                pass
            self._v156_ready = True
            _write_probe()
        except Exception:
            _write_probe_error()
            raise

    def cycle(self):
        result = _PREVIOUS_CYCLE(self)
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
    app.App.render_logs = _render_logs_v156
    engine.Engine.cycle = cycle
    engine.Engine._kaytrade_v156_applied = True
    app.App._kaytrade_v156_applied = True


apply()

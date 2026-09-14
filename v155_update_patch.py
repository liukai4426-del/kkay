"""KAYTRADE V1.5.5 UI/settings overlay on top of Build 1544.

Confirmed V1.5.5 changes:
- keep the Build1544 trading model and Path C disabled;
- account connection must not leave Risk/Execution inputs locked;
- every visible run-log row shows its stored timestamp;
- run log gets mouse-wheel/trackpad scrolling and preserves reader position;
- remove the visible second-signal field entirely;
- first-signal amount is redefined as isolated-margin allocation;
- strategy capital becomes read-only leveraged position notional = margin * leverage;
- hidden second-signal compatibility value becomes first margin * 3, but Path C stays OFF.
"""
from __future__ import annotations

import math
import tkinter as tk
from dataclasses import asdict

from v154_build1544_patch import apply as apply_previous
apply_previous()

import app
import engine
import visual
import v138_strategy_patch as v138
import v140_score_dialog_patch as v140
import v154_build1543_patch as v1543
import v154_build1544_patch as v1544

VERSION = "1.5.5"
BUILD = "1550"
PATH_C_ENABLED = False
PATH_C_COMPAT_MULTIPLIER = 3.0
READONLY_FIELDS = {"capital", "score_threshold", "stop_atr"}
EDITABLE_FIELDS = {
    "first_signal_notional", "risk_usdt", "risk_pct", "daily_loss",
    "leverage", "cooldown_minutes",
}


def _walk(root):
    try:
        children = root.winfo_children()
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk(child)


def _entry_for(owner, field_name):
    var = owner.fields.get(field_name)
    if var is None:
        return None
    for widget in _walk(owner.root):
        if isinstance(widget, app.RoundedEntry) and getattr(widget, "variable", None) is var:
            return widget
    return None


def _set_entry_state(entry, enabled):
    if entry is None:
        return
    try:
        entry.entry.configure(
            state="normal" if enabled else "disabled",
            disabledbackground=app.FIELD,
            disabledforeground=app.MUTED,
        )
    except Exception:
        pass


def _apply_editable_state(owner):
    """Connected-but-idle settings remain editable; live trading locks edits."""
    running = bool(getattr(owner, "engine", None) and getattr(owner.engine, "enabled", False))
    for name in EDITABLE_FIELDS:
        _set_entry_state(_entry_for(owner, name), not running)
    for name in READONLY_FIELDS:
        _set_entry_state(_entry_for(owner, name), False)
    owner._v155_settings_locked_for_trading = running


def _remove_second_signal_row(owner):
    """Remove Build1543's compatibility row and collapse the grid gap."""
    second = getattr(owner, "_v1543_second_signal_entry", None)
    second_var = getattr(owner, "_v1543_second_signal_var", None)
    parent = getattr(second, "master", None)
    row = None
    if second is not None:
        try:
            info = second.grid_info()
            if info:
                row = int(info.get("row", -1))
        except Exception:
            row = None

    if parent is not None and row is not None and row >= 0:
        for child in list(parent.winfo_children()):
            try:
                info = child.grid_info()
                child_row = int(info.get("row", -99)) if info else -99
                text = str(child.cget("text") or "") if hasattr(child, "cget") else ""
            except Exception:
                continue
            if child is second or child_row == row and text.startswith("第二信号仓位 / 加仓资金"):
                try:
                    child.grid_remove()
                except Exception:
                    pass
        for child in list(parent.winfo_children()):
            if child is second:
                continue
            try:
                info = child.grid_info()
                if info and int(info.get("row", -1)) > row:
                    child.grid_configure(row=int(info["row"]) - 1)
            except Exception:
                pass
    elif second is not None:
        try:
            second.grid_remove()
        except Exception:
            pass

    owner.fields.pop("second_signal_notional", None)
    owner._v155_hidden_second_signal_var = second_var
    owner._v155_second_signal_removed = True


def _rename_money_labels(owner):
    for widget in _walk(owner.root):
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            continue
        try:
            if text.startswith("第一信号仓位 / 开仓资金") or text.startswith("开仓信号仓位") or text.startswith("第一信号仓位 USDT"):
                widget.configure(text="第一信号仓位 / 开仓保证金 USDT")
            elif text.startswith("策略资金预算 USDT"):
                widget.configure(text="策略资金预算 / 杠杆后仓位金额 USDT（自动）")
        except Exception:
            pass


def _sync_leveraged_capital(owner, *_):
    """capital is display/persistence for leveraged notional, never user-entered."""
    try:
        first = float(owner.fields["first_signal_notional"].get())
        leverage = float(owner.fields["leverage"].get())
        if not math.isfinite(first) or not math.isfinite(leverage) or first <= 0 or leverage <= 0:
            raise ValueError
        notional = first * leverage
        owner.fields["capital"].set(f"{notional:g}")
        hidden = getattr(owner, "_v155_hidden_second_signal_var", None)
        if hidden is not None:
            hidden.set(f"{first * PATH_C_COMPAT_MULTIPLIER:g}")
        owner._v155_margin_amount = first
        owner._v155_leveraged_notional = notional
    except Exception:
        try:
            owner.fields["capital"].set("")
        except Exception:
            pass


def _install_money_semantics(owner):
    _remove_second_signal_row(owner)
    _rename_money_labels(owner)
    _sync_leveraged_capital(owner)
    for key in ("first_signal_notional", "leverage"):
        var = owner.fields.get(key)
        if var is None:
            continue
        try:
            token = var.trace_add("write", lambda *_args, o=owner: _sync_leveraged_capital(o))
            setattr(owner, f"_v155_trace_{key}", token)
        except Exception:
            pass
    _apply_editable_state(owner)
    owner._v155_money_semantics_ready = True


def _settings_from_ui_v155(owner):
    """Translate margin UI semantics into the inherited notional sizing fields."""
    SettingsClass = app.Settings
    values = asdict(SettingsClass())
    for key, var in owner.fields.items():
        if key not in values:
            continue
        try:
            values[key] = float(var.get())
        except Exception:
            raise engine.Halt(key + "必须是有效数字") from None

    for key in ("leverage", "cooldown_minutes"):
        value = float(values[key])
        if int(value) != value:
            raise engine.Halt(key + "必须是整数")
        values[key] = int(value)

    first_margin = float(values.get("first_signal_notional") or 0.0)
    leverage = int(values.get("leverage") or 0)
    if not math.isfinite(first_margin) or first_margin <= 0:
        raise engine.Halt("第一信号开仓保证金必须是有限正数")
    if leverage <= 0:
        raise engine.Halt("逐仓杠杆必须是正整数")

    leveraged_notional = first_margin * leverage
    if not math.isfinite(leveraged_notional) or leveraged_notional <= 0:
        raise engine.Halt("杠杆后仓位金额无效")

    # V1.5.5 semantics: the editable first-signal value is margin.  The inherited
    # engine still consumes max_notional as a notional cap, so convert exactly once.
    values["first_signal_notional"] = first_margin
    values["second_signal_notional"] = first_margin * PATH_C_COMPAT_MULTIPLIER
    values["third_signal_notional"] = max(float(values.get("third_signal_notional") or 1.0), 1e-9)
    values["capital"] = leveraged_notional
    values["max_initial_notional"] = leveraged_notional
    values["max_notional"] = leveraged_notional
    values["consecutive_losses"] = 3  # compatibility only; live pause remains disabled
    values["score_threshold"] = 6.0
    values["stop_atr"] = 1.0

    settings = SettingsClass(**values).validate()
    return settings


def _release_stale_grab(owner):
    """Do not let a destroyed result dialog retain the Aqua/Tk input grab."""
    try:
        current = owner.grab_current()
    except Exception:
        current = None
    if current is not None:
        try:
            exists = bool(current.winfo_exists())
            mapped = bool(current.winfo_ismapped()) if exists else False
        except Exception:
            exists = mapped = False
        if not exists or not mapped:
            try:
                current.grab_release()
            except Exception:
                pass
    try:
        if owner.grab_current() is None:
            owner.focus_force()
    except Exception:
        pass


def _safe_modal(owner, *args, **kwargs):
    win = _PREVIOUS_MODAL(owner, *args, **kwargs)
    try:
        def released(event=None):
            if event is not None and getattr(event, "widget", None) is not win:
                return
            try:
                owner.after_idle(lambda: (_release_stale_grab(owner), _apply_editable_state_from_root(owner)))
            except Exception:
                pass
        win.bind("<Destroy>", released, add="+")
    except Exception:
        pass
    return win


def _apply_editable_state_from_root(root):
    app_owner = getattr(root, "_v155_app_owner", None)
    if app_owner is not None:
        _apply_editable_state(app_owner)


def _hide_old_log(owner):
    old_surface = getattr(owner, "_v146_log_surface", None)
    if old_surface is not None:
        try:
            old_surface.pack_forget()
        except Exception:
            pass
    old_log = getattr(owner, "log", None)
    if old_log is not None:
        try:
            old_log.pack_forget()
        except Exception:
            pass
    old_filter = getattr(owner, "_v154_log_filterbar", None) or getattr(owner, "_v146_hidden_log_filter", None)
    if old_filter is not None:
        try:
            old_filter.pack_forget()
        except Exception:
            pass


def _on_log_wheel(owner, event):
    text = getattr(owner, "_v155_log_text", None)
    if text is None:
        return
    delta = int(getattr(event, "delta", 0) or 0)
    if delta:
        units = (-1 if delta > 0 else 1) if abs(delta) < 120 else int(-delta / 120)
        text.yview_scroll(units, "units")
        owner._v155_log_user_scrolled = True
        return "break"


def _on_linux_wheel(owner, units):
    text = getattr(owner, "_v155_log_text", None)
    if text is not None:
        text.yview_scroll(units, "units")
        owner._v155_log_user_scrolled = True
        return "break"


def _build_scrollable_log(owner):
    _hide_old_log(owner)
    surface = app.Card(owner.root, height=255)
    try:
        surface.pack(side="bottom", fill="x", padx=15, pady=(0, 12), before=owner.book)
    except Exception:
        surface.pack(side="bottom", fill="x", padx=15, pady=(0, 12))
    body = surface.body
    app.card_label(body, text="运行记录", size=17, bold=True).pack(anchor="w")
    app.card_label(body, text="带时间戳 · 鼠标滚轮/触控板可查看历史记录", color=app.MUTED, size=10).pack(anchor="w", pady=(5, 8))
    holder = tk.Frame(body, bg=app.PANEL, bd=0, highlightthickness=0)
    holder.pack(fill="both", expand=True)
    text = tk.Text(
        holder, height=7, wrap="word", bg=app.PANEL, fg="#b9c9d0",
        font=("Menlo", 10), bd=0, highlightthickness=0, relief="flat",
        padx=4, pady=4, state="disabled",
    )
    scroll = app.WideScrollbar(holder, command=text.yview, width=16)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    text.tag_configure("normal", foreground="#9acbb9")
    text.tag_configure("alarm", foreground="#ff9d96")
    text.bind("<MouseWheel>", lambda e: _on_log_wheel(owner, e))
    text.bind("<Button-4>", lambda e: _on_linux_wheel(owner, -1))
    text.bind("<Button-5>", lambda e: _on_linux_wheel(owner, 1))
    owner._v155_log_surface = surface
    owner._v155_log_text = text
    owner._v155_log_scrollbar = scroll
    owner._v155_log_scroll_ready = True


def _render_logs_v155(self):
    text = getattr(self, "_v155_log_text", None)
    if text is None:
        return _PREVIOUS_RENDER_LOGS(self)
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
            text.insert("end", "等待运行记录\n", "normal")
        else:
            for kind, line in rows:
                tag = "alarm" if kind == "alarm" or "警报：" in str(line) else "normal"
                text.insert("end", "● " + str(line).rstrip() + "\n", tag)
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
    self._v155_log_at_bottom = at_bottom


_PREVIOUS_RENDER_LOGS = app.App.render_logs
_PREVIOUS_DRAIN = app.App.drain
_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_ARM = app.App.arm
_PREVIOUS_CYCLE = engine.Engine.cycle
_PREVIOUS_MODAL = v140._rounded_modal


def apply():
    if getattr(engine.Engine, "_kaytrade_v155_applied", False):
        return

    # Keep Path C hard-off.  The 3x value is compatibility metadata only.
    v1544.PATH_C_ENABLED = False
    v1543._settings_from_ui = _settings_from_ui_v155
    v140._rounded_modal = _safe_modal

    def app_init(self, *args, **kwargs):
        _PREVIOUS_APP_INIT(self, *args, **kwargs)
        try:
            self.root.title("KAYTRADE 1.5.5 · BTC 策略控制台")
            self.root._v155_app_owner = self
        except Exception:
            pass
        _install_money_semantics(self)
        _build_scrollable_log(self)
        self.render_logs()
        try:
            text = str(self.signal.get() or "")
            self.signal.set(text.replace("1.5.4", "1.5.5"))
        except Exception:
            pass
        self._v155_ready = True

    def app_settings(self):
        _sync_leveraged_capital(self)
        return _settings_from_ui_v155(self)

    def drain(self):
        result = _PREVIOUS_DRAIN(self)
        _apply_editable_state(self)
        return result

    def app_arm(self):
        original = app.simpledialog.askstring
        def askstring(title, prompt, *args, **kwargs):
            text = str(prompt)
            text += (
                "\n\nV1.5.5：第一信号输入值代表逐仓保证金；策略资金预算自动=第一信号保证金×逐仓杠杆且不可手动修改。"
                "风险设置不再显示第二信号栏。路径C继续关闭，不创建第二信号；内部兼容值按第一信号×3保存但不参与下单。"
            )
            return original(title, text, *args, **kwargs)
        app.simpledialog.askstring = askstring
        try:
            return _PREVIOUS_APP_ARM(self)
        finally:
            app.simpledialog.askstring = original

    def cycle(self):
        result = _PREVIOUS_CYCLE(self)
        if self.store:
            active = self.store.data.get("active")
            if isinstance(active, dict):
                changed = active.get("version") != VERSION or active.get("build") != BUILD
                active["version"] = VERSION
                active["build"] = BUILD
                active["path_c_enabled"] = False
                if changed:
                    self.store.save()
        return result

    app.App.__init__ = app_init
    app.App.settings = app_settings
    app.App.render_logs = _render_logs_v155
    app.App.drain = drain
    app.App.arm = app_arm
    engine.Engine.cycle = cycle
    engine.Engine._kaytrade_v155_applied = True
    app.App._kaytrade_v155_applied = True


apply()

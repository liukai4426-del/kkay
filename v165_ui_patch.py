"""KAYTRADE V1.6.5 presentation overlay — Build 1650.

Visible UI is synchronized to the production V1.6.5 strategy only. Historical
strategy text and DEMO/LIVE typed authorization are removed from the automatic
trading confirmation flow.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from v164_ui_patch import apply as apply_previous
apply_previous()

import app
import visual
import v161_ui_status_patch as ui161
import v164_ui_patch as ui164
import v165_model as model

VERSION = "1.6.5"
BUILD = "1650"
VERSION_SUBTITLE = f"BTC / USDT   ·   V{VERSION}"
WINDOW_TITLE = f"KAYTRADE {VERSION} · BTC 策略控制台 · Build {BUILD}"

_PREVIOUS_INIT = app.App.__init__
_PREVIOUS_STATUS_SNAPSHOT = ui161._status_snapshot

GREEN = app.GREEN
RED = app.RED
MUTED = app.MUTED

ui161.model = model
ui164.model = model
ui161._STATUS_ITEMS = (
    ("score", "评分≥6"),
    ("direction", "1H/15m主方向"),
    ("boll", "5m外轨BOLL"),
    ("signal_window", "最新5m信号周期"),
    ("middle_macd", "5m MACD改善"),
    ("outer_4h", "4H同向"),
    ("macd_clean", "5m RSI 30–70"),
    ("entry_drift", "入场未偏离"),
    ("front_space", "前方≥1.5R"),
    ("stop_structure", "止损结构"),
    ("cost", "成本≤0.30R"),
    ("adverse_4h", "4H结构安全"),
    ("daily_risk", "日内风险"),
    ("volume_gate", "Volume<1.20×"),
    ("clock", "时钟同步"),
)


def _status_snapshot_v165(owner, data):
    snapshot = dict(_PREVIOUS_STATUS_SNAPSHOT(owner, data) or {})
    snapshot.pop("funds", None)
    if not isinstance(data, dict):
        return snapshot
    _side, row = ui161._candidate(data)
    if not isinstance(row, dict):
        return snapshot
    confirmations = row.get("confirmations") or {}
    required = confirmations.get("required") or {}
    if "signal_window_ok" in confirmations:
        snapshot["signal_window"] = bool(confirmations.get("signal_window_ok"))
    elif "signal_window_5m" in required:
        snapshot["signal_window"] = bool(required.get("signal_window_5m"))
    return snapshot


ui161._status_snapshot = _status_snapshot_v165


def _render_status_v165(owner):
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
            text = "Volume：允许开仓" if value is True else "Volume：禁止开仓" if value is False else "Volume<1.20×"
        elif key == "signal_window":
            text = "5m外轨：当前周期有效" if value is True else "5m外轨：等待最新信号" if value is False else "最新5m信号周期"
        try:
            widget.configure(fg=color, text="● " + text)
        except Exception:
            pass


ui161._render_status = _render_status_v165


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
    owner._v165_version_label = target
    owner._v165_version_label_updated = updated
    return updated


def _authorization_text(owner, settings):
    env = "OKX模拟盘" if owner.engine.x.demo else "真实账户"
    base_risk = min(settings.risk_usdt, settings.capital * settings.risk_pct / 100)
    return (
        f"{env}  ·  BTC-USDT-SWAP  ·  逐仓 {settings.leverage}×\n\n"
        "V1.6.5 当前生效策略\n"
        "• 所有技术指标仅使用各自周期最新已收盘K线，不使用未收盘K线。\n"
        "• 1H / 15m 确认主方向；5m只允许BOLL上下外轨入场。\n"
        "• 最新已收盘5m触发外轨时 BOLL +2.5；5m RSI必须30–70；5m MACD必须连续向交易方向改善。\n"
        "• 5m Volume必须 < 前20根5m均量1.20×；达到或超过1.20×时 Hard Gate 禁止开仓，Volume不计分。\n"
        "• BOLL及同根5m RSI / Volume / KDJ 信号属性有效至下一根5m收盘；新5m收盘后整组失效并重新计算。\n"
        "• 15m / 1H / 4H 在各自新K线收盘后自动刷新；4H必须与交易方向同向。\n"
        "• 评分必须 ≥6；前方强结构至少1.5R；成本≤0.30R；止损结构和入场偏离限制必须通过。\n"
        "• 下单前 Final Entry Guard 再次确认各周期数据新鲜度与全部动态 Hard Gate。\n"
        "• 首次开仓固定1× LIMIT；止损=1×1H ATR；整仓2R止盈；No-BE。\n\n"
        f"资金预算 {settings.capital:g} USDT  ·  最大名义仓位 {settings.max_notional:g} USDT  ·  "
        f"基础单笔风险≤{base_risk:g} USDT\n\n"
        "确认后将启动自动交易；取消则保持停止新开仓。"
    )


def _show_authorization_dialog(owner, settings):
    result = {"confirmed": False}
    win = tk.Toplevel(owner.root)
    win.title("启动自动交易 · V1.6.5")
    win.configure(bg=visual.BG)
    win.transient(owner.root)
    win.resizable(False, False)
    win.protocol("WM_DELETE_WINDOW", win.destroy)

    outer = tk.Frame(win, bg=visual.BG, padx=18, pady=18)
    outer.pack(fill="both", expand=True)
    card = tk.Frame(outer, bg=visual.PANEL, padx=24, pady=22)
    card.pack(fill="both", expand=True)

    visual.label(card, text="启动自动交易", size=20, bold=True, color=visual.TEXT, bg=visual.PANEL).pack(anchor="w")
    visual.label(card, text="V1.6.5 · 当前正式策略确认", size=11, color=visual.GREEN, bg=visual.PANEL).pack(anchor="w", pady=(5, 16))
    tk.Label(
        card, text=_authorization_text(owner, settings), justify="left", anchor="nw",
        wraplength=600, bg=visual.PANEL, fg=visual.TEXT,
        font=("Helvetica", 11), bd=0, highlightthickness=0,
    ).pack(fill="x", anchor="w")

    buttons = tk.Frame(card, bg=visual.PANEL)
    buttons.pack(fill="x", pady=(22, 0))

    def cancel():
        win.destroy()

    def confirm():
        result["confirmed"] = True
        win.destroy()

    visual.RoundedButton(buttons, text="取消", command=cancel, variant="neutral", width=150, height=44, radius=16).pack(side="right")
    visual.RoundedButton(buttons, text="确认", command=confirm, variant="accent", width=150, height=44, radius=16).pack(side="right", padx=(0, 10))

    win.update_idletasks()
    width = 680
    height = max(560, min(720, win.winfo_reqheight()))
    try:
        x = owner.root.winfo_rootx() + max(0, (owner.root.winfo_width() - width) // 2)
        y = owner.root.winfo_rooty() + max(0, (owner.root.winfo_height() - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")
    except Exception:
        win.geometry(f"{width}x{height}")
    win.grab_set()
    win.focus_force()
    owner._v165_authorization_dialog = win
    owner.root.wait_window(win)
    return bool(result["confirmed"])


def app_arm_v165(self):
    if not self.engine:
        messagebox.showerror("未连接", "先测试连接")
        return
    if self.network_paused:
        messagebox.showerror("网络暂停", "等待网络恢复并核对账户后再启动")
        return
    if self.host.get() != self.engine.x.host or (self.mode.get() == "OKX模拟盘") != self.engine.x.demo:
        messagebox.showerror("连接不一致", "账户环境已修改，请重新测试连接")
        return
    try:
        settings = self.settings()
    except Exception as exc:
        messagebox.showerror("设置错误", str(exc))
        return
    if _show_authorization_dialog(self, settings):
        self.submit("arm", settings)


def app_init(self, *args, **kwargs):
    _PREVIOUS_INIT(self, *args, **kwargs)
    self.root.title(WINDOW_TITLE)
    _sync_visible_version(self)
    try:
        self.signal.set(
            "V1.6.5 · 最新已收盘K线 / 1H+15m方向 / 5m外轨2.5 / RSI30–70 / MACD改善 / Volume<1.20× / 4H同向"
        )
    except Exception:
        pass
    self._v165_ui_ready = True


def apply():
    if getattr(app.App, "_kaytrade_v165_ui_applied", False):
        return
    app.App.__init__ = app_init
    app.App.arm = app_arm_v165
    app.App._kaytrade_v165_ui_applied = True


apply()

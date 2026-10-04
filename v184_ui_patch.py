"""KAYTRADE V1.8.4 clean UI overlay.

Pure presentation layer. No Paper runtime, no local matching, no Paper APIs.
Restores the refined V1.8.2 AI plan layout on top of the V1.8.4 OKX Demo engine.
"""
from __future__ import annotations

from v183_okx_demo_patch import apply as apply_previous
apply_previous()

import app
import visual
import v165_model as model
import v165_update_patch as runtime
import v166_ui_patch as ui166
import v168_update_patch as v168
import v168_build1681_patch as b1681
import v170_update_patch as v170
import v170_build1701_patch as v1701
import v171_update_patch as v171
import v172_ai_only_patch as v172
import v180_ai_only_patch as v180
import v183_okx_demo_patch as v183

VERSION = "1.8.4"
BUILD = "1840"

_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_REFRESH = v183._refresh_v183_dashboard


def _badge(parent, variable, fg=visual.TEXT, fill="#1b2a32", width=126):
    panel = visual.RoundedPanel(
        parent,
        height=34,
        width=width,
        fill=fill,
        radius=10,
        pad_x=1,
        pad_y=1,
    )
    panel.pack_propagate(False)
    app.tk.Label(
        panel.body,
        textvariable=variable,
        bg=fill,
        fg=fg,
        font=("Helvetica", 10, "bold"),
        anchor="center",
        bd=0,
        highlightthickness=0,
    ).pack(fill="both", expand=True)
    return panel


def _install_refined_plan(owner):
    try:
        dash = owner.book.pages[3].body
    except Exception:
        return

    old = getattr(owner, "_v180_plan_card", None)
    if old is not None:
        try:
            old.pack_forget()
        except Exception:
            pass

    execution = getattr(owner, "_v180_execution_card", None)
    card = visual.Card(dash, height=505)
    kwargs = dict(fill="x", pady=(0, 12))
    if execution is not None:
        kwargs["before"] = execution
    card.pack(**kwargs)

    header = app.tk.Frame(card.body, bg=visual.PANEL)
    header.pack(fill="x")
    title_wrap = app.tk.Frame(header, bg=visual.PANEL)
    title_wrap.pack(side="left", fill="x", expand=True)
    visual.label(
        title_wrap,
        text="AI 交易方案看板",
        size=19,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL,
    ).pack(anchor="w")
    visual.label(
        title_wrap,
        text="主方案 / 推荐理由 / 操作建议 / 最近方案 · OKX DEMO · LIMIT ONLY",
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(3, 0))

    owner._v180_status_chip_var = app.tk.StringVar(value="WAITING")
    owner._v180_mode_var = app.tk.StringVar(value="OKX DEMO")
    _badge(
        header,
        owner._v180_status_chip_var,
        fg=visual.TEXT,
        fill=visual.PANEL_ALT,
        width=118,
    ).pack(side="right", padx=(8, 0))
    _badge(
        header,
        owner._v180_mode_var,
        fg=visual.GREEN,
        fill="#143229",
        width=142,
    ).pack(side="right")

    hero = app.tk.Frame(card.body, bg=visual.PANEL)
    hero.pack(fill="x", pady=(12, 10))
    hero.columnconfigure(0, weight=3)
    hero.columnconfigure(1, weight=1)

    main = visual.RoundedPanel(
        hero,
        height=176,
        fill=visual.PANEL_ALT,
        radius=16,
        pad_x=18,
        pad_y=14,
    )
    main.grid(row=0, column=0, sticky="nsew", padx=(0, 7))

    owner._v180_side_var = app.tk.StringVar(value="等待 AI 方向")
    owner._v180_entry_var = app.tk.StringVar(value="—")
    owner._v180_type_var = app.tk.StringVar(value="委托：OKX限价")
    owner._v180_tier_var = app.tk.StringVar(value="档位：—")
    owner._v180_leverage_var = app.tk.StringVar(value="杠杆：—")

    visual.label(
        main.body,
        text="当前主方案",
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL_ALT,
    ).pack(anchor="w")
    owner._v184_direction_label = visual.label(
        main.body,
        variable=owner._v180_side_var,
        size=23,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL_ALT,
    )
    owner._v184_direction_label.pack(anchor="w", pady=(7, 0))

    visual.label(
        main.body,
        text="建议 / 委托入场",
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL_ALT,
    ).pack(anchor="w", pady=(13, 0))
    owner._v184_entry_value_label = visual.label(
        main.body,
        variable=owner._v180_entry_var,
        size=28,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL_ALT,
    )
    owner._v184_entry_value_label.pack(anchor="w", pady=(2, 0))

    meta = app.tk.Frame(main.body, bg=visual.PANEL_ALT)
    meta.pack(fill="x", pady=(10, 0))
    for var in (owner._v180_type_var, owner._v180_tier_var, owner._v180_leverage_var):
        _badge(meta, var, width=128).pack(side="left", padx=(0, 7))

    risk = app.tk.Frame(hero, bg=visual.PANEL)
    risk.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
    risk.rowconfigure(0, weight=1)
    risk.rowconfigure(1, weight=1)
    risk.columnconfigure(0, weight=1)

    owner._v180_tp_var = app.tk.StringVar(value="—")
    owner._v180_sl_var = app.tk.StringVar(value="—")

    tp_panel = visual.RoundedPanel(
        risk,
        height=84,
        fill="#123028",
        radius=14,
        pad_x=14,
        pad_y=10,
    )
    tp_panel.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
    visual.label(
        tp_panel.body,
        text="止盈 TP · 限价",
        size=9,
        color="#8fbcae",
        bg="#123028",
    ).pack(anchor="w")
    visual.label(
        tp_panel.body,
        variable=owner._v180_tp_var,
        size=20,
        bold=True,
        color=visual.GREEN,
        bg="#123028",
    ).pack(anchor="w", pady=(5, 0))

    sl_panel = visual.RoundedPanel(
        risk,
        height=84,
        fill="#321a22",
        radius=14,
        pad_x=14,
        pad_y=10,
    )
    sl_panel.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
    visual.label(
        sl_panel.body,
        text="止损 SL · 限价",
        size=9,
        color="#c39aa5",
        bg="#321a22",
    ).pack(anchor="w")
    visual.label(
        sl_panel.body,
        variable=owner._v180_sl_var,
        size=20,
        bold=True,
        color=visual.RED,
        bg="#321a22",
    ).pack(anchor="w", pady=(5, 0))

    text_row = app.tk.Frame(card.body, bg=visual.PANEL)
    text_row.pack(fill="x", pady=(0, 9))
    text_row.columnconfigure(0, weight=2)
    text_row.columnconfigure(1, weight=1)

    owner._v180_reason_var = app.tk.StringVar(value="等待 AI 推荐理由…")
    owner._v180_advice_var = app.tk.StringVar(value="等待 AI 运行操作建议…")

    reason = visual.RoundedPanel(
        text_row,
        height=120,
        fill=visual.PANEL_ALT,
        radius=14,
        pad_x=14,
        pad_y=10,
    )
    reason.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    visual.label(
        reason.body,
        text="AI 推荐理由",
        size=10,
        bold=True,
        color=visual.MUTED,
        bg=visual.PANEL_ALT,
    ).pack(anchor="w")
    app.tk.Label(
        reason.body,
        textvariable=owner._v180_reason_var,
        bg=visual.PANEL_ALT,
        fg=visual.TEXT,
        font=("Helvetica", 10),
        justify="left",
        anchor="nw",
        wraplength=760,
        bd=0,
        highlightthickness=0,
    ).pack(fill="both", expand=True, pady=(6, 0))

    advice = visual.RoundedPanel(
        text_row,
        height=120,
        fill="#10271f",
        radius=14,
        pad_x=14,
        pad_y=10,
    )
    advice.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
    visual.label(
        advice.body,
        text="运行操作建议",
        size=10,
        bold=True,
        color=visual.GREEN,
        bg="#10271f",
    ).pack(anchor="w")
    app.tk.Label(
        advice.body,
        textvariable=owner._v180_advice_var,
        bg="#10271f",
        fg=visual.TEXT,
        font=("Helvetica", 10, "bold"),
        justify="left",
        anchor="nw",
        wraplength=460,
        bd=0,
        highlightthickness=0,
    ).pack(fill="both", expand=True, pady=(6, 0))

    owner._v180_history_var = app.tk.StringVar(value="暂无 AI 方案记录")
    history = visual.RoundedPanel(
        card.body,
        height=83,
        fill=visual.PANEL_ALT,
        radius=14,
        pad_x=14,
        pad_y=9,
    )
    history.pack(fill="x")
    visual.label(
        history.body,
        text="最近方案",
        size=9,
        bold=True,
        color=visual.MUTED,
        bg=visual.PANEL_ALT,
    ).pack(anchor="w")
    app.tk.Label(
        history.body,
        textvariable=owner._v180_history_var,
        bg=visual.PANEL_ALT,
        fg="#c9d3d8",
        font=("Menlo", 9),
        justify="left",
        anchor="nw",
        wraplength=1450,
        bd=0,
        highlightthickness=0,
    ).pack(fill="both", expand=True, pady=(4, 0))

    owner._v180_plan_card = card
    owner._v184_plan_card = card


def _refresh_v184(owner):
    _PREVIOUS_REFRESH(owner)
    try:
        latest = {}
        engine = getattr(owner, "engine", None)
        store = getattr(engine, "store", None) if engine is not None else None
        if store is not None:
            history = store.data.get("ai_plan_history") or []
            latest = history[-1] if history else {}
        direction = str(latest.get("direction") or "").lower()
        accent = (
            visual.GREEN if direction == "long"
            else visual.RED if direction == "short"
            else visual.TEXT
        )
        if getattr(owner, "_v184_direction_label", None) is not None:
            owner._v184_direction_label.configure(fg=accent)
        if getattr(owner, "_v184_entry_value_label", None) is not None:
            owner._v184_entry_value_label.configure(fg=accent)
    except Exception:
        pass


def _app_init_v184(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    _install_refined_plan(self)
    try:
        self.root.title("KAYTRADE 1.8.4 · OKX DEMO CLEAN · Build 1840")
        self.signal.set(
            "V1.8.4 Build1840 CLEAN｜OKX DEMO ONLY｜LIMIT ONLY｜TP/SL 禁止 -1 市价哨兵"
        )
    except Exception:
        pass
    try:
        packed = self.book.pages[3].body.pack_slaves()
        auto = getattr(self, "_v183_auto_exec_card", None)
        plan = getattr(self, "_v180_plan_card", None)
        execution = getattr(self, "_v180_execution_card", None)
        if auto is not None and plan is not None:
            auto.pack_forget()
            auto.pack(fill="x", pady=(0, 12), before=plan)
        if getattr(self, "quote", None) is not None and auto is not None:
            self.quote.pack_forget()
            self.quote.pack(fill="x", pady=(0, 10), before=auto)
    except Exception:
        pass
    self._v184_ui_ready = True


def apply():
    if getattr(model, "_kaytrade_v184_applied", False):
        return

    # Promote the exchange-backed runtime; the class reads these globals at call time.
    v183.VERSION = VERSION
    v183.BUILD = BUILD
    for module in (model, runtime, v168, b1681, v170, v1701, v171, v172, v180):
        module.VERSION = VERSION
        module.BUILD = BUILD
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.8.4 · OKX DEMO CLEAN · Build 1840"

    v183._refresh_v183_dashboard = _refresh_v184
    v180._refresh_v180_dashboard = _refresh_v184
    app.App.__init__ = _app_init_v184

    model._kaytrade_v184_applied = True
    app.App._kaytrade_v184_applied = True


apply()

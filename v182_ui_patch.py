"""KAYTRADE V1.8.2 UI finalization overlay.

Build1820 aligns Trading Overview with the active V1.8.1 LIMIT-ONLY Paper runtime:
- BTC quote first;
- AI auto-execution control directly above the AI plan board;
- stacked/aligned entry label + price;
- LONG entry/side green, SHORT entry/side red;
- rounded nested AI-plan surfaces;
- Paper/Limit-only wording only;
- legacy strategy/position wording hidden.
"""
from __future__ import annotations

import time

from v181_paper_patch import apply as apply_previous
apply_previous()

import app
import engine as legacy_engine
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
import v181_paper_patch as v181
import visual

VERSION = "1.8.2"
BUILD = "1820"
AI_ONLY = True
PAPER_ONLY = True
LIMIT_ONLY = True

_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_EMIT = app.App.emit


def _hide(widget):
    if widget is None:
        return
    try:
        widget.pack_forget()
    except Exception:
        pass
    try:
        widget.grid_forget()
    except Exception:
        pass


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


def _exec_tile(parent, title, variable, accent=visual.TEXT, height=80):
    panel = visual.RoundedPanel(
        parent,
        height=height,
        fill=visual.PANEL_ALT,
        radius=12,
        pad_x=14,
        pad_y=10,
    )
    visual.label(
        panel.body,
        text=title,
        color=visual.MUTED,
        size=9,
        bg=visual.PANEL_ALT,
    ).pack(anchor="w")
    visual.label(
        panel.body,
        variable=variable,
        color=accent,
        size=16,
        bold=True,
        bg=visual.PANEL_ALT,
    ).pack(anchor="w", pady=(6, 0))
    return panel


def _install_auto_card(owner, dash, actions):
    card = visual.Card(dash, height=112)
    card.pack(fill="x", pady=(0, 12), before=actions)

    row = app.tk.Frame(card.body, bg=visual.PANEL)
    row.pack(fill="both", expand=True)
    left = app.tk.Frame(row, bg=visual.PANEL)
    left.pack(side="left", fill="both", expand=True)

    visual.label(
        left,
        text="AI方案自动执行",
        size=16,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL,
    ).pack(anchor="w")

    owner._v181_auto_exec_status_var = app.tk.StringVar(value="关闭 · AI推荐仅展示")
    owner._v181_auto_exec_note_var = app.tk.StringVar(
        value="开启后，完整AI入场方案自动转换为Paper限价挂单；TP / SL 均为强制限价保护。"
    )
    visual.label(
        left,
        variable=owner._v181_auto_exec_status_var,
        size=11,
        bold=True,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(5, 1))
    visual.label(
        left,
        variable=owner._v181_auto_exec_note_var,
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w")

    owner._v181_auto_exec_button = visual.RoundedButton(
        row,
        text="开启自动执行",
        command=lambda: v181._toggle_auto_execute(owner),
        variant="accent",
        width=160,
        height=42,
    )
    owner._v181_auto_exec_button.pack(side="right", padx=(20, 0), pady=10)
    owner._v181_auto_exec_card = card
    owner._v182_auto_card = card


def _install_plan_card(owner, dash, actions):
    plan = visual.Card(dash, height=505)
    plan.pack(fill="x", pady=(0, 12), before=actions)

    header = app.tk.Frame(plan.body, bg=visual.PANEL)
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
        text="主方案 / 推荐理由 / 操作建议 / 最近方案 · LIMIT ONLY",
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(3, 0))

    owner._v180_status_chip_var = app.tk.StringVar(value="WAITING")
    owner._v180_mode_var = app.tk.StringVar(value="PAPER · MANUAL")
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
        width=132,
    ).pack(side="right")

    hero = app.tk.Frame(plan.body, bg=visual.PANEL)
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
    owner._v180_type_var = app.tk.StringVar(value="委托：限价")
    owner._v180_tier_var = app.tk.StringVar(value="档位：—")
    owner._v180_leverage_var = app.tk.StringVar(value="杠杆：—")

    visual.label(
        main.body,
        text="当前主方案",
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL_ALT,
    ).pack(anchor="w")
    owner._v181_direction_label = visual.label(
        main.body,
        variable=owner._v180_side_var,
        size=23,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL_ALT,
    )
    owner._v181_direction_label.pack(anchor="w", pady=(7, 0))

    visual.label(
        main.body,
        text="建议 / 委托入场",
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL_ALT,
    ).pack(anchor="w", pady=(13, 0))
    owner._v182_entry_value_label = visual.label(
        main.body,
        variable=owner._v180_entry_var,
        size=28,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL_ALT,
    )
    owner._v182_entry_value_label.pack(anchor="w", pady=(2, 0))

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

    text_row = app.tk.Frame(plan.body, bg=visual.PANEL)
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
        plan.body,
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

    owner._v180_plan_card = plan
    owner._v182_plan_card = plan


def _install_execution_card(owner, dash, actions):
    execution = visual.Card(dash, height=482)
    execution.pack(fill="x", pady=(0, 12), before=actions)

    header = app.tk.Frame(execution.body, bg=visual.PANEL)
    header.pack(fill="x")
    title_wrap = app.tk.Frame(header, bg=visual.PANEL)
    title_wrap.pack(side="left", fill="x", expand=True)
    visual.label(
        title_wrap,
        text="订单 / 持仓执行",
        size=19,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL,
    ).pack(anchor="w")
    visual.label(
        title_wrap,
        text="双档Paper订单 · 入场 / TP / SL / 减仓均为限价",
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(3, 0))

    owner._v180_position_badge_var = app.tk.StringVar(value="Paper 空仓")
    owner._v181_position_badge_label = _badge(
        header,
        owner._v180_position_badge_var,
        fg=visual.MUTED,
        fill=visual.PANEL_ALT,
        width=130,
    )
    owner._v181_position_badge_label.pack(side="right")

    owner._v180_order_state_var = app.tk.StringVar(value="等待委托")
    owner._v180_order_type_var = app.tk.StringVar(value="限价")
    owner._v180_order_px_var = app.tk.StringVar(value="—")
    owner._v180_fill_px_var = app.tk.StringVar(value="—")
    owner._v180_size_var = app.tk.StringVar(value="—")
    owner._v180_position_tp_var = app.tk.StringVar(value="—")
    owner._v180_position_sl_var = app.tk.StringVar(value="—")
    owner._v180_proposal_var = app.tk.StringVar(value="—")

    summary = app.tk.Frame(execution.body, bg=visual.PANEL)
    summary.pack(fill="x", pady=(12, 8))
    _exec_tile(summary, "订单状态", owner._v180_order_state_var).pack(
        side="left", fill="x", expand=True, padx=(0, 5)
    )
    _exec_tile(summary, "委托方式", owner._v180_order_type_var).pack(
        side="left", fill="x", expand=True, padx=(5, 0)
    )

    grid = app.tk.Frame(execution.body, bg=visual.PANEL)
    grid.pack(fill="x", pady=(0, 10))
    items = (
        ("委托入场", owner._v180_order_px_var, visual.TEXT),
        ("成交均价", owner._v180_fill_px_var, visual.TEXT),
        ("止盈 TP · 限价", owner._v180_position_tp_var, visual.GREEN),
        ("止损 SL · 限价", owner._v180_position_sl_var, visual.RED),
        ("持仓 / 委托数量", owner._v180_size_var, visual.TEXT),
        ("Proposal ID", owner._v180_proposal_var, visual.MUTED),
    )
    for idx, (title, var, accent) in enumerate(items):
        tile = _exec_tile(grid, title, var, accent=accent, height=80)
        tile.grid(row=idx // 3, column=idx % 3, sticky="ew", padx=4, pady=4)
    for col in range(3):
        grid.columnconfigure(col, weight=1, uniform="v182exec")

    owner._v180_tier1_var = app.tk.StringVar(value="等待第一档 AI 计划")
    owner._v180_tier2_var = app.tk.StringVar(value="等待第二档 AI 计划")

    tiers = app.tk.Frame(execution.body, bg=visual.PANEL)
    tiers.pack(fill="both", expand=True)
    tiers.columnconfigure(0, weight=1, uniform="v182tiers")
    tiers.columnconfigure(1, weight=1, uniform="v182tiers")

    tier1 = visual.RoundedPanel(
        tiers,
        height=118,
        fill=visual.PANEL_ALT,
        radius=12,
        pad_x=14,
        pad_y=10,
    )
    tier1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    owner._v181_tier1_label = visual.label(
        tier1.body,
        text="第一档执行方案",
        size=11,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL_ALT,
    )
    owner._v181_tier1_label.pack(anchor="w")
    app.tk.Label(
        tier1.body,
        textvariable=owner._v180_tier1_var,
        bg=visual.PANEL_ALT,
        fg=visual.TEXT,
        font=("Helvetica", 10),
        justify="left",
        anchor="nw",
        wraplength=650,
        bd=0,
        highlightthickness=0,
    ).pack(fill="both", expand=True, pady=(6, 0))

    tier2 = visual.RoundedPanel(
        tiers,
        height=118,
        fill=visual.PANEL_ALT,
        radius=12,
        pad_x=14,
        pad_y=10,
    )
    tier2.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
    owner._v181_tier2_label = visual.label(
        tier2.body,
        text="第二档执行方案",
        size=11,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL_ALT,
    )
    owner._v181_tier2_label.pack(anchor="w")
    app.tk.Label(
        tier2.body,
        textvariable=owner._v180_tier2_var,
        bg=visual.PANEL_ALT,
        fg=visual.TEXT,
        font=("Helvetica", 10),
        justify="left",
        anchor="nw",
        wraplength=650,
        bd=0,
        highlightthickness=0,
    ).pack(fill="both", expand=True, pady=(6, 0))

    owner._v180_execution_card = execution
    owner._v182_execution_card = execution


def _clean_legacy_overview(owner, dash):
    # Old strategy position label is stale in Paper mode.
    position_var = getattr(owner, "position", None)
    if position_var is not None:
        _hide(v181._find_label_for_var(dash, position_var))

    actions = getattr(getattr(owner, "trade_button", None), "master", None)
    if actions is not None:
        for child in list(actions.winfo_children()):
            if child is getattr(owner, "trade_button", None):
                continue
            text = v172._widget_text(child)
            if "仅平本程序仓位" in text or "核对后解除故障锁" in text:
                _hide(child)
    return actions


def _install_v182_overview(owner):
    try:
        dash = owner.book.pages[3].body
    except Exception:
        return

    for name in (
        "_v180_plan_card",
        "_v180_execution_card",
        "_v181_auto_exec_card",
        "_v172_ai_card",
        "_v172_ai_plan_card",
        "_v172_position_card",
    ):
        _hide(getattr(owner, name, None))

    actions = _clean_legacy_overview(owner, dash)
    if actions is None:
        return

    # Exact visible order: quote -> auto setting -> AI plan -> execution -> channel controls.
    quote = getattr(owner, "quote", None)
    if quote is not None:
        _hide(quote)
        quote.pack(fill="x", pady=(0, 12), before=actions)

    _install_auto_card(owner, dash, actions)
    _install_plan_card(owner, dash, actions)
    _install_execution_card(owner, dash, actions)


def _refresh_v182_dashboard(owner):
    try:
        engine = getattr(owner, "engine", None)
        store = getattr(engine, "store", None) if engine is not None else None
        data = getattr(store, "data", {}) if store is not None else {}
        paper = data.get("paper_execution") or {}
        tiers = paper.get("tiers") or {}
        history = data.get("ai_plan_history") or []
        latest = history[-1] if history else {}
        auto_on = bool(paper.get("auto_execute_plans"))

        status_var = getattr(owner, "_v181_auto_exec_status_var", None)
        note_var = getattr(owner, "_v181_auto_exec_note_var", None)
        button = getattr(owner, "_v181_auto_exec_button", None)
        if status_var is not None:
            status_var.set(
                "已开启 · AI方案到达即自动限价挂单"
                if auto_on else
                "关闭 · AI推荐仅展示"
            )
        if note_var is not None:
            note_var.set(
                "强制条件：方向 / 数量 / 杠杆 / 限价入场价 / TP / SL 完整；所有执行均为 LIMIT。"
                if auto_on else
                "开启后，完整AI入场方案自动转换为Paper限价挂单；TP / SL 均为强制限价保护。"
            )
        if button is not None:
            button.configure(
                text="关闭自动执行" if auto_on else "开启自动执行",
                variant="danger" if auto_on else "accent",
            )
        owner._v180_mode_var.set("PAPER · AUTO ON" if auto_on else "PAPER · MANUAL")

        direction = ""
        if latest:
            direction = str(latest.get("direction") or "").lower()
            owner._v180_side_var.set(v180._side_cn(direction))
            owner._v180_type_var.set("委托：限价")
            owner._v180_tier_var.set(f"档位：{latest.get('tier') or 1}")
            lev = latest.get("leverage")
            owner._v180_leverage_var.set(f"杠杆：{lev}×" if lev else "杠杆：—")
            entry = latest.get("limit_price") or latest.get("suggested_entry")
            owner._v180_entry_var.set(v180._fmt_px(entry))
            owner._v180_tp_var.set(v180._fmt_px(latest.get("take_profit")))
            owner._v180_sl_var.set(v180._fmt_px(latest.get("stop_loss")))
            owner._v180_reason_var.set(str(latest.get("reason") or "—"))
            owner._v180_advice_var.set(
                str(latest.get("operation_advice") or "等待AI建议")
            )
            owner._v180_status_chip_var.set(
                str(latest.get("status") or "RECOMMENDED")
            )
        else:
            owner._v180_side_var.set("等待 AI 方向")
            owner._v180_entry_var.set("—")
            owner._v180_status_chip_var.set("WAITING")

        accent = (
            visual.GREEN if direction == "long"
            else visual.RED if direction == "short"
            else visual.TEXT
        )
        if getattr(owner, "_v181_direction_label", None) is not None:
            owner._v181_direction_label.configure(fg=accent)
        if getattr(owner, "_v182_entry_value_label", None) is not None:
            owner._v182_entry_value_label.configure(fg=accent)

        rows = []
        for item in history[-5:][::-1]:
            stamp = time.strftime(
                "%H:%M:%S",
                time.localtime(float(item.get("time") or time.time())),
            )
            side = str(item.get("direction") or "WAIT").upper()
            kind = "LMT" if str(item.get("order_type") or "limit").lower() == "limit" else "INVALID"
            entry = item.get("limit_price") or item.get("suggested_entry")
            rows.append(
                f"{stamp}  T{item.get('tier') or 1}  {side:<5} {kind}  "
                f"{v180._fmt_px(entry):>10}  {str(item.get('status') or '')[:15]}"
            )
        owner._v180_history_var.set(
            "\n".join(rows) if rows else "暂无 AI 方案记录"
        )

        t1 = tiers.get("1")
        t2 = tiers.get("2")
        owner._v180_tier1_var.set(v181._tier_ui_text(t1))
        owner._v180_tier2_var.set(v181._tier_ui_text(t2))

        for label, item in (
            (getattr(owner, "_v181_tier1_label", None), t1),
            (getattr(owner, "_v181_tier2_label", None), t2),
        ):
            if label is not None:
                side = str((item or {}).get("direction") or "").lower()
                label.configure(
                    fg=visual.GREEN if side == "long"
                    else visual.RED if side == "short"
                    else visual.TEXT
                )

        active_items = [
            item for item in (t1, t2)
            if isinstance(item, dict) and v181._active_status(item)
        ]
        if active_items:
            item = next(
                (x for x in active_items if x.get("filled_size")),
                active_items[0],
            )
            state = str(
                item.get("order_state") or item.get("status") or "—"
            ).upper()
            owner._v180_order_state_var.set(state)
            owner._v180_order_type_var.set("限价")
            owner._v180_order_px_var.set(
                v180._fmt_px(item.get("limit_price") or item.get("requested_entry"))
            )
            owner._v180_fill_px_var.set(v180._fmt_px(item.get("entry_price")))
            remaining = sum(
                max(
                    0.0,
                    float(x.get("filled_size") or 0)
                    - float(x.get("closed_size") or 0),
                )
                for x in active_items
            )
            owner._v180_size_var.set(f"{remaining:g} 张")
            owner._v180_position_tp_var.set(
                v180._fmt_px(item.get("take_profit"))
            )
            owner._v180_position_sl_var.set(
                v180._fmt_px(item.get("stop_loss"))
            )
            owner._v180_proposal_var.set(
                str(item.get("proposal_id") or "—")[:22]
            )
            owner._v180_position_badge_var.set("PAPER 持仓/委托")
            side = str(item.get("direction") or "").lower()
            if getattr(owner, "_v181_position_badge_label", None) is not None:
                owner._v181_position_badge_label.body.winfo_children()[0].configure(
                    fg=visual.GREEN if side == "long"
                    else visual.RED if side == "short"
                    else visual.TEXT
                )
        else:
            owner._v180_order_state_var.set("等待委托")
            owner._v180_order_type_var.set("限价")
            owner._v180_order_px_var.set("—")
            owner._v180_fill_px_var.set("—")
            owner._v180_size_var.set("—")
            owner._v180_position_tp_var.set("—")
            owner._v180_position_sl_var.set("—")
            owner._v180_proposal_var.set("—")
            owner._v180_position_badge_var.set("Paper 空仓")
            if getattr(owner, "_v181_position_badge_label", None) is not None:
                owner._v181_position_badge_label.body.winfo_children()[0].configure(
                    fg=visual.MUTED
                )
    except Exception:
        pass

    try:
        if owner.root.winfo_exists():
            owner.root.after(650, lambda: _refresh_v182_dashboard(owner))
    except Exception:
        pass


def _rewrite_v182_text(data):
    if not isinstance(data, str):
        return data
    text = (
        data.replace("V1.7.2", VERSION)
        .replace("V1.8.0", VERSION)
        .replace("V1.8.1", VERSION)
        .replace("Build1723", f"Build{BUILD}")
        .replace("Build1801", f"Build{BUILD}")
        .replace("Build1813", f"Build{BUILD}")
    )
    if text.startswith("全自动运行 / "):
        return text.replace("全自动运行 / ", "Paper运行 / ", 1)
    if text.startswith("AI Only运行 / "):
        return text.replace("AI Only运行 / ", "Paper运行 / ", 1)
    return text


def _app_emit_v182(self, kind, data):
    return _PREVIOUS_APP_EMIT(self, kind, _rewrite_v182_text(data))


def _update_trade_button_v182(self):
    button = getattr(self, "trade_button", None)
    if not button:
        return
    running = bool(self.engine and self.engine.enabled)
    button.configure(
        text="■  停止 Paper AI通道" if running else "▶  启用 Paper AI通道",
        variant="danger" if running else "accent",
    )


def _app_init_v182(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    self.root.title("KAYTRADE 1.8.2 · PAPER AI · Build 1820")
    try:
        self.signal.set(
            "V1.8.2 PAPER｜LIMIT ONLY｜AI方案自动限价挂单｜TP/SL限价保护｜双档 / 改单 / 撤单 / 60分钟自动撤单"
        )
    except Exception:
        pass
    label = getattr(self, "_v170_version_label", None)
    if label is not None:
        try:
            label.configure(text="BTC / USDT   ·   V1.8.2 PAPER · LIMIT ONLY")
        except Exception:
            pass

    _install_v182_overview(self)
    self._v182_ui_ready = True
    self.update_trade_button()


def apply():
    if getattr(model, "_kaytrade_v182_ui_applied", False):
        return

    for module in (
        model,
        runtime,
        v168,
        b1681,
        v170,
        v1701,
        v171,
        v172,
        v180,
        v181,
    ):
        module.VERSION = VERSION
        module.BUILD = BUILD

    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.8.2 · PAPER AI · Build 1820"

    # Prevent the hidden V1.8 card from starting its own refresh loop.
    v180._refresh_v180_dashboard = lambda _owner: None
    # The V1.8.1 scheduled callback resolves this symbol at runtime.
    v181._refresh_v181_dashboard = _refresh_v182_dashboard

    app.App.__init__ = _app_init_v182
    app.App.emit = _app_emit_v182
    app.App.update_trade_button = _update_trade_button_v182

    model._kaytrade_v182_ui_applied = True
    app.App._kaytrade_v182_ui_applied = True


apply()

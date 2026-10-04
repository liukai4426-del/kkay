"""KAYTRADE V1.8.0 AI Only overlay.

V1.8 keeps the AI-only execution architecture from V1.7.2 and adds:
- market and limit entry orders;
- persistent pending-limit reconciliation;
- a hierarchy-first KAYTRADE AI plan board;
- a KAYTRADE card-style order/position execution board.

Autonomous AI writes remain restricted to OKX Demo Trading.
"""
from __future__ import annotations

import time
from decimal import Decimal

from v172_ai_only_patch import apply as apply_previous
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
import visual
from core import INSTRUMENT
from exchange import APIError

VERSION = "1.8.0"
BUILD = "1801"
AI_ONLY = True

AI_MAX_LEVERAGE = 20
AI_MAX_NOTIONAL_USDT = Decimal("3500")
AI_MAX_ESTIMATED_STOP_LOSS_USDT = Decimal("100")


def _fmt_px(value):
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "—"


def _fmt_num(value, digits=4):
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return "—"


def _order_type_cn(value):
    return "限价" if str(value or "").lower() == "limit" else "市价"


def _side_cn(value):
    value = str(value or "").lower()
    if value in ("long", "做多"):
        return "做多 / LONG"
    if value in ("short", "做空"):
        return "做空 / SHORT"
    return "等待方向"


class AIOnlyEngineV180(v172.AIOnlyEngine):
    """V1.8 execution engine: AI-only, market/limit entries, demo-only writes."""

    def connect(self):
        super().connect()
        self.emit(
            "log",
            "V1.8 AI Only：新仓支持市价/限价委托；本地指标策略保持停用。",
        )

    def ai_state(self):
        state = super().ai_state()
        state["version"] = VERSION
        state["build"] = BUILD
        state["supported_entry_order_types"] = ["market", "limit"]
        state["limits"] = {
            "max_leverage": AI_MAX_LEVERAGE,
            "max_notional_usdt": str(AI_MAX_NOTIONAL_USDT),
            "max_estimated_stop_loss_usdt": str(AI_MAX_ESTIMATED_STOP_LOSS_USDT),
        }
        active = self.store.data.get("active") if self.store else None
        if isinstance(active, dict):
            state["active"] = dict(state.get("active") or {})
            for key in (
                "order_type",
                "limit_price",
                "requested_entry",
                "entry_px",
                "tier",
                "order_state",
                "operation_advice",
                "protected",
            ):
                if key in active:
                    state["active"][key] = active.get(key)
        if self.store:
            state["ai_tiers"] = self.store.data.get("ai_tiers") or {}
            state["recent_ai_plans"] = (self.store.data.get("ai_plan_history") or [])[-8:]
        return state

    def _record_ai_plan(self, proposal, proposal_id=None, status="RECOMMENDED", detail=""):
        item = super()._record_ai_plan(proposal, proposal_id=proposal_id, status=status, detail=detail)
        if not item:
            return item
        item["order_type"] = str(proposal.get("order_type") or "market").lower()
        item["limit_price"] = proposal.get("limit_price")
        if item["order_type"] == "limit" and item.get("suggested_entry") in (None, ""):
            item["suggested_entry"] = item.get("limit_price")
        tiers = self.store.data.setdefault("ai_tiers", {})
        tiers[str(item["tier"])] = item
        history = self.store.data.get("ai_plan_history") or []
        if history:
            history[-1] = item
        self.store.save()
        return item

    def _validate_open(self, proposal):
        if self.x.positions() or self.x.orders() or self.x.algos():
            raise legacy_engine.Halt("已有BTC仓位或挂单；V1.8 AI Only 单仓模式拒绝新开仓")

        direction = str(proposal.get("direction") or "").lower()
        if direction not in ("long", "short"):
            raise legacy_engine.Halt("direction 必须为 long 或 short")

        order_type = str(proposal.get("order_type") or "market").lower()
        if order_type not in ("market", "limit"):
            raise legacy_engine.Halt("order_type 必须为 market 或 limit")

        qty = v172._decimal(proposal.get("size"), "size")
        leverage = int(proposal.get("leverage", AI_MAX_LEVERAGE))
        if not 1 <= leverage <= AI_MAX_LEVERAGE:
            raise legacy_engine.Halt(f"leverage 必须在 1—{AI_MAX_LEVERAGE}")

        ticker = self.x.ticker()
        last = v172._decimal(ticker.get("last"), "OKX last")
        meta = self.x.instrument()
        tick = v172._decimal(meta.get("tickSz"), "tickSz")

        limit_price = None
        if order_type == "limit":
            limit_price = v172._decimal(proposal.get("limit_price"), "limit_price")
            if limit_price % tick != 0:
                raise legacy_engine.Halt(f"limit_price 必须按 tickSz={tick} 递增")
            entry_ref = limit_price
        else:
            entry_ref = last

        tp = v172._decimal(proposal.get("take_profit"), "take_profit")
        sl = v172._decimal(proposal.get("stop_loss"), "stop_loss")

        if direction == "long" and not (sl < entry_ref < tp):
            raise legacy_engine.Halt("LONG 必须满足 stop_loss < 参考入场价 < take_profit")
        if direction == "short" and not (tp < entry_ref < sl):
            raise legacy_engine.Halt("SHORT 必须满足 take_profit < 参考入场价 < stop_loss")

        minimum = v172._decimal(meta.get("minSz"), "minSz")
        lot = v172._decimal(meta.get("lotSz"), "lotSz")
        if qty < minimum or qty % lot != 0:
            raise legacy_engine.Halt(f"size 必须≥{minimum} 且按 lotSz={lot} 递增")

        unit = v172._decimal(meta.get("ctVal"), "ctVal") * v172._decimal(
            meta.get("ctMult") or "1", "ctMult"
        )
        notional = qty * unit * entry_ref
        if notional > AI_MAX_NOTIONAL_USDT:
            raise legacy_engine.Halt(
                f"名义仓位 {notional:.4f} USDT 超过 AI Only 上限 {AI_MAX_NOTIONAL_USDT} USDT"
            )

        equity, available = self.x.balance()
        account_cap = Decimal(str(available)) * Decimal(leverage) * Decimal("0.90")
        if notional > account_cap:
            raise legacy_engine.Halt("可用保证金不足以覆盖该AI交易请求")

        estimated_loss = qty * unit * abs(entry_ref - sl) + notional * Decimal("0.0012")
        if estimated_loss > AI_MAX_ESTIMATED_STOP_LOSS_USDT:
            raise legacy_engine.Halt(
                f"估算止损损失 {estimated_loss:.4f} USDT 超过 AI Only 上限 "
                f"{AI_MAX_ESTIMATED_STOP_LOSS_USDT} USDT"
            )

        return {
            "direction": direction,
            "order_type": order_type,
            "limit_price": limit_price,
            "qty": qty,
            "leverage": leverage,
            "last": last,
            "entry_ref": entry_ref,
            "tp": tp,
            "sl": sl,
            "meta": meta,
            "equity": equity,
            "notional": notional,
            "estimated_loss": estimated_loss,
            "unit": unit,
        }

    def _submit_open(self, proposal, proposal_id):
        validated = self._validate_open(proposal)
        direction = validated["direction"]
        pos_side = direction
        exchange_side = "buy" if direction == "long" else "sell"
        qty = validated["qty"]
        tp = validated["tp"]
        sl = validated["sl"]
        last = validated["last"]
        entry_ref = validated["entry_ref"]
        order_type = validated["order_type"]
        limit_price = validated["limit_price"]
        leverage = validated["leverage"]

        self._set_leverage(pos_side, leverage)

        cid = v172._client_id("ai")
        tp_id = v172._client_id("tp")
        sl_id = v172._client_id("sl")
        reason = str(proposal.get("reason") or "").strip()[:1000]
        tier = int(proposal.get("tier") or 1)
        operation_advice = str(
            proposal.get("operation_advice") or proposal.get("advice") or ""
        ).strip()[:1000]

        active = {
            "ai_only": True,
            "version": VERSION,
            "proposal_id": proposal_id,
            "reason": reason,
            "operation_advice": operation_advice,
            "tier": tier,
            "side": "做多" if direction == "long" else "做空",
            "posSide": pos_side,
            "exchange_side": exchange_side,
            "order_type": order_type,
            "limit_price": format(limit_price, "f") if limit_price is not None else "",
            "requested_entry": format(entry_ref, "f"),
            "px": format(entry_ref, "f"),
            "market_reference_px": format(last, "f"),
            "sz": format(qty, "f"),
            "tp": format(tp, "f"),
            "tp1": format(tp, "f"),
            "tp2": format(tp, "f"),
            "sl": format(sl, "f"),
            "tp_id": tp_id,
            "sl_id": sl_id,
            "client_id": cid,
            "submitted": time.time(),
            "equity_before": validated["equity"],
            "filled": False,
            "close_id": "",
            "notional": float(validated["notional"]),
            "estimated_loss": float(validated["estimated_loss"]),
            "btc": float(qty * validated["unit"]),
            "position_multiplier": 1.0,
            "leverage": leverage,
            "order_state": "submitting",
        }

        self.store.data["active"] = active
        self.store.save()

        body = {
            "instId": INSTRUMENT,
            "tdMode": "isolated",
            "side": exchange_side,
            "posSide": pos_side,
            "ordType": order_type,
            "sz": format(qty, "f"),
            "clOrdId": cid,
            "attachAlgoOrds": [
                {
                    "attachAlgoClOrdId": tp_id,
                    "tpTriggerPx": format(tp, "f"),
                    "tpOrdPx": "-1",
                    "tpTriggerPxType": "last",
                },
                {
                    "attachAlgoClOrdId": sl_id,
                    "slTriggerPx": format(sl, "f"),
                    "slOrdPx": "-1",
                    "slTriggerPxType": "last",
                },
            ],
        }
        if order_type == "limit":
            body["px"] = format(limit_price, "f")

        try:
            reply = self.x.post("/api/v5/trade/order", body)
        except APIError as exc:
            if getattr(exc, "write_rejected", False):
                self.store.data["active"] = None
                self.store.save()
            raise

        row = reply[0] if reply else {}
        order_id = str(row.get("ordId") or "") if isinstance(row, dict) else ""
        if not order_id:
            raise legacy_engine.Halt("OKX下单响应缺少ordId；本地已锁住该请求，禁止重复提交")
        returned_client = str(row.get("clOrdId") or "") if isinstance(row, dict) else ""
        if returned_client and returned_client != cid:
            raise legacy_engine.Halt("OKX返回clOrdId与AI请求不一致；已停止重复提交")

        active["order_id"] = order_id
        active["order_state"] = "submitted"
        active["okx_ack_at"] = time.time()
        self.store.save()
        self.store.record("V1.8 AI提交开仓请求", active)
        self.emit(
            "log",
            f"AI交易请求已提交OKX模拟盘：{active['side']} · {_order_type_cn(order_type)} · "
            f"{active['sz']}张 · 入场 {active['requested_entry']} · TP {active['tp']} · "
            f"SL {active['sl']} · proposal={proposal_id}",
        )
        self.emit("plan", active)
        return {
            "version": VERSION,
            "build": BUILD,
            "mode": "AI_ONLY",
            "environment": "OKX_DEMO",
            "status": "EXECUTED",
            "proposal_id": proposal_id,
            "order_id": order_id,
            "client_order_id": cid,
            "direction": direction,
            "order_type": order_type,
            "limit_price": active["limit_price"] or None,
            "size": active["sz"],
            "reference_price": active["requested_entry"],
            "take_profit": active["tp"],
            "stop_loss": active["sl"],
            "notional_usdt": active["notional"],
            "estimated_stop_loss_usdt": active["estimated_loss"],
        }

    def reconcile(self):
        """Allow valid limit orders to remain pending without a 15-second market timeout."""
        with self._ai_lock:
            if not self.store:
                return
            active = self.store.data.get("active")
            if not isinstance(active, dict) or not active.get("ai_only"):
                return super().reconcile()

            order = self._lookup_parent_order(active)
            if order is None:
                return

            status = str(order.get("state") or "")
            active["order_state"] = status or active.get("order_state") or "unknown"
            avg_px = str(order.get("avgPx") or order.get("fillPx") or "").strip()
            filled = float(order.get("accFillSz") or 0)
            if filled > 0:
                active["filled_sz"] = filled
                if avg_px and avg_px not in ("0", "0.0"):
                    active["entry_px"] = avg_px
            self.store.save()

            if active.get("order_type") == "limit" and status in ("live", "partially_filled"):
                positions = self.x.positions()
                if any(
                    p.get("mgnMode") != "isolated"
                    or p.get("posSide") != active["posSide"]
                    or abs(float(p.get("pos") or 0)) > float(active["sz"]) + 1e-10
                    for p in positions
                ):
                    raise legacy_engine.Halt("V1.8限价委托对应仓位与本地记录不一致，请到OKX核对")
                if status == "partially_filled":
                    self.emit(
                        "log",
                        f"限价委托部分成交：{filled:g}/{float(active['sz']):g}张；剩余委托继续挂单。",
                    )
                return

        return super().reconcile()


_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_EMIT = app.App.emit
_PREVIOUS_APP_ARM = app.App.arm


def _rewrite_v180_text(data):
    if not isinstance(data, str):
        return data
    return (
        data.replace("V1.7.2", "V1.8.0")
        .replace("Build1723", "Build1801")
        .replace("Build1722", "Build1801")
    )


def _app_emit_v180(self, kind, data):
    return _PREVIOUS_APP_EMIT(self, kind, _rewrite_v180_text(data))


def _mini_badge(parent, variable, fg=visual.TEXT, bg=visual.PANEL_ALT, width=118):
    frame = app.tk.Frame(parent, bg=bg, height=30, width=width, bd=0, highlightthickness=0)
    frame.pack_propagate(False)
    app.tk.Label(
        frame,
        textvariable=variable,
        bg=bg,
        fg=fg,
        font=("Helvetica", 10, "bold"),
        anchor="center",
        bd=0,
    ).pack(fill="both", expand=True)
    return frame


def _plan_tile(parent, title, variable, accent=visual.TEXT, height=78):
    tile = app.tk.Frame(parent, bg=visual.PANEL_ALT, bd=0, highlightthickness=0, height=height)
    tile.pack_propagate(False)
    visual.label(
        tile,
        text=title,
        color=visual.MUTED,
        size=9,
        bg=visual.PANEL_ALT,
    ).pack(anchor="w", padx=14, pady=(11, 0))
    visual.label(
        tile,
        variable=variable,
        color=accent,
        size=16,
        bold=True,
        bg=visual.PANEL_ALT,
    ).pack(anchor="w", padx=14, pady=(5, 8))
    return tile


def _hide_v172_cards(owner):
    for name in ("_v172_ai_card", "_v172_ai_plan_card", "_v172_position_card"):
        widget = getattr(owner, name, None)
        if widget is not None:
            try:
                widget.pack_forget()
            except Exception:
                pass


def _install_v180_dashboard(owner):
    try:
        dash = owner.book.pages[3].body
    except Exception:
        return

    _hide_v172_cards(owner)
    if getattr(owner, "_v180_plan_card", None) is not None:
        return

    quote = getattr(owner, "quote", None)
    actions = getattr(getattr(owner, "trade_button", None), "master", None)

    # ------------------------------------------------------------------
    # AI plan board: asymmetric hierarchy, not a wall of identical tiles.
    # ------------------------------------------------------------------
    plan = visual.Card(dash, height=455)
    plan_kwargs = dict(fill="x", pady=(0, 12))
    if quote is not None:
        plan_kwargs["before"] = quote
    plan.pack(**plan_kwargs)

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
        text="主方案突出方向与入场 · AI推荐理由 / 操作建议 / 最近方案分层展示",
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(3, 0))

    owner._v180_status_chip_var = app.tk.StringVar(value="WAITING")
    owner._v180_mode_var = app.tk.StringVar(value="AI ONLY · DEMO")
    _mini_badge(header, owner._v180_status_chip_var, fg=visual.TEXT, bg=visual.PANEL_ALT, width=108).pack(
        side="right", padx=(8, 0)
    )
    _mini_badge(header, owner._v180_mode_var, fg=visual.GREEN, bg="#143229", width=126).pack(
        side="right"
    )

    hero = app.tk.Frame(plan.body, bg=visual.PANEL)
    hero.pack(fill="x", pady=(12, 10))

    # Main recommendation hero: large direction and entry, visually dominant.
    main = app.tk.Frame(hero, bg=visual.PANEL_ALT, height=150, bd=0, highlightthickness=0)
    main.pack(side="left", fill="both", expand=True, padx=(0, 7))
    main.pack_propagate(False)
    owner._v180_side_var = app.tk.StringVar(value="等待 AI 方向")
    owner._v180_entry_var = app.tk.StringVar(value="—")
    owner._v180_type_var = app.tk.StringVar(value="委托：—")
    owner._v180_tier_var = app.tk.StringVar(value="档位：—")
    owner._v180_leverage_var = app.tk.StringVar(value="杠杆：—")
    visual.label(main, text="当前主方案", size=9, color=visual.MUTED, bg=visual.PANEL_ALT).pack(
        anchor="w", padx=16, pady=(14, 0)
    )
    visual.label(main, variable=owner._v180_side_var, size=23, bold=True, color=visual.TEXT, bg=visual.PANEL_ALT).pack(
        anchor="w", padx=16, pady=(6, 0)
    )
    price_row = app.tk.Frame(main, bg=visual.PANEL_ALT)
    price_row.pack(fill="x", padx=16, pady=(5, 0))
    visual.label(price_row, text="建议 / 委托入场", size=9, color=visual.MUTED, bg=visual.PANEL_ALT).pack(
        side="left", anchor="s"
    )
    visual.label(price_row, variable=owner._v180_entry_var, size=28, bold=True, color=visual.TEXT, bg=visual.PANEL_ALT).pack(
        side="left", padx=(12, 0), anchor="s"
    )
    meta = app.tk.Frame(main, bg=visual.PANEL_ALT)
    meta.pack(fill="x", padx=16, pady=(7, 0))
    for var in (owner._v180_type_var, owner._v180_tier_var, owner._v180_leverage_var):
        _mini_badge(meta, var, bg="#1b2a32", width=124).pack(side="left", padx=(0, 6))

    # TP/SL use two tall, different-purpose blocks.
    risk = app.tk.Frame(hero, bg=visual.PANEL, width=400)
    risk.pack(side="right", fill="y")
    owner._v180_tp_var = app.tk.StringVar(value="—")
    owner._v180_sl_var = app.tk.StringVar(value="—")
    tp_box = app.tk.Frame(risk, bg="#123028", height=70, bd=0, highlightthickness=0)
    tp_box.pack(fill="x", pady=(0, 6))
    tp_box.pack_propagate(False)
    visual.label(tp_box, text="止盈 TP", size=9, color="#8fbcae", bg="#123028").pack(anchor="w", padx=14, pady=(10, 0))
    visual.label(tp_box, variable=owner._v180_tp_var, size=20, bold=True, color=visual.GREEN, bg="#123028").pack(anchor="w", padx=14, pady=(3, 6))
    sl_box = app.tk.Frame(risk, bg="#321a22", height=70, bd=0, highlightthickness=0)
    sl_box.pack(fill="x")
    sl_box.pack_propagate(False)
    visual.label(sl_box, text="止损 SL", size=9, color="#c39aa5", bg="#321a22").pack(anchor="w", padx=14, pady=(10, 0))
    visual.label(sl_box, variable=owner._v180_sl_var, size=20, bold=True, color=visual.RED, bg="#321a22").pack(anchor="w", padx=14, pady=(3, 6))

    # Reason and action advice intentionally use different visual weights.
    owner._v180_reason_var = app.tk.StringVar(value="等待 AI 推荐理由…")
    owner._v180_advice_var = app.tk.StringVar(value="等待 AI 运行操作建议…")
    text_row = app.tk.Frame(plan.body, bg=visual.PANEL)
    text_row.pack(fill="x", pady=(0, 9))

    reason_box = app.tk.Frame(text_row, bg=visual.PANEL_ALT)
    reason_box.pack(side="left", fill="both", expand=True, padx=(0, 6))
    visual.label(reason_box, text="AI 推荐理由", size=10, bold=True, color=visual.MUTED, bg=visual.PANEL_ALT).pack(
        anchor="w", padx=14, pady=(10, 4)
    )
    app.tk.Label(
        reason_box,
        textvariable=owner._v180_reason_var,
        bg=visual.PANEL_ALT,
        fg=visual.TEXT,
        font=("Helvetica", 10),
        justify="left",
        anchor="nw",
        wraplength=720,
    ).pack(fill="x", padx=14, pady=(0, 10))

    advice_box = app.tk.Frame(text_row, bg="#10271f", width=480)
    advice_box.pack(side="right", fill="both", padx=(6, 0))
    visual.label(advice_box, text="运行操作建议", size=10, bold=True, color=visual.GREEN, bg="#10271f").pack(
        anchor="w", padx=14, pady=(10, 4)
    )
    app.tk.Label(
        advice_box,
        textvariable=owner._v180_advice_var,
        bg="#10271f",
        fg=visual.TEXT,
        font=("Helvetica", 10, "bold"),
        justify="left",
        anchor="nw",
        wraplength=440,
    ).pack(fill="x", padx=14, pady=(0, 10))

    owner._v180_history_var = app.tk.StringVar(value="暂无 AI 方案记录")
    history = app.tk.Frame(plan.body, bg=visual.PANEL_ALT)
    history.pack(fill="x")
    visual.label(history, text="最近方案", size=9, bold=True, color=visual.MUTED, bg=visual.PANEL_ALT).pack(
        anchor="w", padx=14, pady=(8, 2)
    )
    app.tk.Label(
        history,
        textvariable=owner._v180_history_var,
        bg=visual.PANEL_ALT,
        fg="#c9d3d8",
        font=("Menlo", 9),
        justify="left",
        anchor="nw",
        wraplength=1400,
    ).pack(fill="x", padx=14, pady=(0, 8))
    owner._v180_plan_card = plan

    # ------------------------------------------------------------------
    # Order / position execution: KAYTRADE's previous "交易计划" card language.
    # ------------------------------------------------------------------
    execution = visual.Card(dash, height=470)
    exec_kwargs = dict(fill="x", pady=(0, 12))
    if actions is not None:
        exec_kwargs["before"] = actions
    execution.pack(**exec_kwargs)

    exec_header = app.tk.Frame(execution.body, bg=visual.PANEL)
    exec_header.pack(fill="x")
    exec_title = app.tk.Frame(exec_header, bg=visual.PANEL)
    exec_title.pack(side="left", fill="x", expand=True)
    visual.label(exec_title, text="订单 / 持仓执行", size=19, bold=True, color=visual.TEXT, bg=visual.PANEL).pack(anchor="w")
    visual.label(
        exec_title,
        text="委托状态与持仓保护统一展示 · 市价 / 限价订单均实时同步",
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(3, 0))
    owner._v180_position_badge_var = app.tk.StringVar(value="无持仓")
    _mini_badge(exec_header, owner._v180_position_badge_var, fg=visual.TEXT, bg=visual.PANEL_ALT, width=120).pack(side="right")

    owner._v180_order_state_var = app.tk.StringVar(value="等待委托")
    owner._v180_order_type_var = app.tk.StringVar(value="—")
    owner._v180_order_px_var = app.tk.StringVar(value="—")
    owner._v180_fill_px_var = app.tk.StringVar(value="—")
    owner._v180_size_var = app.tk.StringVar(value="—")
    owner._v180_position_tp_var = app.tk.StringVar(value="—")
    owner._v180_position_sl_var = app.tk.StringVar(value="—")
    owner._v180_proposal_var = app.tk.StringVar(value="—")

    summary = app.tk.Frame(execution.body, bg=visual.PANEL)
    summary.pack(fill="x", pady=(12, 8))
    state_tile = _plan_tile(summary, "订单状态", owner._v180_order_state_var, height=82)
    state_tile.pack(side="left", fill="x", expand=True, padx=(0, 5))
    type_tile = _plan_tile(summary, "委托方式", owner._v180_order_type_var, height=82)
    type_tile.pack(side="left", fill="x", expand=True, padx=(5, 0))

    grid = app.tk.Frame(execution.body, bg=visual.PANEL)
    grid.pack(fill="x", pady=(0, 10))
    items = (
        ("委托入场", owner._v180_order_px_var, visual.TEXT),
        ("成交均价", owner._v180_fill_px_var, visual.TEXT),
        ("止盈 TP", owner._v180_position_tp_var, visual.GREEN),
        ("止损 SL", owner._v180_position_sl_var, visual.RED),
        ("持仓 / 委托数量", owner._v180_size_var, visual.TEXT),
        ("Proposal ID", owner._v180_proposal_var, visual.MUTED),
    )
    for idx, (title, var, accent) in enumerate(items):
        tile = _plan_tile(grid, title, var, accent=accent, height=78)
        tile.grid(row=idx // 3, column=idx % 3, sticky="ew", padx=4, pady=4)
    for col in range(3):
        grid.columnconfigure(col, weight=1, uniform="v180execution")

    owner._v180_tier1_var = app.tk.StringVar(value="等待第一档 AI 计划")
    owner._v180_tier2_var = app.tk.StringVar(value="等待第二档 AI 计划")
    tiers = app.tk.Frame(execution.body, bg=visual.PANEL)
    tiers.pack(fill="both", expand=True)
    for idx, (title, var, accent) in enumerate(
        (
            ("第一档执行方案", owner._v180_tier1_var, visual.GREEN),
            ("第二档执行方案", owner._v180_tier2_var, visual.TEXT),
        )
    ):
        box = app.tk.Frame(tiers, bg=visual.PANEL_ALT, bd=0, highlightthickness=0)
        box.grid(row=0, column=idx, sticky="nsew", padx=(0, 6) if idx == 0 else (6, 0))
        visual.label(box, text=title, size=11, bold=True, color=accent, bg=visual.PANEL_ALT).pack(
            anchor="w", padx=14, pady=(10, 5)
        )
        app.tk.Label(
            box,
            textvariable=var,
            bg=visual.PANEL_ALT,
            fg=visual.TEXT,
            font=("Helvetica", 10),
            justify="left",
            anchor="nw",
            wraplength=650,
        ).pack(fill="both", expand=True, padx=14, pady=(0, 10))
        tiers.columnconfigure(idx, weight=1, uniform="v180tiers")
    owner._v180_execution_card = execution

    owner.root.after(350, lambda: _refresh_v180_dashboard(owner))


def _plan_summary(item):
    if not isinstance(item, dict):
        return "暂无计划"
    order_type = _order_type_cn(item.get("order_type"))
    entry = item.get("limit_price") if str(item.get("order_type")).lower() == "limit" else item.get("suggested_entry")
    return (
        f"{_side_cn(item.get('direction'))}  ·  {order_type}  ·  "
        f"入场 {_fmt_px(entry)}  ·  TP {_fmt_px(item.get('take_profit'))}  ·  "
        f"SL {_fmt_px(item.get('stop_loss'))}\n"
        f"{item.get('operation_advice') or '等待AI操作建议'}"
    )


def _refresh_v180_dashboard(owner):
    try:
        engine = getattr(owner, "engine", None)
        store = getattr(engine, "store", None) if engine is not None else None
        data = getattr(store, "data", {}) if store is not None else {}
        history = data.get("ai_plan_history") or []
        tiers = data.get("ai_tiers") or {}
        active = data.get("active") if isinstance(data.get("active"), dict) else {}
        latest = history[-1] if history else {}

        if latest:
            owner._v180_side_var.set(_side_cn(latest.get("direction")))
            owner._v180_type_var.set("委托：" + _order_type_cn(latest.get("order_type")))
            owner._v180_tier_var.set(f"档位：{latest.get('tier') or 1}")
            lev = latest.get("leverage")
            owner._v180_leverage_var.set(f"杠杆：{lev}×" if lev else "杠杆：—")
            entry = latest.get("limit_price") if str(latest.get("order_type")).lower() == "limit" else latest.get("suggested_entry")
            owner._v180_entry_var.set(_fmt_px(entry))
            owner._v180_tp_var.set(_fmt_px(latest.get("take_profit")))
            owner._v180_sl_var.set(_fmt_px(latest.get("stop_loss")))
            owner._v180_reason_var.set(str(latest.get("reason") or "—"))
            owner._v180_advice_var.set(str(latest.get("operation_advice") or "等待AI建议"))
            owner._v180_status_chip_var.set(str(latest.get("status") or "RECOMMENDED"))
        else:
            owner._v180_status_chip_var.set("WAITING")

        rows = []
        for item in history[-5:][::-1]:
            stamp = time.strftime("%H:%M:%S", time.localtime(float(item.get("time") or time.time())))
            direction = str(item.get("direction") or "WAIT").upper()
            kind = "LMT" if str(item.get("order_type")).lower() == "limit" else "MKT"
            entry = item.get("limit_price") if kind == "LMT" else item.get("suggested_entry")
            rows.append(
                f"{stamp}  T{item.get('tier') or 1}  {direction:<5} {kind}  "
                f"{_fmt_px(entry):>10}  {str(item.get('status') or '')[:12]}"
            )
        owner._v180_history_var.set("\n".join(rows) if rows else "暂无 AI 方案记录")

        owner._v180_tier1_var.set(_plan_summary(tiers.get("1")))
        owner._v180_tier2_var.set(_plan_summary(tiers.get("2")))

        if active:
            state = str(active.get("order_state") or ("filled" if active.get("filled") else "submitted"))
            owner._v180_order_state_var.set(state.upper())
            owner._v180_order_type_var.set(_order_type_cn(active.get("order_type")))
            requested = active.get("limit_price") or active.get("requested_entry") or active.get("px")
            owner._v180_order_px_var.set(_fmt_px(requested))
            owner._v180_fill_px_var.set(_fmt_px(active.get("entry_px")))
            owner._v180_size_var.set(str(active.get("filled_sz") or active.get("sz") or "—") + " 张")
            owner._v180_position_tp_var.set(_fmt_px(active.get("tp")))
            owner._v180_position_sl_var.set(_fmt_px(active.get("sl")))
            owner._v180_proposal_var.set(str(active.get("proposal_id") or "—")[:22])
            owner._v180_position_badge_var.set(
                "持仓中" if active.get("filled") else "挂单中" if active.get("order_state") in ("live", "partially_filled") else "已提交"
            )
        else:
            owner._v180_order_state_var.set("等待委托")
            owner._v180_order_type_var.set("—")
            owner._v180_order_px_var.set("—")
            owner._v180_fill_px_var.set("—")
            owner._v180_size_var.set("—")
            owner._v180_position_tp_var.set("—")
            owner._v180_position_sl_var.set("—")
            owner._v180_proposal_var.set("—")
            owner._v180_position_badge_var.set("无持仓")
    except Exception:
        pass

    try:
        if owner.root.winfo_exists():
            owner.root.after(650, lambda: _refresh_v180_dashboard(owner))
    except Exception:
        pass


def _app_init_v180(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    self.root.title("KAYTRADE 1.8.0 · AI ONLY · Build 1801")
    try:
        self.signal.set("V1.8 AI ONLY｜支持市价 / 限价委托｜等待 Codex / AI 交易方案")
    except Exception:
        pass
    label = getattr(self, "_v170_version_label", None)
    if label is not None:
        try:
            label.configure(text="BTC / USDT   ·   V1.8 AI ONLY")
        except Exception:
            pass
    _install_v180_dashboard(self)
    self._v180_ai_only_ready = True


def _app_arm_v180(self):
    if not self.engine:
        app.messagebox.showerror("未连接", "先测试连接")
        return
    if self.network_paused:
        app.messagebox.showerror("网络暂停", "等待网络恢复并核对账户后再启用AI通道")
        return
    if not self.engine.x.demo:
        app.messagebox.showerror(
            "AI实盘已禁用",
            "V1.8 AI Only 自动AI执行只允许 OKX模拟盘；真实账户不会接受AI写入。",
        )
        return
    typed = app.simpledialog.askstring(
        "启用AI交易通道",
        "KAYTRADE V1.8 AI ONLY\n\n"
        "AI开仓支持：市价委托 / 限价委托。\n"
        "限价委托可持续挂单等待成交，不使用市价单15秒超时规则。\n"
        f"硬上限：杠杆≤{AI_MAX_LEVERAGE}x；名义仓位≤{AI_MAX_NOTIONAL_USDT} USDT；"
        f"估算止损≤{AI_MAX_ESTIMATED_STOP_LOSS_USDT} USDT。\n"
        "本地BOLL / EMA / RSI / 评分 / Gate不参与开仓。\n\n"
        "确认启用请输入 AI",
        parent=self.root,
    )
    if typed and typed.strip().upper() == "AI":
        v172._start_ai_enable(self)


def apply():
    if getattr(model, "_kaytrade_v180_ai_only_applied", False):
        return

    # Dynamic globals used by the V1.7.2 bridge now advertise V1.8.
    v172.VERSION = VERSION
    v172.BUILD = BUILD
    v172.AI_MAX_LEVERAGE = AI_MAX_LEVERAGE
    v172.AI_MAX_NOTIONAL_USDT = AI_MAX_NOTIONAL_USDT
    v172.AI_MAX_ESTIMATED_STOP_LOSS_USDT = AI_MAX_ESTIMATED_STOP_LOSS_USDT

    for module in (model, runtime, v168, b1681, v170, v1701, v171):
        module.VERSION = VERSION
        module.BUILD = BUILD
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.8.0 · AI ONLY · Build 1801"

    model.STRATEGY_ENABLED = False
    model.AI_ONLY = True

    app.Engine = AIOnlyEngineV180
    legacy_engine.AIOnlyEngine = AIOnlyEngineV180

    app.App.__init__ = _app_init_v180
    app.App.emit = _app_emit_v180
    app.App.arm = _app_arm_v180

    model._kaytrade_v180_ai_only_applied = True
    app.App._kaytrade_v180_ai_only_applied = True


apply()

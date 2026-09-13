"""KAYTRADE V1.5.4 live runtime overlay.

Confirmed V1.5.4 changes only:
- hidden wheel/trackpad scrolling is coalesced and de-duplicated across the UI;
- the run log uses the same smooth scrolling and does not jump to bottom while
  the user is reading older lines;
- a 5m BOLL signal can open only in the immediately following first minute;
- opening orders are OKX MARKET orders, with taker/slippage-aware risk economics.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
import uuid
from dataclasses import asdict
from decimal import Decimal
from tkinter import ttk

from v153_runtime_patch import apply as apply_v153
apply_v153()

import app
import engine
import v136_entry_fix
import v136_runtime
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v152_strategy_patch as v152_runtime
import v153_backtest_core as v153_bt
import v153_runtime_patch as v153_runtime
import v154_model as model
import visual

VERSION = "1.5.4"
SCROLL_FLUSH_MS = 18


def _v154_make_plan(s, side, ticker, meta, atr, available, daily_remaining, position_multiplier=1.0):
    """Build the inherited 2R/1H-ATR plan using MARKET-entry economics."""
    s.validate()
    if side not in ("做多", "做空") or not math.isfinite(float(atr)) or float(atr) <= 0:
        raise engine.Halt("无效信号或ATR")
    multiplier = float(position_multiplier)
    if multiplier not in (1.0, 1.5, 2.0):
        raise engine.Halt("评分仓位倍率必须是1 / 1.5 / 2")

    buy = side == "做多"
    direction = 1 if buy else -1
    ask, bid = float(ticker["askPx"]), float(ticker["bidPx"])
    if not 0 < bid <= ask or (ask - bid) / bid > float(s.slippage_bps) / 10000:
        raise engine.Halt("买卖价差过大")

    # A market buy is budgeted from ask; a market sell is budgeted from bid.
    entry = float(engine.rounded(ask if buy else bid, meta["tickSz"], buy))
    dist = float(atr) * float(s.stop_atr)
    sl = engine.rounded(entry - direction * dist, meta["tickSz"], not buy)
    tp = engine.rounded(entry + direction * dist * 2.0, meta["tickSz"], buy)
    if not (float(sl) < entry < float(tp) if buy else float(tp) < entry < float(sl)):
        raise engine.Halt("止盈止损价格非法")

    unit = float(meta["ctVal"]) * float(meta.get("ctMult") or 1)
    if unit <= 0 or float(meta["minSz"]) <= 0 or float(meta["lotSz"]) <= 0:
        raise engine.Halt("合约单位异常")

    taker = float(s.taker_fee_bps) / 10000.0
    slip = float(s.slippage_bps) / 10000.0
    # MARKET entry and MARKET-on-trigger exit both reserve taker fee + slippage.
    per_btc = abs(entry - float(sl)) + entry * (taker + slip) + float(sl) * (taker + slip)

    account_equity = v136_runtime._live_equity_for(available)
    risk_capital = min(float(s.capital), float(account_equity))
    base_risk = min(float(s.risk_usdt), risk_capital * float(s.risk_pct) / 100.0)
    risk = min(base_risk * multiplier, float(daily_remaining))
    notional_cap = min(float(s.max_notional), risk_capital * float(s.leverage), float(available) * .9 * float(s.leverage))
    quantity = engine.rounded(min(risk / per_btc, notional_cap / entry) / unit, meta["lotSz"])
    if Decimal(quantity) < Decimal(str(meta["minSz"])):
        raise engine.Halt("风险预算不足以满足最小下单量，跳过")
    btc = float(quantity) * unit
    if btc * per_btc > risk + 1e-9 or btc * entry > notional_cap + 1e-9:
        raise engine.Halt("取整后风险超限")

    expected_cost_per_btc = entry * taker + float(tp) * taker
    worst_cost_per_btc = entry * (taker + slip) + float(tp) * (taker + slip)
    reward_distance = dist * 2.0
    expected_multiple = reward_distance / expected_cost_per_btc if expected_cost_per_btc > 0 else math.inf
    worst_multiple = reward_distance / worst_cost_per_btc if worst_cost_per_btc > 0 else math.inf
    mapped = v136_entry_fix._engine_gate_multiple(expected_multiple)

    entry_fee = btc * entry * taker
    exit_fee = btc * float(tp) * taker
    gross = btc * reward_distance
    expected_cost = entry_fee + exit_fee
    worst_cost = btc * worst_cost_per_btc
    expected_net = gross - expected_cost
    worst_net = gross - worst_cost

    ctx = dict(v137._CURRENT_CONTEXT)
    plan = dict(
        side=side, posSide="long" if buy else "short", exchange_side="buy" if buy else "sell",
        px=str(entry), sz=quantity, sl=sl, tp=tp, tp1=tp, tp2=tp, tp1_sz=quantity, tp2_sz="0",
        btc=btc, notional=btc * entry, estimated_loss=btc * per_btc, position_multiplier=multiplier,
        base_risk_budget=base_risk, effective_risk_budget=risk,
        account_equity=float(account_equity), available_balance=float(available), strategy_capital_cap=float(s.capital),
        risk_capital=risk_capital, atr_15m=float(atr), stop_distance=dist,
        expected_roundtrip_cost=expected_cost, worst_roundtrip_cost=worst_cost,
        expected_fee_multiple=expected_multiple, worst_cost_multiple=worst_multiple,
        cost_multiple=mapped, legacy_engine_cost_multiple=mapped, expected_cost_min_multiple=v138.EXPECTED_COST_MIN,
        tp_full_gross=gross, tp_full_net=expected_net, expected_net_profit=expected_net, worst_net_profit=worst_net,
        reward_r=2.0, breakeven_after_tp1=False, entry_fee_budget_bps=float(s.taker_fee_bps),
        entry_slippage_budget_bps=float(s.slippage_bps), entry_order_type="market", market_entry=True,
        v137=True, signal_bar=ctx.get("bar"), signal_tier=ctx.get("tier"), signal_level=ctx.get("level"),
    )
    v136_runtime._LAST_PLAN_DIAG = dict(plan)
    v136_entry_fix._LAST_PLAN = plan
    return plan


def _inside_first_minute(self, market):
    opp = (market or {}).get("opportunity") or {}
    try:
        start = int(opp.get("signal_close_ms") or 0)
        now_ms = int(float(self.market_now()) * 1000.0)
    except Exception:
        return False
    return start > 0 and start <= now_ms < start + model.ENTRY_WINDOW_MS


def _submit_initial_market(self, market, score, equity, available, remaining):
    """Safety-preserving copy of the inherited initial submit path using ordType=market."""
    if not _inside_first_minute(self, market):
        self.emit("log", "V1.5.4 BOLL首分钟执行窗口已结束；本信号不再开仓")
        return
    prepared = v138._prepare_order(self, market["side"], score, market, equity, available, remaining)
    if not prepared:
        return
    plan, tier, multiplier, level = prepared
    if not v138._set_leverage(self, plan) or not self.enabled:
        return
    # Re-check the wall-clock window after preparation/network reads and before the write.
    if not _inside_first_minute(self, market):
        self.emit("log", "V1.5.4 开仓准备完成时已超过BOLL首分钟窗口；未提交市价订单")
        return
    self._verify_latest("1m", market["bar"], v138.ONE_MINUTE_STEP)

    cid = "mac" + uuid.uuid4().hex[:28]
    algo = "br" + uuid.uuid4().hex[:28]
    state = self.store.data
    before_last = state.get("last_bar")
    leg = dict(
        plan, client_id=cid, order_id="", bracket_id=algo, submitted=time.time(), expires=time.time()+60,
        filled=False, state="pending", cancel_requested=False, cancel_on_reconcile=False,
        tier=tier, score=float(score["total"]), signal_bar=market["bar"], counted=True, protected=False,
        v154=True, version=VERSION, order_type="market",
    )
    root = dict(
        plan, client_id=cid, order_id="", bracket_id=algo, submitted=leg["submitted"], expires=leg["expires"],
        equity_before=equity, filled=False, protected=False, score=float(score["total"]), signal_bar=market["bar"],
        signal_tier=tier, signal_level=level, v137=True, v138=True, v152=True, v153=True, v154=True,
        version=VERSION, legs=[leg], tier_counts={"1":0,"2":0,"3":0}, highest_tier=tier,
        last_entry_bar=market["bar"], order_type="market",
    )
    root["tier_counts"][str(tier)] = 1
    state["last_bar"] = market["bar"]
    state["active"] = root
    self.store.save()

    body = {
        "instId": engine.INSTRUMENT,
        "tdMode": "isolated",
        "side": plan["exchange_side"],
        "posSide": plan["posSide"],
        "ordType": "market",
        "sz": plan["sz"],
        "clOrdId": cid,
        "attachAlgoOrds": [{
            "attachAlgoClOrdId": algo,
            "tpOrdKind": "condition",
            "tpTriggerPx": plan["tp"],
            "tpOrdPx": "-1",
            "tpTriggerPxType": "last",
            "slTriggerPx": plan["sl"],
            "slOrdPx": "-1",
            "slTriggerPxType": "last",
        }],
    }
    try:
        reply = self.x.post("/api/v5/trade/order", body)
    except Exception as exc:
        if getattr(exc, "write_rejected", False):
            state["active"] = None
            state["last_bar"] = before_last
            self.store.save()
            self.enabled = False
            self.stopped = True
            self.emit("log", f"OKX明确拒绝V1.5.4市价开仓：{exc}；未产生新订单，已释放占位；自动新开仓已停止")
            return
        # Ambiguous write: preserve the local occupancy and let the inherited
        # fault-lock/reconciliation path verify the exchange before any retry.
        raise

    row = reply[0] if reply else {}
    oid = str(row.get("ordId") or "") if isinstance(row, dict) else ""
    if not oid:
        raise engine.Halt("V1.5.4市价开仓响应缺少ordId；本地占位已保留，禁止重复提交")
    leg["order_id"] = oid
    root["order_id"] = oid
    root["okx_ack_at"] = time.time()
    self.store.save()
    self.store.record("V1.5.4提交市价开仓", {
        "tier": tier, "score": score["total"], "client_id": cid, "order_id": oid,
        "entry_window_minute": 1, "plan": plan,
    })
    self.emit("log", f"V1.5.4 {level}：BOLL首分钟内已提交市价{market['side']}；{multiplier:g}×风险仓位，整仓TP=2R，SL=1×1H ATR")
    self.emit("plan", plan)


def _efficient_scrollbar_set(self, first, last):
    """Keep scrollbar state synchronized without redrawing controls hidden in V1.5.3+."""
    try:
        self.first = max(0.0, min(1.0, float(first)))
        self.last = max(self.first, min(1.0, float(last)))
    except Exception:
        self.first, self.last = 0.0, 1.0
    try:
        mapped = bool(self.winfo_ismapped())
    except Exception:
        mapped = False
    if mapped:
        self._draw()


def _scroll_target(widget):
    w = widget
    while w is not None:
        if isinstance(w, visual.ScoreTable):
            return w.canvas
        if isinstance(w, (ttk.Treeview, tk.Text, tk.Listbox)):
            return w
        if isinstance(w, visual.ScrollablePage):
            return w.canvas
        try:
            w = w.master
        except Exception:
            return None
    return None


def _queue_scroll(owner, target, amount, horizontal=False):
    if target is None or not amount:
        return
    states = getattr(owner, "_v154_scroll_states", None)
    if states is None:
        states = owner._v154_scroll_states = {}
    key = (str(target), bool(horizontal))
    state = states.setdefault(key, {"pending": 0.0, "after": None, "target": target, "horizontal": bool(horizontal)})
    state["pending"] += float(amount)
    if state["after"] is not None:
        return

    def flush():
        state["after"] = None
        pending = float(state["pending"])
        if abs(pending) >= 1.0:
            units = int(pending)
        elif abs(pending) >= 0.45:
            units = 1 if pending > 0 else -1
        else:
            return
        units = max(-6, min(6, units))
        state["pending"] -= units
        try:
            if state["horizontal"]:
                state["target"].xview_scroll(units, "units")
            else:
                state["target"].yview_scroll(units, "units")
        except Exception:
            state["pending"] = 0.0

    state["after"] = owner.root.after(SCROLL_FLUSH_MS, flush)


def _wheel_amount(event):
    delta = float(getattr(event, "delta", 0) or 0)
    if not delta:
        return 0.0
    if abs(delta) >= 120:
        return -delta / 120.0
    # Aqua trackpads emit many small deltas. Accumulate them instead of turning
    # every tiny delta into a full row jump.
    return -delta / 3.0


def _bind_smooth_scroll(owner):
    root = owner.root
    for sequence in ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>"):
        try:
            root.unbind_all(sequence)
        except Exception:
            pass

    def bind_target(widget, target):
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                widget.unbind(sequence)
            except Exception:
                pass
        widget.bind("<MouseWheel>", lambda e, t=target: (_queue_scroll(owner, t, _wheel_amount(e)), "break")[1])
        widget.bind("<Shift-MouseWheel>", lambda e, t=target: (_queue_scroll(owner, t, _wheel_amount(e), True), "break")[1])
        widget.bind("<Button-4>", lambda e, t=target: (_queue_scroll(owner, t, -1.0), "break")[1])
        widget.bind("<Button-5>", lambda e, t=target: (_queue_scroll(owner, t, 1.0), "break")[1])

    for widget in v138._widgets(root):
        if isinstance(widget, visual.ScoreTable):
            for child in (widget, widget.header, widget.canvas):
                bind_target(child, widget.canvas)
        elif isinstance(widget, (ttk.Treeview, tk.Text, tk.Listbox)):
            bind_target(widget, widget)

    def global_wheel(event):
        target = _scroll_target(getattr(event, "widget", None))
        if target is None:
            return None
        _queue_scroll(owner, target, _wheel_amount(event))
        return "break"

    def global_shift(event):
        target = _scroll_target(getattr(event, "widget", None))
        if target is None:
            return None
        _queue_scroll(owner, target, _wheel_amount(event), True)
        return "break"

    def global_button(event, amount):
        target = _scroll_target(getattr(event, "widget", None))
        if target is None:
            return None
        _queue_scroll(owner, target, amount)
        return "break"

    root.bind_all("<MouseWheel>", global_wheel, add=False)
    root.bind_all("<Shift-MouseWheel>", global_shift, add=False)
    root.bind_all("<Button-4>", lambda e: global_button(e, -1.0), add=False)
    root.bind_all("<Button-5>", lambda e: global_button(e, 1.0), add=False)
    owner._v154_smooth_scroll_ready = True


def _render_logs_v154(self):
    """Append cheaply when possible; never steal the reader's scroll position."""
    try:
        view = self.log.yview()
        at_bottom = not view or float(view[1]) >= 0.995
        top = float(view[0]) if view else 0.0
    except Exception:
        at_bottom, top = True, 0.0

    current_filter = self.log_filter.get()
    source_len = len(self.log_lines)
    previous_len = getattr(self, "_v154_log_source_len", -1)
    previous_filter = getattr(self, "_v154_log_filter", None)
    incremental = previous_filter == current_filter and source_len == previous_len + 1 and source_len < 500

    self.log.configure(state="normal")
    if incremental:
        kind, line = self.log_lines[-1]
        if current_filter == "全部" or kind == "alarm":
            self.log.insert("end", line + "\n", kind)
    else:
        self.log.delete("1.0", "end")
        for kind, line in self.log_lines:
            if current_filter == "全部" or kind == "alarm":
                self.log.insert("end", line + "\n", kind)
    self.log.configure(state="disabled")

    if at_bottom:
        self.log.see("end")
    else:
        try:
            self.log.yview_moveto(top)
        except Exception:
            pass
    self._v154_log_source_len = source_len
    self._v154_log_filter = current_filter


def _rewrite_v154(text):
    out = _PREVIOUS_V153_REWRITE(text)
    if not isinstance(out, str):
        return out
    if out.startswith("自动交易启动；"):
        return (
            "自动交易启动；V1.5.4：1H/15m趋势定方向→5m BOLL回调触发→仅首个1m执行窗口→评分/限制→市价开仓；"
            "BOLL模块固定+2且不叠加；1m不做EMA/突破/KDJ等技术确认；单一1×仓位、不加仓；"
            "固定6分；前方强结构至少1.5R；1.0×1H ATR止损/整仓2R；市价开仓按Taker+滑点预算；"
            "连续3次净亏损暂停新开仓1小时"
        )
    replacements = (
        ("V1.5.3", "V1.5.4"),
        ("V1.5.2", "V1.5.4"),
        ("1.5.3", "1.5.4"),
        ("4根1m直接执行窗口", "仅首个1m执行窗口"),
        ("4分钟直接执行", "首分钟市价执行"),
        ("4分钟执行窗口", "首分钟执行窗口"),
        ("4分钟窗口", "首分钟窗口"),
        ("计划限价", "计划市价参考"),
        ("实际限价", "实际市价参考"),
        ("限价开仓", "市价开仓"),
    )
    for old, new in replacements:
        out = out.replace(old, new)
    return out


def apply():
    if getattr(engine.Engine, "_kaytrade_v154_applied", False):
        return

    # Route both live wrappers and shared backtest facade to the one-minute model.
    v152_runtime.model = model
    v152_runtime.V152_VERSION = VERSION
    v153_runtime.model = model
    v153_runtime.VERSION = VERSION
    v153_bt.model = model

    # Preserve all inherited sizing/safety gates, but budget the actual market entry.
    v137._v137_make_plan = _v154_make_plan
    v138._submit_initial = _submit_initial_market

    # Hidden scrollbars should not repaint on every wheel delta.
    visual.WideScrollbar.set = _efficient_scrollbar_set

    global _PREVIOUS_V153_REWRITE
    _PREVIOUS_V153_REWRITE = v153_runtime._rewrite_text
    v153_runtime._rewrite_text = _rewrite_v154

    previous_app_init = app.App.__init__

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        try:
            self.root.title("KAYTRADE 1.5.4 · BTC 策略控制台")
            self.signal.set("V1.5.4 · 等待趋势 / 5m BOLL首分钟信号")
        except Exception:
            pass
        for widget in v138._widgets(self.root):
            try:
                text = str(widget.cget("text") or "")
            except Exception:
                text = ""
            if not text:
                continue
            rewritten = _rewrite_v154(text)
            rewritten = rewritten.replace(
                "Maker 0.02%   ·   Taker 0.05%   ·   滑点预算 0.05%",
                "市价开仓/平仓 Taker 0.05%   ·   滑点预算 0.05%",
            )
            try:
                if rewritten != text:
                    widget.configure(text=rewritten)
            except Exception:
                pass
        _bind_smooth_scroll(self)
        self._v154_ui_ready = True

    app.App.render_logs = _render_logs_v154
    app.App.__init__ = app_init
    engine.Engine._kaytrade_v154_applied = True
    app.App._kaytrade_v154_smooth_scroll_applied = True


_PREVIOUS_V153_REWRITE = v153_runtime._rewrite_text
apply()

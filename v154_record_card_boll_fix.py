"""KAYTRADE V1.5.4 round-two repair overlay.

Confirmed changes:
- restore the original rounded 运行记录 card as the only visible run record UI;
- remove the added legacy Text log/filter presentation and persisted-log replay;
- keep LIMIT entry, but remove the BOLL post-trigger one-minute submission limit;
- annotate score-detail rows as 开仓必要 vs 辅助评分;
- remove the black rectangular gutter around the fixed execution-cost card.
"""
from __future__ import annotations

import time
import uuid

from v154_limit_log_fix import apply as apply_limit_fix
apply_limit_fix()

import app
import engine
import v138_strategy_patch as v138
import v146_ui_log_polish_patch as v146_logs
import v154_model as model
import visual

VERSION = "1.5.4"
BUILD = "154.2"


def _submit_initial_limit_persistent(self, market, score, equity, available, remaining):
    """Submit LIMIT entry without a BOLL wall-clock expiry.

    The opportunity itself is kept/invalidated by the shared V1.5.4 model.
    Each individual passive LIMIT order still uses the inherited 60-second
    pending-order lifecycle so stale exchange orders are reconciled/cancelled;
    an unfilled valid BOLL opportunity may be evaluated again on a later 1m bar.
    """
    opportunity = (market or {}).get("opportunity") or (score or {}).get("opportunity") or {}
    if not isinstance(opportunity, dict) or not opportunity.get("id"):
        self.emit("log", "V1.5.4 缺少有效5m BOLL机会状态；未提交限价订单")
        return

    prepared = v138._prepare_order(self, market["side"], score, market, equity, available, remaining)
    if not prepared:
        return
    plan, tier, multiplier, level = prepared
    if not v138._set_leverage(self, plan) or not self.enabled:
        return
    self._verify_latest("1m", market["bar"], v138.ONE_MINUTE_STEP)

    cid = "mac" + uuid.uuid4().hex[:28]
    algo = "br" + uuid.uuid4().hex[:28]
    state = self.store.data
    before_last = state.get("last_bar")
    submitted_at = time.time()
    expires_at = submitted_at + 60.0
    signal_close_ms = int(opportunity.get("signal_close_ms") or opportunity.get("created_ms") or 0)

    leg = dict(
        plan,
        client_id=cid,
        order_id="",
        bracket_id=algo,
        submitted=submitted_at,
        expires=expires_at,
        filled=False,
        state="pending",
        cancel_requested=False,
        cancel_on_reconcile=False,
        tier=tier,
        score=float(score["total"]),
        signal_bar=market["bar"],
        counted=True,
        protected=False,
        v154=True,
        version=VERSION,
        build=BUILD,
        order_type="limit",
        signal_close_ms=signal_close_ms,
        boll_time_window_enabled=False,
    )
    root = dict(
        plan,
        client_id=cid,
        order_id="",
        bracket_id=algo,
        submitted=submitted_at,
        expires=expires_at,
        equity_before=equity,
        filled=False,
        protected=False,
        score=float(score["total"]),
        signal_bar=market["bar"],
        signal_tier=tier,
        signal_level=level,
        v137=True,
        v138=True,
        v152=True,
        v153=True,
        v154=True,
        version=VERSION,
        build=BUILD,
        legs=[leg],
        tier_counts={"1": 0, "2": 0, "3": 0},
        highest_tier=tier,
        last_entry_bar=market["bar"],
        order_type="limit",
        signal_close_ms=signal_close_ms,
        boll_time_window_enabled=False,
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
        "ordType": "limit",
        "px": plan["px"],
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
            self.emit("log", f"OKX明确拒绝V1.5.4限价开仓：{exc}；未产生新订单，已释放占位；自动新开仓已停止")
            return
        # Ambiguous write stays fail-closed: keep local occupancy until
        # reconciliation proves the exchange state.
        raise

    row = reply[0] if reply else {}
    oid = str(row.get("ordId") or "") if isinstance(row, dict) else ""
    if not oid:
        raise engine.Halt("V1.5.4限价开仓响应缺少ordId；本地占位已保留，禁止重复提交")
    leg["order_id"] = oid
    root["order_id"] = oid
    root["okx_ack_at"] = time.time()
    self.store.save()
    self.store.record("V1.5.4提交限价开仓", {
        "tier": tier,
        "score": score["total"],
        "client_id": cid,
        "order_id": oid,
        "boll_time_window_enabled": False,
        "plan": plan,
    })
    self.emit(
        "log",
        f"V1.5.4 {level}：有效5m BOLL机会下已提交限价{market['side']}；"
        f"不设BOLL触发后1分钟截止；{multiplier:g}×风险仓位，整仓TP=2R，SL=1×1H ATR",
    )
    self.emit("plan", plan)


def _walk(root):
    try:
        children = root.winfo_children()
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk(child)


def _find_added_log_filterbar(owner):
    bar = getattr(owner, "_v154_log_filterbar", None)
    if bar is not None:
        return bar
    try:
        return v146_logs._find_legacy_log_filter(owner)
    except Exception:
        return None


def _restore_original_record_card(owner):
    """Hide the added Text log UI and restore V1.4.6+ rounded record card."""
    legacy_log = getattr(owner, "log", None)
    if legacy_log is not None:
        try:
            legacy_log.pack_forget()
        except Exception:
            pass
        try:
            legacy_log.grid_remove()
        except Exception:
            pass

    filterbar = _find_added_log_filterbar(owner)
    if filterbar is not None:
        try:
            filterbar.pack_forget()
        except Exception:
            pass
        try:
            filterbar.grid_remove()
        except Exception:
            pass

    # v154_limit_log_fix may have replayed events.log during its inherited init.
    # The original record card is session-oriented, so remove that injected
    # history and let current-session events populate it normally.
    owner.log_lines = []

    surface = getattr(owner, "_v146_log_surface", None)
    book = getattr(owner, "book", None)
    if surface is not None and book is not None:
        try:
            book.pack_forget()
        except Exception:
            pass
        try:
            surface.pack_forget()
        except Exception:
            pass
        try:
            surface.pack(side="bottom", fill="x", padx=15, pady=(0, 12))
            book.pack(fill="both", expand=True, padx=15, pady=10)
        except Exception:
            pass

    owner._v154_added_text_log_removed = True
    owner._v154_original_record_card_restored = surface is not None
    owner.render_logs()


def _clean_fixed_cost_card(owner):
    """Blend the Card canvas gutter into its PANEL parent, removing black box."""
    cleaned = False
    for widget in _walk(owner.root):
        if not isinstance(widget, visual.Card):
            continue
        found = False
        for child in _walk(widget.body):
            try:
                if str(child.cget("text") or "") == "固定执行成本":
                    found = True
                    break
            except Exception:
                continue
        if not found:
            continue
        try:
            widget.configure(bg=app.PANEL, highlightthickness=0, borderwidth=0)
            widget._v154_fixed_cost_gutter_removed = True
            cleaned = True
        except Exception:
            pass
    owner._v154_fixed_cost_card_clean = cleaned
    return cleaned


def _rewrite_no_window_text(text):
    if not isinstance(text, str):
        return text
    out = text
    replacements = (
        ("首分钟限价执行", "BOLL有效期间限价执行"),
        ("首分钟市价执行", "BOLL有效期间限价执行"),
        ("BOLL首分钟内", "有效BOLL信号下"),
        ("BOLL首分钟执行窗口", "BOLL信号有效状态"),
        ("仅首个1m执行窗口", "1m仅负责执行，不设时间截止"),
        ("仅第1根1m", "1m仅负责执行，不设时间截止"),
        ("4根1m直接执行窗口", "BOLL信号有效期间直接执行"),
        ("4分钟直接执行", "BOLL信号有效期间执行"),
        ("4分钟执行窗口", "BOLL信号有效状态"),
        ("5m信号后仅4分钟有效", "5m BOLL信号不设时间截止"),
        ("5m信号后仅1分钟有效", "5m BOLL信号不设时间截止"),
        ("60秒", "无BOLL时间截止"),
    )
    for old, new in replacements:
        out = out.replace(old, new)
    return out


def apply():
    if getattr(engine.Engine, "_kaytrade_v154_record_boll_fix_applied", False):
        return

    # Runtime entry path: LIMIT stays, BOLL one-minute wall-clock gate is gone.
    v138._submit_initial = _submit_initial_limit_persistent

    # Restore the original rounded record-card renderer before new App objects.
    app.App.render_logs = v146_logs._render_logs

    previous_app_init = app.App.__init__
    previous_app_arm = app.App.arm

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        try:
            self.root.title("KAYTRADE 1.5.4 · BTC 策略控制台 · 修复版")
        except Exception:
            pass

        # Ensure the class-level renderer is still the original record-card
        # renderer even after inherited V1.5.4 initializers have run.
        self.render_logs = v146_logs._render_logs.__get__(self, app.App)
        _restore_original_record_card(self)
        _clean_fixed_cost_card(self)

        try:
            self.score_table.heading("#0", text="已收盘K线 · 评分条件【必要/辅助】", anchor="w")
        except Exception:
            pass

        for widget in _walk(self.root):
            try:
                text = str(widget.cget("text") or "")
            except Exception:
                continue
            if not text:
                continue
            rewritten = _rewrite_no_window_text(text)
            try:
                if rewritten != text:
                    widget.configure(text=rewritten)
            except Exception:
                pass
        try:
            self.signal.set(_rewrite_no_window_text(str(self.signal.get() or "")))
        except Exception:
            pass
        self._v154_round2_ready = True

    def app_arm(self):
        original = app.simpledialog.askstring

        def askstring(title, prompt, *args, **kwargs):
            text = _rewrite_no_window_text(str(prompt))
            if "V1.5.4持续有效规则" not in text:
                text += (
                    "\nV1.5.4持续有效规则：5m BOLL信号成立后不再设置1分钟/4分钟墙钟截止；"
                    "只要方向、结构、追价、成本等Hard Block未使机会失效，就继续允许后续1m执行评估。"
                    "1m不增加EMA/KDJ/RSI/MACD技术确认。"
                )
            return original(title, text, *args, **kwargs)

        app.simpledialog.askstring = askstring
        try:
            return previous_app_arm(self)
        finally:
            app.simpledialog.askstring = original

    app.App.__init__ = app_init
    app.App.arm = app_arm
    app.App.render_logs = v146_logs._render_logs
    engine.Engine._kaytrade_v154_record_boll_fix_applied = True
    app.App._kaytrade_v154_record_card_restored = True


apply()

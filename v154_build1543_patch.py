"""KAYTRADE V1.5.4 Build 1543 overlay.

Confirmed changes:
- remove the consecutive-loss entry pause from live V1.5.4;
- remove the corresponding Risk-page control/help text;
- expose first-signal position funds and a read-only second-signal value fixed at 2x;
- add long-only Path C: after a filled/protected Path-A (middle+RSI) first entry,
  a later closed 5m candle touching the lower BOLL band creates a second-signal
  opportunity; RSI is ignored for Path C, while the normal score threshold and
  hard blockers are re-evaluated before a 2x LIMIT add-on can be submitted.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import asdict, replace
from decimal import Decimal
import tkinter as tk

from v154_record_card_boll_fix import apply as apply_previous
apply_previous()

import app
import engine
from core import indicators
import v136_entry_fix
import v136_runtime
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v154_model as model

VERSION = "1.5.4"
BUILD = "154.3"
PATH_C_SIDE = "做多"
PATH_C_LABEL = "path_c_lower_band"


def _walk(root):
    try:
        children = root.winfo_children()
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk(child)


def _clear_loss_pause_state(store):
    """Remove all live entry-stop state tied to consecutive losses.

    max_streak is intentionally left intact as historical diagnostics only.
    """
    if store is None:
        return False
    state = store.data
    changed = False
    updates = {
        "streak": 0,
        "streak_pause_until": 0.0,
        "streak_notice_day": "",
        "streak_day": "",
    }
    for key, value in updates.items():
        if state.get(key) != value:
            state[key] = value
            changed = True
    if changed:
        store.save()
    return changed


def _rewrite_loss_text(text):
    if not isinstance(text, str):
        return text
    if any(token in text for token in (
        "连续净亏损已达", "连续亏损1小时暂停", "连亏暂停剩余",
        "中国时间本日已连续亏损", "中国时间本日连续净亏损已达",
    )):
        return None
    if "；中国时间连续亏损" in text:
        text = text.split("；中国时间连续亏损", 1)[0]
    return (text
            .replace("；连续3次净亏损暂停新开仓1小时", "")
            .replace("连续3次净亏损暂停新开仓1小时；", "")
            .replace("连续3次净亏损暂停1小时；", ""))


def _remove_loss_control(owner):
    """Hide the consecutive-loss Risk-page row and its explanatory note."""
    targets = []
    for widget in _walk(owner.root):
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            continue
        if text == "中国时间连续亏损停开次数" or (
            "连续亏损停开次数" in text and "其余风险" in text
        ):
            targets.append(widget)
    for widget in targets:
        try:
            widget.grid_remove()
        except Exception:
            try:
                widget.pack_forget()
            except Exception:
                pass
    owner.fields.pop("consecutive_losses", None)
    owner._v1543_loss_control_removed = True


def _find_first_signal_row(owner):
    first_var = owner.fields.get("first_signal_notional")
    label = None
    entry = None
    if first_var is None:
        return None, None, None, None
    for widget in _walk(owner.root):
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            text = ""
        if text.startswith("开仓信号仓位 USDT") or text.startswith("第一信号仓位 USDT"):
            label = widget
        if isinstance(widget, app.RoundedEntry) and getattr(widget, "variable", None) is first_var:
            entry = widget
    if label is None or entry is None:
        return first_var, label, entry, None
    try:
        parent = label.master
        row = int(label.grid_info().get("row"))
    except Exception:
        parent = entry.master
        try:
            row = int(entry.grid_info().get("row"))
        except Exception:
            row = None
    return first_var, label, entry, (parent, row)


def _install_signal_funds_ui(owner):
    first_var, first_label, first_entry, location = _find_first_signal_row(owner)
    if first_var is None or first_label is None or first_entry is None or location is None:
        owner._v1543_signal_funds_ready = False
        return False
    parent, first_row = location
    second_row = first_row + 1

    # Make space for the new read-only row using only currently mapped widgets.
    mapped = []
    for child in parent.winfo_children():
        try:
            info = child.grid_info()
            if info:
                mapped.append((child, int(info.get("row", -1))))
        except Exception:
            pass
    for child, row in sorted(mapped, key=lambda x: x[1], reverse=True):
        if row >= second_row:
            try:
                child.grid_configure(row=row + 1)
            except Exception:
                pass

    try:
        first_label.configure(text="第一信号仓位 / 开仓资金 USDT")
    except Exception:
        pass

    try:
        first_value = float(first_var.get())
    except Exception:
        first_value = 0.0
    second_var = tk.StringVar(value=(f"{first_value * 2:g}" if first_value > 0 else ""))

    second_label = tk.Label(
        parent,
        text="第二信号仓位 / 加仓资金 USDT（自动=第一信号×2）",
        wraplength=420,
        bg=app.PANEL,
        fg="#dbe6eb",
        font=("Helvetica", 12),
        anchor="w",
        bd=0,
    )
    second_label.grid(row=second_row, column=0, sticky="w", padx=6, pady=11)
    second_entry = app.RoundedEntry(parent, textvariable=second_var, width=170, height=40, font=("Helvetica", 14))
    second_entry.grid(row=second_row, column=1, sticky="e", padx=8, pady=11)
    try:
        second_entry.entry.configure(
            state="disabled", disabledbackground=app.FIELD, disabledforeground=app.MUTED,
        )
    except Exception:
        pass

    def sync_second(*_):
        try:
            value = float(first_var.get())
            second_var.set(f"{value * 2:g}" if math.isfinite(value) and value > 0 else "")
        except Exception:
            second_var.set("")

    try:
        owner._v1543_first_trace = first_var.trace_add("write", sync_second)
    except Exception:
        owner._v1543_first_trace = None
    sync_second()

    owner.fields["second_signal_notional"] = second_var
    owner._v1543_second_signal_var = second_var
    owner._v1543_second_signal_entry = second_entry
    owner._v1543_signal_funds_ready = True
    return True


def _settings_from_ui(owner):
    """Rebuild the current V1.5 settings with a forced 1x/2x signal-fund pair."""
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

    first = float(values.get("first_signal_notional") or 0.0)
    if not math.isfinite(first) or first <= 0:
        raise engine.Halt("第一信号仓位必须是有限正数")
    second = first * 2.0
    values["first_signal_notional"] = first
    values["second_signal_notional"] = second
    # Backward-compatible hidden field only; V1.5.4 Build 1543 never uses it.
    values["third_signal_notional"] = max(float(values.get("third_signal_notional") or 1.0), 1e-9)
    values["consecutive_losses"] = 3  # compatibility-only, live pause logic is disabled below
    values["score_threshold"] = 6.0
    values["max_initial_notional"] = first
    # Opening order is capped at the first-signal amount. Path C temporarily
    # switches this cap to second_signal_notional when building the add-on plan.
    values["max_notional"] = first

    settings = SettingsClass(**values).validate()
    if abs(float(settings.stop_atr) - 1.0) > 1e-9:
        raise engine.Halt("V1.5.4固定使用1.0×1H ATR止损")
    if first + second > float(settings.capital) * float(settings.leverage) + 1e-9:
        raise engine.Halt("第一+第二信号仓位合计超过策略资金×杠杆上限")
    return settings


def _capture_candles(previous_refresh):
    def refresh(self):
        captured = {}
        original = self.x.candles

        def candles(bar):
            rows = original(bar)
            captured[bar] = rows
            return rows

        self.x.candles = candles
        try:
            result = previous_refresh(self)
        finally:
            self.x.candles = original
        if captured:
            self._v1543_candles = captured
        return result
    return refresh


def _first_path_a_filled(active):
    if not isinstance(active, dict):
        return False
    if active.get("side") != PATH_C_SIDE:
        return False
    if str(active.get("boll_signal_path") or "") != "middle_rsi":
        return False
    if not active.get("filled") or not active.get("protected"):
        return False
    legs = list(active.get("legs") or [])
    if not legs:
        return False
    first = legs[0]
    return first.get("state") == "filled" and not first.get("path_c")


def _path_c_leg_state(active):
    states = [str(leg.get("state") or "") for leg in (active.get("legs") or []) if leg.get("path_c")]
    if "filled" in states:
        return "filled"
    if "pending" in states:
        return "pending"
    return "none"


def _new_path_c_opportunity(active, candles):
    five = candles.get("5m") or []
    hour = candles.get("1H") or []
    quarter = candles.get("15m") or []
    one = candles.get("1m") or []
    if min(len(five), len(hour), len(quarter), len(one)) < 201:
        return None
    cur = indicators(five)
    bar = five[-1]
    lower = float(cur["lower"])
    if float(bar["l"]) > lower:
        return None
    close_ms = int(bar["t"]) + 5 * 60 * 1000
    first_close_ms = int(active.get("signal_close_ms") or 0)
    if first_close_ms and close_ms <= first_close_ms:
        return None
    last_signal_bar = int(active.get("path_c_last_signal_bar_t") or -1)
    if int(bar["t"]) <= last_signal_bar:
        return None

    signal = {
        "path": "lower_band",
        "bar_t": int(bar["t"]),
        "signal_close_ms": close_ms,
        "reference": lower,
        "middle": float(cur["middle"]),
        "upper": float(cur["upper"]),
        "lower": lower,
        # Diagnostic only. Path C eligibility deliberately does not use RSI.
        "rsi5": float(cur["rsi"]),
        "atr5": float(cur["atr"]),
        "bar_low": float(bar["l"]),
        "bar_high": float(bar["h"]),
    }
    opp = model.new_opportunity(hour, quarter, five, one, PATH_C_SIDE, signal)
    opp["path_c"] = True
    opp["signal_path"] = PATH_C_LABEL
    opp["trigger_detail"]["boll_path"] = PATH_C_LABEL
    opp["trigger_detail"]["path_c_rsi_required"] = False
    opp["trigger_detail"]["first_signal_path_required"] = "middle_rsi"
    return opp


def _evaluate_path_c(self, active):
    candles = getattr(self, "_v1543_candles", None) or {}
    required = ("1H", "15m", "5m", "1m", "4H")
    if any(not candles.get(key) for key in required):
        return None

    opp = active.get("path_c_opportunity") if isinstance(active.get("path_c_opportunity"), dict) else None
    if opp is None:
        opp = _new_path_c_opportunity(active, candles)
        if opp is None:
            return None
        active["path_c_opportunity"] = dict(opp)
        active["path_c_last_signal_bar_t"] = int(opp.get("signal_bar_t") or 0)
        active["path_c_last_block_signature"] = ""
        self.store.save()
        self.store.record("V1.5.4路径C机会创建", {
            "opportunity_id": opp.get("id"),
            "signal_bar_t": opp.get("signal_bar_t"),
            "lower": opp.get("trigger_reference"),
            "rsi_required": False,
        })
        self.emit("log", "V1.5.4 路径C：路径A首仓已成交；新的已收盘5m触碰BOLL下轨，第二信号开始评估（不要求RSI）")

    hour = candles["1H"]
    quarter = candles["15m"]
    five = candles["5m"]
    one = candles["1m"]
    four = candles["4H"]
    now_ms = int(one[-1]["t"]) + 60_000
    result, new_opp, transition = model.evaluate(
        hour, quarter, five, one, four,
        opportunity=opp,
        stop_atr=float(self.settings.stop_atr),
        maker_bps=float(self.settings.fee_bps),
        taker_bps=float(self.settings.taker_fee_bps),
        slippage_bps=float(self.settings.slippage_bps),
        now_ms=now_ms,
        allow_new=False,
    )
    if not isinstance(new_opp, dict):
        active["path_c_opportunity"] = None
        self.store.save()
        if transition and transition[0] == "invalidated":
            self.emit("log", "V1.5.4 路径C机会已失效：" + str(transition[1]))
        return None
    new_opp["path_c"] = True
    new_opp["signal_path"] = PATH_C_LABEL
    active["path_c_opportunity"] = dict(new_opp)
    self.store.save()

    score = (result.get("scores") or {}).get(PATH_C_SIDE) or {}
    score = dict(score)
    score["opportunity"] = dict(new_opp)
    confirmations = dict(score.get("confirmations") or {})
    confirmations["path_c"] = True
    confirmations["path_c_rsi_required"] = False
    confirmations["first_signal_path"] = "middle_rsi"
    score["confirmations"] = confirmations

    if not score.get("eligible"):
        blockers = list(confirmations.get("blockers") or [])
        signature = f"{float(score.get('total') or 0):g}|{'|'.join(blockers)}|{score.get('reason','')}"
        if signature != active.get("path_c_last_block_signature"):
            active["path_c_last_block_signature"] = signature
            self.store.save()
            detail = "；".join(blockers) if blockers else str(score.get("reason") or "未达到开仓条件")
            self.emit("log", f"V1.5.4 路径C暂不加仓：{detail}")
        return None

    market = dict(result)
    market.update({
        "side": PATH_C_SIDE,
        "bar": int(one[-1]["t"]),
        "bar1m": int(one[-1]["t"]),
        "bar5m": int(five[-1]["t"]),
        "bar15": int(quarter[-1]["t"]),
        "bar1h": int(hour[-1]["t"]),
        "bar4h": int(four[-1]["t"]),
        "close": float(one[-1]["c"]),
        "opportunity": dict(new_opp),
    })
    return market, score, new_opp


def _make_path_c_plan(self, market, score, opportunity, available, risk_remaining):
    ticker = self.x.ticker()
    self.emit("ticker", ticker)
    meta = self.x.instrument()
    hour_rows = (getattr(self, "_v1543_candles", {}) or {}).get("1H") or []
    if len(hour_rows) < 201:
        raise engine.Halt("路径C缺少1H ATR数据")
    atr1h = float(indicators(hour_rows)["atr"])

    target_notional = float(self.settings.second_signal_notional)
    temp = replace(self.settings, max_notional=target_notional)
    v137._CURRENT_CONTEXT.update(bar=market["bar"], tier=2, level="第二信号 · 路径C", score=float(score["total"]))
    try:
        plan = v137._v137_make_plan(temp, PATH_C_SIDE, ticker, meta, atr1h, available, risk_remaining, 2.0)
    except engine.Halt as exc:
        message = str(exc)
        if any(token in message for token in (
            "买卖价差过大", "风险预算不足以满足最小下单量", "无效信号或ATR", "止盈止损价格非法"
        )):
            self.emit("log", "V1.5.4 路径C本轮跳过：" + message)
            return None
        raise

    ok, diag, blockers = model.execution_checks(plan, opportunity, score)
    if not ok:
        self.emit("log", "V1.5.4 路径C实际限价二次检查禁止加仓：" + "；".join(blockers))
        return None
    if float(plan.get("expected_fee_multiple") or 0.0) < float(v138.EXPECTED_COST_MIN):
        self.emit("log", f"V1.5.4 路径C成本过滤：2R TP/预计手续费 {float(plan.get('expected_fee_multiple') or 0):.2f}×，未达最低{float(v138.EXPECTED_COST_MIN):.2f}×")
        return None

    plan.update({
        "version": VERSION,
        "build": BUILD,
        "v152": True,
        "v153": True,
        "v154": True,
        "path_c": True,
        "signal_type": "second_signal_path_c",
        "boll_signal_path": PATH_C_LABEL,
        "position_multiplier": 2.0,
        "first_signal_notional_setting": float(self.settings.first_signal_notional),
        "second_signal_notional_setting": target_notional,
        "opportunity_id": opportunity.get("id", ""),
        "front_r": diag.get("front_r"),
        "front_space_status": diag.get("front_space_status"),
        "cost_r": diag.get("cost_r"),
        "stop_buffer_ok": diag.get("stop_buffer_ok"),
        "entry_order_type": "limit",
        "market_entry": False,
        "entry_slippage_budget_bps": 0.0,
    })
    return plan, meta


def _submit_path_c(self, active, market, score, opportunity, equity, available, remaining):
    filled_risk = sum(float(leg.get("estimated_loss") or 0.0) for leg in (active.get("legs") or []) if leg.get("state") == "filled")
    risk_remaining = float(remaining) - filled_risk
    if risk_remaining <= 0:
        self.emit("log", "V1.5.4 路径C：日内剩余风险预算不足，未加仓")
        return

    built = _make_path_c_plan(self, market, score, opportunity, available, risk_remaining)
    if not built:
        return
    plan, _meta = built
    if not v138._set_leverage(self, plan) or not self.enabled:
        return
    self._verify_latest("1m", market["bar"], v138.ONE_MINUTE_STEP)

    cid = "mac" + uuid.uuid4().hex[:28]
    algo = "br" + uuid.uuid4().hex[:28]
    state = self.store.data
    before_last = state.get("last_bar")
    submitted = time.time()
    leg = dict(
        plan,
        client_id=cid,
        order_id="",
        bracket_id=algo,
        submitted=submitted,
        expires=submitted + 60.0,
        filled=False,
        state="pending",
        cancel_requested=False,
        cancel_on_reconcile=False,
        tier=2,
        score=float(score["total"]),
        signal_bar=market["bar"],
        counted=True,
        protected=False,
        path_c=True,
        second_signal=True,
        signal_close_ms=int(opportunity.get("signal_close_ms") or 0),
    )
    active.setdefault("legs", []).append(leg)
    active.setdefault("tier_counts", {"1": 0, "2": 0, "3": 0})
    active["tier_counts"]["2"] = int(active["tier_counts"].get("2", 0) or 0) + 1
    active["highest_tier"] = max(int(active.get("highest_tier") or 1), 2)
    active["last_entry_bar"] = market["bar"]
    active["path_c_last_submit_bar"] = market["bar"]
    active["path_c_last_block_signature"] = ""
    active["version"] = VERSION
    active["build"] = BUILD
    state["last_bar"] = market["bar"]
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
            try:
                active["legs"].remove(leg)
            except ValueError:
                pass
            active["tier_counts"]["2"] = max(0, int(active["tier_counts"].get("2", 0)) - 1)
            active["highest_tier"] = max([int(k) for k, v in active["tier_counts"].items() if int(v or 0) > 0] or [1])
            state["last_bar"] = before_last
            self.store.save()
            self.enabled = False
            self.stopped = True
            self.emit("log", f"OKX明确拒绝V1.5.4路径C加仓：{exc}；未产生第二订单，已释放占位；自动新开仓已停止")
            return
        # Ambiguous write remains fail-closed; keep the local pending leg until
        # reconciliation proves exchange state.
        raise

    row = reply[0] if reply else {}
    oid = str(row.get("ordId") or "") if isinstance(row, dict) else ""
    if not oid:
        raise engine.Halt("V1.5.4路径C加仓响应缺少ordId；本地占位已保留，禁止重复提交")
    leg["order_id"] = oid
    leg["okx_ack_at"] = time.time()
    self.store.save()
    self.store.record("V1.5.4路径C提交加仓", {
        "score": score.get("total"),
        "client_id": cid,
        "order_id": oid,
        "first_signal_notional": float(self.settings.first_signal_notional),
        "second_signal_notional": float(self.settings.second_signal_notional),
        "rsi_required": False,
        "opportunity_id": opportunity.get("id"),
        "plan": plan,
    })
    self.emit(
        "log",
        f"V1.5.4 路径C第二信号：已提交限价加仓；第二信号资金={float(self.settings.second_signal_notional):g} USDT（第一信号×2）；"
        f"评分{float(score.get('total') or 0):g}/10；RSI不参与路径C；SL=1×1H ATR，TP=2R",
    )
    self.emit("plan", plan)


def _maybe_path_c(self):
    if not self.enabled or not self.store or not self.settings:
        return
    active = self.store.data.get("active")
    if not _first_path_a_filled(active):
        return
    state = _path_c_leg_state(active)
    if state in ("pending", "filled"):
        return
    if active.get("emergency_close_id"):
        return
    evaluated = _evaluate_path_c(self, active)
    if not evaluated:
        return
    market, score, opportunity = evaluated
    if not score.get("gate") or float(score.get("total") or 0.0) < float(model.THRESHOLD):
        return
    equity, available = self.x.balance()
    remaining = self.daily(equity)
    _submit_path_c(self, active, market, score, opportunity, equity, available, remaining)


def apply():
    if getattr(engine.Engine, "_kaytrade_v154_build1543_applied", False):
        return

    previous_refresh = engine.Engine.refresh_market
    previous_cycle = engine.Engine.cycle
    previous_arm_engine = engine.Engine.arm
    previous_finalize = v137._finalize_cycle
    previous_app_init = app.App.__init__
    previous_app_arm = app.App.arm

    engine.Engine.refresh_market = _capture_candles(previous_refresh)

    def finalize_cycle(self, p):
        original_emit = self.emit
        def emit(kind, data):
            rewritten = _rewrite_loss_text(data) if isinstance(data, str) else data
            if rewritten is None:
                return None
            return original_emit(kind, rewritten)
        self.emit = emit
        try:
            result = previous_finalize(self, p)
        finally:
            self.emit = original_emit
        _clear_loss_pause_state(self.store)
        return result

    def cycle(self):
        _clear_loss_pause_state(self.store)
        original_emit = self.emit
        def emit(kind, data):
            rewritten = _rewrite_loss_text(data) if isinstance(data, str) else data
            if rewritten is None:
                return None
            return original_emit(kind, rewritten)
        self.emit = emit
        try:
            result = previous_cycle(self)
        finally:
            self.emit = original_emit
        _clear_loss_pause_state(self.store)
        _maybe_path_c(self)
        return result

    def engine_arm(self, settings):
        _clear_loss_pause_state(self.store)
        original_emit = self.emit
        def emit(kind, data):
            rewritten = _rewrite_loss_text(data) if isinstance(data, str) else data
            if rewritten is None:
                return None
            return original_emit(kind, rewritten)
        self.emit = emit
        try:
            return previous_arm_engine(self, settings)
        finally:
            self.emit = original_emit

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        try:
            self.root.title("KAYTRADE 1.5.4 · BTC 策略控制台 · Build 1543")
        except Exception:
            pass
        _remove_loss_control(self)
        _install_signal_funds_ui(self)
        try:
            self.signal.set(str(self.signal.get() or "").replace("单一1×仓位、不加仓", "路径A首仓1×；路径C第二信号2×加仓"))
        except Exception:
            pass
        self._v1543_ready = True

    def app_settings(self):
        return _settings_from_ui(self)

    def app_arm(self):
        original = app.simpledialog.askstring
        def askstring(title, prompt, *args, **kwargs):
            text = str(prompt)
            for token in (
                "连续3次净亏损暂停新开仓1小时",
                "连续3次净亏损暂停1小时",
                "中国时间连续亏损停开",
            ):
                text = text.replace(token, "")
            text += (
                "\n\nV1.5.4 Build 1543：连续亏损不再触发停开。"
                "第一信号仓位由风险设置手动输入；第二信号仓位固定为第一信号2倍且不可编辑。"
                "路径C仅用于做多：路径A（5m中轨+RSI）首仓实际成交并保护后，后续新的已收盘5m最低价触碰BOLL下轨可形成第二信号；"
                "路径C不要求RSI，但仍需评分≥6与全部Hard Block通过，随后以LIMIT提交第二仓。"
            )
            return original(title, text, *args, **kwargs)
        app.simpledialog.askstring = askstring
        try:
            return previous_app_arm(self)
        finally:
            app.simpledialog.askstring = original

    v137._finalize_cycle = finalize_cycle
    engine.Engine.cycle = cycle
    engine.Engine.arm = engine_arm
    app.App.__init__ = app_init
    app.App.settings = app_settings
    app.App.arm = app_arm
    engine.Engine._kaytrade_v154_build1543_applied = True
    app.App._kaytrade_v154_build1543_applied = True


apply()

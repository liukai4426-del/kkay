"""KAYTRADE V1.5.4 repair overlay.

Repairs only:
- restore all initial entries to OKX LIMIT orders while keeping the exact
  first-minute BOLL execution window;
- restore Maker-entry / Taker-exit cost economics;
- make run-log rendering deterministic and restore recent persisted events.log
  lines at startup so the panel is not blank after relaunch.
"""
from __future__ import annotations

import math
import time
import uuid
from decimal import Decimal

from v154_runtime_patch import apply as apply_v154
apply_v154()

import app
import engine
import v136_entry_fix
import v136_runtime
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v154_model as model

VERSION = "1.5.4"
BUILD = "154.1"
MAX_LOG_LINES = 500


def _v154_limit_make_plan(s, side, ticker, meta, atr, available, daily_remaining, position_multiplier=1.0):
    """Build the inherited 2R/1H-ATR plan with LIMIT-entry economics."""
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

    # Passive entry: long rests at bid, short rests at ask.
    limit = float(engine.rounded(bid if buy else ask, meta["tickSz"], not buy))
    dist = float(atr) * float(s.stop_atr)
    sl = engine.rounded(limit - direction * dist, meta["tickSz"], not buy)
    tp = engine.rounded(limit + direction * dist * 2.0, meta["tickSz"], buy)
    if not (float(sl) < limit < float(tp) if buy else float(tp) < limit < float(sl)):
        raise engine.Halt("止盈止损价格非法")

    unit = float(meta["ctVal"]) * float(meta.get("ctMult") or 1)
    if unit <= 0 or float(meta["minSz"]) <= 0 or float(meta["lotSz"]) <= 0:
        raise engine.Halt("合约单位异常")

    maker = float(s.fee_bps) / 10000.0
    taker = float(s.taker_fee_bps) / 10000.0
    slip = float(s.slippage_bps) / 10000.0
    # Size conservatively: a resting order is expected Maker, but reserve Taker
    # at entry for worst-case exchange execution plus Taker+slippage at the stop.
    risk_entry_fee = max(maker, taker)
    per_btc = abs(limit - float(sl)) + limit * risk_entry_fee + float(sl) * (taker + slip)

    account_equity = v136_runtime._live_equity_for(available)
    risk_capital = min(float(s.capital), float(account_equity))
    base_risk = min(float(s.risk_usdt), risk_capital * float(s.risk_pct) / 100.0)
    risk = min(base_risk * multiplier, float(daily_remaining))
    notional_cap = min(float(s.max_notional), risk_capital * float(s.leverage), float(available) * .9 * float(s.leverage))
    quantity = engine.rounded(min(risk / per_btc, notional_cap / limit) / unit, meta["lotSz"])
    if Decimal(quantity) < Decimal(str(meta["minSz"])):
        raise engine.Halt("风险预算不足以满足最小下单量，跳过")
    btc = float(quantity) * unit
    if btc * per_btc > risk + 1e-9 or btc * limit > notional_cap + 1e-9:
        raise engine.Halt("取整后风险超限")

    expected_cost_per_btc = limit * maker + float(tp) * taker
    worst_cost_per_btc = limit * risk_entry_fee + float(tp) * (taker + slip)
    reward_distance = dist * 2.0
    expected_multiple = reward_distance / expected_cost_per_btc if expected_cost_per_btc > 0 else math.inf
    worst_multiple = reward_distance / worst_cost_per_btc if worst_cost_per_btc > 0 else math.inf
    mapped = v136_entry_fix._engine_gate_multiple(expected_multiple)

    entry_fee = btc * limit * maker
    exit_fee = btc * float(tp) * taker
    gross = btc * reward_distance
    expected_cost = entry_fee + exit_fee
    worst_cost = btc * worst_cost_per_btc
    expected_net = gross - expected_cost
    worst_net = gross - worst_cost

    ctx = dict(v137._CURRENT_CONTEXT)
    plan = dict(
        side=side, posSide="long" if buy else "short", exchange_side="buy" if buy else "sell",
        px=str(limit), sz=quantity, sl=sl, tp=tp, tp1=tp, tp2=tp, tp1_sz=quantity, tp2_sz="0",
        btc=btc, notional=btc * limit, estimated_loss=btc * per_btc, position_multiplier=multiplier,
        base_risk_budget=base_risk, effective_risk_budget=risk,
        account_equity=float(account_equity), available_balance=float(available), strategy_capital_cap=float(s.capital),
        risk_capital=risk_capital, atr_15m=float(atr), stop_distance=dist,
        expected_roundtrip_cost=expected_cost, worst_roundtrip_cost=worst_cost,
        expected_fee_multiple=expected_multiple, worst_cost_multiple=worst_multiple,
        cost_multiple=mapped, legacy_engine_cost_multiple=mapped, expected_cost_min_multiple=v138.EXPECTED_COST_MIN,
        tp_full_gross=gross, tp_full_net=expected_net, expected_net_profit=expected_net, worst_net_profit=worst_net,
        reward_r=2.0, breakeven_after_tp1=False,
        entry_fee_budget_bps=float(s.fee_bps), entry_slippage_budget_bps=0.0,
        entry_order_type="limit", market_entry=False,
        v137=True, signal_bar=ctx.get("bar"), signal_tier=ctx.get("tier"), signal_level=ctx.get("level"),
    )
    v136_runtime._LAST_PLAN_DIAG = dict(plan)
    v136_entry_fix._LAST_PLAN = plan
    return plan


def _window_timing(self, market):
    opp = (market or {}).get("opportunity") or {}
    try:
        start_ms = int(opp.get("signal_close_ms") or 0)
        now_ms = int(float(self.market_now()) * 1000.0)
    except Exception:
        return 0, 0, False
    end_ms = start_ms + model.ENTRY_WINDOW_MS
    return start_ms, end_ms, bool(start_ms > 0 and start_ms <= now_ms < end_ms)


def _submit_initial_limit(self, market, score, equity, available, remaining):
    """Submit one passive LIMIT entry and expire it at the end of BOLL minute one."""
    start_ms, end_ms, inside = _window_timing(self, market)
    if not inside:
        self.emit("log", "V1.5.4 BOLL首分钟执行窗口已结束；本信号不再开仓")
        return

    prepared = v138._prepare_order(self, market["side"], score, market, equity, available, remaining)
    if not prepared:
        return
    plan, tier, multiplier, level = prepared
    if not v138._set_leverage(self, plan) or not self.enabled:
        return

    # Network reads above can consume part of the 60-second window. Re-check
    # immediately before the write so a stale signal can never create an order.
    _, end_ms, inside = _window_timing(self, market)
    if not inside:
        self.emit("log", "V1.5.4 开仓准备完成时已超过BOLL首分钟窗口；未提交限价订单")
        return
    self._verify_latest("1m", market["bar"], v138.ONE_MINUTE_STEP)

    now_exchange_ms = int(float(self.market_now()) * 1000.0)
    seconds_left = max(1.0, (end_ms - now_exchange_ms) / 1000.0)
    expires_at = time.time() + seconds_left

    cid = "mac" + uuid.uuid4().hex[:28]
    algo = "br" + uuid.uuid4().hex[:28]
    state = self.store.data
    before_last = state.get("last_bar")
    leg = dict(
        plan, client_id=cid, order_id="", bracket_id=algo, submitted=time.time(), expires=expires_at,
        filled=False, state="pending", cancel_requested=False, cancel_on_reconcile=False,
        tier=tier, score=float(score["total"]), signal_bar=market["bar"], counted=True, protected=False,
        v154=True, version=VERSION, order_type="limit", signal_close_ms=start_ms,
    )
    root = dict(
        plan, client_id=cid, order_id="", bracket_id=algo, submitted=leg["submitted"], expires=expires_at,
        equity_before=equity, filled=False, protected=False, score=float(score["total"]), signal_bar=market["bar"],
        signal_tier=tier, signal_level=level, v137=True, v138=True, v152=True, v153=True, v154=True,
        version=VERSION, build=BUILD, legs=[leg], tier_counts={"1": 0, "2": 0, "3": 0}, highest_tier=tier,
        last_entry_bar=market["bar"], order_type="limit", signal_close_ms=start_ms,
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
        # Ambiguous write remains fail-closed: keep local occupancy until the
        # inherited reconciliation/fault-lock path proves exchange state.
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
        "tier": tier, "score": score["total"], "client_id": cid, "order_id": oid,
        "entry_window_minute": 1, "expires_at": expires_at, "plan": plan,
    })
    self.emit(
        "log",
        f"V1.5.4 {level}：BOLL首分钟内已提交限价{market['side']}；剩余有效约{seconds_left:.0f}秒；"
        f"{multiplier:g}×风险仓位，整仓TP=2R，SL=1×1H ATR",
    )
    self.emit("plan", plan)


def _load_recent_logs(self):
    """Restore the tail of persisted events.log into the visible run-log model."""
    path = self.folder / "events.log"
    if not path.exists():
        return 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_LOG_LINES:]
    except Exception:
        return 0
    restored = []
    for line in lines:
        text = str(line).rstrip()
        if not text:
            continue
        kind = "alarm" if "警报：" in text else "log"
        restored.append((kind, text))
    if restored:
        self.log_lines = restored[-MAX_LOG_LINES:]
    return len(restored)


def _render_logs_fixed(self):
    """Deterministic log render that preserves the reader's scroll position."""
    try:
        view = self.log.yview()
        at_bottom = not view or float(view[1]) >= 0.995
        top = float(view[0]) if view else 0.0
    except Exception:
        at_bottom, top = True, 0.0

    current_filter = self.log_filter.get()
    try:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        for kind, line in self.log_lines[-MAX_LOG_LINES:]:
            if current_filter == "全部" or kind == "alarm":
                self.log.insert("end", str(line) + "\n", kind)
    finally:
        try:
            self.log.configure(state="disabled")
        except Exception:
            pass

    if at_bottom:
        try:
            self.log.see("end")
        except Exception:
            pass
    else:
        try:
            self.log.yview_moveto(top)
        except Exception:
            pass
    self._v154_log_source_len = len(self.log_lines)
    self._v154_log_filter = current_filter


def _rewrite_limit_text(text):
    if not isinstance(text, str):
        return text
    replacements = (
        ("首分钟市价执行", "首分钟限价执行"),
        ("计划市价参考", "计划限价"),
        ("实际市价参考", "实际限价"),
        ("市价开仓/平仓 Taker 0.05%   ·   滑点预算 0.05%", "限价开仓 Maker 0.02%   ·   市价平仓 Taker 0.05%   ·   平仓滑点预算 0.05%"),
        ("市价开仓按Taker+滑点预算", "限价开仓按Maker计费，市价退出按Taker+滑点预算"),
        ("市价开仓", "限价开仓"),
    )
    out = text
    for old, new in replacements:
        out = out.replace(old, new)
    return out


def apply():
    if getattr(engine.Engine, "_kaytrade_v154_limit_log_fix_applied", False):
        return

    # Restore LIMIT economics and LIMIT submission, while the shared V1.5.4
    # model still enforces ENTRY_WINDOW_MS == 60_000.
    v137._v137_make_plan = _v154_limit_make_plan
    v138._submit_initial = _submit_initial_limit

    # Replace the fragile incremental renderer. New events still originate from
    # App.drain and continue to be appended to events.log as before.
    app.App.render_logs = _render_logs_fixed

    previous_app_init = app.App.__init__

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        try:
            self.root.title("KAYTRADE 1.5.4 · BTC 策略控制台 · 修复版")
        except Exception:
            pass
        _load_recent_logs(self)
        try:
            self.render_logs()
        except Exception:
            pass
        for widget in v138._widgets(self.root):
            try:
                text = str(widget.cget("text") or "")
            except Exception:
                continue
            if not text:
                continue
            rewritten = _rewrite_limit_text(text)
            try:
                if rewritten != text:
                    widget.configure(text=rewritten)
            except Exception:
                pass
        try:
            current = str(self.signal.get() or "")
            self.signal.set(_rewrite_limit_text(current))
        except Exception:
            pass
        self._v154_log_fix_ready = True

    app.App.__init__ = app_init
    engine.Engine._kaytrade_v154_limit_log_fix_applied = True
    app.App._kaytrade_v154_log_fix_applied = True


apply()

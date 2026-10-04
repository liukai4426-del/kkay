"""KAYTRADE V1.7.2 AI Only overlay.

V1.7.2 disables every local strategy entry path. The runtime may reconcile
existing positions, but a NEW order can only originate from submit_ai_trade().

Security model:
- bridge binds to 127.0.0.1 only;
- a random bearer token is written to a mode-0600 bridge descriptor;
- OKX credentials stay inside the KAYTRADE process and are never returned;
- autonomous AI execution is allowed only when the GUI is connected to
  OKX Demo Trading. Live-account AI writes are rejected.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from v171_update_patch import apply as apply_previous
apply_previous()

import app
import engine as legacy_engine
import v165_model as model
import v165_ui_patch as ui165
import v165_update_patch as runtime
import v166_ui_patch as ui166
import v168_update_patch as v168
import v168_build1681_patch as b1681
import v170_update_patch as v170
import v170_build1701_patch as v1701
import v171_update_patch as v171
import v165_keepawake_patch as keepawake
import visual
from core import INSTRUMENT
from exchange import APIError

VERSION = "1.7.2"
BUILD = "1722"
AI_ONLY = True

AI_BRIDGE_HOST = "127.0.0.1"
AI_BRIDGE_PORT = int(os.getenv("KAYTRADE_AI_BRIDGE_PORT", "17872"))
AI_MAX_LEVERAGE = 5
AI_MAX_NOTIONAL_USDT = Decimal(os.getenv("KAYTRADE_AI_MAX_NOTIONAL_USDT", "100"))
AI_MAX_ESTIMATED_STOP_LOSS_USDT = Decimal(os.getenv("KAYTRADE_AI_MAX_STOP_LOSS_USDT", "5"))
AI_MIN_ORDER_INTERVAL_SEC = float(os.getenv("KAYTRADE_AI_MIN_ORDER_INTERVAL_SEC", "5"))
AI_BRIDGE_FILENAME = "ai_bridge.json"


@dataclass(frozen=True)
class AIOnlySettings:
    """Execution-only safety settings; no indicator/score strategy fields exist."""

    leverage: int = AI_MAX_LEVERAGE
    consecutive_losses: int = 999999


def _decimal(value, name):
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise legacy_engine.Halt(f"{name} 无效") from None
    if not out.is_finite() or out <= 0:
        raise legacy_engine.Halt(f"{name} 必须为有限正数")
    return out


def _client_id(prefix="ai"):
    return prefix + uuid.uuid4().hex[:28]


def _clean_active(active):
    """Return a credential-free state snapshot suitable for the local AI bridge."""
    if not isinstance(active, dict):
        return None
    allowed = (
        "proposal_id", "side", "posSide", "px", "sz", "sl", "tp",
        "submitted", "filled", "order_id", "reason", "close_id",
    )
    return {k: active.get(k) for k in allowed if k in active}


class AIOnlyEngine(legacy_engine.Engine):
    """Execution engine whose only NEW-entry source is submit_ai_trade()."""

    def __init__(self, exchange, folder, emit=lambda kind, data: None):
        super().__init__(exchange, folder, emit)
        self.settings = AIOnlySettings()
        self._ai_lock = threading.RLock()
        self._ai_last_submit = 0.0

    def connect(self):
        super().connect()
        self.emit("log", "V1.7.2 AI Only：本地指标/评分/方向策略已停用；仅接受AI结构化交易请求")

    def refresh_market(self):
        """No candles, indicators, or strategy evaluation in V1.7.2."""
        ticker = self.x.ticker()
        self.market = {"last": float(ticker["last"]), "at": time.time()}
        self.market_at = time.time()
        self.market_monotonic = time.monotonic()
        self.emit("ticker", ticker)
        return self.market

    def _preflight_account(self):
        if not self.store:
            raise legacy_engine.Halt("先测试连接")
        if not self.x.demo:
            raise legacy_engine.Halt("V1.7.2 AI Only 自动AI下单仅允许 OKX 模拟盘；真实账户写入已硬禁用")
        if self.store.data.get("halt"):
            reason = legacy_engine.normalize_halt_reason(self.store.data.get("halt"))
            raise legacy_engine.Halt(legacy_engine.HALT_PREFIX + reason + legacy_engine.HALT_SUFFIX)
        account = self.x.account()
        if account.get("uid") != self.connection_id:
            raise legacy_engine.Halt("账户标识发生变化")
        if account.get("posMode") != "long_short_mode":
            raise legacy_engine.Halt("AI Only 要求 OKX 双向持仓模式")
        perms = set(str(account.get("perm") or "").split(","))
        if "trade" not in perms or "withdraw" in perms:
            raise legacy_engine.Halt("API必须有交易权限且不得有提币权限")
        return account

    def _clear_known_v172_activation_lock(self):
        """Migrate only the known Build1720 dict/stop_atr startup bug lock."""
        if not self.store:
            return
        reason = str(self.store.data.get("halt") or "")
        if "stop_atr" in reason and "dict" in reason:
            self.store.data["halt"] = ""
            self.store.save()
            self.emit(
                "log",
                "V1.7.2 Build1722：已清理Build1720启用AI通道时产生的 stop_atr 兼容故障锁；未改变任何仓位/订单。",
            )

    def arm(self, _settings=None):
        with self._ai_lock:
            self._clear_known_v172_activation_lock()
            self._preflight_account()
            if not self.store.data.get("active") and (self.x.positions() or self.x.orders() or self.x.algos()):
                raise legacy_engine.Halt("BTC存在非KAYTRADE管理的仓位或挂单；AI执行通道拒绝启动")
            self.settings = AIOnlySettings()
            self.enabled = True
            self.stopped = False
            self.poll_at = time.monotonic()
            self.emit(
                "log",
                "AI执行通道已启用：无BOLL/EMA/RSI/MACD/评分/Gate/PEE4；新仓唯一入口=submit_ai_trade；OKX模拟盘自动执行",
            )

    def stop(self):
        with self._ai_lock:
            self.enabled = False
            self.stopped = True
            self.emit("log", "AI执行通道已停止；不再接受新的AI交易请求，已有仓位继续核对")

    def cycle(self):
        """Reconciliation only. Never evaluates market direction or creates an order."""
        with self._ai_lock:
            self.poll_at = time.monotonic()
            if not self.store:
                return
            if self.store.data.get("active"):
                self.reconcile()

    def ai_state(self):
        with self._ai_lock:
            if not self.store:
                return {
                    "version": VERSION,
                    "build": BUILD,
                    "mode": "AI_ONLY",
                    "connected": False,
                    "ai_enabled": False,
                    "live_ai_writes": False,
                }
            ticker = self.x.ticker()
            equity, available = self.x.balance()
            return {
                "version": VERSION,
                "build": BUILD,
                "mode": "AI_ONLY",
                "connected": True,
                "environment": "OKX_DEMO" if self.x.demo else "OKX_LIVE",
                "ai_enabled": bool(self.enabled),
                "live_ai_writes": False,
                "instrument": INSTRUMENT,
                "ticker": {
                    "last": ticker.get("last"),
                    "bidPx": ticker.get("bidPx"),
                    "askPx": ticker.get("askPx"),
                    "ts": ticker.get("ts"),
                },
                "equity_usdt": equity,
                "available_usdt": available,
                "positions": self.x.positions(),
                "active": _clean_active(self.store.data.get("active")),
                "limits": {
                    "max_leverage": AI_MAX_LEVERAGE,
                    "max_notional_usdt": str(AI_MAX_NOTIONAL_USDT),
                    "max_estimated_stop_loss_usdt": str(AI_MAX_ESTIMATED_STOP_LOSS_USDT),
                },
            }

    def _remember_result(self, proposal_id, result):
        bucket = self.store.data.setdefault("ai_results", {})
        bucket[proposal_id] = result
        while len(bucket) > 100:
            bucket.pop(next(iter(bucket)))
        self.store.save()

    def _existing_result(self, proposal_id):
        return (self.store.data.get("ai_results") or {}).get(proposal_id)

    def _validate_open(self, proposal):
        if self.x.positions() or self.x.orders() or self.x.algos():
            raise legacy_engine.Halt("已有BTC仓位或挂单；AI Only 单仓模式拒绝新开仓")

        direction = str(proposal.get("direction") or "").lower()
        if direction not in ("long", "short"):
            raise legacy_engine.Halt("direction 必须为 long 或 short")

        order_type = str(proposal.get("order_type") or "market").lower()
        if order_type != "market":
            raise legacy_engine.Halt("V1.7.2 AI Only 首版只允许 market 开仓")

        qty = _decimal(proposal.get("size"), "size")
        leverage = int(proposal.get("leverage", AI_MAX_LEVERAGE))
        if not 1 <= leverage <= AI_MAX_LEVERAGE:
            raise legacy_engine.Halt(f"leverage 必须在 1—{AI_MAX_LEVERAGE}")

        ticker = self.x.ticker()
        last = _decimal(ticker.get("last"), "OKX last")
        tp = _decimal(proposal.get("take_profit"), "take_profit")
        sl = _decimal(proposal.get("stop_loss"), "stop_loss")

        if direction == "long" and not (sl < last < tp):
            raise legacy_engine.Halt("LONG 必须满足 stop_loss < 当前价 < take_profit")
        if direction == "short" and not (tp < last < sl):
            raise legacy_engine.Halt("SHORT 必须满足 take_profit < 当前价 < stop_loss")

        meta = self.x.instrument()
        minimum = _decimal(meta.get("minSz"), "minSz")
        lot = _decimal(meta.get("lotSz"), "lotSz")
        if qty < minimum or qty % lot != 0:
            raise legacy_engine.Halt(f"size 必须≥{minimum} 且按 lotSz={lot} 递增")

        unit = _decimal(meta.get("ctVal"), "ctVal") * _decimal(meta.get("ctMult") or "1", "ctMult")
        notional = qty * unit * last
        if notional > AI_MAX_NOTIONAL_USDT:
            raise legacy_engine.Halt(
                f"名义仓位 {notional:.4f} USDT 超过 AI Only 上限 {AI_MAX_NOTIONAL_USDT} USDT"
            )

        equity, available = self.x.balance()
        account_cap = Decimal(str(available)) * Decimal(leverage) * Decimal("0.90")
        if notional > account_cap:
            raise legacy_engine.Halt("可用保证金不足以覆盖该AI交易请求")

        estimated_loss = qty * unit * abs(last - sl) + notional * Decimal("0.0012")
        if estimated_loss > AI_MAX_ESTIMATED_STOP_LOSS_USDT:
            raise legacy_engine.Halt(
                f"估算止损损失 {estimated_loss:.4f} USDT 超过 AI Only 上限 "
                f"{AI_MAX_ESTIMATED_STOP_LOSS_USDT} USDT"
            )

        return {
            "direction": direction,
            "qty": qty,
            "leverage": leverage,
            "last": last,
            "tp": tp,
            "sl": sl,
            "meta": meta,
            "equity": equity,
            "notional": notional,
            "estimated_loss": estimated_loss,
            "unit": unit,
        }

    def _set_leverage(self, pos_side, leverage):
        self.x.post(
            "/api/v5/account/set-leverage",
            {
                "instId": INSTRUMENT,
                "lever": str(leverage),
                "mgnMode": "isolated",
                "posSide": pos_side,
            },
        )
        rows = self.x.get(
            "/api/v5/account/leverage-info",
            {"instId": INSTRUMENT, "mgnMode": "isolated"},
            True,
        )
        ok = any(
            row.get("posSide") == pos_side
            and row.get("mgnMode") == "isolated"
            and abs(float(row.get("lever") or 0) - leverage) < 1e-9
            for row in rows
        )
        if not ok:
            raise legacy_engine.Halt("逐仓杠杆回读不一致")

    def _submit_open(self, proposal, proposal_id):
        validated = self._validate_open(proposal)
        direction = validated["direction"]
        pos_side = direction
        exchange_side = "buy" if direction == "long" else "sell"
        qty = validated["qty"]
        tp = validated["tp"]
        sl = validated["sl"]
        last = validated["last"]
        leverage = validated["leverage"]

        self._set_leverage(pos_side, leverage)

        cid = _client_id("ai")
        tp_id = _client_id("tp")
        sl_id = _client_id("sl")
        reason = str(proposal.get("reason") or "").strip()[:1000]

        active = {
            "ai_only": True,
            "proposal_id": proposal_id,
            "reason": reason,
            "side": "做多" if direction == "long" else "做空",
            "posSide": pos_side,
            "exchange_side": exchange_side,
            "px": str(last),
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
        }

        self.store.data["active"] = active
        self.store.save()

        body = {
            "instId": INSTRUMENT,
            "tdMode": "isolated",
            "side": exchange_side,
            "posSide": pos_side,
            "ordType": "market",
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
        active["okx_ack_at"] = time.time()
        self.store.save()
        self.store.record("V1.7.2 AI提交开仓请求", active)
        self.emit(
            "log",
            f"AI交易请求已提交OKX模拟盘：{active['side']} · market · {active['sz']}张 · "
            f"TP {active['tp']} · SL {active['sl']} · proposal={proposal_id}",
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
            "size": active["sz"],
            "reference_price": active["px"],
            "take_profit": active["tp"],
            "stop_loss": active["sl"],
            "notional_usdt": active["notional"],
            "estimated_stop_loss_usdt": active["estimated_loss"],
        }

    def _submit_close(self, proposal, proposal_id):
        active = self.store.data.get("active")
        if not isinstance(active, dict):
            raise legacy_engine.Halt("没有KAYTRADE管理中的仓位可供AI平仓")
        positions = self.x.positions()
        if len(positions) != 1:
            raise legacy_engine.Halt("当前BTC仓位不唯一；拒绝AI自动平仓")
        pos = positions[0]
        if pos.get("mgnMode") != "isolated" or pos.get("posSide") != active.get("posSide"):
            raise legacy_engine.Halt("交易所仓位与KAYTRADE记录不一致；拒绝AI自动平仓")
        if active.get("close_id"):
            raise legacy_engine.Halt("已有平仓请求，禁止重复发送")

        requested_direction = str(proposal.get("direction") or active.get("posSide") or "").lower()
        if requested_direction != active.get("posSide"):
            raise legacy_engine.Halt("AI平仓方向与当前仓位不一致")

        size = abs(float(pos.get("pos") or 0))
        if size <= 0 or size > float(active.get("sz") or 0) + 1e-10:
            raise legacy_engine.Halt("平仓数量与KAYTRADE记录不一致")

        close_id = _client_id("ac")
        active["close_id"] = close_id
        active["close_proposal_id"] = proposal_id
        active["close_reason"] = str(proposal.get("reason") or "").strip()[:1000]
        active["close_requested"] = time.time()
        self.store.save()

        self.x.post(
            "/api/v5/trade/order",
            {
                "instId": INSTRUMENT,
                "tdMode": "isolated",
                "posSide": active["posSide"],
                "side": "sell" if active["posSide"] == "long" else "buy",
                "ordType": "market",
                "sz": str(size),
                "clOrdId": close_id,
            },
        )
        self.store.record(
            "V1.7.2 AI提交平仓请求",
            {"proposal_id": proposal_id, "client_id": active.get("client_id"), "close_id": close_id, "size": size},
        )
        self.emit("log", f"AI平仓请求已提交OKX模拟盘：{active['posSide']} {size:g}张 · proposal={proposal_id}")
        return {
            "version": VERSION,
            "build": BUILD,
            "mode": "AI_ONLY",
            "environment": "OKX_DEMO",
            "status": "EXECUTED",
            "proposal_id": proposal_id,
            "close_client_order_id": close_id,
            "closed_direction": active["posSide"],
            "size": size,
        }

    def submit_ai_trade(self, proposal):
        with self._ai_lock:
            if not isinstance(proposal, dict):
                raise legacy_engine.Halt("AI交易请求必须为JSON对象")
            self._preflight_account()
            if not self.enabled:
                raise legacy_engine.Halt("AI执行通道未启用；请先在KAYTRADE界面启用")

            proposal_id = str(proposal.get("proposal_id") or uuid.uuid4().hex).strip()
            if not proposal_id or len(proposal_id) > 64:
                raise legacy_engine.Halt("proposal_id 无效")
            existing = self._existing_result(proposal_id)
            if existing is not None:
                return dict(existing, duplicate=True)

            now = time.monotonic()
            if now - self._ai_last_submit < AI_MIN_ORDER_INTERVAL_SEC:
                raise legacy_engine.Halt("AI交易请求过快；冷却时间尚未结束")

            action = str(proposal.get("action") or "").lower()
            if action not in ("open", "close"):
                raise legacy_engine.Halt("action 必须为 open 或 close")

            self._ai_last_submit = now
            result = self._submit_open(proposal, proposal_id) if action == "open" else self._submit_close(proposal, proposal_id)
            self._remember_result(proposal_id, result)
            return result

    def reconcile(self):
        with self._ai_lock:
            state = self.store.data
            active = state.get("active")
            if not active:
                return
            if not active.get("ai_only"):
                return super().reconcile()

            order = self._lookup_parent_order(active)
            if order is None:
                return

            status = str(order.get("state") or "")
            filled = float(order.get("accFillSz") or 0)
            positions = self.x.positions()

            if any(
                p.get("mgnMode") != "isolated"
                or p.get("posSide") != active["posSide"]
                or abs(float(p.get("pos") or 0)) > float(active["sz"]) + 1e-10
                for p in positions
            ):
                raise legacy_engine.Halt("AI Only仓位与本地记录不一致，请立即到OKX核对")

            if status in ("live", "partially_filled"):
                if time.time() - float(active.get("submitted") or time.time()) > 15:
                    raise legacy_engine.Halt("AI market订单15秒后仍未确认完成；禁止重复提交，请到OKX核对")
                return

            if status == "canceled" and filled <= 0:
                if positions:
                    raise legacy_engine.Halt("AI订单已取消但交易所仍有仓位")
                state["active"] = None
                self.store.save()
                self.store.record("V1.7.2 AI开仓未成交", active)
                self.emit("log", "AI market开仓未成交/已取消；本地活动记录已清理")
                return

            if status not in ("filled", "canceled"):
                if time.time() - float(active.get("submitted") or time.time()) > 15:
                    raise legacy_engine.Halt("AI订单状态长时间不确定；禁止重复提交")
                return

            if filled <= 0:
                raise legacy_engine.Halt("AI订单成交状态异常")

            if not active.get("filled"):
                active["filled"] = True
                active["filled_sz"] = filled
                active["filled_at"] = time.time()
                self.store.save()

            if not positions:
                if time.time() - float(active.get("filled_at") or active.get("submitted") or time.time()) < 5:
                    return
                equity, _ = self.x.balance()
                pnl = equity - float(active["equity_before"])
                state["active"] = None
                state["last_close"] = time.time()
                self.store.save()
                self.store.record(
                    "V1.7.2仓位归零",
                    {
                        "client_id": active["client_id"],
                        "equity_change": pnl,
                        "side": active["side"],
                        "px": active["px"],
                        "sz": active["sz"],
                        "proposal_id": active.get("proposal_id"),
                    },
                )
                self.emit("log", f"AI Only仓位已归零；本轮USDT净权益变化 {pnl:+.4f}")
                return

            if len(positions) != 1:
                raise legacy_engine.Halt("AI Only检测到多个BTC仓位；停止AI执行并人工核对")

            algos = self.x.algos()

            def live(cid):
                return next(
                    (
                        a
                        for a in algos
                        if a.get("algoClOrdId") == cid
                        and a.get("state") == "live"
                        and a.get("posSide") == active["posSide"]
                        and a.get("tdMode") == "isolated"
                    ),
                    None,
                )

            tp = live(active["tp_id"])
            sl = live(active["sl_id"])
            tp_ok = bool(
                tp
                and float(tp.get("tpTriggerPx") or 0) == float(active["tp"])
                and str(tp.get("tpOrdPx") or "") == "-1"
            )
            sl_ok = bool(
                sl
                and float(sl.get("slTriggerPx") or 0) == float(active["sl"])
                and str(sl.get("slOrdPx") or "") == "-1"
            )
            if not (tp_ok and sl_ok):
                grace = float(active.get("filled_at") or active.get("submitted") or time.time())
                if time.time() - grace > 15:
                    raise legacy_engine.Halt("AI Only附带TP/SL无法核实；已停止AI执行，请立即到OKX核对")
            else:
                if not active.get("protected"):
                    active["protected"] = True
                    self.store.save()
                    self.emit("log", "已核对AI Only仓位：OKX交易所TP/SL保护单有效")
            self.emit("position", positions)


class _BridgeHandler(BaseHTTPRequestHandler):
    server_version = "KayTradeAI/1.7.2"

    def log_message(self, _format, *_args):
        return

    @property
    def bridge(self):
        return self.server.bridge

    def _json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self):
        return self.headers.get("Authorization", "") == f"Bearer {self.bridge.token}"

    def _require_auth(self):
        if self._authorized():
            return True
        self._json(401, {"error": "unauthorized"})
        return False

    def do_GET(self):
        if not self._require_auth():
            return
        if self.path == "/health":
            self._json(
                200,
                {
                    "name": "KAYTRADE",
                    "version": VERSION,
                    "build": BUILD,
                    "mode": "AI_ONLY",
                    "live_ai_writes": False,
                },
            )
            return
        if self.path == "/v1/state":
            try:
                engine = self.bridge.owner.engine
                payload = engine.ai_state() if engine else {
                    "version": VERSION,
                    "build": BUILD,
                    "mode": "AI_ONLY",
                    "connected": False,
                    "ai_enabled": False,
                    "live_ai_writes": False,
                }
                self._json(200, payload)
            except Exception as exc:
                self._json(409, {"error": str(exc)})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        if not self._require_auth():
            return
        if self.path != "/v1/trade":
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 65536:
                raise ValueError("invalid content length")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            engine = self.bridge.owner.engine
            if engine is None:
                raise legacy_engine.Halt("KAYTRADE尚未连接OKX")
            result = engine.submit_ai_trade(payload)
            self._json(200, result)
        except legacy_engine.Halt as exc:
            self._json(409, {"error": str(exc)})
        except APIError as exc:
            self._json(502, {"error": str(exc), "code": getattr(exc, "code", "")})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


class AIBridgeServer:
    def __init__(self, owner):
        self.owner = owner
        self.token = secrets.token_urlsafe(32)
        self.httpd = None
        self.thread = None
        self.path = Path(owner.folder) / AI_BRIDGE_FILENAME

    def start(self):
        port = AI_BRIDGE_PORT
        try:
            httpd = ThreadingHTTPServer((AI_BRIDGE_HOST, port), _BridgeHandler)
        except OSError:
            httpd = ThreadingHTTPServer((AI_BRIDGE_HOST, 0), _BridgeHandler)
        httpd.bridge = self
        self.httpd = httpd
        actual_port = int(httpd.server_address[1])
        descriptor = {
            "version": VERSION,
            "build": BUILD,
            "mode": "AI_ONLY",
            "host": AI_BRIDGE_HOST,
            "port": actual_port,
            "token": self.token,
            "live_ai_writes": False,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(descriptor, handle, ensure_ascii=False, indent=2)
        self.thread = threading.Thread(target=httpd.serve_forever, name="kaytrade-ai-bridge", daemon=True)
        self.thread.start()
        self.owner.emit("log", f"AI Bridge已启动：127.0.0.1:{actual_port} · Bearer令牌仅保存在本机0600文件")

    def stop(self):
        httpd = self.httpd
        self.httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
            finally:
                httpd.server_close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_EMIT = app.App.emit
_PREVIOUS_APP_QUIT = app.App.quit


def _rewrite_ui_text(data):
    if not isinstance(data, str):
        return data
    text = data.replace("V1.7.1", "V1.7.2").replace("Build1710", "Build1722")
    if "开始加载1D / 4H / 1H / 15m / 5m指标历史K线" in text:
        return "V1.7.2 AI Only：策略K线/指标计算已停用；仅同步OKX实时行情与账户状态"
    if text.startswith("全自动运行 / "):
        return text.replace("全自动运行 / ", "AI Only运行 / ", 1)
    return text


def _app_emit_v172(self, kind, data):
    return _PREVIOUS_APP_EMIT(self, kind, _rewrite_ui_text(data))


def _widget_text(widget):
    parts = []
    try:
        value = widget.cget("text")
        if value:
            parts.append(str(value))
    except Exception:
        pass
    value = getattr(widget, "text", None)
    if value:
        parts.append(str(value))
    try:
        for child in widget.winfo_children():
            parts.append(_widget_text(child))
    except Exception:
        pass
    return " ".join(parts)


def _hide_legacy_strategy_tabs(owner):
    book = getattr(owner, "book", None)
    if book is not None:
        # visual.Tabs order: connection, risk, execution, overview, history.
        for index in (1, 2):
            try:
                book.buttons[index].pack_forget()
            except Exception:
                pass
            try:
                book.pages[index].pack_forget()
            except Exception:
                pass
        try:
            if book.active in (1, 2):
                book.select(0)
        except Exception:
            pass


def _install_ai_only_dashboard(owner):
    """Remove V1.7.1 strategy surfaces and add an execution-only AI status card."""
    book = getattr(owner, "book", None)
    try:
        dash = book.pages[3].body
    except Exception:
        return

    # Hide the old LONG/SHORT score cards, ATR plan, score/indicator tabs and PEE4.
    for child in list(dash.winfo_children()):
        text = _widget_text(child)
        if any(token in text for token in (
            "做多 / LONG",
            "交易计划",
            "评分明细",
            "规则策略 · 未调用GPT/Gemini",
        )):
            try:
                child.pack_forget()
            except Exception:
                pass

    pee4 = getattr(owner, "_v170_pee4_card", None)
    if pee4 is not None:
        try:
            pee4.pack_forget()
        except Exception:
            pass

    if getattr(owner, "_v172_ai_card", None) is not None:
        return

    card = visual.Card(dash, height=220)
    actions = getattr(getattr(owner, "trade_button", None), "master", None)
    kwargs = dict(fill="x", pady=(0, 12))
    if actions is not None:
        kwargs["before"] = actions
    card.pack(**kwargs)

    visual.label(
        card.body,
        text="AI ONLY 执行状态",
        size=18,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL,
    ).pack(anchor="w")
    visual.label(
        card.body,
        text="V1.7.2 Build1722 · 本地技术指标策略已断开，新仓只接受 Codex / AI 结构化交易请求",
        size=10,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(3, 12))

    owner._v172_ai_channel_var = app.tk.StringVar(value="AI交易通道：未启用")
    owner._v172_ai_bridge_var = app.tk.StringVar(value="AI Bridge：本机 127.0.0.1 · READY")
    owner._v172_ai_env_var = app.tk.StringVar(value="执行环境：仅 OKX 模拟盘")
    owner._v172_ai_limits_var = app.tk.StringVar(
        value=f"硬风控：≤{AI_MAX_LEVERAGE}× · 名义仓位≤{AI_MAX_NOTIONAL_USDT} USDT · 估算止损≤{AI_MAX_ESTIMATED_STOP_LOSS_USDT} USDT"
    )

    for var, color in (
        (owner._v172_ai_channel_var, visual.GREEN),
        (owner._v172_ai_bridge_var, visual.TEXT),
        (owner._v172_ai_env_var, visual.TEXT),
        (owner._v172_ai_limits_var, visual.MUTED),
    ):
        visual.label(card.body, variable=var, size=11, color=color, bg=visual.PANEL).pack(
            anchor="w", pady=2
        )
    owner._v172_ai_card = card


def _set_ai_channel_ui(owner, enabled):
    var = getattr(owner, "_v172_ai_channel_var", None)
    if var is not None:
        var.set("AI交易通道：已启用 · 等待AI请求" if enabled else "AI交易通道：未启用")


def _ensure_ai_only_engine(owner):
    """Tolerate frozen-module class identity differences without entering legacy arm()."""
    e = getattr(owner, "engine", None)
    if e is None:
        raise legacy_engine.Halt("先连接OKX模拟盘")

    # Frozen/overlay builds can produce an equivalent AIOnlyEngine object whose
    # class identity is not identical to this module's class object.
    if callable(getattr(e, "submit_ai_trade", None)) and hasattr(e, "_ai_lock"):
        return e

    # If the connection was created with an older Engine object, rebuild only
    # the runtime wrapper around the same Exchange connection. This does not
    # copy credentials out of the process and does not enter the legacy
    # Settings/stop_atr strategy arm chain.
    x = getattr(e, "x", None)
    if x is None:
        raise legacy_engine.Halt("当前连接缺少Exchange对象，无法切换到AI Only Runtime")

    replacement = AIOnlyEngine(x, owner.folder, owner.emit)
    replacement.connect()
    owner.engine = replacement
    owner.emit("log", "Build1722：已将当前连接切换为AIOnlyEngine Runtime")
    return replacement


def _run_ai_enable(owner):
    """Dedicated AI-only enable path; never enters legacy worker kind='arm'."""
    try:
        e = _ensure_ai_only_engine(owner)
        e.arm(None)
        try:
            keepawake._start_keepawake(owner)
        except Exception:
            pass
        _set_ai_channel_ui(owner, True)
        owner.emit("status", "AI Only运行 / OKX模拟盘")
        owner.emit(
            "log",
            "V1.7.2 Build1722 AI交易通道已启用：启动流程未调用旧Settings/stop_atr/技术指标策略。",
        )
    except Exception as exc:
        e = getattr(owner, "engine", None)
        if e is not None:
            e.enabled = False
        _set_ai_channel_ui(owner, False)
        owner.emit("alarm", "启用AI交易通道失败：" + str(exc))
    finally:
        owner.emit("done", None)


def _start_ai_enable(owner):
    if getattr(owner, "busy", False):
        app.messagebox.showinfo("处理中", "请等待当前操作完成")
        return
    owner.busy = True
    thread = threading.Thread(
        target=_run_ai_enable,
        args=(owner,),
        name="kaytrade-ai-enable",
        daemon=True,
    )
    owner._v172_ai_enable_thread = thread
    thread.start()


def _app_init_v172(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    self.root.title("KAYTRADE 1.7.2 · AI ONLY · Build 1722")
    try:
        self.signal.set("V1.7.2 AI ONLY｜无本地交易策略｜等待 Codex / AI 提交结构化交易请求")
    except Exception:
        pass
    label = getattr(self, "_v170_version_label", None)
    if label is not None:
        try:
            label.configure(text="BTC / USDT   ·   V1.7.2 AI ONLY")
        except Exception:
            pass
    _hide_legacy_strategy_tabs(self)
    _install_ai_only_dashboard(self)
    self._v172_ai_only_ready = True
    self.ai_bridge = AIBridgeServer(self)
    self.ai_bridge.start()
    try:
        self.update_trade_button()
    except Exception:
        pass


def _app_arm_v172(self):
    if not self.engine:
        app.messagebox.showerror("未连接", "先测试连接")
        return
    if self.network_paused:
        app.messagebox.showerror("网络暂停", "等待网络恢复并核对账户后再启用AI通道")
        return
    if not self.engine.x.demo:
        app.messagebox.showerror(
            "AI实盘已禁用",
            "V1.7.2 AI Only 自动AI执行只允许 OKX模拟盘；真实账户不会接受AI写入。",
        )
        return
    typed = app.simpledialog.askstring(
        "启用AI交易通道",
        "V1.7.2 AI ONLY\n\n"
        "本地BOLL / EMA / RSI / 评分 / Gate 全部不参与开仓。\n"
        "启用后，Codex/AI 可通过本机AI Bridge提交结构化交易请求，"
        "KayTrade完成硬风控后直接发送到 OKX模拟盘。\n"
        f"硬上限：杠杆≤{AI_MAX_LEVERAGE}x；名义仓位≤{AI_MAX_NOTIONAL_USDT} USDT；"
        f"估算止损损失≤{AI_MAX_ESTIMATED_STOP_LOSS_USDT} USDT。\n\n"
        "确认启用请输入 AI",
        parent=self.root,
    )
    if typed and typed.strip().upper() == "AI":
        _start_ai_enable(self)


def _update_trade_button_v172(self):
    button = getattr(self, "trade_button", None)
    if not button:
        return
    running = bool(self.engine and self.engine.enabled)
    _set_ai_channel_ui(self, running)
    button.configure(
        text="■  停止AI交易通道" if running else "▶  启用AI交易通道",
        variant="danger" if running else "accent",
    )


def _app_quit_v172(self):
    _PREVIOUS_APP_QUIT(self)
    if self.finished.is_set():
        bridge = getattr(self, "ai_bridge", None)
        if bridge is not None:
            bridge.stop()


def apply():
    if getattr(model, "_kaytrade_v172_ai_only_applied", False):
        return

    for module in (model, runtime, v168, b1681, v170, v1701, v171):
        module.VERSION = VERSION
        module.BUILD = BUILD
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.7.2 · AI ONLY · Build 1722"

    model.STRATEGY_ENABLED = False
    model.AI_ONLY = True

    app.Engine = AIOnlyEngine
    legacy_engine.AIOnlyEngine = AIOnlyEngine

    app.App.__init__ = _app_init_v172
    app.App.emit = _app_emit_v172
    app.App.arm = _app_arm_v172
    app.App.update_trade_button = _update_trade_button_v172
    app.App.quit = _app_quit_v172

    model._kaytrade_v172_ai_only_applied = True
    app.App._kaytrade_v172_ai_only_applied = True


apply()

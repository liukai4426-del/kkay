"""KAYTRADE V1.8.3 OKX Demo Execution overlay.

V1.8.3 removes local Paper matching. AI plans are sent immediately to OKX
Demo Trading as LIMIT orders. Order/fill state is reconciled from OKX only.

Execution contract:
- OKX Demo only; live-account writes remain hard-disabled.
- LIMIT ONLY for entry, TP, SL and reductions.
- AI plan auto execution defaults ON once the Demo AI channel is enabled.
- Tier 1 + Tier 2 may coexist only in the same direction.
- TP and SL are mandatory and attached to the exchange entry order.
- Unfilled entry remainder is canceled after 60 minutes.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from decimal import Decimal

from v180_ai_only_patch import apply as apply_previous
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
import visual
from core import INSTRUMENT
from exchange import APIError

VERSION = "1.8.3"
BUILD = "1831"
AI_ONLY = True
DEMO_EXECUTION = True
LIMIT_ONLY = True

AI_MAX_LEVERAGE = Decimal("20")
AI_MAX_NOTIONAL_USDT = Decimal("3500")
AI_MAX_ESTIMATED_STOP_LOSS_USDT = Decimal("100")
ENTRY_TTL_SEC = 60 * 60


def _d(value, name="value"):
    return v172._decimal(value, name)


def _now():
    return time.time()


def _active(item):
    if not isinstance(item, dict):
        return False
    return str(item.get("status") or "") in {
        "submitting",
        "submitted",
        "live",
        "partially_filled",
        "filled",
        "cancel_requested",
        "amend_requested",
        "close_live",
    }


def _position_size(item):
    if not isinstance(item, dict):
        return 0.0
    return max(
        0.0,
        float(item.get("filled_size") or 0)
        - float(item.get("closed_size") or 0),
    )


def _fmt_order_type(_value):
    return "限价"


class OKXDemoEngineV183(v180.AIOnlyEngineV180):
    """Exchange-backed AI engine. All writes are hard-limited to OKX Demo."""

    def connect(self):
        super().connect()
        self._purge_removed_paper_state()
        self._ensure_demo_state()
        self.emit(
            "log",
            "V1.8.3 Build1831 Clean：仅OKX模拟盘交易所执行；本地Paper Runtime/撮合/API已从当前程序删除。",
        )

    def _purge_removed_paper_state(self):
        """One-way migration: delete obsolete local Paper execution state."""
        if not self.store:
            return
        removed = self.store.data.pop("paper_execution", None) is not None
        history = self.store.data.get("ai_plan_history") or []
        clean_history = [
            item for item in history
            if "PAPER" not in str((item or {}).get("status") or "").upper()
            and "PAPER" not in str((item or {}).get("mode") or "").upper()
        ]
        if len(clean_history) != len(history):
            self.store.data["ai_plan_history"] = clean_history[-30:]
            removed = True
        tiers = self.store.data.get("ai_tiers") or {}
        for key in list(tiers):
            item = tiers.get(key)
            if isinstance(item, dict) and (
                "PAPER" in str(item.get("status") or "").upper()
                or "PAPER" in str(item.get("mode") or "").upper()
            ):
                tiers.pop(key, None)
                removed = True
        if removed:
            self.store.save()
            self.emit("log", "Build1831：已删除旧 paper_execution 本地模拟状态；不会迁移为OKX订单。")

    def _ensure_demo_state(self):
        if not self.store:
            return None
        state = self.store.data.setdefault("okx_demo_execution", {})
        state.setdefault("tiers", {"1": None, "2": None})
        state.setdefault("close_orders", [])
        state.setdefault("events", [])
        state.setdefault("results", {})
        # User asked for strategy -> automatic exchange order. New Demo state defaults ON.
        state.setdefault("auto_execute_plans", True)
        state.setdefault("updated_at", _now())
        if set(state["tiers"].keys()) != {"1", "2"}:
            state["tiers"] = {
                "1": state["tiers"].get("1"),
                "2": state["tiers"].get("2"),
            }
        self.store.save()
        return state

    def _demo_state(self):
        if not self.store:
            raise legacy_engine.Halt("KAYTRADE尚未连接")
        return self._ensure_demo_state()

    def _event(self, name, payload):
        state = self._demo_state()
        row = {"time": _now(), "event": name, **dict(payload)}
        state["events"].append(row)
        del state["events"][:-120]
        state["updated_at"] = _now()
        self.store.save()
        self.emit(
            "log",
            f"OKX DEMO · {name} · {payload.get('detail') or payload.get('tier') or ''}",
        )
        return row

    def _tier(self, tier, require=False):
        key = str(int(tier))
        if key not in ("1", "2"):
            raise legacy_engine.Halt("tier 仅支持 1 或 2")
        item = self._demo_state()["tiers"].get(key)
        if require and not isinstance(item, dict):
            raise legacy_engine.Halt(f"第{key}档当前没有OKX模拟盘管理订单")
        return key, item

    def _managed_ids(self):
        state = self._demo_state()
        order_ids = set()
        client_ids = set()
        algo_client_ids = set()
        for item in state["tiers"].values():
            if not isinstance(item, dict):
                continue
            if item.get("order_id"):
                order_ids.add(str(item["order_id"]))
            if item.get("client_order_id"):
                client_ids.add(str(item["client_order_id"]))
            for key in ("tp_client_id", "sl_client_id"):
                if item.get(key):
                    algo_client_ids.add(str(item[key]))
        for item in state["close_orders"]:
            if item.get("order_id"):
                order_ids.add(str(item["order_id"]))
            if item.get("client_order_id"):
                client_ids.add(str(item["client_order_id"]))
        return order_ids, client_ids, algo_client_ids

    def _read_order(self, item):
        try:
            row = self.x.order(
                order_id=str(item.get("order_id") or ""),
                client_id=str(item.get("client_order_id") or ""),
            )
            if row and not item.get("order_id") and row.get("ordId"):
                item["order_id"] = str(row.get("ordId"))
                self.store.save()
            return row
        except APIError as exc:
            if str(getattr(exc, "code", "")) != "51603":
                raise
        # Order-detail can lag; fall back to recent history/fills.
        oid = str(item.get("order_id") or "")
        cid = str(item.get("client_order_id") or "")
        for row in self.x.recent_orders():
            if str(row.get("ordId") or "") == oid or str(row.get("clOrdId") or "") == cid:
                return row
        for row in self.x.fills():
            if str(row.get("ordId") or "") == oid or str(row.get("clOrdId") or "") == cid:
                return {
                    "state": "filled",
                    "accFillSz": row.get("fillSz") or item.get("size") or "0",
                    "avgPx": row.get("fillPx") or "",
                    "ordId": oid,
                    "clOrdId": cid,
                }
        return None

    def _same_direction_and_leverage(self, direction, leverage, exclude_tier=None):
        for key, item in self._demo_state()["tiers"].items():
            if key == str(exclude_tier):
                continue
            if not _active(item):
                continue
            if str(item.get("direction") or "") != direction:
                raise legacy_engine.Halt("双档同时挂单只允许同方向")
            other_lev = Decimal(str(item.get("leverage") or leverage))
            if other_lev != leverage:
                raise legacy_engine.Halt("同方向双档必须使用相同杠杆，避免OKX逐仓杠杆被另一档覆盖")

    def _risk_totals(self, candidate=None, replace_tier=None):
        notional = Decimal("0")
        estimated = Decimal("0")
        for key, item in self._demo_state()["tiers"].items():
            if key == str(replace_tier) or not _active(item):
                continue
            notional += Decimal(str(item.get("notional_usdt") or 0))
            estimated += Decimal(str(item.get("estimated_stop_loss_usdt") or 0))
        if candidate:
            notional += candidate["notional"]
            estimated += candidate["estimated_loss"]
        if notional > AI_MAX_NOTIONAL_USDT:
            raise legacy_engine.Halt(
                f"两档合计名义仓位 {notional:.4f} USDT 超过 {AI_MAX_NOTIONAL_USDT} USDT"
            )
        if estimated > AI_MAX_ESTIMATED_STOP_LOSS_USDT:
            raise legacy_engine.Halt(
                f"两档合计估算止损 {estimated:.4f} USDT 超过 {AI_MAX_ESTIMATED_STOP_LOSS_USDT} USDT"
            )
        return notional, estimated

    def _validate_demo_open(self, proposal, replace_tier=None):
        direction = str(proposal.get("direction") or "").lower()
        if direction not in ("long", "short"):
            raise legacy_engine.Halt("direction 必须为 long 或 short")

        order_type = str(proposal.get("order_type") or "limit").lower()
        if order_type != "limit":
            raise legacy_engine.Halt("LIMIT ONLY：order_type 只允许 limit")

        qty = _d(proposal.get("size"), "size")
        leverage = _d(proposal.get("leverage", AI_MAX_LEVERAGE), "leverage")
        if leverage > AI_MAX_LEVERAGE:
            raise legacy_engine.Halt(f"leverage 必须≤{AI_MAX_LEVERAGE}x")

        self._same_direction_and_leverage(direction, leverage, exclude_tier=replace_tier)

        meta = self.x.instrument()
        tick = _d(meta.get("tickSz"), "tickSz")
        minimum = _d(meta.get("minSz"), "minSz")
        lot = _d(meta.get("lotSz"), "lotSz")
        if qty < minimum or qty % lot != 0:
            raise legacy_engine.Halt(f"size 必须≥{minimum} 且按 lotSz={lot} 递增")

        limit_price = _d(
            proposal.get("limit_price", proposal.get("suggested_entry")),
            "limit_price",
        )
        if limit_price % tick != 0:
            raise legacy_engine.Halt(f"limit_price 必须按 tickSz={tick} 递增")

        tp = _d(proposal.get("take_profit"), "take_profit")
        sl = _d(proposal.get("stop_loss"), "stop_loss")
        if direction == "long" and not (sl < limit_price < tp):
            raise legacy_engine.Halt("LONG 必须满足 SL < 限价入场 < TP")
        if direction == "short" and not (tp < limit_price < sl):
            raise legacy_engine.Halt("SHORT 必须满足 TP < 限价入场 < SL")

        tp_type = str(proposal.get("tp_exit_type") or "limit").lower()
        sl_type = str(proposal.get("sl_exit_type") or "limit").lower()
        if tp_type != "limit" or sl_type != "limit":
            raise legacy_engine.Halt("LIMIT ONLY：TP/SL 保护退出只允许 limit")
        tp_limit = _d(proposal.get("tp_limit_price", tp) or tp, "tp_limit_price")
        sl_limit = _d(proposal.get("sl_limit_price", sl) or sl, "sl_limit_price")
        if tp_limit % tick != 0 or sl_limit % tick != 0:
            raise legacy_engine.Halt(f"TP/SL保护限价必须按 tickSz={tick} 递增")

        unit = _d(meta.get("ctVal"), "ctVal") * _d(meta.get("ctMult") or "1", "ctMult")
        notional = qty * unit * limit_price
        estimated_loss = qty * unit * abs(limit_price - sl) + notional * Decimal("0.0012")
        self._risk_totals(
            {"notional": notional, "estimated_loss": estimated_loss},
            replace_tier=replace_tier,
        )

        equity, available = self.x.balance()
        account_cap = Decimal(str(available)) * leverage * Decimal("0.90")
        if notional > account_cap:
            raise legacy_engine.Halt("可用保证金不足以覆盖该AI限价订单")

        return {
            "direction": direction,
            "qty": qty,
            "leverage": leverage,
            "limit_price": limit_price,
            "tp": tp,
            "sl": sl,
            "tp_limit": tp_limit,
            "sl_limit": sl_limit,
            "unit": unit,
            "notional": notional,
            "estimated_loss": estimated_loss,
            "equity": equity,
        }

    def _entry_body(self, checked, ids):
        direction = checked["direction"]
        return {
            "instId": INSTRUMENT,
            "tdMode": "isolated",
            "side": "buy" if direction == "long" else "sell",
            "posSide": direction,
            "ordType": "limit",
            "px": format(checked["limit_price"], "f"),
            "sz": format(checked["qty"], "f"),
            "clOrdId": ids["client"],
            "attachAlgoOrds": [
                {
                    "attachAlgoClOrdId": ids["tp"],
                    "tpTriggerPx": format(checked["tp"], "f"),
                    "tpOrdPx": format(checked["tp_limit"], "f"),
                    "tpTriggerPxType": "last",
                    "tpOrdKind": "limit",
                },
                {
                    "attachAlgoClOrdId": ids["sl"],
                    "slTriggerPx": format(checked["sl"], "f"),
                    "slOrdPx": format(checked["sl_limit"], "f"),
                    "slTriggerPxType": "last",
                },
            ],
        }

    def _submit_open(self, proposal, proposal_id):
        self._preflight_account()
        checked = self._validate_demo_open(proposal)
        tier_key, current = self._tier(proposal.get("tier") or 1)
        if _active(current):
            raise legacy_engine.Halt(f"第{tier_key}档已有活动OKX订单/仓位")

        direction = checked["direction"]
        self._set_leverage(direction, int(checked["leverage"]))

        ids = {
            "client": v172._client_id("ai"),
            "tp": v172._client_id("tp"),
            "sl": v172._client_id("sl"),
        }
        accepted = _now()
        item = {
            "okx_demo": True,
            "version": VERSION,
            "tier": int(tier_key),
            "proposal_id": proposal_id,
            "direction": direction,
            "side": "做多" if direction == "long" else "做空",
            "order_type": "limit",
            "limit_price": float(checked["limit_price"]),
            "requested_entry": float(checked["limit_price"]),
            "size": float(checked["qty"]),
            "filled_size": 0.0,
            "closed_size": 0.0,
            "entry_price": None,
            "leverage": float(checked["leverage"]),
            "take_profit": float(checked["tp"]),
            "stop_loss": float(checked["sl"]),
            "tp_exit_type": "limit",
            "sl_exit_type": "limit",
            "tp_limit_price": float(checked["tp_limit"]),
            "sl_limit_price": float(checked["sl_limit"]),
            "client_order_id": ids["client"],
            "tp_client_id": ids["tp"],
            "sl_client_id": ids["sl"],
            "order_id": "",
            "status": "submitting",
            "order_state": "submitting",
            "accepted_at": accepted,
            "cancel_deadline": accepted + ENTRY_TTL_SEC,
            "reason": str(proposal.get("reason") or "")[:1000],
            "operation_advice": str(
                proposal.get("operation_advice") or proposal.get("advice") or ""
            )[:1000],
            "notional_usdt": float(checked["notional"]),
            "estimated_stop_loss_usdt": float(checked["estimated_loss"]),
            "protected": False,
        }
        state = self._demo_state()
        state["tiers"][tier_key] = item
        state["updated_at"] = _now()
        self.store.save()

        body = self._entry_body(checked, ids)
        try:
            rows = self.x.post("/api/v5/trade/order", body)
        except APIError as exc:
            if getattr(exc, "write_rejected", False):
                state["tiers"][tier_key] = None
                self.store.save()
            raise

        row = rows[0] if rows else {}
        order_id = str(row.get("ordId") or "") if isinstance(row, dict) else ""
        if not order_id:
            raise legacy_engine.Halt("OKX下单响应缺少ordId；保留本地幂等记录并停止重复提交")
        item["order_id"] = order_id
        item["status"] = "submitted"
        item["order_state"] = "submitted"
        item["okx_ack_at"] = _now()
        self.store.save()

        self._event(
            "ORDER_SUBMITTED",
            {
                "tier": int(tier_key),
                "detail": (
                    f"第{tier_key}档已立即提交OKX模拟盘："
                    f"{direction.upper()} {item['size']:g}张 @ {item['limit_price']:.2f} · "
                    f"TP {item['take_profit']:.2f}/{item['tp_limit_price']:.2f} · "
                    f"SL {item['stop_loss']:.2f}/{item['sl_limit_price']:.2f}"
                ),
            },
        )
        return {
            "version": VERSION,
            "build": BUILD,
            "mode": "OKX_DEMO_EXECUTION",
            "environment": "OKX_DEMO",
            "status": "SUBMITTED_TO_OKX",
            "proposal_id": proposal_id,
            "tier": int(tier_key),
            "order_id": order_id,
            "client_order_id": ids["client"],
            "direction": direction,
            "order_type": "limit",
            "limit_price": item["limit_price"],
            "size": item["size"],
            "take_profit": item["take_profit"],
            "stop_loss": item["stop_loss"],
            "tp_limit_price": item["tp_limit_price"],
            "sl_limit_price": item["sl_limit_price"],
            "cancel_deadline": item["cancel_deadline"],
        }

    def _auto_proposal_from_plan(self, plan, item):
        tp = plan.get("take_profit")
        sl = plan.get("stop_loss")
        if tp in (None, "") or sl in (None, ""):
            raise legacy_engine.Halt("自动执行要求每个AI入场方案必须同时设置 take_profit 和 stop_loss")
        order_type = str(plan.get("order_type") or "limit").lower()
        if order_type != "limit":
            raise legacy_engine.Halt("LIMIT ONLY：AI方案只允许 limit")
        entry = plan.get("limit_price")
        if entry in (None, ""):
            entry = plan.get("suggested_entry", plan.get("entry"))
        if entry in (None, ""):
            raise legacy_engine.Halt("AI限价方案必须包含 limit_price 或 suggested_entry")
        if plan.get("size") in (None, ""):
            raise legacy_engine.Halt("AI自动执行方案缺少 size")
        if plan.get("leverage") in (None, ""):
            raise legacy_engine.Halt("AI自动执行方案缺少 leverage")
        direction = str(plan.get("direction") or "").lower()
        if direction not in ("long", "short"):
            raise legacy_engine.Halt("AI自动执行方案 direction 必须为 long 或 short")

        proposal_id = str(plan.get("proposal_id") or "").strip() or (
            f"autookx-{item.get('plan_id') or 'plan'}-{uuid.uuid4().hex[:10]}"
        )
        return {
            "proposal_id": proposal_id,
            "plan_id": item.get("plan_id"),
            "action": "open",
            "tier": int(plan.get("tier") or 1),
            "direction": direction,
            "order_type": "limit",
            "limit_price": entry,
            "size": plan.get("size"),
            "leverage": plan.get("leverage"),
            "take_profit": tp,
            "stop_loss": sl,
            "tp_exit_type": "limit",
            "sl_exit_type": "limit",
            "tp_limit_price": plan.get("tp_limit_price", tp) or tp,
            "sl_limit_price": plan.get("sl_limit_price", sl) or sl,
            "reason": str(plan.get("reason") or "")[:1000],
            "operation_advice": str(
                plan.get("operation_advice") or plan.get("advice") or ""
            )[:1000],
        }

    def _same_pending(self, current, proposal):
        if not isinstance(current, dict):
            return False
        if str(current.get("order_state") or current.get("status")) not in {
            "submitting",
            "submitted",
            "live",
            "partially_filled",
            "amend_requested",
        }:
            return False
        if float(current.get("filled_size") or 0) > 0:
            return False

        keys = (
            ("direction", "direction"),
            ("limit_price", "limit_price"),
            ("size", "size"),
            ("leverage", "leverage"),
            ("take_profit", "take_profit"),
            ("stop_loss", "stop_loss"),
            ("tp_limit_price", "tp_limit_price"),
            ("sl_limit_price", "sl_limit_price"),
        )
        for old_key, new_key in keys:
            a = current.get(old_key)
            b = proposal.get(new_key)
            try:
                if abs(float(a) - float(b)) > 1e-9:
                    return False
            except Exception:
                if str(a) != str(b):
                    return False
        return True

    def _amend_pending_from_plan(self, current, proposal):
        if not current.get("order_id"):
            raise legacy_engine.Halt("上一笔OKX提交结果仍未知，缺少ordId；禁止改单或重复下单")
        checked = self._validate_demo_open(proposal, replace_tier=current["tier"])
        if float(current.get("filled_size") or 0) > 0:
            raise legacy_engine.Halt("已部分成交的档位不会被新的AI入场方案覆盖")
        body = {
            "instId": INSTRUMENT,
            "ordId": str(current["order_id"]),
            "reqId": v172._client_id("am"),
            "newPx": format(checked["limit_price"], "f"),
            "newSz": format(checked["qty"], "f"),
            "cxlOnFail": False,
            "attachAlgoOrds": [
                {
                    "attachAlgoClOrdId": current["tp_client_id"],
                    "newTpTriggerPx": format(checked["tp"], "f"),
                    "newTpOrdPx": format(checked["tp_limit"], "f"),
                    "newTpTriggerPxType": "last",
                    "newTpOrdKind": "limit",
                },
                {
                    "attachAlgoClOrdId": current["sl_client_id"],
                    "newSlTriggerPx": format(checked["sl"], "f"),
                    "newSlOrdPx": format(checked["sl_limit"], "f"),
                    "newSlTriggerPxType": "last",
                },
            ],
        }
        self.x.post("/api/v5/trade/amend-order", body)

        current.update(
            {
                "proposal_id": proposal["proposal_id"],
                "limit_price": float(checked["limit_price"]),
                "requested_entry": float(checked["limit_price"]),
                "size": float(checked["qty"]),
                "leverage": float(checked["leverage"]),
                "take_profit": float(checked["tp"]),
                "stop_loss": float(checked["sl"]),
                "tp_limit_price": float(checked["tp_limit"]),
                "sl_limit_price": float(checked["sl_limit"]),
                "notional_usdt": float(checked["notional"]),
                "estimated_stop_loss_usdt": float(checked["estimated_loss"]),
                "reason": str(proposal.get("reason") or "")[:1000],
                "operation_advice": str(proposal.get("operation_advice") or "")[:1000],
                "status": "amend_requested",
                "order_state": "amend_requested",
                "amended_at": _now(),
                # A materially new AI plan starts a new 60-minute validity window.
                "cancel_deadline": _now() + ENTRY_TTL_SEC,
            }
        )
        self.store.save()
        self._event(
            "ORDER_AMENDED",
            {
                "tier": current["tier"],
                "detail": (
                    f"第{current['tier']}档已立即向OKX提交改单："
                    f"{current['limit_price']:.2f} / {current['size']:g}张"
                ),
            },
        )
        return {
            "version": VERSION,
            "build": BUILD,
            "mode": "OKX_DEMO_EXECUTION",
            "status": "AMEND_SUBMITTED_TO_OKX",
            "tier": current["tier"],
            "order_id": current["order_id"],
            "proposal_id": current["proposal_id"],
            "limit_price": current["limit_price"],
            "size": current["size"],
        }

    def publish_ai_plan(self, plan):
        with self._ai_lock:
            if not isinstance(plan, dict):
                raise legacy_engine.Halt("AI推荐计划必须为JSON对象")
            item = self._record_ai_plan(plan, status="RECOMMENDED")
            state = self._demo_state()
            auto_on = bool(state.get("auto_execute_plans", True))
            base = {
                "version": VERSION,
                "build": BUILD,
                "mode": "OKX_DEMO_EXECUTION",
                "environment": "OKX_DEMO",
                "auto_execute_plans": auto_on,
                "plan": item,
            }
            if str(plan.get("action") or "open").lower() != "open":
                return dict(base, status="RECOMMENDED", auto_execution="NOT_ENTRY")
            if not auto_on:
                self.emit("log", f"AI推荐计划已更新：第{item['tier']}档 · 自动执行关闭")
                return dict(base, status="RECOMMENDED", auto_execution="OFF")
            if not self.enabled:
                item["status"] = "AUTO_BLOCKED"
                item["detail"] = "OKX Demo AI通道未启用"
                self.store.save()
                return dict(
                    base,
                    status="AUTO_BLOCKED",
                    auto_execution="BLOCKED",
                    error="OKX Demo AI通道未启用",
                )

            try:
                proposal = self._auto_proposal_from_plan(plan, item)
                key, current = self._tier(proposal["tier"])
                if _active(current):
                    if _position_size(current) > 0 or float(current.get("filled_size") or 0) > 0:
                        raise legacy_engine.Halt(
                            f"第{key}档已有OKX成交仓位/部分成交；新的AI入场方案不会覆盖"
                        )
                    if self._same_pending(current, proposal):
                        item["status"] = "OKX_ALREADY_ACTIVE"
                        item["detail"] = "相同OKX限价挂单已存在，不重复发送"
                        self.store.save()
                        return dict(
                            base,
                            status="OKX_ALREADY_ACTIVE",
                            auto_execution="ALREADY_ACTIVE",
                            execution={
                                "tier": int(key),
                                "order_id": current.get("order_id"),
                                "client_order_id": current.get("client_order_id"),
                                "status": current.get("order_state"),
                            },
                        )
                    execution = self._amend_pending_from_plan(current, proposal)
                else:
                    execution = self._submit_open(proposal, proposal["proposal_id"])
            except APIError as exc:
                if not getattr(exc, "write_rejected", False):
                    item["status"] = "OKX_RESULT_UNKNOWN"
                    item["detail"] = str(exc)[:1000]
                    self.store.save()
                    self._event(
                        "OKX_RESULT_UNKNOWN",
                        {
                            "tier": item["tier"],
                            "detail": "OKX写入结果未知；保留幂等状态，禁止重复提交：" + str(exc),
                        },
                    )
                    raise
                item["status"] = "AUTO_REJECTED"
                item["detail"] = str(exc)[:1000]
                self.store.save()
                return dict(
                    base,
                    status="AUTO_REJECTED",
                    auto_execution="REJECTED",
                    error=str(exc),
                )
            except legacy_engine.Halt as exc:
                item["status"] = "AUTO_REJECTED"
                item["detail"] = str(exc)[:1000]
                self.store.save()
                self._event(
                    "AUTO_PLAN_REJECTED",
                    {"tier": item["tier"], "detail": str(exc)},
                )
                return dict(
                    base,
                    status="AUTO_REJECTED",
                    auto_execution="REJECTED",
                    error=str(exc),
                )

            item["status"] = "OKX_SUBMITTED"
            item["proposal_id"] = execution.get("proposal_id", item.get("proposal_id"))
            item["detail"] = "AI方案已立即发送到OKX模拟盘，不等待价格触达"
            self.store.data.setdefault("ai_tiers", {})[str(item["tier"])] = item
            self.store.save()
            return dict(
                base,
                status="OKX_SUBMITTED",
                auto_execution="EXECUTED",
                plan=item,
                execution=execution,
            )

    def submit_ai_trade(self, proposal):
        with self._ai_lock:
            if not isinstance(proposal, dict):
                raise legacy_engine.Halt("AI交易请求必须为JSON对象")
            self._preflight_account()
            if not self.enabled:
                raise legacy_engine.Halt("OKX Demo AI执行通道未启用")
            proposal_id = str(proposal.get("proposal_id") or uuid.uuid4().hex).strip()
            if not proposal_id or len(proposal_id) > 64:
                raise legacy_engine.Halt("proposal_id 无效")
            state = self._demo_state()
            existing = state["results"].get(proposal_id)
            if existing is not None:
                return dict(existing, duplicate=True)

            action = str(proposal.get("action") or "").lower()
            if action == "open":
                result = self._submit_open(proposal, proposal_id)
            elif action == "close":
                payload = dict(proposal)
                payload["proposal_id"] = proposal_id
                result = self.close_demo_position(payload)
            else:
                raise legacy_engine.Halt("action 必须为 open 或 close")

            state["results"][proposal_id] = result
            while len(state["results"]) > 100:
                state["results"].pop(next(iter(state["results"])))
            self.store.save()
            return result

    def set_auto_execute_plans(self, enabled):
        with self._ai_lock:
            if not self.enabled:
                raise legacy_engine.Halt("先启用OKX Demo AI执行通道")
            state = self._demo_state()
            state["auto_execute_plans"] = bool(enabled)
            state["updated_at"] = _now()
            self.store.save()
            self.emit(
                "log",
                "AI方案自动执行已"
                + ("开启：AI完整策略到达后立即提交OKX模拟盘限价单" if enabled else "关闭"),
            )
            return {
                "version": VERSION,
                "build": BUILD,
                "mode": "OKX_DEMO_EXECUTION",
                "auto_execute_plans": bool(enabled),
                "requires_take_profit": True,
                "requires_stop_loss": True,
                "limit_only": True,
            }

    def _foreign_state_check(self):
        order_ids, client_ids, algo_client_ids = self._managed_ids()
        foreign_orders = [
            row for row in self.x.orders()
            if str(row.get("ordId") or "") not in order_ids
            and str(row.get("clOrdId") or "") not in client_ids
        ]
        if foreign_orders:
            raise legacy_engine.Halt("OKX模拟盘存在非KAYTRADE管理的BTC普通挂单；拒绝启用AI执行")

        foreign_algos = [
            row for row in self.x.algos()
            if str(row.get("algoClOrdId") or "") not in algo_client_ids
        ]
        if foreign_algos:
            raise legacy_engine.Halt("OKX模拟盘存在非KAYTRADE管理的BTC策略挂单；拒绝启用AI执行")

        positions = self.x.positions()
        if not positions:
            return
        managed_filled = [
            item for item in self._demo_state()["tiers"].values()
            if isinstance(item, dict) and float(item.get("filled_size") or 0) > 0
        ]
        if not managed_filled:
            raise legacy_engine.Halt("OKX模拟盘存在非KAYTRADE管理的BTC仓位；拒绝启用AI执行")
        sides = {str(item.get("direction") or "") for item in managed_filled}
        for pos in positions:
            if str(pos.get("posSide") or "") not in sides:
                raise legacy_engine.Halt("OKX模拟盘仓位方向与KAYTRADE管理状态不一致")

    def arm(self, _settings=None):
        with self._ai_lock:
            self._preflight_account()
            self._ensure_demo_state()
            # Reconcile any persisted IDs before deciding whether exchange state is foreign.
            self._sync_all(cancel_expired=False)
            self._foreign_state_check()
            self.enabled = True
            self.stopped = False
            self.poll_at = time.monotonic()
            self.emit(
                "log",
                "V1.8.3 OKX Demo AI执行已启用：AI方案自动执行默认开启；策略到达即提交交易所限价挂单。",
            )

    def stop(self):
        with self._ai_lock:
            self.enabled = False
            self.stopped = True
            self.emit("log", "OKX Demo AI执行通道已停止；不再接收新AI开仓，既有OKX订单继续只读核对")

    def _cancel_entry_internal(self, item, reason):
        if not item.get("order_id") and not item.get("client_order_id"):
            raise legacy_engine.Halt("当前档位缺少OKX ordId/clOrdId，无法撤单")
        body = {"instId": INSTRUMENT}
        if item.get("order_id"):
            body["ordId"] = str(item["order_id"])
        else:
            body["clOrdId"] = str(item["client_order_id"])
        self.x.post("/api/v5/trade/cancel-order", body)
        item["status"] = "cancel_requested"
        item["order_state"] = "cancel_requested"
        item["cancel_requested_at"] = _now()
        item["cancel_reason"] = reason
        self.store.save()
        self._event(
            "CANCEL_SUBMITTED",
            {
                "tier": item["tier"],
                "detail": f"第{item['tier']}档已向OKX提交撤单：{reason}",
            },
        )

    def cancel_demo_entry(self, payload):
        with self._ai_lock:
            self._preflight_account()
            _key, item = self._tier(payload.get("tier"), require=True)
            if str(item.get("order_state") or item.get("status")) not in {
                "submitted",
                "live",
                "partially_filled",
                "amend_requested",
            }:
                raise legacy_engine.Halt("当前档位没有可撤销的OKX未完成入场单")
            self._cancel_entry_internal(item, str(payload.get("reason") or "AI撤销入场"))
            return {
                "status": "CANCEL_SUBMITTED_TO_OKX",
                "tier": item["tier"],
                "order_id": item["order_id"],
            }

    def amend_demo_entry(self, payload):
        with self._ai_lock:
            self._preflight_account()
            key, item = self._tier(payload.get("tier"), require=True)
            if str(item.get("order_state") or item.get("status")) not in {
                "submitted",
                "live",
                "partially_filled",
                "amend_requested",
            }:
                raise legacy_engine.Halt("当前档位没有可修改的OKX未完成入场单")

            proposal = {
                "proposal_id": str(item.get("proposal_id") or uuid.uuid4().hex),
                "tier": int(key),
                "direction": item["direction"],
                "order_type": "limit",
                "limit_price": payload.get("new_price", item["limit_price"]),
                "size": payload.get("new_size", item["size"]),
                "leverage": item["leverage"],
                "take_profit": item["take_profit"],
                "stop_loss": item["stop_loss"],
                "tp_limit_price": item["tp_limit_price"],
                "sl_limit_price": item["sl_limit_price"],
            }
            checked = self._validate_demo_open(proposal, replace_tier=key)
            if float(checked["qty"]) < float(item.get("filled_size") or 0):
                raise legacy_engine.Halt("new_size 不能小于已成交数量")
            body = {
                "instId": INSTRUMENT,
                "reqId": v172._client_id("am"),
                "cxlOnFail": False,
                "newPx": format(checked["limit_price"], "f"),
                "newSz": format(checked["qty"], "f"),
            }
            if item.get("order_id"):
                body["ordId"] = str(item["order_id"])
            else:
                body["clOrdId"] = str(item["client_order_id"])
            self.x.post("/api/v5/trade/amend-order", body)
            item["limit_price"] = float(checked["limit_price"])
            item["requested_entry"] = float(checked["limit_price"])
            item["size"] = float(checked["qty"])
            item["notional_usdt"] = float(checked["notional"])
            item["estimated_stop_loss_usdt"] = float(checked["estimated_loss"])
            item["status"] = "amend_requested"
            item["order_state"] = "amend_requested"
            item["amended_at"] = _now()
            self.store.save()
            self._event(
                "ENTRY_AMEND_SUBMITTED",
                {
                    "tier": int(key),
                    "detail": f"第{key}档已提交OKX改单：{item['limit_price']:.2f} / {item['size']:g}张",
                },
            )
            return {
                "status": "AMEND_SUBMITTED_TO_OKX",
                "tier": int(key),
                "order_id": item["order_id"],
                "limit_price": item["limit_price"],
                "size": item["size"],
            }

    def amend_demo_protection(self, payload):
        with self._ai_lock:
            self._preflight_account()
            key, item = self._tier(payload.get("tier"), require=True)
            if not _active(item):
                raise legacy_engine.Halt("当前档位没有可修改保护的OKX订单/仓位")

            entry_ref = _d(item.get("entry_price") or item.get("limit_price"), "entry")
            tp = _d(payload.get("take_profit", item["take_profit"]), "take_profit")
            sl = _d(payload.get("stop_loss", item["stop_loss"]), "stop_loss")
            if item["direction"] == "long" and not (sl < entry_ref < tp):
                raise legacy_engine.Halt("LONG保护必须满足 SL < 入场 < TP")
            if item["direction"] == "short" and not (tp < entry_ref < sl):
                raise legacy_engine.Halt("SHORT保护必须满足 TP < 入场 < SL")
            tp_limit = _d(payload.get("tp_limit_price", tp) or tp, "tp_limit_price")
            sl_limit = _d(payload.get("sl_limit_price", sl) or sl, "sl_limit_price")

            parent_state = str(item.get("order_state") or item.get("status") or "")
            if parent_state in {"submitted", "live", "partially_filled", "amend_requested"}:
                body = {
                    "instId": INSTRUMENT,
                    "ordId": str(item["order_id"]),
                    "reqId": v172._client_id("ap"),
                    "cxlOnFail": False,
                    "attachAlgoOrds": [
                        {
                            "attachAlgoClOrdId": item["tp_client_id"],
                            "newTpTriggerPx": format(tp, "f"),
                            "newTpOrdPx": format(tp_limit, "f"),
                            "newTpTriggerPxType": "last",
                            "newTpOrdKind": "limit",
                        },
                        {
                            "attachAlgoClOrdId": item["sl_client_id"],
                            "newSlTriggerPx": format(sl, "f"),
                            "newSlOrdPx": format(sl_limit, "f"),
                            "newSlTriggerPxType": "last",
                        },
                    ],
                }
                self.x.post("/api/v5/trade/amend-order", body)
            else:
                self.x.post(
                    "/api/v5/trade/amend-algos",
                    {
                        "instId": INSTRUMENT,
                        "algoClOrdId": item["tp_client_id"],
                        "reqId": v172._client_id("tp"),
                        "newTpTriggerPx": format(tp, "f"),
                        "newTpOrdPx": format(tp_limit, "f"),
                        "newTpTriggerPxType": "last",
                    },
                )
                self.x.post(
                    "/api/v5/trade/amend-algos",
                    {
                        "instId": INSTRUMENT,
                        "algoClOrdId": item["sl_client_id"],
                        "reqId": v172._client_id("sl"),
                        "newSlTriggerPx": format(sl, "f"),
                        "newSlOrdPx": format(sl_limit, "f"),
                        "newSlTriggerPxType": "last",
                    },
                )

            item["take_profit"] = float(tp)
            item["stop_loss"] = float(sl)
            item["tp_limit_price"] = float(tp_limit)
            item["sl_limit_price"] = float(sl_limit)
            item["protection_amended_at"] = _now()
            self.store.save()
            self._event(
                "PROTECTION_AMEND_SUBMITTED",
                {"tier": int(key), "detail": f"第{key}档TP/SL修改已提交OKX模拟盘"},
            )
            return {
                "status": "PROTECTION_AMEND_SUBMITTED_TO_OKX",
                "tier": int(key),
                "take_profit": float(tp),
                "stop_loss": float(sl),
                "tp_limit_price": float(tp_limit),
                "sl_limit_price": float(sl_limit),
            }

    def close_demo_position(self, payload):
        with self._ai_lock:
            self._preflight_account()
            order_type = str(payload.get("order_type") or payload.get("close_type") or "limit").lower()
            if order_type != "limit":
                raise legacy_engine.Halt("LIMIT ONLY：平仓/减仓只允许 limit")
            qty = _d(payload.get("size") or payload.get("close_size"), "close_size")
            limit_price = _d(payload.get("limit_price"), "limit_price")
            target_tier = payload.get("tier")

            if target_tier is not None:
                _key, target = self._tier(target_tier, require=True)
                direction = target["direction"]
                if float(qty) > _position_size(target) + 1e-12:
                    raise legacy_engine.Halt("减仓数量超过该档已成交未平数量")
            else:
                active_positions = [
                    item for item in self._demo_state()["tiers"].values()
                    if isinstance(item, dict) and _position_size(item) > 0
                ]
                if not active_positions:
                    raise legacy_engine.Halt("没有KAYTRADE管理的OKX模拟盘仓位")
                directions = {item["direction"] for item in active_positions}
                if len(directions) != 1:
                    raise legacy_engine.Halt("管理仓位方向不唯一，拒绝自动减仓")
                direction = next(iter(directions))
                if float(qty) > sum(_position_size(x) for x in active_positions) + 1e-12:
                    raise legacy_engine.Halt("减仓数量超过KAYTRADE管理仓位")

            cid = v172._client_id("cl")
            rows = self.x.post(
                "/api/v5/trade/order",
                {
                    "instId": INSTRUMENT,
                    "tdMode": "isolated",
                    "posSide": direction,
                    "side": "sell" if direction == "long" else "buy",
                    "ordType": "limit",
                    "px": format(limit_price, "f"),
                    "sz": format(qty, "f"),
                    "clOrdId": cid,
                },
            )
            row = rows[0] if rows else {}
            oid = str(row.get("ordId") or "")
            if not oid:
                raise legacy_engine.Halt("OKX减仓下单响应缺少ordId")
            item = {
                "order_id": oid,
                "client_order_id": cid,
                "tier": int(target_tier) if target_tier is not None else None,
                "direction": direction,
                "size": float(qty),
                "limit_price": float(limit_price),
                "status": "submitted",
                "order_state": "submitted",
                "filled_size": 0.0,
                "created_at": _now(),
                "proposal_id": str(payload.get("proposal_id") or ""),
                "reason": str(payload.get("reason") or "AI限价减仓")[:500],
            }
            self._demo_state()["close_orders"].append(item)
            self.store.save()
            self._event(
                "CLOSE_SUBMITTED",
                {"detail": f"已向OKX提交限价减仓 {float(qty):g}张 @ {float(limit_price):.2f}"},
            )
            return {
                "version": VERSION,
                "build": BUILD,
                "mode": "OKX_DEMO_EXECUTION",
                "status": "SUBMITTED_TO_OKX",
                "order_id": oid,
                "client_order_id": cid,
                "size": float(qty),
                "limit_price": float(limit_price),
            }

    def _sync_tier(self, item, algos, cancel_expired=True):
        if not isinstance(item, dict) or (
            not item.get("order_id") and not item.get("client_order_id")
        ):
            return
        order = self._read_order(item)
        if not order:
            return

        exchange_state = str(order.get("state") or "")
        filled = float(order.get("accFillSz") or item.get("filled_size") or 0)
        avg_px = str(order.get("avgPx") or order.get("fillPx") or "").strip()
        item["exchange_state"] = exchange_state
        item["order_state"] = exchange_state or item.get("order_state")
        item["filled_size"] = filled
        if avg_px and avg_px not in ("0", "0.0"):
            item["entry_price"] = float(avg_px)

        if exchange_state in {"live", "partially_filled"}:
            item["status"] = exchange_state
            if (
                cancel_expired
                and _now() >= float(item.get("cancel_deadline") or 0)
                and not item.get("cancel_requested_at")
            ):
                self._cancel_entry_internal(item, "60分钟未完全成交，自动撤销剩余入场单")
        elif exchange_state == "filled":
            if item.get("status") != "filled":
                item["filled_at"] = _now()
            item["status"] = "filled"
        elif exchange_state == "canceled":
            if filled > 0:
                item["status"] = "filled"
                item["order_state"] = "canceled_partial"
            else:
                item["status"] = "canceled"
        elif exchange_state:
            item["status"] = exchange_state

        if filled > 0:
            tp = next(
                (a for a in algos if str(a.get("algoClOrdId") or "") == str(item.get("tp_client_id"))),
                None,
            )
            sl = next(
                (a for a in algos if str(a.get("algoClOrdId") or "") == str(item.get("sl_client_id"))),
                None,
            )
            if tp and sl:
                item["protected"] = True
            item["position_size"] = _position_size(item)
        self.store.save()

    def _sync_close_order(self, item):
        if not isinstance(item, dict) or not item.get("order_id"):
            return
        order = self._read_order(item)
        if not order:
            return
        state = str(order.get("state") or "")
        filled = float(order.get("accFillSz") or 0)
        avg_px = str(order.get("avgPx") or order.get("fillPx") or "").strip()
        item["order_state"] = state or item.get("order_state")
        item["status"] = state or item.get("status")
        item["filled_size"] = filled
        if avg_px and avg_px not in ("0", "0.0"):
            item["fill_price"] = float(avg_px)

        if filled > 0:
            remaining = filled
            targets = []
            if item.get("tier") is not None:
                _key, target = self._tier(item["tier"], require=True)
                targets = [target]
            else:
                targets = [
                    x for x in self._demo_state()["tiers"].values()
                    if isinstance(x, dict) and _position_size(x) > 0
                ]
            already_applied = float(item.get("applied_filled_size") or 0)
            delta = max(0.0, filled - already_applied)
            if delta > 0:
                left = delta
                for target in targets:
                    if left <= 1e-12:
                        break
                    reduce_by = min(left, _position_size(target))
                    target["closed_size"] = float(target.get("closed_size") or 0) + reduce_by
                    left -= reduce_by
                item["applied_filled_size"] = filled
        self.store.save()

    def _sync_all(self, cancel_expired=True):
        if not self.store:
            return
        algos = self.x.algos()
        for item in self._demo_state()["tiers"].values():
            self._sync_tier(item, algos, cancel_expired=cancel_expired)
        for item in self._demo_state()["close_orders"]:
            if str(item.get("status") or "") not in {"filled", "canceled"}:
                self._sync_close_order(item)
        self._demo_state()["updated_at"] = _now()
        self.store.save()

    def reconcile(self):
        with self._ai_lock:
            self._sync_all(cancel_expired=True)
            try:
                self.emit("position", self.x.positions())
            except Exception:
                pass

    def cycle(self):
        with self._ai_lock:
            self.poll_at = time.monotonic()
            if self.store:
                self._sync_all(cancel_expired=True)

    def ai_state(self):
        state = {
            "version": VERSION,
            "build": BUILD,
            "mode": "OKX_DEMO_EXECUTION",
            "connected": bool(self.store),
            "environment": "OKX_DEMO" if getattr(self.x, "demo", False) else "OKX_LIVE",
            "ai_enabled": bool(self.enabled),
            "demo_exchange_writes": True,
            "live_ai_writes": False,
            "paper_runtime_present": False,
            "limit_only": True,
            "supported_entry_order_types": ["limit"],
            "supported_close_order_types": ["limit"],
            "supported_protection_exit_order_types": ["limit"],
            "supports_two_same_direction_tiers": True,
            "supports_auto_execute_ai_plans": True,
            "auto_execute_requires_tp_sl": True,
            "entry_auto_cancel_seconds": ENTRY_TTL_SEC,
            "limits": {
                "max_leverage": str(AI_MAX_LEVERAGE),
                "max_notional_usdt": str(AI_MAX_NOTIONAL_USDT),
                "max_estimated_stop_loss_usdt": str(AI_MAX_ESTIMATED_STOP_LOSS_USDT),
            },
        }
        if self.store:
            demo = self._demo_state()
            state["auto_execute_plans"] = bool(demo.get("auto_execute_plans", True))
            state["okx_demo_execution"] = demo
            state["recent_ai_plans"] = (self.store.data.get("ai_plan_history") or [])[-8:]
            state["positions"] = self.x.positions()
            state["pending_orders"] = self.x.orders()
        try:
            state["ticker"] = self.x.ticker()
        except Exception:
            state["ticker"] = {}
        return state


def _tier_ui_text(item):
    if not isinstance(item, dict):
        return "等待 AI 方案"
    status = str(item.get("order_state") or item.get("status") or "—").upper()
    entry = item.get("entry_price") or item.get("limit_price")
    pos = _position_size(item)
    return (
        f"OKX限价 · {status}\n"
        f"方向：{'LONG' if item.get('direction') == 'long' else 'SHORT'}   "
        f"入场：{v180._fmt_px(entry)}   已成交/持仓：{pos:g}张\n"
        f"TP：{v180._fmt_px(item.get('take_profit'))} / 限价 {v180._fmt_px(item.get('tp_limit_price'))}   "
        f"SL：{v180._fmt_px(item.get('stop_loss'))} / 限价 {v180._fmt_px(item.get('sl_limit_price'))}"
    )


def _install_auto_exec_card_v183(owner):
    if getattr(owner, "_v183_auto_exec_card", None) is not None:
        return
    dash = owner.book.pages[3].body
    card = visual.Card(dash, height=112)
    kwargs = dict(fill="x", pady=(0, 12))
    plan = getattr(owner, "_v180_plan_card", None)
    if plan is not None:
        kwargs["before"] = plan
    card.pack(**kwargs)

    row = app.tk.Frame(card.body, bg=visual.PANEL)
    row.pack(fill="both", expand=True)
    left = app.tk.Frame(row, bg=visual.PANEL)
    left.pack(side="left", fill="both", expand=True)
    visual.label(
        left,
        text="OKX Demo · AI方案自动执行",
        size=16,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL,
    ).pack(anchor="w")
    owner._v183_auto_exec_status_var = app.tk.StringVar(
        value="等待连接 · 默认自动执行"
    )
    owner._v183_auto_exec_note_var = app.tk.StringVar(
        value="完整AI策略到达后立即提交OKX模拟盘LIMIT挂单；不等待价格触达。"
    )
    visual.label(
        left,
        variable=owner._v183_auto_exec_status_var,
        size=11,
        bold=True,
        color=visual.GREEN,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(5, 1))
    visual.label(
        left,
        variable=owner._v183_auto_exec_note_var,
        size=9,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w")

    owner._v183_auto_exec_button = visual.RoundedButton(
        row,
        text="关闭自动执行",
        command=lambda: _toggle_auto_execute_v183(owner),
        variant="danger",
        width=160,
        height=42,
    )
    owner._v183_auto_exec_button.pack(side="right", padx=(20, 0), pady=10)
    owner._v183_auto_exec_card = card


def _refresh_v183_dashboard(owner):
    try:
        engine = getattr(owner, "engine", None)
        store = getattr(engine, "store", None) if engine is not None else None
        data = getattr(store, "data", {}) if store is not None else {}
        demo = data.get("okx_demo_execution") or {}
        tiers = demo.get("tiers") or {}
        history = data.get("ai_plan_history") or []
        latest = history[-1] if history else {}
        auto_on = bool(demo.get("auto_execute_plans", True))

        if hasattr(owner, "_v183_auto_exec_status_var"):
            owner._v183_auto_exec_status_var.set(
                "已开启 · AI策略到达即提交OKX模拟盘"
                if auto_on else
                "已关闭 · AI推荐仅展示"
            )
            owner._v183_auto_exec_note_var.set(
                "完整策略立即发送OKX限价挂单；不等待价格触达。TP / SL 必须同时存在且均为限价保护。"
                if auto_on else
                "自动执行关闭：AI方案只显示，不向OKX发送新订单。"
            )
            owner._v183_auto_exec_button.configure(
                text="关闭自动执行" if auto_on else "开启自动执行",
                variant="danger" if auto_on else "accent",
            )

        owner._v180_mode_var.set(
            "OKX DEMO · AUTO ON" if auto_on else "OKX DEMO · MANUAL"
        )

        if latest:
            direction = str(latest.get("direction") or "").lower()
            owner._v180_side_var.set(v180._side_cn(direction))
            owner._v180_type_var.set("委托：OKX限价")
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

        rows = []
        for item in history[-5:][::-1]:
            stamp = time.strftime(
                "%H:%M:%S",
                time.localtime(float(item.get("time") or _now())),
            )
            side = str(item.get("direction") or "WAIT").upper()
            entry = item.get("limit_price") or item.get("suggested_entry")
            rows.append(
                f"{stamp}  T{item.get('tier') or 1}  {side:<5} LMT  "
                f"{v180._fmt_px(entry):>10}  {str(item.get('status') or '')[:18]}"
            )
        owner._v180_history_var.set(
            "\n".join(rows) if rows else "暂无 AI 方案记录"
        )

        t1 = tiers.get("1")
        t2 = tiers.get("2")
        owner._v180_tier1_var.set(_tier_ui_text(t1))
        owner._v180_tier2_var.set(_tier_ui_text(t2))

        active_items = [x for x in (t1, t2) if _active(x)]
        if active_items:
            item = next(
                (x for x in active_items if float(x.get("filled_size") or 0) > 0),
                active_items[0],
            )
            owner._v180_order_state_var.set(
                str(item.get("order_state") or item.get("status") or "—").upper()
            )
            owner._v180_order_type_var.set("OKX限价")
            owner._v180_order_px_var.set(
                v180._fmt_px(item.get("limit_price"))
            )
            owner._v180_fill_px_var.set(
                v180._fmt_px(item.get("entry_price"))
            )
            owner._v180_size_var.set(
                f"{sum(_position_size(x) for x in active_items):g} 张"
            )
            owner._v180_position_tp_var.set(
                v180._fmt_px(item.get("take_profit"))
            )
            owner._v180_position_sl_var.set(
                v180._fmt_px(item.get("stop_loss"))
            )
            owner._v180_proposal_var.set(
                str(item.get("proposal_id") or "—")[:22]
            )
            owner._v180_position_badge_var.set("OKX 持仓/委托")
        else:
            owner._v180_order_state_var.set("等待OKX委托")
            owner._v180_order_type_var.set("OKX限价")
            owner._v180_order_px_var.set("—")
            owner._v180_fill_px_var.set("—")
            owner._v180_size_var.set("—")
            owner._v180_position_tp_var.set("—")
            owner._v180_position_sl_var.set("—")
            owner._v180_proposal_var.set("—")
            owner._v180_position_badge_var.set("OKX 空仓")
    except Exception:
        pass

    try:
        if owner.root.winfo_exists():
            owner.root.after(650, lambda: _refresh_v183_dashboard(owner))
    except Exception:
        pass


def _toggle_auto_execute_v183(owner):
    engine = getattr(owner, "engine", None)
    if engine is None or not isinstance(engine, OKXDemoEngineV183):
        app.messagebox.showerror(
            "未连接",
            "先连接并启用 V1.8.3 Build1831 OKX Demo AI执行通道",
        )
        return
    try:
        current = bool(engine._demo_state().get("auto_execute_plans", True))
        engine.set_auto_execute_plans(not current)
        _refresh_v183_dashboard(owner)
    except Exception as exc:
        app.messagebox.showerror("自动执行设置失败", str(exc))


_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_EMIT = app.App.emit


def _rewrite_v183_text(data):
    if not isinstance(data, str):
        return data
    text = (
        data.replace("V1.7.2", VERSION)
        .replace("V1.8.0", VERSION)
        .replace("Build1723", f"Build{BUILD}")
        .replace("Build1801", f"Build{BUILD}")
    )
    if text.startswith("全自动运行 / 模拟盘"):
        return "OKX模拟盘执行 / AI限价委托"
    if text.startswith("AI Only运行 / OKX模拟盘"):
        return "OKX模拟盘执行 / AI限价委托"
    return text


def _app_emit_v183(self, kind, data):
    return _PREVIOUS_APP_EMIT(self, kind, _rewrite_v183_text(data))


def _update_trade_button_v183(self):
    button = getattr(self, "trade_button", None)
    if not button:
        return
    running = bool(self.engine and self.engine.enabled)
    button.configure(
        text="■  停止 OKX Demo AI通道" if running else "▶  启用 OKX Demo AI通道",
        variant="danger" if running else "accent",
    )


def _ensure_v183_runtime(owner):
    """Build1831 strict runtime repair: the only accepted engine is OKXDemoEngineV183."""
    current = getattr(owner, "engine", None)
    if current is None:
        raise legacy_engine.Halt("先连接OKX模拟盘")
    if isinstance(current, OKXDemoEngineV183):
        return current

    x = getattr(current, "x", None)
    if x is None:
        raise legacy_engine.Halt("当前连接缺少Exchange对象，无法切换到OKX Demo Runtime")

    try:
        current.enabled = False
    except Exception:
        pass
    replacement = OKXDemoEngineV183(x, owner.folder, owner.emit)
    replacement.connect()
    owner.engine = replacement
    owner.emit(
        "log",
        "Build1831：检测到旧Runtime并已强制替换为 OKXDemoEngineV183；Paper Runtime不会被保留。",
    )
    return replacement


def _app_arm_v183(self):
    if not self.engine:
        app.messagebox.showerror("未连接", "先连接 OKX模拟盘")
        return
    if not self.engine.x.demo:
        app.messagebox.showerror(
            "禁止实盘",
            "V1.8.3 Build1831 只允许 OKX模拟盘自动下单；真实账户写入保持硬禁用。",
        )
        return
    typed = app.simpledialog.askstring(
        "启用 OKX Demo AI执行",
        "KAYTRADE V1.8.3 Build1831 CLEAN\n\n"
        "本地Paper Runtime / 本地撮合 / Paper API 已删除。\n"
        "AI完整策略到达后立即向OKX模拟盘提交LIMIT挂单，不等待行情到达入场价。\n"
        "TP和SL必须同时存在并使用限价保护。\n"
        "支持同方向第一档/第二档、60分钟未完全成交撤销剩余挂单、改单/撤单/限价减仓。\n"
        "真实账户自动写入仍被硬禁用。\n\n"
        "确认启用请输入 DEMO",
        parent=self.root,
    )
    if typed and typed.strip().upper() == "DEMO":
        v172._start_ai_enable(self)


def _app_init_v183(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    self.root.title("KAYTRADE 1.8.3 · OKX DEMO CLEAN · Build 1831")
    try:
        self.signal.set(
            "V1.8.3 Build1831 CLEAN｜OKX DEMO ONLY｜AI策略即刻提交LIMIT挂单｜无本地Paper Runtime"
        )
    except Exception:
        pass
    label = getattr(self, "_v170_version_label", None)
    if label is not None:
        try:
            label.configure(
                text="BTC / USDT   ·   V1.8.3 Build1831 · OKX DEMO ONLY"
            )
        except Exception:
            pass
    _install_auto_exec_card_v183(self)
    try:
        self._v180_mode_var.set("OKX DEMO")
        self._v180_position_badge_var.set("OKX 空仓")
        self._v180_order_type_var.set("OKX限价")
    except Exception:
        pass
    self._v183_okx_demo_ready = True
    self._v183_paper_runtime_present = False
    self.update_trade_button()


def _bridge_get_v183(self):
    if not self._require_auth():
        return
    if self.path == "/health":
        self._json(
            200,
            {
                "name": "KAYTRADE",
                "version": VERSION,
                "build": BUILD,
                "mode": "OKX_DEMO_EXECUTION",
                "demo_exchange_writes": True,
                "live_ai_writes": False,
                "paper_runtime_present": False,
                "limit_only": True,
            },
        )
        return
    if self.path == "/v1/state":
        try:
            engine = self.bridge.owner.engine
            payload = engine.ai_state() if engine else {
                "version": VERSION,
                "build": BUILD,
                "mode": "OKX_DEMO_EXECUTION",
                "connected": False,
                "ai_enabled": False,
                "demo_exchange_writes": True,
                "live_ai_writes": False,
                "limit_only": True,
            }
            self._json(200, payload)
        except Exception as exc:
            self._json(409, {"error": str(exc)})
        return
    self._json(404, {"error": "not_found"})


def _bridge_post_v183(self):
    if not self._require_auth():
        return
    allowed = {
        "/v1/trade",
        "/v1/plan",
        "/v1/demo/cancel-entry",
        "/v1/demo/amend-entry",
        "/v1/demo/amend-protection",
        "/v1/demo/close",
    }
    if self.path not in allowed:
        self._json(404, {"error": "not_found"})
        return
    try:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 65536:
            raise ValueError("invalid content length")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        engine = self.bridge.owner.engine
        if not isinstance(engine, OKXDemoEngineV183):
            raise legacy_engine.Halt("当前Runtime不是 V1.8.3 OKX Demo Execution")
        routes = {
            "/v1/trade": engine.submit_ai_trade,
            "/v1/plan": engine.publish_ai_plan,
            "/v1/demo/cancel-entry": engine.cancel_demo_entry,
            "/v1/demo/amend-entry": engine.amend_demo_entry,
            "/v1/demo/amend-protection": engine.amend_demo_protection,
            "/v1/demo/close": engine.close_demo_position,
        }
        self._json(200, routes[self.path](payload))
    except legacy_engine.Halt as exc:
        self._json(409, {"error": str(exc)})
    except APIError as exc:
        self._json(502, {"error": str(exc), "code": getattr(exc, "code", "")})
    except Exception as exc:
        self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


def _bridge_start_v183(self):
    port = v172.AI_BRIDGE_PORT
    try:
        httpd = v172.ThreadingHTTPServer((v172.AI_BRIDGE_HOST, port), v172._BridgeHandler)
    except OSError:
        httpd = v172.ThreadingHTTPServer((v172.AI_BRIDGE_HOST, 0), v172._BridgeHandler)
    httpd.bridge = self
    self.httpd = httpd
    actual_port = int(httpd.server_address[1])
    descriptor = {
        "version": VERSION,
        "build": BUILD,
        "mode": "OKX_DEMO_EXECUTION",
        "host": v172.AI_BRIDGE_HOST,
        "port": actual_port,
        "token": self.token,
        "demo_exchange_writes": True,
        "live_ai_writes": False,
        "paper_runtime_present": False,
        "limit_only": True,
    }
    self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(descriptor, handle, ensure_ascii=False, indent=2)
    self.thread = threading.Thread(
        target=httpd.serve_forever,
        name="kaytrade-ai-bridge",
        daemon=True,
    )
    self.thread.start()
    self.owner.emit(
        "log",
        f"AI Bridge已启动：127.0.0.1:{actual_port} · V1.8.3 OKX Demo交易所执行",
    )


def apply():
    if getattr(model, "_kaytrade_v183_okx_demo_applied", False):
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
    ):
        module.VERSION = VERSION
        module.BUILD = BUILD

    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.8.3 · OKX DEMO CLEAN · Build 1831"
    model.STRATEGY_ENABLED = False
    model.AI_ONLY = True

    app.Engine = OKXDemoEngineV183
    legacy_engine.AIOnlyEngine = OKXDemoEngineV183
    v172.AIOnlyEngine = OKXDemoEngineV183
    v172._ensure_ai_only_engine = _ensure_v183_runtime
    # V1.8.0 owns the clean dashboard; redirect its scheduled refresh loop.
    v180._refresh_v180_dashboard = _refresh_v183_dashboard

    app.App.__init__ = _app_init_v183
    app.App.emit = _app_emit_v183
    app.App.arm = _app_arm_v183
    app.App.update_trade_button = _update_trade_button_v183

    v172._BridgeHandler.do_GET = _bridge_get_v183
    v172._BridgeHandler.do_POST = _bridge_post_v183
    v172.AIBridgeServer.start = _bridge_start_v183

    model._kaytrade_v183_okx_demo_applied = True
    app.App._kaytrade_v183_okx_demo_applied = True


apply()

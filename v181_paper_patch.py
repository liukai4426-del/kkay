"""KAYTRADE V1.8.1 Paper Execution overlay.

Paper-only execution simulator. It never sends OKX write requests.

Implements:
- market/limit paper entries;
- same-direction Tier 1 + Tier 2 concurrent entries;
- 60-minute auto-cancel for unfilled entry remainder;
- cancel/amend pending entry;
- amend TP/SL protection;
- TP/SL trigger -> market or limit exit;
- market/limit partial close with explicit size;
- persistent paper state restored after restart;
- Trading Overview BTC quote moved to top;
- LONG green / SHORT red UI semantics;
- optional user-controlled auto execution of executable AI entry plans with mandatory TP/SL.
"""
from __future__ import annotations

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

VERSION = "1.8.1"
BUILD = "1811"
AI_ONLY = True
PAPER_ONLY = True

AI_MAX_LEVERAGE = Decimal("20")
AI_MAX_NOTIONAL_USDT = Decimal("3500")
AI_MAX_ESTIMATED_STOP_LOSS_USDT = Decimal("100")
PAPER_ENTRY_TTL_SEC = 60 * 60


def _d(value, name="value"):
    return v172._decimal(value, name)


def _now():
    return time.time()


def _paper_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _active_status(tier):
    return str((tier or {}).get("status") or "") in (
        "live",
        "filled",
        "partially_filled",
        "exit_live",
    )


class PaperEngineV181(v180.AIOnlyEngineV180):
    """AI-only paper engine. No method in this class writes to OKX."""

    def connect(self):
        # Reuse authenticated/read-only connection for market/account display.
        super().connect()
        self.emit(
            "log",
            "V1.8.1 Paper Execution：仅使用OKX读取行情；所有开仓/平仓/改单/撤单均为本地模拟。",
        )

    def _set_leverage(self, *_args, **_kwargs):
        raise legacy_engine.Halt("V1.8.1 Paper禁止调用交易所杠杆写接口")

    def _submit_open(self, *_args, **_kwargs):
        raise legacy_engine.Halt("V1.8.1 Paper禁止调用交易所下单接口")

    def _submit_close(self, *_args, **_kwargs):
        raise legacy_engine.Halt("V1.8.1 Paper禁止调用交易所平仓接口")

    def reconcile(self):
        self._paper_tick()

    def arm(self, _settings=None):
        with self._ai_lock:
            if not self.store:
                raise legacy_engine.Halt("先连接OKX模拟盘")
            if not self.x.demo:
                raise legacy_engine.Halt("V1.8.1 Paper Execution 仅允许在 OKX模拟盘连接下运行")
            self._ensure_paper_state()
            self.enabled = True
            self.stopped = False
            self.poll_at = time.monotonic()
            if self.store.data.get("active"):
                self.emit(
                    "log",
                    "检测到旧版本active记录：V1.8.1 Paper不会读取或修改该真实/模拟交易所订单，仅管理paper_execution状态。",
                )
            self.emit(
                "log",
                "Paper AI通道已启用：双档并行 / 市价限价 / 部分减仓 / 改单撤单 / 60分钟自动撤单。",
            )

    def _ensure_paper_state(self):
        paper = self.store.data.setdefault("paper_execution", {})
        paper.setdefault("tiers", {"1": None, "2": None})
        paper.setdefault("close_orders", [])
        paper.setdefault("events", [])
        paper.setdefault("results", {})
        paper.setdefault("last_price", None)
        paper.setdefault("auto_execute_plans", False)
        paper.setdefault("updated_at", _now())
        if set(paper["tiers"].keys()) != {"1", "2"}:
            paper["tiers"] = {
                "1": paper["tiers"].get("1"),
                "2": paper["tiers"].get("2"),
            }
        self.store.save()
        return paper

    def _paper(self):
        if not self.store:
            raise legacy_engine.Halt("KAYTRADE尚未连接")
        return self._ensure_paper_state()

    def set_auto_execute_plans(self, enabled):
        """User-controlled Paper setting. AI cannot enable live exchange writes."""
        with self._ai_lock:
            if not self.store:
                raise legacy_engine.Halt("先连接 OKX模拟盘")
            if not self.enabled:
                raise legacy_engine.Halt("先启用 Paper AI交易通道，再开启AI方案自动执行")
            paper = self._paper()
            paper["auto_execute_plans"] = bool(enabled)
            paper["updated_at"] = _now()
            self.store.save()
            state = "开启" if enabled else "关闭"
            self.emit(
                "log",
                f"AI方案自动执行已{state}：仅Paper；自动入场必须同时包含TP和SL。",
            )
            return {
                "version": VERSION,
                "build": BUILD,
                "paper_only": True,
                "auto_execute_plans": bool(enabled),
                "requires_take_profit": True,
                "requires_stop_loss": True,
            }

    def _set_plan_status(self, item, status, detail=""):
        if not isinstance(item, dict):
            return
        item["status"] = str(status)
        item["detail"] = str(detail or "")[:1000]
        item["updated_at"] = _now()
        if self.store:
            tier = str(int(item.get("tier") or 1))
            self.store.data.setdefault("ai_tiers", {})[tier] = item
            self.store.save()

    def _auto_proposal_from_plan(self, plan, item):
        """Convert a recommendation into an executable paper proposal."""
        direction = str(plan.get("direction") or "").lower()
        order_type = str(plan.get("order_type") or "market").lower()
        tier = int(plan.get("tier") or 1)

        tp = plan.get("take_profit")
        sl = plan.get("stop_loss")
        if tp in (None, "") or sl in (None, ""):
            raise legacy_engine.Halt("自动执行要求每个入场方案必须同时设置 take_profit 和 stop_loss")

        size = plan.get("size")
        leverage = plan.get("leverage")
        missing = []
        if direction not in ("long", "short"):
            missing.append("direction")
        if size in (None, ""):
            missing.append("size")
        if leverage in (None, ""):
            missing.append("leverage")
        if order_type not in ("market", "limit"):
            missing.append("order_type")
        if missing:
            raise legacy_engine.Halt("自动执行方案缺少/无效字段：" + ", ".join(missing))

        proposal_id = str(
            plan.get("proposal_id")
            or f"autoplan-{item.get('plan_id') or uuid.uuid4().hex}"
        )
        proposal = {
            "proposal_id": proposal_id,
            "plan_id": item.get("plan_id"),
            "action": "open",
            "tier": tier,
            "direction": direction,
            "order_type": order_type,
            "size": size,
            "leverage": leverage,
            "take_profit": tp,
            "stop_loss": sl,
            "reason": str(plan.get("reason") or "")[:1000],
            "operation_advice": str(
                plan.get("operation_advice") or plan.get("advice") or ""
            )[:1000],
            "tp_exit_type": str(plan.get("tp_exit_type") or "market").lower(),
            "sl_exit_type": str(plan.get("sl_exit_type") or "market").lower(),
        }
        if proposal["tp_exit_type"] == "limit":
            proposal["tp_limit_price"] = plan.get("tp_limit_price")
        if proposal["sl_exit_type"] == "limit":
            proposal["sl_limit_price"] = plan.get("sl_limit_price")

        if order_type == "limit":
            limit_price = plan.get("limit_price")
            if limit_price in (None, ""):
                limit_price = plan.get("suggested_entry", plan.get("entry"))
            if limit_price in (None, ""):
                raise legacy_engine.Halt("限价AI方案自动执行必须包含 limit_price 或 suggested_entry")
            proposal["limit_price"] = limit_price
        return proposal

    def publish_ai_plan(self, plan):
        """Publish recommendation; optionally convert it into a Paper entry immediately."""
        with self._ai_lock:
            if not isinstance(plan, dict):
                raise legacy_engine.Halt("AI推荐计划必须为JSON对象")

            item = self._record_ai_plan(plan, status="RECOMMENDED")
            paper = self._paper()
            auto_on = bool(paper.get("auto_execute_plans"))
            base = {
                "version": VERSION,
                "build": BUILD,
                "mode": "PAPER_EXECUTION",
                "paper_only": True,
                "auto_execute_plans": auto_on,
                "plan": item,
            }

            if str(plan.get("action") or "open").lower() != "open":
                self.emit("log", f"AI推荐计划已更新：第{item['tier']}档；非入场方案不触发自动挂单")
                return dict(base, status="RECOMMENDED", auto_execution="NOT_ENTRY")

            if not auto_on:
                self.emit(
                    "log",
                    f"AI推荐计划已更新：第{item['tier']}档 · 自动执行关闭，仅展示方案",
                )
                return dict(base, status="RECOMMENDED", auto_execution="OFF")

            if not self.enabled:
                self._set_plan_status(item, "AUTO_BLOCKED", "Paper AI交易通道未启用")
                self.emit("log", f"第{item['tier']}档AI方案未自动执行：Paper AI交易通道未启用")
                return dict(
                    base,
                    status="AUTO_BLOCKED",
                    auto_execution="BLOCKED",
                    error="Paper AI交易通道未启用",
                )

            try:
                proposal = self._auto_proposal_from_plan(plan, item)
                execution = self.submit_ai_trade(proposal)
            except legacy_engine.Halt as exc:
                message = str(exc)
                status = "AUTO_REJECTED_NO_TP_SL" if (
                    "take_profit" in message or "stop_loss" in message
                ) else "AUTO_REJECTED"
                self._set_plan_status(item, status, message)
                self._event(
                    "AUTO_PLAN_REJECTED",
                    {"tier": item["tier"], "detail": message},
                )
                return dict(
                    base,
                    status=status,
                    auto_execution="REJECTED",
                    error=message,
                )

            # submit_ai_trade records the executable copy of the plan. Remove the
            # earlier RECOMMENDED duplicate so the dashboard shows one row per plan.
            history = self.store.data.get("ai_plan_history") or []
            try:
                history.remove(item)
            except ValueError:
                pass
            latest = history[-1] if history else item
            latest["detail"] = "AI推荐方案已自动转换为Paper委托"
            self.store.data.setdefault("ai_tiers", {})[str(item["tier"])] = latest
            self.store.save()
            self._event(
                "AUTO_PLAN_EXECUTED",
                {
                    "tier": item["tier"],
                    "detail": f"第{item['tier']}档AI方案已自动执行为{_order_type(execution.get('order_type'))}Paper委托",
                },
            )
            return {
                **base,
                "status": "AUTO_EXECUTED",
                "auto_execution": "EXECUTED",
                "plan": latest,
                "execution": execution,
            }

    def _event(self, event, payload):
        paper = self._paper()
        row = {"time": _now(), "event": event, **dict(payload)}
        paper["events"].append(row)
        del paper["events"][:-100]
        paper["updated_at"] = _now()
        self.store.save()
        self.emit("log", f"Paper · {event} · {payload.get('detail') or payload.get('tier') or ''}")
        return row

    def _ticker_price(self):
        ticker = self.x.ticker()
        return _d(ticker.get("last"), "OKX last")

    def _tier(self, tier, require=False):
        key = str(int(tier))
        if key not in ("1", "2"):
            raise legacy_engine.Halt("tier 仅支持 1 或 2")
        item = self._paper()["tiers"].get(key)
        if require and not isinstance(item, dict):
            raise legacy_engine.Halt(f"第{key}档当前没有Paper订单")
        return key, item

    def _same_direction_guard(self, direction, exclude_tier=None):
        for key, item in self._paper()["tiers"].items():
            if key == str(exclude_tier):
                continue
            if isinstance(item, dict) and _active_status(item):
                if item.get("direction") != direction:
                    raise legacy_engine.Halt("V1.8.1 双档并行仅允许同方向；另一档当前方向相反")

    def _risk_totals(self, candidate=None, replace_tier=None):
        total_notional = Decimal("0")
        total_loss = Decimal("0")
        for key, item in self._paper()["tiers"].items():
            if key == str(replace_tier):
                continue
            if isinstance(item, dict) and _active_status(item):
                total_notional += Decimal(str(item.get("notional_usdt") or 0))
                total_loss += Decimal(str(item.get("estimated_stop_loss_usdt") or 0))
        if candidate:
            total_notional += candidate["notional"]
            total_loss += candidate["estimated_loss"]
        if total_notional > AI_MAX_NOTIONAL_USDT:
            raise legacy_engine.Halt(
                f"两档合计名义仓位 {total_notional:.4f} USDT 超过 {AI_MAX_NOTIONAL_USDT} USDT"
            )
        if total_loss > AI_MAX_ESTIMATED_STOP_LOSS_USDT:
            raise legacy_engine.Halt(
                f"两档合计估算止损 {total_loss:.4f} USDT 超过 {AI_MAX_ESTIMATED_STOP_LOSS_USDT} USDT"
            )
        return total_notional, total_loss

    def _validate_paper_open(self, proposal, replace_tier=None):
        direction = str(proposal.get("direction") or "").lower()
        if direction not in ("long", "short"):
            raise legacy_engine.Halt("direction 必须为 long 或 short")
        self._same_direction_guard(direction, exclude_tier=replace_tier)

        order_type = str(proposal.get("order_type") or "market").lower()
        if order_type not in ("market", "limit"):
            raise legacy_engine.Halt("order_type 必须为 market 或 limit")

        qty = _d(proposal.get("size"), "size")
        leverage = _d(proposal.get("leverage", 1), "leverage")
        if leverage > AI_MAX_LEVERAGE:
            raise legacy_engine.Halt(f"leverage 必须≤{AI_MAX_LEVERAGE}x")

        meta = self.x.instrument()
        minimum = _d(meta.get("minSz"), "minSz")
        lot = _d(meta.get("lotSz"), "lotSz")
        tick = _d(meta.get("tickSz"), "tickSz")
        if qty < minimum or qty % lot != 0:
            raise legacy_engine.Halt(f"size 必须≥{minimum} 且按 lotSz={lot} 递增")

        last = self._ticker_price()
        limit_price = None
        if order_type == "limit":
            limit_price = _d(proposal.get("limit_price"), "limit_price")
            if limit_price % tick != 0:
                raise legacy_engine.Halt(f"limit_price 必须按 tickSz={tick} 递增")
            entry_ref = limit_price
        else:
            entry_ref = last

        tp = _d(proposal.get("take_profit"), "take_profit")
        sl = _d(proposal.get("stop_loss"), "stop_loss")
        if direction == "long" and not (sl < entry_ref < tp):
            raise legacy_engine.Halt("LONG 必须满足 SL < 入场参考价 < TP")
        if direction == "short" and not (tp < entry_ref < sl):
            raise legacy_engine.Halt("SHORT 必须满足 TP < 入场参考价 < SL")

        tp_exit_type = str(proposal.get("tp_exit_type") or "market").lower()
        sl_exit_type = str(proposal.get("sl_exit_type") or "market").lower()
        if tp_exit_type not in ("market", "limit") or sl_exit_type not in ("market", "limit"):
            raise legacy_engine.Halt("tp_exit_type / sl_exit_type 必须为 market 或 limit")
        tp_limit = None
        sl_limit = None
        if tp_exit_type == "limit":
            tp_limit = _d(proposal.get("tp_limit_price"), "tp_limit_price")
            if tp_limit % tick != 0:
                raise legacy_engine.Halt(f"tp_limit_price 必须按 tickSz={tick} 递增")
        if sl_exit_type == "limit":
            sl_limit = _d(proposal.get("sl_limit_price"), "sl_limit_price")
            if sl_limit % tick != 0:
                raise legacy_engine.Halt(f"sl_limit_price 必须按 tickSz={tick} 递增")

        unit = _d(meta.get("ctVal"), "ctVal") * _d(meta.get("ctMult") or "1", "ctMult")
        notional = qty * unit * entry_ref
        estimated_loss = qty * unit * abs(entry_ref - sl) + notional * Decimal("0.0012")
        candidate = {"notional": notional, "estimated_loss": estimated_loss}
        self._risk_totals(candidate, replace_tier=replace_tier)

        return {
            "direction": direction,
            "order_type": order_type,
            "qty": qty,
            "leverage": leverage,
            "last": last,
            "entry_ref": entry_ref,
            "limit_price": limit_price,
            "tp": tp,
            "sl": sl,
            "tp_exit_type": tp_exit_type,
            "sl_exit_type": sl_exit_type,
            "tp_limit_price": tp_limit,
            "sl_limit_price": sl_limit,
            "notional": notional,
            "estimated_loss": estimated_loss,
            "unit": unit,
        }

    def _fill_entry(self, item, price):
        item["filled_size"] = float(item["size"])
        item["remaining_entry_size"] = 0.0
        item["entry_price"] = float(price)
        item["status"] = "filled"
        item["filled_at"] = _now()
        item["order_state"] = "filled"
        self._event(
            "ENTRY_FILLED",
            {
                "tier": item["tier"],
                "paper_order_id": item["paper_order_id"],
                "detail": f"第{item['tier']}档 {_order_type(item['order_type'])} 入场成交 @{float(price):.2f}",
            },
        )

    def submit_ai_trade(self, proposal):
        with self._ai_lock:
            if not self.enabled:
                raise legacy_engine.Halt("Paper AI交易通道未启用")
            if not isinstance(proposal, dict):
                raise legacy_engine.Halt("AI交易请求必须为JSON对象")
            action = str(proposal.get("action") or "").lower()
            if action == "close":
                return self.paper_close_position(proposal)
            if action != "open":
                raise legacy_engine.Halt("action 必须为 open 或 close")

            proposal_id = str(proposal.get("proposal_id") or uuid.uuid4().hex)
            paper = self._paper()
            if proposal_id in paper["results"]:
                return dict(paper["results"][proposal_id], duplicate=True)

            key, current = self._tier(proposal.get("tier") or 1)
            if isinstance(current, dict) and _active_status(current):
                raise legacy_engine.Halt(f"第{key}档已有活动Paper订单/持仓，请先撤销或平仓")

            checked = self._validate_paper_open(proposal)
            accepted = _now()
            item = {
                "paper_only": True,
                "version": VERSION,
                "tier": int(key),
                "proposal_id": proposal_id,
                "paper_order_id": _paper_id("entry"),
                "direction": checked["direction"],
                "order_type": checked["order_type"],
                "limit_price": float(checked["limit_price"]) if checked["limit_price"] is not None else None,
                "requested_entry": float(checked["entry_ref"]),
                "size": float(checked["qty"]),
                "filled_size": 0.0,
                "remaining_entry_size": float(checked["qty"]),
                "entry_price": None,
                "leverage": float(checked["leverage"]),
                "take_profit": float(checked["tp"]),
                "stop_loss": float(checked["sl"]),
                "tp_exit_type": checked["tp_exit_type"],
                "sl_exit_type": checked["sl_exit_type"],
                "tp_limit_price": float(checked["tp_limit_price"]) if checked["tp_limit_price"] is not None else None,
                "sl_limit_price": float(checked["sl_limit_price"]) if checked["sl_limit_price"] is not None else None,
                "exit_order": None,
                "status": "live" if checked["order_type"] == "limit" else "accepted",
                "order_state": "live" if checked["order_type"] == "limit" else "accepted",
                "accepted_at": accepted,
                "cancel_deadline": accepted + PAPER_ENTRY_TTL_SEC,
                "reason": str(proposal.get("reason") or "")[:1000],
                "operation_advice": str(proposal.get("operation_advice") or "")[:1000],
                "notional_usdt": float(checked["notional"]),
                "estimated_stop_loss_usdt": float(checked["estimated_loss"]),
            }
            paper["tiers"][key] = item
            paper["updated_at"] = _now()
            self.store.save()

            if checked["order_type"] == "market":
                self._fill_entry(item, checked["last"])
                self.store.save()
            else:
                self._event(
                    "ENTRY_ACCEPTED",
                    {
                        "tier": int(key),
                        "paper_order_id": item["paper_order_id"],
                        "detail": f"第{key}档限价入场 @{item['limit_price']:.2f}，60分钟未成交自动撤单",
                    },
                )

            self._record_ai_plan(proposal, proposal_id=proposal_id, status="PAPER_" + item["status"].upper())
            result = {
                "version": VERSION,
                "build": BUILD,
                "mode": "PAPER_EXECUTION",
                "status": item["status"].upper(),
                "proposal_id": proposal_id,
                "tier": int(key),
                "paper_order_id": item["paper_order_id"],
                "direction": item["direction"],
                "order_type": item["order_type"],
                "limit_price": item["limit_price"],
                "size": item["size"],
                "take_profit": item["take_profit"],
                "stop_loss": item["stop_loss"],
                "cancel_deadline": item["cancel_deadline"],
            }
            paper["results"][proposal_id] = result
            self.store.save()
            return result

    def cancel_paper_entry(self, payload):
        with self._ai_lock:
            key, item = self._tier(payload.get("tier"), require=True)
            if item.get("status") not in ("live", "partially_filled"):
                raise legacy_engine.Halt("只有未完全成交的Paper入场委托可以撤销")
            item["remaining_entry_size"] = 0.0
            item["status"] = "filled" if float(item.get("filled_size") or 0) > 0 else "canceled"
            item["order_state"] = "canceled"
            item["canceled_at"] = _now()
            self.store.save()
            self._event("ENTRY_CANCELED", {"tier": int(key), "detail": f"第{key}档入场委托已撤销"})
            return {"status": "CANCELED", "tier": int(key), "paper_order_id": item["paper_order_id"]}

    def amend_paper_entry(self, payload):
        with self._ai_lock:
            key, item = self._tier(payload.get("tier"), require=True)
            if item.get("status") not in ("live", "partially_filled"):
                raise legacy_engine.Halt("只有活动中的限价入场委托可以修改")
            if item.get("order_type") != "limit":
                raise legacy_engine.Halt("市价入场成交后不存在可修改的入场委托")

            proposal = {
                "tier": int(key),
                "direction": item["direction"],
                "order_type": "limit",
                "limit_price": payload.get("new_price", item["limit_price"]),
                "size": payload.get("new_size", item["size"]),
                "leverage": item["leverage"],
                "take_profit": item["take_profit"],
                "stop_loss": item["stop_loss"],
                "tp_exit_type": item["tp_exit_type"],
                "sl_exit_type": item["sl_exit_type"],
                "tp_limit_price": item.get("tp_limit_price"),
                "sl_limit_price": item.get("sl_limit_price"),
            }
            checked = self._validate_paper_open(proposal, replace_tier=key)
            new_size = float(checked["qty"])
            filled = float(item.get("filled_size") or 0)
            if new_size < filled:
                raise legacy_engine.Halt("new_size 不能小于已成交数量")
            item["limit_price"] = float(checked["limit_price"])
            item["requested_entry"] = float(checked["entry_ref"])
            item["size"] = new_size
            item["remaining_entry_size"] = max(0.0, new_size - filled)
            item["notional_usdt"] = float(checked["notional"])
            item["estimated_stop_loss_usdt"] = float(checked["estimated_loss"])
            item["amended_at"] = _now()
            # accepted_at/cancel_deadline deliberately stay unchanged.
            self.store.save()
            self._event(
                "ENTRY_AMENDED",
                {"tier": int(key), "detail": f"第{key}档入场改为 {item['limit_price']:.2f} / {new_size:g}张"},
            )
            return {
                "status": "AMENDED",
                "tier": int(key),
                "limit_price": item["limit_price"],
                "size": item["size"],
                "cancel_deadline": item["cancel_deadline"],
            }

    def amend_paper_protection(self, payload):
        with self._ai_lock:
            key, item = self._tier(payload.get("tier"), require=True)
            if not _active_status(item):
                raise legacy_engine.Halt("当前档位没有可修改保护的活动订单/持仓")

            entry_ref = Decimal(str(item.get("entry_price") or item.get("requested_entry")))
            tp = _d(payload.get("take_profit", item["take_profit"]), "take_profit")
            sl = _d(payload.get("stop_loss", item["stop_loss"]), "stop_loss")
            if item["direction"] == "long" and not (sl < entry_ref < tp):
                raise legacy_engine.Halt("LONG保护必须满足 SL < 入场价 < TP")
            if item["direction"] == "short" and not (tp < entry_ref < sl):
                raise legacy_engine.Halt("SHORT保护必须满足 TP < 入场价 < SL")

            tp_type = str(payload.get("tp_exit_type", item.get("tp_exit_type") or "market")).lower()
            sl_type = str(payload.get("sl_exit_type", item.get("sl_exit_type") or "market")).lower()
            if tp_type not in ("market", "limit") or sl_type not in ("market", "limit"):
                raise legacy_engine.Halt("保护退出方式必须为 market 或 limit")
            meta = self.x.instrument()
            tick = _d(meta.get("tickSz"), "tickSz")
            tp_limit = payload.get("tp_limit_price", item.get("tp_limit_price"))
            sl_limit = payload.get("sl_limit_price", item.get("sl_limit_price"))
            if tp_type == "limit":
                tp_limit = _d(tp_limit, "tp_limit_price")
                if tp_limit % tick != 0:
                    raise legacy_engine.Halt(f"tp_limit_price 必须按 tickSz={tick} 递增")
            else:
                tp_limit = None
            if sl_type == "limit":
                sl_limit = _d(sl_limit, "sl_limit_price")
                if sl_limit % tick != 0:
                    raise legacy_engine.Halt(f"sl_limit_price 必须按 tickSz={tick} 递增")
            else:
                sl_limit = None

            item["take_profit"] = float(tp)
            item["stop_loss"] = float(sl)
            item["tp_exit_type"] = tp_type
            item["sl_exit_type"] = sl_type
            item["tp_limit_price"] = float(tp_limit) if tp_limit is not None else None
            item["sl_limit_price"] = float(sl_limit) if sl_limit is not None else None
            item["exit_order"] = None
            item["protection_amended_at"] = _now()
            self.store.save()
            self._event("PROTECTION_AMENDED", {"tier": int(key), "detail": f"第{key}档TP/SL保护已更新"})
            return {
                "status": "PROTECTION_AMENDED",
                "tier": int(key),
                "take_profit": item["take_profit"],
                "stop_loss": item["stop_loss"],
                "tp_exit_type": item["tp_exit_type"],
                "sl_exit_type": item["sl_exit_type"],
                "tp_limit_price": item["tp_limit_price"],
                "sl_limit_price": item["sl_limit_price"],
            }

    def _position_remaining(self, item):
        return max(0.0, float(item.get("filled_size") or 0) - float(item.get("closed_size") or 0))

    def _apply_close(self, item, size, price, reason):
        remaining = self._position_remaining(item)
        amount = min(float(size), remaining)
        if amount <= 0:
            return 0.0
        item["closed_size"] = float(item.get("closed_size") or 0) + amount
        item["last_exit_price"] = float(price)
        item["last_exit_reason"] = reason
        item["last_exit_at"] = _now()
        if self._position_remaining(item) <= 1e-12:
            item["status"] = "closed"
            item["order_state"] = "closed"
            item["exit_order"] = None
        else:
            item["status"] = "filled"
            item["order_state"] = "filled"
        self.store.save()
        self._event(
            "POSITION_REDUCED",
            {"tier": item["tier"], "detail": f"第{item['tier']}档减仓 {amount:g}张 @{float(price):.2f} · {reason}"},
        )
        return amount

    def paper_close_position(self, payload):
        with self._ai_lock:
            order_type = str(payload.get("order_type") or payload.get("close_type") or "market").lower()
            if order_type not in ("market", "limit"):
                raise legacy_engine.Halt("close order_type 必须为 market 或 limit")
            target_tier = payload.get("tier")
            requested = _d(payload.get("size") or payload.get("close_size"), "close_size")
            last = self._ticker_price()
            limit_price = None
            if order_type == "limit":
                limit_price = _d(payload.get("limit_price"), "limit_price")

            targets = []
            if target_tier is not None:
                key, item = self._tier(target_tier, require=True)
                targets = [(key, item)]
            else:
                targets = [
                    (key, item)
                    for key, item in sorted(self._paper()["tiers"].items())
                    if isinstance(item, dict) and self._position_remaining(item) > 0
                ]
            available = sum(self._position_remaining(item) for _, item in targets)
            if float(requested) > available + 1e-12:
                raise legacy_engine.Halt(f"减仓数量 {requested} 超过Paper持仓 {available:g}")

            close_order = {
                "paper_close_id": _paper_id("close"),
                "order_type": order_type,
                "limit_price": float(limit_price) if limit_price is not None else None,
                "requested_size": float(requested),
                "remaining_size": float(requested),
                "tier": int(target_tier) if target_tier is not None else None,
                "status": "live" if order_type == "limit" else "accepted",
                "created_at": _now(),
                "reason": str(payload.get("reason") or "AI指定减仓")[:500],
            }
            self._paper()["close_orders"].append(close_order)
            self.store.save()
            if order_type == "market":
                self._execute_close_order(close_order, last)
            else:
                self._event(
                    "CLOSE_ACCEPTED",
                    {"detail": f"限价减仓 {float(requested):g}张 @{float(limit_price):.2f}"},
                )
            return dict(close_order)

    def _execute_close_order(self, close_order, price):
        remaining = float(close_order.get("remaining_size") or 0)
        target_tier = close_order.get("tier")
        targets = []
        if target_tier is not None:
            key, item = self._tier(target_tier, require=True)
            targets = [(key, item)]
        else:
            targets = [
                (key, item)
                for key, item in sorted(self._paper()["tiers"].items())
                if isinstance(item, dict) and self._position_remaining(item) > 0
            ]
        for _, item in targets:
            if remaining <= 1e-12:
                break
            reduced = self._apply_close(
                item,
                min(remaining, self._position_remaining(item)),
                price,
                close_order.get("reason") or "AI减仓",
            )
            remaining -= reduced
        close_order["remaining_size"] = max(0.0, remaining)
        close_order["status"] = "filled" if remaining <= 1e-12 else "partially_filled"
        close_order["fill_price"] = float(price)
        close_order["filled_at"] = _now()
        self.store.save()

    def _entry_crossed(self, item, price):
        limit_price = Decimal(str(item["limit_price"]))
        return price <= limit_price if item["direction"] == "long" else price >= limit_price

    def _close_limit_crossed(self, direction, limit_price, price):
        limit_price = Decimal(str(limit_price))
        # long position exits by sell limit; short exits by buy limit.
        return price >= limit_price if direction == "long" else price <= limit_price

    def _triggered(self, item, kind, price):
        trigger = Decimal(str(item["take_profit"] if kind == "tp" else item["stop_loss"]))
        if item["direction"] == "long":
            return price >= trigger if kind == "tp" else price <= trigger
        return price <= trigger if kind == "tp" else price >= trigger

    def _start_protection_exit(self, item, kind, price):
        exit_type = item[f"{kind}_exit_type"]
        if exit_type == "market":
            self._apply_close(item, self._position_remaining(item), price, kind.upper())
            return
        limit_price = item.get(f"{kind}_limit_price")
        item["exit_order"] = {
            "paper_exit_id": _paper_id(kind),
            "reason": kind,
            "order_type": "limit",
            "limit_price": float(limit_price),
            "size": self._position_remaining(item),
            "status": "live",
            "triggered_at": _now(),
        }
        item["status"] = "exit_live"
        item["order_state"] = f"{kind}_limit_live"
        self.store.save()
        self._event(
            "PROTECTION_TRIGGERED",
            {
                "tier": item["tier"],
                "detail": f"第{item['tier']}档 {kind.upper()} 已触发，挂限价退出 @{float(limit_price):.2f}",
            },
        )

    def _paper_tick(self):
        if not self.enabled or not self.store:
            return
        paper = self._paper()
        price = self._ticker_price()
        paper["last_price"] = float(price)
        now = _now()

        for key, item in paper["tiers"].items():
            if not isinstance(item, dict):
                continue

            if item.get("status") == "live":
                if now >= float(item.get("cancel_deadline") or 0):
                    item["remaining_entry_size"] = 0.0
                    item["status"] = "canceled"
                    item["order_state"] = "auto_canceled_60m"
                    item["canceled_at"] = now
                    self.store.save()
                    self._event("AUTO_CANCEL_60M", {"tier": int(key), "detail": f"第{key}档60分钟未成交，已自动撤单"})
                    continue
                if item.get("order_type") == "limit" and self._entry_crossed(item, price):
                    self._fill_entry(item, Decimal(str(item["limit_price"])))
                    self.store.save()

            if self._position_remaining(item) <= 1e-12:
                continue

            exit_order = item.get("exit_order")
            if isinstance(exit_order, dict) and exit_order.get("status") == "live":
                if self._close_limit_crossed(item["direction"], exit_order["limit_price"], price):
                    self._apply_close(
                        item,
                        min(float(exit_order["size"]), self._position_remaining(item)),
                        Decimal(str(exit_order["limit_price"])),
                        exit_order.get("reason", "PROTECTION").upper(),
                    )
                    exit_order["status"] = "filled"
                    exit_order["filled_at"] = now
                    self.store.save()
                continue

            if self._triggered(item, "sl", price):
                self._start_protection_exit(item, "sl", price)
            elif self._triggered(item, "tp", price):
                self._start_protection_exit(item, "tp", price)

        for close_order in paper["close_orders"]:
            if close_order.get("status") != "live":
                continue
            direction = None
            if close_order.get("tier") is not None:
                _, item = self._tier(close_order["tier"], require=True)
                direction = item["direction"]
            else:
                for _, item in sorted(paper["tiers"].items()):
                    if isinstance(item, dict) and self._position_remaining(item) > 0:
                        direction = item["direction"]
                        break
            if direction and self._close_limit_crossed(direction, close_order["limit_price"], price):
                self._execute_close_order(close_order, Decimal(str(close_order["limit_price"])))

        paper["updated_at"] = now
        self.store.save()

    def cycle(self):
        with self._ai_lock:
            self.poll_at = time.monotonic()
            self._paper_tick()

    def ai_state(self):
        state = {
            "version": VERSION,
            "build": BUILD,
            "mode": "PAPER_EXECUTION",
            "paper_only": True,
            "connected": bool(self.store),
            "ai_enabled": bool(self.enabled),
            "live_ai_writes": False,
            "supported_entry_order_types": ["market", "limit"],
            "supported_close_order_types": ["market", "limit"],
            "supports_partial_close": True,
            "supports_amend_entry": True,
            "supports_cancel_entry": True,
            "supports_amend_protection": True,
            "supports_two_same_direction_tiers": True,
            "entry_auto_cancel_seconds": PAPER_ENTRY_TTL_SEC,
            "supports_auto_execute_ai_plans": True,
            "auto_execute_requires_tp_sl": True,
            "limits": {
                "max_leverage": str(AI_MAX_LEVERAGE),
                "max_notional_usdt": str(AI_MAX_NOTIONAL_USDT),
                "max_estimated_stop_loss_usdt": str(AI_MAX_ESTIMATED_STOP_LOSS_USDT),
            },
        }
        if self.store:
            paper = self._paper()
            state["paper_execution"] = paper
            state["auto_execute_plans"] = bool(paper.get("auto_execute_plans"))
            state["recent_ai_plans"] = (self.store.data.get("ai_plan_history") or [])[-8:]
        try:
            state["ticker"] = self.x.ticker()
        except Exception:
            state["ticker"] = {}
        return state


def _order_type(value):
    return "限价" if str(value).lower() == "limit" else "市价"


def _tier_ui_text(item):
    if not isinstance(item, dict):
        return "等待 AI 计划"
    status = str(item.get("order_state") or item.get("status") or "—").upper()
    entry = item.get("entry_price") or item.get("limit_price") or item.get("requested_entry")
    remaining = max(0.0, float(item.get("filled_size") or 0) - float(item.get("closed_size") or 0))
    return (
        f"{_order_type(item.get('order_type'))} · {status}\n"
        f"方向：{'LONG' if item.get('direction') == 'long' else 'SHORT'}   "
        f"入场：{v180._fmt_px(entry)}   持仓：{remaining:g}张\n"
        f"TP：{v180._fmt_px(item.get('take_profit'))} ({_order_type(item.get('tp_exit_type'))})   "
        f"SL：{v180._fmt_px(item.get('stop_loss'))} ({_order_type(item.get('sl_exit_type'))})"
    )


def _find_label_for_var(widget, variable):
    try:
        if isinstance(widget, app.tk.Label) and str(widget.cget("textvariable")) == str(variable):
            return widget
    except Exception:
        pass
    try:
        for child in widget.winfo_children():
            found = _find_label_for_var(child, variable)
            if found is not None:
                return found
    except Exception:
        pass
    return None


def _find_label_text(widget, text_value):
    try:
        if isinstance(widget, app.tk.Label) and str(widget.cget("text")) == str(text_value):
            return widget
    except Exception:
        pass
    try:
        for child in widget.winfo_children():
            found = _find_label_text(child, text_value)
            if found is not None:
                return found
    except Exception:
        pass
    return None


def _move_btc_quote_to_top(owner):
    quote = getattr(owner, "quote", None)
    plan = getattr(owner, "_v180_plan_card", None)
    if quote is None or plan is None:
        return
    try:
        quote.pack_forget()
        quote.pack(fill="x", pady=(0, 12), before=plan)
    except Exception:
        pass


def _install_auto_execute_setting(owner):
    try:
        dash = owner.book.pages[3].body
        execution = owner._v180_execution_card
    except Exception:
        return
    if getattr(owner, "_v181_auto_exec_card", None) is not None:
        return

    card = visual.Card(dash, height=104)
    card.pack(fill="x", pady=(0, 12), before=execution)
    row = app.tk.Frame(card.body, bg=visual.PANEL)
    row.pack(fill="both", expand=True)

    left = app.tk.Frame(row, bg=visual.PANEL)
    left.pack(side="left", fill="both", expand=True)
    visual.label(
        left,
        text="AI方案自动执行",
        size=15,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL,
    ).pack(anchor="w")
    owner._v181_auto_exec_status_var = app.tk.StringVar(value="关闭")
    owner._v181_auto_exec_note_var = app.tk.StringVar(
        value="AI推荐仅展示；开启后，完整入场方案会立即转换为Paper委托。TP + SL 为强制条件。"
    )
    visual.label(
        left,
        variable=owner._v181_auto_exec_status_var,
        size=11,
        bold=True,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(4, 1))
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
        command=lambda: _toggle_auto_execute(owner),
        variant="accent",
        width=150,
        height=40,
    )
    owner._v181_auto_exec_button.pack(side="right", padx=(18, 0), pady=10)
    owner._v181_auto_exec_card = card


def _toggle_auto_execute(owner):
    engine = getattr(owner, "engine", None)
    if engine is None or not callable(getattr(engine, "set_auto_execute_plans", None)):
        app.messagebox.showerror("未连接", "先连接 OKX模拟盘并启用 Paper AI交易通道")
        return
    try:
        paper = engine._paper()
        target = not bool(paper.get("auto_execute_plans"))
        engine.set_auto_execute_plans(target)
        _refresh_v181_dashboard(owner)
    except Exception as exc:
        app.messagebox.showerror("自动执行设置失败", str(exc))


def _refresh_v181_dashboard(owner):
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
            status_var.set("已开启 · AI方案到达即自动Paper挂单" if auto_on else "关闭 · AI推荐仅展示")
        if note_var is not None:
            note_var.set(
                "强制条件：方向 / 数量 / 杠杆 / 入场方式 / TP / SL 完整；限价方案还必须有入场价。"
                if auto_on else
                "AI推荐仅展示；开启后，完整入场方案会立即转换为Paper委托。TP + SL 为强制条件。"
            )
        if button is not None:
            button.configure(
                text="关闭自动执行" if auto_on else "开启自动执行",
                variant="danger" if auto_on else "accent",
            )
        try:
            owner._v180_mode_var.set("PAPER · AUTO ON" if auto_on else "PAPER · MANUAL")
        except Exception:
            pass

        # Reuse the V1.8 KAYTRADE-native cards, but source execution state from paper tiers.
        if latest:
            direction = str(latest.get("direction") or "").lower()
            owner._v180_side_var.set(v180._side_cn(direction))
            owner._v180_type_var.set("委托：" + v180._order_type_cn(latest.get("order_type")))
            owner._v180_tier_var.set(f"档位：{latest.get('tier') or 1}")
            lev = latest.get("leverage")
            owner._v180_leverage_var.set(f"杠杆：{lev}×" if lev else "杠杆：—")
            entry = latest.get("limit_price") if str(latest.get("order_type")).lower() == "limit" else latest.get("suggested_entry")
            owner._v180_entry_var.set(v180._fmt_px(entry))
            owner._v180_tp_var.set(v180._fmt_px(latest.get("take_profit")))
            owner._v180_sl_var.set(v180._fmt_px(latest.get("stop_loss")))
            owner._v180_reason_var.set(str(latest.get("reason") or "—"))
            owner._v180_advice_var.set(str(latest.get("operation_advice") or "等待AI建议"))
            owner._v180_status_chip_var.set(str(latest.get("status") or "RECOMMENDED"))
            label = getattr(owner, "_v181_direction_label", None)
            if label is not None:
                label.configure(fg=visual.GREEN if direction == "long" else visual.RED if direction == "short" else visual.TEXT)
        else:
            owner._v180_status_chip_var.set("WAITING")

        rows = []
        for item in history[-5:][::-1]:
            stamp = time.strftime("%H:%M:%S", time.localtime(float(item.get("time") or _now())))
            direction = str(item.get("direction") or "WAIT").upper()
            kind = "LMT" if str(item.get("order_type")).lower() == "limit" else "MKT"
            entry = item.get("limit_price") if kind == "LMT" else item.get("suggested_entry")
            rows.append(
                f"{stamp}  T{item.get('tier') or 1}  {direction:<5} {kind}  "
                f"{v180._fmt_px(entry):>10}  {str(item.get('status') or '')[:15]}"
            )
        owner._v180_history_var.set("\n".join(rows) if rows else "暂无 AI 方案记录")

        t1 = tiers.get("1")
        t2 = tiers.get("2")
        owner._v180_tier1_var.set(_tier_ui_text(t1))
        owner._v180_tier2_var.set(_tier_ui_text(t2))
        for label, item in (
            (getattr(owner, "_v181_tier1_label", None), t1),
            (getattr(owner, "_v181_tier2_label", None), t2),
        ):
            if label is not None:
                direction = str((item or {}).get("direction") or "").lower()
                label.configure(
                    fg=visual.GREEN if direction == "long"
                    else visual.RED if direction == "short"
                    else visual.TEXT
                )

        active_items = [
            item for item in (t1, t2)
            if isinstance(item, dict) and _active_status(item)
        ]
        if active_items:
            item = next((x for x in active_items if x.get("filled_size")), active_items[0])
            state = str(item.get("order_state") or item.get("status") or "—").upper()
            owner._v180_order_state_var.set(state)
            owner._v180_order_type_var.set(_order_type(item.get("order_type")))
            owner._v180_order_px_var.set(v180._fmt_px(item.get("limit_price") or item.get("requested_entry")))
            owner._v180_fill_px_var.set(v180._fmt_px(item.get("entry_price")))
            remaining = sum(
                max(0.0, float(x.get("filled_size") or 0) - float(x.get("closed_size") or 0))
                for x in active_items
            )
            owner._v180_size_var.set(f"{remaining:g} 张")
            owner._v180_position_tp_var.set(v180._fmt_px(item.get("take_profit")))
            owner._v180_position_sl_var.set(v180._fmt_px(item.get("stop_loss")))
            owner._v180_proposal_var.set(str(item.get("proposal_id") or "—")[:22])
            owner._v180_position_badge_var.set("PAPER 持仓/委托")
            badge = getattr(owner, "_v181_position_badge_label", None)
            if badge is not None:
                direction = str(item.get("direction") or "").lower()
                badge.configure(
                    fg=visual.GREEN if direction == "long"
                    else visual.RED if direction == "short"
                    else visual.TEXT
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
            owner._v180_position_badge_var.set("Paper 空仓")
            badge = getattr(owner, "_v181_position_badge_label", None)
            if badge is not None:
                badge.configure(fg=visual.MUTED)
    except Exception:
        pass

    try:
        if owner.root.winfo_exists():
            owner.root.after(650, lambda: _refresh_v181_dashboard(owner))
    except Exception:
        pass


_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_ARM = app.App.arm
_PREVIOUS_BRIDGE_POST = v172._BridgeHandler.do_POST


def _app_init_v181(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    self.root.title("KAYTRADE 1.8.1 · PAPER AI · Build 1811")
    try:
        self.signal.set("V1.8.1 PAPER｜BTC行情只读｜AI方案可自动执行｜TP/SL强制｜双档 / 改单 / 撤单 / 60分钟自动撤单")
    except Exception:
        pass
    _move_btc_quote_to_top(self)
    _install_auto_execute_setting(self)
    try:
        self._v181_direction_label = _find_label_for_var(self._v180_plan_card, self._v180_side_var)
        self._v181_tier1_label = _find_label_text(self._v180_execution_card, "第一档执行方案")
        self._v181_tier2_label = _find_label_text(self._v180_execution_card, "第二档执行方案")
        self._v181_position_badge_label = _find_label_for_var(
            self._v180_execution_card, self._v180_position_badge_var
        )
    except Exception:
        self._v181_direction_label = None
        self._v181_tier1_label = None
        self._v181_tier2_label = None
        self._v181_position_badge_label = None
    self._v181_paper_ready = True
    self.root.after(250, lambda: _refresh_v181_dashboard(self))


def _app_arm_v181(self):
    if not self.engine:
        app.messagebox.showerror("未连接", "先连接 OKX模拟盘读取行情")
        return
    if not self.engine.x.demo:
        app.messagebox.showerror("仅Paper模式", "V1.8.1 Paper Execution 只在 OKX模拟盘连接下运行")
        return
    typed = app.simpledialog.askstring(
        "启用 Paper AI",
        "KAYTRADE V1.8.1 PAPER EXECUTION\n\n"
        "所有交易操作仅在本地模拟，不向OKX发送任何下单/改单/撤单请求。\n"
        "支持：市价/限价、双档同向挂单、60分钟自动撤单、修改入场、修改保护、"
        "TP/SL触发后市价/限价退出、指定数量市价/限价减仓。\n"
        "交易总览可开启「AI方案自动执行」；自动入场必须同时设置止盈TP和止损SL。\n\n"
        "确认启用请输入 PAPER",
        parent=self.root,
    )
    if typed and typed.strip().upper() == "PAPER":
        v172._start_ai_enable(self)


def _bridge_post_v181(self):
    if self.path not in (
        "/v1/paper/cancel-entry",
        "/v1/paper/amend-entry",
        "/v1/paper/amend-protection",
        "/v1/paper/close",
    ):
        return _PREVIOUS_BRIDGE_POST(self)
    if not self._require_auth():
        return
    try:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 65536:
            raise ValueError("invalid content length")
        import json
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        engine = self.bridge.owner.engine
        if not isinstance(engine, PaperEngineV181):
            raise legacy_engine.Halt("当前Runtime不是 V1.8.1 Paper Execution")
        routes = {
            "/v1/paper/cancel-entry": engine.cancel_paper_entry,
            "/v1/paper/amend-entry": engine.amend_paper_entry,
            "/v1/paper/amend-protection": engine.amend_paper_protection,
            "/v1/paper/close": engine.paper_close_position,
        }
        self._json(200, routes[self.path](payload))
    except legacy_engine.Halt as exc:
        self._json(409, {"error": str(exc)})
    except Exception as exc:
        self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


def apply():
    if getattr(model, "_kaytrade_v181_paper_applied", False):
        return

    v172.VERSION = VERSION
    v172.BUILD = BUILD
    v180.VERSION = VERSION
    v180.BUILD = BUILD

    for module in (model, runtime, v168, b1681, v170, v1701, v171):
        module.VERSION = VERSION
        module.BUILD = BUILD
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.8.1 · PAPER AI · Build 1811"

    model.STRATEGY_ENABLED = False
    model.AI_ONLY = True

    app.Engine = PaperEngineV181
    legacy_engine.AIOnlyEngine = PaperEngineV181
    v172.AIOnlyEngine = PaperEngineV181

    # Reuse V1.8 cards, but feed them from paper state and color direction.
    v180._refresh_v180_dashboard = _refresh_v181_dashboard

    app.App.__init__ = _app_init_v181
    app.App.arm = _app_arm_v181
    v172._BridgeHandler.do_POST = _bridge_post_v181

    model._kaytrade_v181_paper_applied = True
    app.App._kaytrade_v181_paper_applied = True


apply()

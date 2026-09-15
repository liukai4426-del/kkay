"""KAYTRADE V1.6.3 production runtime overlay.

Changes on top of V1.6.2:
1) Route all strategy facades through the V1.6.3 outer-only model.
2) Safely migrate stale historical OKX 51290 permanent locks after read-only
   exposure/order verification.
3) Treat every newly observed OKX 51290 as a temporary soft pause; never write
   it into the permanent halt field.
4) Keep V1.6.2 1x LIMIT sizing, 1H ATR stop, 2R full TP and BE disabled.
"""
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path

from v162_update_patch import apply as apply_previous
apply_previous()

import app
import engine
import exchange
import v138_strategy_patch as v138
import v152_strategy_patch as v152_runtime
import v153_runtime_patch as v153_runtime
import v153_backtest_core as v153_bt
import v154_runtime_patch as v154_runtime
import v154_limit_log_fix as v154_limit
import v154_record_card_boll_fix as v154_record
import v160_update_patch as v160
import v161_update_patch as v161
import v162_update_patch as v162
import v163_model as model

VERSION = "1.6.3"
BUILD = "1630"
BOT_UPGRADE_CODE = "51290"
BOT_RETRY_SECONDS = (5.0, 10.0, 15.0, 30.0)

# Route the inherited production stack through V1.6.3 without rewriting the
# already-audited order lifecycle. V1.6.2's order-preparation closure reads its
# module globals at call time, so update those globals too.
v160.model = model
v161.model = model
v162.model = model
v162.VERSION = VERSION
v162.BUILD = BUILD
v152_runtime.model = model
v152_runtime.V152_VERSION = VERSION
v153_runtime.model = model
v153_runtime.VERSION = VERSION
v153_bt.model = model
v154_runtime.model = model
v154_limit.model = model
if hasattr(v154_record, "model"):
    v154_record.model = model

# Keep V1.6.2 execution semantics that remain valid in V1.6.3.
v138._prepare_order = v162._prepare_order_v162
v161._maybe_trigger_be = lambda owner: None
v162._write_probe = lambda: None

_PREVIOUS_CONNECT = engine.Engine.connect
_PREVIOUS_ARM = engine.Engine.arm
_PREVIOUS_HALT = engine.Engine.halt
_PREVIOUS_SUBMIT = v162._submit_v162


def _probe_target():
    return os.environ.get("KAYTRADE_STARTUP_PROBE", "").strip()


def _write_probe():
    target = _probe_target()
    if not target:
        return
    try:
        Path(target).write_text(
            f"PASS KAYTRADE {VERSION} Build {BUILD} outer_only=on rsi=30-70 "
            "outer_macd=required outer_4h=aligned_required all_boll=1x "
            "stop=1h_atr_1x tp=2r be=off okx_51290=soft_pause_and_legacy_migration\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _write_probe_error():
    target = _probe_target()
    if not target:
        return
    try:
        Path(target + ".error").write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


def _clean_halt(reason):
    try:
        return engine.normalize_halt_reason(reason)
    except Exception:
        return str(reason or "").strip()


def _is_legacy_51290_lock(reason):
    text = _clean_halt(reason)
    if BOT_UPGRADE_CODE not in text:
        return False
    low = text.lower()
    return (
        "trading bot engine currently upgrading" in low
        or "策略交易引擎正在升级" in text
        or "try again later" in low
    )


def _same_order(row, active):
    if not isinstance(row, dict) or not isinstance(active, dict):
        return False
    order_id = str(active.get("order_id") or "")
    client_id = str(active.get("client_id") or "")
    return bool(
        (order_id and str(row.get("ordId") or "") == order_id)
        or (client_id and str(row.get("clOrdId") or "") == client_id)
    )


def _order_has_exposure(row):
    if not isinstance(row, dict):
        return False
    state = str(row.get("state") or "")
    try:
        filled = float(row.get("accFillSz") or 0.0)
    except (TypeError, ValueError):
        filled = 0.0
    # A zero-fill canceled/rejected order is terminal and safe. A filled order,
    # partial fill, live order or unknown non-terminal state requires manual review.
    return filled > 0.0 or state not in ("canceled", "filled", "rejected")


def _migrate_legacy_51290_lock(owner):
    """Release only a known stale 51290 lock after read-only exposure checks."""
    store = getattr(owner, "store", None)
    if store is None or not _is_legacy_51290_lock(store.data.get("halt", "")):
        return False

    try:
        positions = owner.x.positions()
        orders = owner.x.orders()
        algos = owner.x.algos()
    except Exception as exc:
        owner.emit("log", "V1.6.3检测到历史51290故障锁，但只读核对暂时失败；保留故障锁等待再次核对：" + str(exc))
        return False

    if positions or orders or algos:
        store.data["halt"] = "历史51290故障锁：检测到BTC仓位或挂单，必须先核对现有风险"
        store.save()
        owner.emit("alarm", "V1.6.3检测到历史51290锁，同时账户仍存在BTC仓位/普通委托/策略委托；为避免重复下单，继续保留故障锁")
        return False

    active = store.data.get("active")
    if isinstance(active, dict) and (active.get("order_id") or active.get("client_id")):
        found = None
        try:
            found = owner.x.order(active.get("client_id", ""), active.get("order_id", ""))
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "")
            if code != "51603":
                owner.emit("log", "V1.6.3历史51290锁关联订单核对失败；保留故障锁：" + str(exc))
                return False
        if found is not None and _order_has_exposure(found):
            store.data["halt"] = "历史51290故障锁：关联订单存在成交或非终态，必须人工核对"
            store.save()
            owner.emit("alarm", "V1.6.3历史51290锁关联订单仍有成交/非终态证据；继续保留故障锁")
            return False

        try:
            history = owner.x.recent_orders() if hasattr(owner.x, "recent_orders") else []
        except Exception as exc:
            owner.emit("log", "V1.6.3历史51290锁历史订单核对失败；保留故障锁：" + str(exc))
            return False
        history_row = next((row for row in history if _same_order(row, active)), None)
        if history_row is not None and _order_has_exposure(history_row):
            store.data["halt"] = "历史51290故障锁：历史订单存在成交或非终态，必须人工核对"
            store.save()
            owner.emit("alarm", "V1.6.3历史51290锁在历史订单中发现成交/非终态证据；继续保留故障锁")
            return False

    old_active = active if isinstance(active, dict) else None
    store.data["active"] = None
    store.data["halt"] = ""
    store.save()
    try:
        store.record("V1.6.3历史51290锁安全迁移", {
            "client_id": (old_active or {}).get("client_id"),
            "order_id": (old_active or {}).get("order_id"),
            "positions": 0,
            "orders": 0,
            "algos": 0,
        })
    except Exception:
        pass
    owner.emit("log", "V1.6.3已安全清除历史51290故障锁：BTC仓位、普通委托、策略委托及关联订单均未发现风险；后续51290只软暂停并自动恢复")
    return True


def _schedule_51290_soft_pause(owner, reason=""):
    streak = int(getattr(owner, "_v162_51290_streak", 0) or 0) + 1
    owner._v162_51290_streak = streak
    delay = BOT_RETRY_SECONDS[min(streak - 1, len(BOT_RETRY_SECONDS) - 1)]
    owner._v162_51290_retry_at = time.monotonic() + delay
    existing_halt = ""
    store = getattr(owner, "store", None)
    if store is not None:
        existing_halt = _clean_halt(store.data.get("halt", ""))
    owner._v162_51290_resume = bool(owner.enabled and not owner.stopped and not existing_halt)
    owner.enabled = False
    owner.emit(
        "alarm",
        f"OKX错误码 51290｜Trading bot engine currently upgrading；仅暂停新开仓 {delay:.0f}s，不写入永久故障锁；已有仓位/TP/SL继续管理",
    )
    return delay


def _connect_v163(self):
    result = _PREVIOUS_CONNECT(self)
    _migrate_legacy_51290_lock(self)
    return result


def _arm_v163(self, settings):
    if self.store and _is_legacy_51290_lock(self.store.data.get("halt", "")):
        _migrate_legacy_51290_lock(self)
    original_emit = self.emit

    def emit(kind, data):
        if isinstance(data, str):
            data = data.replace("V1.6.2", "V1.6.3")
            if data.startswith("自动交易启动；"):
                data = (
                    "自动交易启动；V1.6.3：仅5m BOLL外轨；做多下轨/做空上轨均要求5m RSI 30-70、"
                    "5m MACD连续向交易方向改善、4H同向；全部首次开仓1× LIMIT；"
                    "1×1H ATR止损、2R整仓止盈、No-BE；OKX 51290只软暂停并自动恢复"
                )
        return original_emit(kind, data)

    self.emit = emit
    try:
        return _PREVIOUS_ARM(self, settings)
    finally:
        self.emit = original_emit


def _halt_v163(self, reason):
    # Defense in depth: even if a future inherited call path reaches Engine.halt
    # directly with 51290, never persist it as a permanent strategy fault.
    if _is_legacy_51290_lock(reason):
        return _schedule_51290_soft_pause(self, reason)
    return _PREVIOUS_HALT(self, reason)


def _submit_v163(self, market, score, equity, available, remaining):
    original_emit = self.emit

    def emit(kind, data):
        if isinstance(data, str):
            data = data.replace("V1.6.2", "V1.6.3")
        return original_emit(kind, data)

    self.emit = emit
    try:
        return _PREVIOUS_SUBMIT(self, market, score, equity, available, remaining)
    finally:
        self.emit = original_emit


def apply():
    if getattr(engine.Engine, "_kaytrade_v163_applied", False):
        return
    engine.Engine.connect = _connect_v163
    engine.Engine.arm = _arm_v163
    engine.Engine.halt = _halt_v163
    v138._submit_initial = _submit_v163
    engine.Engine._kaytrade_v163_applied = True
    app.App._kaytrade_v163_runtime_applied = True


apply()

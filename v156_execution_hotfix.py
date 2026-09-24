"""KAYTRADE V1.5.6 execution-stability hotfix.

This patch keeps the verified V1.5.6 strategy model unchanged and repairs only
pre-entry exchange execution:
- read current isolated leverage before issuing a write;
- skip duplicate set-leverage POSTs when OKX already reports the target value;
- if set-leverage returns an ambiguous timeout/network result, read the leverage
  back before deciding whether it is safe to continue to Place Order;
- explicit write rejections still stop new entries immediately.

Place-order timeout classification itself lives in exchange.py so a 51054 can
never be mistaken for an explicit rejection that is safe to clear/retry.
"""
from __future__ import annotations

import math

import engine
import v138_strategy_patch as v138

VERSION = "1.5.6"
BUILD = "1560-hotfix-51054"


def _lever_matches(rows, pos_side, target):
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("mgnMode") or "") != "isolated":
            continue
        if str(row.get("posSide") or "") != str(pos_side):
            continue
        try:
            value = float(row.get("lever"))
        except Exception:
            continue
        if math.isfinite(value) and abs(value - float(target)) <= 1e-9:
            return True
    return False


def _read_leverage(owner, pos_side):
    return owner.x.get(
        "/api/v5/account/leverage-info",
        {"instId": engine.INSTRUMENT, "mgnMode": "isolated"},
        True,
    )


def _stop_new_entries(owner, message, alarm=True):
    owner.enabled = False
    owner.stopped = True
    try:
        owner.startup_buffer_until = 0.0
    except Exception:
        pass
    owner.emit("alarm" if alarm else "log", message)


def _set_leverage_v156(owner, plan):
    """Idempotent leverage preflight with timeout read-back reconciliation."""
    pos_side = str(plan.get("posSide") or "")
    target = float(owner.settings.leverage)
    if not pos_side or not math.isfinite(target) or target <= 0:
        raise engine.Halt("逐仓杠杆预检参数无效")

    # Read first. The old flow performed SET then GET on every signal, which
    # doubled exchange traffic and exposed every otherwise-valid entry to an
    # unnecessary POST timeout.
    infos = _read_leverage(owner, pos_side)
    if _lever_matches(infos, pos_side, target):
        owner._v156_leverage_preflight = {
            "posSide": pos_side,
            "lever": target,
            "source": "read-skip-write",
        }
        return True

    body = {
        "instId": engine.INSTRUMENT,
        "lever": str(owner.settings.leverage),
        "mgnMode": "isolated",
        "posSide": pos_side,
    }
    try:
        owner.x.post("/api/v5/account/set-leverage", body)
    except Exception as exc:
        if getattr(exc, "write_rejected", False):
            _stop_new_entries(
                owner,
                f"OKX明确拒绝设置逐仓杠杆：{exc}；未提交开仓订单，自动新开仓已停止",
                alarm=True,
            )
            return False

        # Ambiguous write (including OKX 51054): do NOT guess and do NOT retry
        # the write. Read the actual leverage state from OKX first.
        owner.emit(
            "alarm",
            f"设置逐仓杠杆返回结果未知：{exc}；不重复提交，正在回读OKX杠杆状态",
        )
        try:
            verify = _read_leverage(owner, pos_side)
        except Exception as verify_exc:
            _stop_new_entries(
                owner,
                f"逐仓杠杆写入结果未知且回读失败：{verify_exc}；为避免错误开仓，自动新开仓已停止",
                alarm=True,
            )
            return False
        if _lever_matches(verify, pos_side, target):
            owner._v156_leverage_preflight = {
                "posSide": pos_side,
                "lever": target,
                "source": "timeout-readback-confirmed",
            }
            owner.emit(
                "log",
                f"V1.5.6 杠杆回读确认：{pos_side} 已为逐仓 {target:g}×；继续本次开仓流程",
            )
            return True
        _stop_new_entries(
            owner,
            f"设置逐仓杠杆结果未知且回读仍不是 {target:g}×；未提交开仓订单，自动新开仓已停止",
            alarm=True,
        )
        return False

    # POST returned success: verify once before the order write.
    verify = _read_leverage(owner, pos_side)
    if not _lever_matches(verify, pos_side, target):
        _stop_new_entries(
            owner,
            f"逐仓杠杆回读不一致：目标 {target:g}×；未提交开仓订单，自动新开仓已停止",
            alarm=True,
        )
        return False

    owner._v156_leverage_preflight = {
        "posSide": pos_side,
        "lever": target,
        "source": "set-and-verified",
    }
    return True


def apply():
    if getattr(engine.Engine, "_kaytrade_v156_execution_hotfix_applied", False):
        return
    v138._set_leverage = _set_leverage_v156
    engine.Engine._kaytrade_v156_execution_hotfix_applied = True


apply()

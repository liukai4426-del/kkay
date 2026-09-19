"""KAYTRADE V1.7.0 Build1702 TP/SL protection reconciliation hotfix.

Build1702 changes no entry signal, score, position sizing, SL/TP price, PEE4 or
Lock1H rule.  It fixes a live execution-layer false emergency close observed
immediately after a fully filled parent order whose attached OKX TP/SL was
already present.

Root cause:
- the inherited V1.3.7 reconciler identifies each attached bracket only through
  `algoClOrdId`;
- OKX / the V1.6.7 Algo stream can expose the same immutable client identifier
  as `attachAlgoClOrdId`;
- the cache therefore contained a real live protective bracket that the legacy
  reconciler could fail to count, making qty > live_qty after 15 seconds and
  triggering an unnecessary market emergency flatten.

Build1702:
1) normalizes cached pending-algo rows so algoClOrdId falls back to
   attachAlgoClOrdId before legacy reconciliation;
2) if the legacy reconciler still reaches the specific "position exceeds
   verifiable TP/SL quantity" emergency path, performs one fresh full REST
   snapshot of all audited pending-algo families;
3) suppresses the emergency close only when that fresh snapshot proves the
   exact local bracket id, side/margin mode, TP, SL and sufficient protected
   quantity; otherwise the inherited emergency close remains fail-closed;
4) a transient REST verification failure gets a short bounded read-only retry
   window, after which the inherited emergency close is allowed again.

No generic emergency-close path is weakened.
"""
from __future__ import annotations

import math
import time
from decimal import Decimal

from v170_build1701_patch import apply as apply_previous
apply_previous()

import app
import engine
import exchange
import v137_strategy_patch as v137
import v165_model as model
import v165_update_patch as runtime
import v166_ui_patch as ui166
import v167_algo_sync_patch as a167
import v168_update_patch as v168
import v168_build1681_patch as b1681
import v170_update_patch as v170
import v170_build1701_patch as v1701

VERSION = "1.7.0"
BUILD = "1702"

# This is only used when the fresh REST verification itself is unavailable.
# A successful fresh snapshot that proves the bracket absent still flattens
# immediately through the inherited fail-closed path.
READ_RETRY_MAX_SECONDS = 30.0
REST_PROOF_CACHE_SECONDS = 5.0
_PROTECTION_MISMATCH = "已成交仓位超过可核实的TP/SL保护数量"

_PREVIOUS_ALGOS = exchange.Exchange.algos
_PREVIOUS_EMERGENCY_FLATTEN = v137._emergency_flatten
_PREVIOUS_RUNTIME_TEXT = runtime._rewrite_runtime_text
_PREVIOUS_APP_EMIT = app.App.emit
_PREVIOUS_APP_INIT = app.App.__init__


def _algo_client_id(row):
    if not isinstance(row, dict):
        return ""
    return str(row.get("algoClOrdId") or row.get("attachAlgoClOrdId") or "").strip()


def _normalize_algo_row(row):
    if not isinstance(row, dict):
        return row
    out = dict(row)
    cid = _algo_client_id(out)
    if cid and not str(out.get("algoClOrdId") or "").strip():
        out["algoClOrdId"] = cid
    return out


def _algos_v1702(self):
    """Preserve V1.6.7 trusted-cache semantics while normalizing OKX ID aliases."""
    rows = _PREVIOUS_ALGOS(self)
    return [_normalize_algo_row(row) for row in (rows or [])]


def _decimal(value, default="0"):
    try:
        result = Decimal(str(value))
    except Exception:
        return Decimal(default)
    return result if result.is_finite() else Decimal(default)


def _active_algo(row):
    state = str((row or {}).get("state") or "").strip().lower()
    # Pending-algo REST/WS rows should be live-like.  Never treat a terminal
    # trigger/cancel/fill state as continuing protection.
    return state in {"live", "partially_effective", "waiting", "pending"}


def _price_equal(a, b, tick):
    try:
        da = Decimal(str(a))
        db = Decimal(str(b))
    except Exception:
        return False
    if not da.is_finite() or not db.is_finite():
        return False
    tolerance = max(tick / Decimal("2"), Decimal("0.00000001"))
    return (da - db).copy_abs() <= tolerance


def _filled_legs(p):
    legs = [x for x in (p.get("legs") or []) if isinstance(x, dict) and x.get("state") == "filled"]
    if legs:
        return legs
    # Defensive compatibility for a single-leg local record.
    bracket = str(p.get("bracket_id") or "").strip()
    if not bracket:
        return []
    return [{
        "bracket_id": bracket,
        "sz": p.get("sz"),
        "tp": p.get("tp") or p.get("tp2"),
        "sl": p.get("sl"),
        "state": "filled",
    }]


def _fresh_rest_protection(owner, p, qty):
    """Return (proven, diagnostics) from a fresh full pending-algo REST snapshot."""
    rows = [_normalize_algo_row(x) for x in a167._snapshot_rows(owner.x)]
    meta = owner.x.instrument()
    tick = _decimal(meta.get("tickSz"), "0.1")
    if tick <= 0:
        tick = Decimal("0.1")

    pos_side = str(p.get("posSide") or "")
    exchange_side = str(p.get("exchange_side") or "")
    expected_exit_side = "sell" if pos_side == "long" else "buy" if pos_side == "short" else ""
    qty_d = _decimal(qty)
    protected = Decimal("0")
    matches = []

    for leg in _filled_legs(p):
        bracket_id = str(leg.get("bracket_id") or "").strip()
        if not bracket_id:
            continue
        expected_sz = _decimal(leg.get("sz"))
        expected_tp = leg.get("tp") or p.get("tp") or p.get("tp2")
        expected_sl = leg.get("sl") or p.get("sl")
        matched = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _algo_client_id(row) != bracket_id:
                continue
            if not _active_algo(row):
                continue
            if str(row.get("instId") or engine.INSTRUMENT) != engine.INSTRUMENT:
                continue
            if str(row.get("posSide") or "") != pos_side:
                continue
            if str(row.get("tdMode") or "") != "isolated":
                continue
            row_side = str(row.get("side") or "")
            if expected_exit_side and row_side and row_side != expected_exit_side:
                continue
            if exchange_side and row_side and row_side == exchange_side:
                continue
            if str(row.get("tpOrdPx") or "") != "-1" or str(row.get("slOrdPx") or "") != "-1":
                continue
            if not _price_equal(row.get("tpTriggerPx"), expected_tp, tick):
                continue
            if not _price_equal(row.get("slTriggerPx"), expected_sl, tick):
                continue
            row_sz = _decimal(row.get("sz"))
            if row_sz > 0 and expected_sz > 0 and row_sz < expected_sz:
                continue
            matched = row
            break
        if matched is not None:
            protected += expected_sz
            matches.append({
                "bracket_id": bracket_id,
                "algo_id": str(matched.get("algoId") or ""),
                "client_id_field": (
                    "algoClOrdId" if str(matched.get("algoClOrdId") or "").strip()
                    else "attachAlgoClOrdId"
                ),
                "state": str(matched.get("state") or ""),
                "sz": str(matched.get("sz") or ""),
                "tp": str(matched.get("tpTriggerPx") or ""),
                "sl": str(matched.get("slTriggerPx") or ""),
            })

    tolerance = max(_decimal(meta.get("lotSz"), "0"), Decimal("0.00000001"))
    proven = qty_d > 0 and protected + tolerance >= qty_d
    return proven, {
        "qty": float(qty_d),
        "protected_qty": float(protected),
        "matched_brackets": matches,
        "rest_rows": len(rows),
    }


def _mark_rest_protected(owner, p, diag):
    now = time.time()
    p["v1702_protection_rest_verified_at"] = now
    p["v1702_protection_rest_verified_until"] = now + REST_PROOF_CACHE_SECONDS
    p["v1702_protection_rest_diag"] = dict(diag or {})
    p.pop("v1702_protection_read_failed_at", None)
    p.pop("v1702_protection_read_failed_count", None)
    p["protected"] = True
    for leg in _filled_legs(p):
        leg["protected"] = True
    owner.store.save()


def _emergency_flatten_v1702(self, p, qty, reason):
    if _PROTECTION_MISMATCH not in str(reason or ""):
        return _PREVIOUS_EMERGENCY_FLATTEN(self, p, qty, reason)

    now = time.time()
    verified_until = float(p.get("v1702_protection_rest_verified_until") or 0.0)
    if now < verified_until:
        # A fresh all-family REST snapshot proved the exact bracket only seconds
        # ago.  Give the WS/cache path a moment to catch up without repeating a
        # destructive market close.
        p["protected"] = True
        self.store.save()
        return

    try:
        proven, diag = _fresh_rest_protection(self, p, qty)
    except Exception as exc:
        first = float(p.get("v1702_protection_read_failed_at") or 0.0)
        if first <= 0:
            first = now
            p["v1702_protection_read_failed_at"] = first
        p["v1702_protection_read_failed_count"] = int(p.get("v1702_protection_read_failed_count") or 0) + 1
        p["v1702_protection_read_last_error"] = str(exc)[:240]
        self.store.save()
        elapsed = max(0.0, now - first)
        if elapsed < READ_RETRY_MAX_SECONDS:
            self.emit(
                "log",
                "V1.7.0 Build1702保护复核：Algo缓存暂未证明TP/SL， fresh REST复核暂时失败；"
                f"本周期不发送额外市价平仓，继续只读重试（{elapsed:.0f}/{READ_RETRY_MAX_SECONDS:.0f}s）",
            )
            return
        return _PREVIOUS_EMERGENCY_FLATTEN(self, p, qty, reason)

    if proven:
        _mark_rest_protected(self, p, diag)
        self.store.record("V1.7.0 Build1702 TP/SL多源复核通过", {
            "client_id": p.get("client_id"),
            "order_id": p.get("order_id"),
            "qty": diag.get("qty"),
            "protected_qty": diag.get("protected_qty"),
            "matched_brackets": diag.get("matched_brackets"),
        })
        self.emit(
            "log",
            "V1.7.0 Build1702保护复核通过：WS/缓存暂未识别完整保护，但fresh REST已确认"
            f"TP/SL保护量 {float(diag.get('protected_qty') or 0):g} >= 仓位 {float(diag.get('qty') or 0):g}；"
            "取消本次误触发安全平仓，继续持仓",
        )
        return

    # Fresh REST succeeded and still cannot prove the exact local bracket.
    # Keep the inherited fail-closed emergency behavior.
    p["v1702_protection_rest_diag"] = dict(diag or {})
    self.store.save()
    return _PREVIOUS_EMERGENCY_FLATTEN(self, p, qty, reason)


def _rewrite_runtime_text_v1702(data):
    text = _PREVIOUS_RUNTIME_TEXT(data)
    if not isinstance(text, str):
        return text
    text = text.replace("Build1701", "Build1702").replace("Build 1701", "Build 1702")
    text = text.replace("Build1700", "Build1702").replace("Build 1700", "Build 1702")
    if text.startswith("自动交易启动；") and "TP/SL多源复核" not in text:
        text += (
            "；Build1702 TP/SL多源复核：兼容algoClOrdId/attachAlgoClOrdId；"
            "保护缓存缺失时先fresh REST证明真实保护，只有无法证明保护才保留一次性安全平仓"
        )
    return text


def _app_emit_v1702(self, kind, data):
    outgoing = _rewrite_runtime_text_v1702(data) if isinstance(data, str) else data
    return _PREVIOUS_APP_EMIT(self, kind, outgoing)


def _app_init_v1702(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    try:
        self.root.title("KAYTRADE 1.7.0 · BTC 策略控制台 · Build 1702")
    except Exception:
        pass
    self._v170_build1702_ready = True


def apply():
    if getattr(model, "_kaytrade_v170_build1702_applied", False):
        return

    exchange.Exchange.algos = _algos_v1702
    v137._emergency_flatten = _emergency_flatten_v1702

    runtime._rewrite_runtime_text = _rewrite_runtime_text_v1702
    runtime.VERSION = VERSION
    runtime.BUILD = BUILD

    model.VERSION = VERSION
    model.BUILD = BUILD
    v170.BUILD = BUILD
    v1701.BUILD = BUILD
    v168.BUILD = BUILD
    b1681.BUILD = BUILD
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.7.0 · BTC 策略控制台 · Build 1702"

    app.App.emit = _app_emit_v1702
    app.App.__init__ = _app_init_v1702

    model._kaytrade_v170_build1702_applied = True
    exchange.Exchange._kaytrade_v170_build1702_applied = True
    app.App._kaytrade_v170_build1702_applied = True


apply()

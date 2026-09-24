"""KAYTRADE V1.6.1 production overlay.

Changes on top of V1.6.0:
1) 5m BOLL outer-band entries require an aligned 4H trend (long/short mirror).
2) When a live position reaches +1R, amend its exchange-native SL to the
   position average entry price (strict break-even, no fee offset), once only.
3) Replace the single-sample clock-sync failure with three-sample NTP-style
   calibration. Slow/incomplete sync pauses NEW entries only and auto-recovers;
   it never writes a permanent strategy fault merely because RTT was high.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import time
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from v160_update_patch import apply as apply_previous
apply_previous()

import app
import engine
import exchange
from core import INSTRUMENT
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v152_strategy_patch as v152_runtime
import v153_runtime_patch as v153_runtime
import v153_backtest_core as v153_bt
import v154_runtime_patch as v154_runtime
import v154_limit_log_fix as v154_limit
import v154_record_card_boll_fix as v154_record
import v160_update_patch as v160
import v161_model as model

VERSION = "1.6.1"
BUILD = "1610"
CLOCK_SAMPLE_COUNT = 3
CLOCK_MAX_RTT_SECONDS = 3.0
CLOCK_MAX_OFFSET_SPREAD_SECONDS = 1.0
CLOCK_REFRESH_SECONDS = 240.0
BE_TRIGGER_R = 1.0

# V1.6.0 already applied the inherited production stack. Route every facade,
# including its path-dependent order-preparation closure, through V1.6.1.
v160.model = model
v152_runtime.model = model
v152_runtime.V152_VERSION = VERSION
v153_runtime.model = model
v153_runtime.VERSION = VERSION
v153_bt.model = model
v154_runtime.model = model
v154_limit.model = model
if hasattr(v154_record, "model"):
    v154_record.model = model

# Suppress the inherited V1.6.0 success probe; only the final V1.6.1 runtime
# hook may mark packaged startup as successful.
v160._write_probe = lambda: None

_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_ENGINE_ARM = engine.Engine.arm
_PREVIOUS_CYCLE = engine.Engine.cycle
_PREVIOUS_POST = exchange.Exchange.post


def _probe_target():
    return os.environ.get("KAYTRADE_STARTUP_PROBE", "").strip()


def _write_probe():
    target = _probe_target()
    if not target:
        return
    try:
        Path(target).write_text(
            f"PASS KAYTRADE {VERSION} Build {BUILD} outer_4h=aligned_required "
            "be_1r=on clock_sync=3sample_auto_recover middle_macd=required "
            "outer_band=2x_single_order limit_entry=on stop=1h_atr_1x tp=2r\n",
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


def _normalize_runtime_text(data):
    if not isinstance(data, str):
        return data
    text = data.replace("V1.5.4未开仓", "V1.6.1未开仓")
    if text.startswith("自动交易启动；"):
        return (
            "自动交易启动；V1.6.1：5m BOLL下轨做多/上轨做空均要求4H同向；"
            "中轨继续要求正式5m macd_improving且使用1×基础仓位，外轨单张LIMIT使用2×基础仓位；"
            "+1R后SL移动到实际开仓均价BE一次；固定6分；1×1H ATR初始止损；整仓2R；"
            "时钟同步采用3样本容错，临时延迟只暂停新开仓并自动恢复"
        )
    return text


# ------------------------------ Clock sync ------------------------------

def _clock_diag(exchange_obj):
    rtt = getattr(exchange_obj, "clock_sync_rtt_ms", None)
    offset = getattr(exchange_obj, "clock_sync_offset_ms", None)
    rtt_text = "—" if rtt is None else f"{float(rtt):.0f}ms"
    offset_text = "—" if offset is None else f"{float(offset):+.0f}ms"
    return rtt_text, offset_text


def _sync_time_v161(self):
    samples = []
    errors = []
    for _ in range(CLOCK_SAMPLE_COUNT):
        fallback_start = time.time()
        try:
            rows = self.get("/api/v5/public/time")
            if not rows:
                raise exchange.APIError("公共时间接口返回空数据")
            server = float(rows[0]["ts"]) / 1000.0
            sent, received = getattr(self, "last_timing", (fallback_start, time.time()))
            sent = float(sent); received = float(received)
            rtt = max(0.0, received - sent)
            offset = server - (sent + received) / 2.0
            samples.append({"server": server, "rtt": rtt, "offset": offset})
        except Exception as exc:
            errors.append(str(exc))

    self.clock_sync_sample_count = len(samples)
    self.clock_sync_last_error = "；".join(errors[-2:]) if errors else ""

    if not samples:
        self.clock_sync_rtt_ms = None
        self.clock_sync_offset_ms = None
        self.clock_sync_offset_spread_ms = None
        self.clock_sync_degraded = True
        self.clock_sync_status = "degraded"
        self.clock_sync_fail_streak = int(getattr(self, "clock_sync_fail_streak", 0) or 0) + 1
        # A previously synchronized monotonic anchor remains valid as a running
        # server-time trajectory. Re-anchor it locally so inherited candles()
        # does not hammer the time endpoint while V1.6.1 performs backoff retry.
        if self.clock_anchor is not None:
            current = self.server_now()
            self.clock_anchor = (current, time.monotonic())
            try:
                self.network_event("时钟同步重试中：保留上一服务器时间基准")
            except Exception:
                pass
            return False
        raise exchange.NetworkError("时钟同步暂时不可用，且尚无可用服务器时间基准；未进行交易写入")

    offsets = [s["offset"] for s in samples]
    offset_mid = float(median(offsets))
    spread = max(abs(value - offset_mid) for value in offsets) if len(offsets) > 1 else 0.0
    best = min(samples, key=lambda item: item["rtt"])
    good = [
        item for item in samples
        if item["rtt"] <= CLOCK_MAX_RTT_SECONDS
        and abs(item["offset"] - offset_mid) <= CLOCK_MAX_OFFSET_SPREAD_SECONDS
    ]

    # Always calibrate from the lowest-RTT successful sample. High RTT is a
    # transport-quality warning, not proof that the local/server clocks differ.
    self.offset = float(best["offset"])
    self.clock_anchor = (float(best["server"]) + float(best["rtt"]) / 2.0, time.monotonic())
    self.clock_sync_rtt_ms = float(best["rtt"]) * 1000.0
    self.clock_sync_offset_ms = offset_mid * 1000.0
    self.clock_sync_offset_spread_ms = spread * 1000.0
    stable = len(good) == CLOCK_SAMPLE_COUNT and spread <= CLOCK_MAX_OFFSET_SPREAD_SECONDS

    if stable:
        self.clock_sync_degraded = False
        self.clock_sync_status = "ok"
        self.clock_sync_fail_streak = 0
        self.clock_sync_good_samples = CLOCK_SAMPLE_COUNT
        try:
            self.network_event("正常")
        except Exception:
            pass
        return True

    self.clock_sync_degraded = True
    self.clock_sync_status = "degraded"
    self.clock_sync_good_samples = len(good)
    self.clock_sync_fail_streak = int(getattr(self, "clock_sync_fail_streak", 0) or 0) + 1
    try:
        self.network_event(f"时钟同步恢复中：有效样本 {len(good)}/{CLOCK_SAMPLE_COUNT}")
    except Exception:
        pass
    return False


# ------------------------------ +1R -> BE ------------------------------

def _amend_algo_post(self, body):
    """One non-retried signed write dedicated to OKX Amend Algo Order."""
    if not all((self.key, self.secret, self.phrase)):
        raise exchange.APIError("请填写API Key、Secret、Passphrase", method="POST", path="/api/v5/trade/amend-algos")
    path = "/api/v5/trade/amend-algos"
    method = "POST"
    raw = json.dumps(body, separators=(",", ":"))
    timestamp = datetime.fromtimestamp(self.server_now(), timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    signature = base64.b64encode(
        hmac.new(self.secret.encode(), (timestamp + method + path + raw).encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "OKXMac/1.0",
        "Accept": "application/json",
        "OK-ACCESS-KEY": self.key,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": self.phrase,
        "x-simulated-trading": "1" if self.demo else "0",
    }
    req = urllib.request.Request("https://" + self.host + path, data=raw.encode(), headers=headers, method=method)
    try:
        with self.opener.open(req, timeout=10) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        code = ""; message = ""
        try:
            payload = json.loads(exc.read(4096))
            code = str(payload.get("code") or "")
            message = str(payload.get("msg") or "")[:160]
        except Exception:
            pass
        detail = (f" / OKX {code}" if code else "") + (f"：{message}" if message else "")
        if exc.code in (408, 409, 425, 429, 500, 502, 503, 504):
            raise exchange.NetworkError(
                f"V1.6.1保本止损改单结果未知：HTTP {exc.code}{detail}；禁止盲目重复改单",
                code, exc.code, method, path,
            ) from None
        raise exchange.APIError(
            f"V1.6.1保本止损改单被拒绝：HTTP {exc.code}{detail}",
            code, True, exc.code, method, path,
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise exchange.NetworkError(
            "V1.6.1保本止损改单网络结果未知；保留原止损并通过只读状态核对，禁止盲目重复改单",
            method=method, path=path,
        ) from None
    except Exception as exc:
        raise exchange.NetworkError(
            "V1.6.1保本止损改单响应异常：" + type(exc).__name__ + "；结果需只读核对",
            method=method, path=path,
        ) from None

    if str(result.get("code") or "") != "0":
        code = str(result.get("code") or "")
        message = str(result.get("msg") or "")[:160]
        raise exchange.APIError(
            f"V1.6.1保本止损改单被OKX拒绝：{code}" + (f"：{message}" if message else ""),
            code, True, None, method, path,
        )
    data = result.get("data")
    if not isinstance(data, list):
        raise exchange.NetworkError("V1.6.1保本止损改单响应结构异常；结果需只读核对", method=method, path=path)
    for row in data:
        if isinstance(row, dict) and str(row.get("sCode") or "0") != "0":
            code = str(row.get("sCode") or "")
            message = str(row.get("sMsg") or "")[:160]
            raise exchange.APIError(
                f"V1.6.1保本止损改单订单级拒绝：{code}" + (f"：{message}" if message else ""),
                code, True, None, method, path,
            )
    return data


def _filled_legs(active):
    return [
        leg for leg in (active.get("legs") or [])
        if isinstance(leg, dict) and leg.get("state") == "filled"
    ]


def _match_algo(rows, leg):
    bracket = str(leg.get("bracket_id") or "")
    if not bracket:
        return None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("algoClOrdId") or "") == bracket and str(row.get("state") or "live") == "live":
            return row
    return None


def _save_be_verified(owner, active, leg, target, row=None):
    if active.get("be_state") == "verified":
        return False
    old_sl = str(leg.get("sl") or active.get("sl") or "")
    leg.setdefault("initial_sl", old_sl)
    active.setdefault("initial_sl", old_sl)
    leg["sl"] = str(target)
    active["sl"] = str(target)
    active["be_state"] = "verified"
    active["be_verified"] = True
    active["be_verified_at"] = time.time()
    active["be_price"] = str(target)
    if isinstance(row, dict) and row.get("algoId"):
        active["be_algo_id"] = str(row.get("algoId"))
    owner.store.save()
    owner.store.record("V1.6.1 +1R保本止损已确认", {
        "client_id": active.get("client_id"), "entry": active.get("be_entry_px"),
        "old_sl": old_sl, "new_sl": str(target), "trigger_r": BE_TRIGGER_R,
    })
    owner.emit("log", f"+1R保本已确认：SL已移动到开仓均价 {target}；原始SL {old_sl}；2R整仓止盈保持不变")
    return True


def _sync_be_from_algos(owner, rows):
    if not owner.store:
        return
    active = owner.store.data.get("active")
    if not isinstance(active, dict) or active.get("be_state") not in ("submitting", "submitted", "uncertain"):
        return
    legs = _filled_legs(active)
    if len(legs) != 1:
        return
    target = active.get("be_price")
    if target in (None, ""):
        return
    row = _match_algo(rows, legs[0])
    if row is None:
        return
    try:
        live_sl = float(row.get("slTriggerPx") or 0.0)
        wanted = float(target)
    except (TypeError, ValueError):
        return
    tick = 1e-8
    try:
        tick = max(1e-8, float(owner.x.instrument()["tickSz"]) / 2.0)
    except Exception:
        pass
    if math.isclose(live_sl, wanted, rel_tol=0.0, abs_tol=tick):
        _save_be_verified(owner, active, legs[0], target, row)


def _with_be_algo_adapter(owner, callback):
    active = owner.store.data.get("active") if owner.store else None
    pending = isinstance(active, dict) and active.get("be_state") in ("submitting", "submitted", "uncertain")
    if not pending:
        return callback()
    original = owner.x.algos
    had_instance = "algos" in getattr(owner.x, "__dict__", {})
    old_instance = getattr(owner.x, "__dict__", {}).get("algos") if had_instance else None

    def algos():
        rows = original()
        _sync_be_from_algos(owner, rows)
        return rows

    owner.x.algos = algos
    try:
        return callback()
    finally:
        if had_instance:
            owner.x.__dict__["algos"] = old_instance
        else:
            owner.x.__dict__.pop("algos", None)


def _position_entry(owner, active):
    rows = owner.x.positions()
    matching = [
        row for row in rows
        if str(row.get("posSide") or "") == str(active.get("posSide") or "")
        and abs(float(row.get("pos") or 0.0)) > 0.0
    ]
    if len(matching) != 1:
        return None
    try:
        entry = float(matching[0].get("avgPx") or 0.0)
    except (TypeError, ValueError):
        return None
    return entry if math.isfinite(entry) and entry > 0 else None


def _maybe_trigger_be(owner):
    if not owner.store:
        return
    active = owner.store.data.get("active")
    if not isinstance(active, dict) or not active.get("filled") or not active.get("protected"):
        return
    state = str(active.get("be_state") or "idle")
    if state in ("submitting", "submitted", "uncertain", "verified", "rejected"):
        return
    legs = _filled_legs(active)
    if len(legs) != 1:
        if not active.get("be_multi_leg_notice"):
            active["be_multi_leg_notice"] = True
            owner.store.save()
            owner.emit("log", "V1.6.1保本检查：检测到非单腿持仓，保持原SL，不自动改单")
        return

    entry = _position_entry(owner, active)
    if entry is None:
        return
    leg = legs[0]
    try:
        original_sl = float(leg.get("initial_sl") or active.get("initial_sl") or leg.get("sl") or active.get("sl"))
    except (TypeError, ValueError):
        return
    risk = abs(entry - original_sl)
    if not math.isfinite(risk) or risk <= 0:
        return

    ticker = owner.x.ticker()
    try:
        last = float(ticker.get("last") or 0.0)
    except (TypeError, ValueError):
        return
    long_side = str(active.get("posSide") or "") == "long"
    trigger = entry + risk if long_side else entry - risk
    reached = last >= trigger if long_side else last <= trigger
    if not reached:
        return

    meta = owner.x.instrument()
    tick = float(meta["tickSz"])
    target = engine.rounded(entry, tick, up=not long_side)
    algos = owner.x.algos()
    row = _match_algo(algos, leg)
    if row is None:
        if not active.get("be_missing_notice"):
            active["be_missing_notice"] = True
            owner.store.save()
            owner.emit("log", "+1R已达到，但尚未读取到本程序SL策略委托；保持原SL并继续核对，不重复创建保护单")
        return

    try:
        current_sl = float(row.get("slTriggerPx") or 0.0)
    except (TypeError, ValueError):
        current_sl = 0.0
    if current_sl and math.isclose(current_sl, float(target), rel_tol=0.0, abs_tol=max(1e-8, tick / 2.0)):
        active["be_entry_px"] = entry
        active["be_price"] = str(target)
        _save_be_verified(owner, active, leg, target, row)
        return

    req_id = "be" + uuid.uuid4().hex[:28]
    active.setdefault("initial_sl", str(leg.get("sl") or active.get("sl") or ""))
    leg.setdefault("initial_sl", str(leg.get("sl") or ""))
    active.update(
        be_state="submitting", be_trigger_r=BE_TRIGGER_R, be_entry_px=entry,
        be_trigger_price=trigger, be_price=str(target), be_req_id=req_id,
        be_triggered_at=time.time(), be_last_price=last,
    )
    owner.store.save()
    body = {
        "instId": INSTRUMENT,
        "newSlTriggerPx": str(target),
        "newSlOrdPx": "-1",
        "cxlOnFail": False,
        "reqId": req_id,
    }
    if row.get("algoId"):
        body["algoId"] = str(row.get("algoId"))
    else:
        body["algoClOrdId"] = str(row.get("algoClOrdId") or leg.get("bracket_id") or "")

    try:
        owner.x.post("/api/v5/trade/amend-algos", body)
    except exchange.NetworkError as exc:
        active["be_state"] = "uncertain"
        active["be_error"] = str(exc)
        active["be_uncertain_at"] = time.time()
        owner.store.save()
        owner.store.record("V1.6.1 +1R保本改单结果未知", {"request": body, "error": str(exc)})
        owner.emit("log", "+1R已达到；保本SL改单结果暂不确定，原SL仍作为安全基线；程序只读核对，不盲目重复提交")
        return
    except exchange.APIError as exc:
        active["be_state"] = "rejected"
        active["be_error"] = str(exc)
        active["be_rejected_at"] = time.time()
        owner.store.save()
        owner.store.record("V1.6.1 +1R保本改单被拒绝", {"request": body, "error": str(exc)})
        owner.emit("log", "+1R已达到，但OKX拒绝保本SL改单；保持原始SL继续保护，本轮不重复改单：" + str(exc))
        return

    active["be_state"] = "submitted"
    active["be_submitted_at"] = time.time()
    owner.store.save()
    owner.store.record("V1.6.1 +1R保本改单已提交", {"request": body, "entry": entry, "trigger": trigger})
    owner.emit("log", f"+1R已达到：已提交SL→开仓均价 {target} 的保本改单；等待OKX只读回查确认")


# ------------------------------ Apply overlay ------------------------------

def apply():
    if getattr(engine.Engine, "_kaytrade_v161_applied", False):
        return

    exchange.Exchange.sync_time = _sync_time_v161

    def post(self, path, body):
        if path == "/api/v5/trade/amend-algos":
            return _amend_algo_post(self, body)
        return _PREVIOUS_POST(self, path, body)

    exchange.Exchange.post = post

    def app_init(self, *args, **kwargs):
        try:
            _PREVIOUS_APP_INIT(self, *args, **kwargs)
            try:
                self.root.title("KAYTRADE 1.6.1 · BTC 策略控制台 · Build 1610")
                self.signal.set("V1.6.1 · 外轨4H同向 / +1R保本 / 时钟自动恢复")
            except Exception:
                pass
            self._v161_ready = True
            _write_probe()
        except Exception:
            _write_probe_error()
            raise

    def engine_arm(self, settings):
        original_emit = self.emit

        def emit(kind, data):
            return original_emit(kind, _normalize_runtime_text(data))

        self.emit = emit
        try:
            return _PREVIOUS_ENGINE_ARM(self, settings)
        finally:
            self.emit = original_emit

    def cycle(self):
        if not self.store:
            return _PREVIOUS_CYCLE(self)

        x = self.x
        now = time.monotonic()
        anchor = getattr(x, "clock_anchor", None)
        anchor_age = (now - float(anchor[1])) if anchor else math.inf
        degraded_before = bool(getattr(x, "clock_sync_degraded", False))
        retry_at = float(getattr(self, "_v161_clock_retry_at", 0.0) or 0.0)
        needs_sync = anchor_age >= CLOCK_REFRESH_SECONDS or (degraded_before and now >= retry_at)
        if needs_sync:
            try:
                x.sync_time()
            except exchange.NetworkError as exc:
                # With an existing anchor _sync_time_v161 already returns False;
                # this branch is only for a startup/no-anchor case and remains a
                # real connectivity failure handled by the outer application.
                if getattr(x, "clock_anchor", None) is None:
                    raise
                x.clock_sync_degraded = True
                x.clock_sync_last_error = str(exc)
            streak = max(1, int(getattr(x, "clock_sync_fail_streak", 0) or 0))
            self._v161_clock_retry_at = time.monotonic() + min(15.0, 5.0 * streak)

        degraded = bool(getattr(x, "clock_sync_degraded", False))
        clock_state = "degraded" if degraded else "ok"
        last_state = getattr(self, "_v161_clock_state", None)
        rtt_text, offset_text = _clock_diag(x)

        if degraded:
            if self.enabled:
                self._v161_clock_resume_after_recovery = True
                self.enabled = False
            if clock_state != last_state:
                good = int(getattr(x, "clock_sync_good_samples", 0) or 0)
                self.emit(
                    "log",
                    f"时钟同步暂时不稳定：RTT {rtt_text}｜时间偏差 {offset_text}｜有效样本 {good}/{CLOCK_SAMPLE_COUNT}；"
                    "仅暂停自动新开仓并按5/10/15秒退避重试，已有仓位/TP/SL/平仓核对继续运行；不写入永久故障锁",
                )
        elif last_state == "degraded":
            if getattr(self, "_v161_clock_resume_after_recovery", False) and not self.stopped and not self.store.data.get("halt"):
                self.enabled = True
                self._v161_clock_resume_after_recovery = False
            self.emit(
                "log",
                f"时钟同步恢复：RTT {rtt_text}｜时间偏差 {offset_text}｜连续{CLOCK_SAMPLE_COUNT}个样本正常；自动恢复新开仓，无需重新授权",
            )

        self._v161_clock_state = clock_state
        if self.stopped:
            self._v161_clock_resume_after_recovery = False

        def run_previous():
            return _PREVIOUS_CYCLE(self)

        result = _with_be_algo_adapter(self, run_previous)

        # Position management remains active even while new entries are paused.
        try:
            _maybe_trigger_be(self)
        except exchange.NetworkError as exc:
            # Read-only checks may fail transiently. Existing exchange SL remains
            # untouched, so do not turn this into a permanent strategy lock.
            self.emit("log", "V1.6.1保本检查暂时网络失败；保留当前SL，下轮继续只读检查：" + str(exc))
        except exchange.APIError as exc:
            self.emit("log", "V1.6.1保本检查暂时无法完成；保留当前SL：" + str(exc))

        active = self.store.data.get("active")
        if isinstance(active, dict):
            changed = active.get("version") != VERSION or active.get("build") != BUILD
            active["version"] = VERSION
            active["build"] = BUILD
            active["outer_4h_aligned_required"] = True
            active["be_trigger_r"] = BE_TRIGGER_R
            active["clock_sync_auto_recover"] = True
            if changed:
                self.store.save()
        return result

    app.App.__init__ = app_init
    engine.Engine.arm = engine_arm
    engine.Engine.cycle = cycle
    engine.Engine._kaytrade_v161_applied = True
    app.App._kaytrade_v161_applied = True


apply()

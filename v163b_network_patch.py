"""KAYTRADE V1.6.3b network-recovery hotfix.

This hotfix does not change strategy semantics. It only changes handling of
read-only transport failures while automatic trading is authorized:

- keep the existing authorization alive for up to 60 seconds;
- while disconnected, do not run the strategy cycle or submit any new order;
- retry a read-only recovery probe every 5 seconds;
- if recovery succeeds inside 60 seconds, verify account/exposure state, skip
  signals missed during the outage, and automatically continue;
- after 60 seconds, stop NEW entries without writing a permanent fault lock;
- continue read-only probing every 5 seconds; a later recovery restores
  observation only and requires manual re-authorization for new entries;
- any POST/write NetworkError keeps the inherited fail-closed behavior and is
  never automatically retried.
"""
from __future__ import annotations

import queue
import time

import app
import v163_update_patch as runtime163
import v163_ui_patch as ui163
from exchange import NetworkError
from engine import Halt

VERSION = "1.6.3b"
BUILD = "1631"
RECOVERY_WINDOW_SECONDS = 60.0
RECOVERY_INTERVAL_SECONDS = 5.0
MAX_WINDOW_PROBES = int(RECOVERY_WINDOW_SECONDS / RECOVERY_INTERVAL_SECONDS)

# Keep the promoted runtime/UI labels synchronized with this hotfix. Strategy
# model.VERSION intentionally remains 1.6.3 because no strategy rule changed.
runtime163.VERSION = VERSION
runtime163.BUILD = BUILD
ui163.VERSION = VERSION
ui163.BUILD = BUILD
ui163.VERSION_SUBTITLE = f"BTC / USDT   ·   V{VERSION}"
ui163.WINDOW_TITLE = f"KAYTRADE {VERSION} · BTC 策略控制台 · Build {BUILD}"

_PREVIOUS_INIT = app.App.__init__


def _ensure_state(owner):
    if not hasattr(owner, "_v163b_network_started_at"):
        owner._v163b_network_started_at = 0.0
    if not hasattr(owner, "_v163b_network_auto_resume"):
        owner._v163b_network_auto_resume = False
    if not hasattr(owner, "_v163b_network_timed_out"):
        owner._v163b_network_timed_out = False
    if not hasattr(owner, "_v163b_network_probe_count"):
        owner._v163b_network_probe_count = 0
    if not hasattr(owner, "_v163b_last_network_error"):
        owner._v163b_last_network_error = ""


def _reset_state(owner):
    _ensure_state(owner)
    owner.network_paused = False
    owner.recovery_count = 0
    owner.probe_at = 0.0
    owner._v163b_network_started_at = 0.0
    owner._v163b_network_auto_resume = False
    owner._v163b_network_timed_out = False
    owner._v163b_network_probe_count = 0
    owner._v163b_last_network_error = ""


def _is_write_network_error(exc):
    """POST uncertainty must never enter the automatic read-only retry loop."""
    return str(getattr(exc, "method", "") or "").upper() == "POST"


def _begin_read_only_recovery(owner, exc):
    """Start (or continue) the 60-second grace window without halting Engine."""
    _ensure_state(owner)
    if owner.engine is None or _is_write_network_error(exc):
        return False
    now = time.monotonic()
    owner._v163b_last_network_error = str(exc)
    if owner.network_paused:
        return True
    owner.network_paused = True
    owner.recovery_count = 0
    owner.probe_at = now
    owner._v163b_network_started_at = now
    owner._v163b_network_auto_resume = bool(owner.engine.enabled and not owner.engine.stopped)
    owner._v163b_network_timed_out = False
    owner._v163b_network_probe_count = 0
    owner.emit("network", "暂时中断 · 5秒后自动重连")
    owner.emit("status", "网络暂时中断 · 60秒自动恢复窗口 · 暂不提交新订单")
    owner.emit(
        "log",
        "网络暂时中断：" + str(exc)
        + "；进入60秒自动恢复窗口，每5秒进行一次只读核对；期间暂停策略循环且不提交新订单",
    )
    return True


def _mark_timeout(owner, now=None):
    _ensure_state(owner)
    if owner._v163b_network_timed_out:
        return False
    now = time.monotonic() if now is None else float(now)
    started = float(owner._v163b_network_started_at or now)
    if now - started < RECOVERY_WINDOW_SECONDS:
        return False
    owner._v163b_network_timed_out = True
    owner._v163b_network_auto_resume = False
    if owner.engine is not None:
        # Stop only new entries. Do not call Engine.stop()/halt(): those have
        # cancellation/permanent-lock semantics that are not appropriate for a
        # plain read-only network outage.
        owner.engine.enabled = False
    owner.emit("network", "连接失败超过60秒 · 已停止自动新开仓 · 继续5秒只读探测")
    owner.emit("status", "网络中断超过60秒 · 已停止自动新开仓 · 已有TP/SL保持")
    owner.emit(
        "log",
        "网络连续中断超过60秒：已停止自动新开仓，但未写入永久故障锁；"
        "已有交易所TP/SL保持有效，程序继续每5秒只读探测网络",
    )
    return True


def _probe_read_only_recovery(owner):
    """Return waiting/retry/recovered after one scheduled read-only probe."""
    _ensure_state(owner)
    if not owner.network_paused or owner.engine is None:
        return "idle"
    now = time.monotonic()
    _mark_timeout(owner, now)
    if now - float(owner.probe_at or 0.0) < RECOVERY_INTERVAL_SECONDS:
        return "waiting"
    owner.probe_at = now
    owner._v163b_network_probe_count += 1

    x = owner.engine.x
    try:
        x.sync_time()
        if bool(getattr(x, "clock_sync_degraded", False)):
            raise NetworkError("时钟同步仍在恢复中；本轮只读探测未通过", method="GET", path="/api/v5/public/time")
        account = x.account()
        if account.get("uid") != owner.engine.connection_id:
            raise Halt("恢复后的账户标识不匹配")
        # All recovery checks are reads. No cancel/place/amend request is issued
        # here even when a local active order exists.
        x.balance()
        x.positions()
        x.orders()
        x.algos()
    except NetworkError as exc:
        owner._v163b_last_network_error = str(exc)
        now2 = time.monotonic()
        _mark_timeout(owner, now2)
        elapsed = max(0.0, now2 - float(owner._v163b_network_started_at or now2))
        if owner._v163b_network_timed_out:
            owner.emit("network", "已停新开仓 · 每5秒只读重连中")
            owner.emit("status", "网络仍未恢复 · 自动新开仓保持停止 · 已有TP/SL保持")
        else:
            attempt = min(MAX_WINDOW_PROBES, max(1, owner._v163b_network_probe_count))
            remaining = max(0, int(round(RECOVERY_WINDOW_SECONDS - elapsed)))
            owner.emit("network", f"自动重连 {attempt}/{MAX_WINDOW_PROBES} · 剩余约{remaining}秒 · 不下单")
            owner.emit("status", f"网络恢复窗口 · 第{attempt}/{MAX_WINDOW_PROBES}次只读重连 · 暂不提交新订单")
        return "retry"

    store = owner.engine.store
    if store is not None:
        # Do not backfill a signal that appeared while disconnected.
        current_closed = int(owner.engine.market_now() // 300) * 300000 - 300000
        store.data["last_bar"] = max(int(store.data.get("last_bar") or 0), current_closed)
        store.save()

    owner.network_paused = False
    owner.recovery_count = 0
    owner.engine.poll_at = time.monotonic()
    timed_out = bool(owner._v163b_network_timed_out)
    can_resume = bool(
        not timed_out
        and owner._v163b_network_auto_resume
        and not owner.engine.stopped
        and store is not None
        and not str(store.data.get("halt") or "").strip()
    )
    owner.engine.enabled = can_resume
    owner.emit("network", "正常")
    if can_resume:
        owner.emit("status", "网络已恢复 · 账户/仓位/委托核对通过 · 自动交易继续")
        owner.emit(
            "log",
            "网络已在60秒内恢复：账户、余额、BTC仓位、普通委托与策略委托只读核对通过；"
            "自动交易继续运行，不补发断网期间错过的信号",
        )
    else:
        owner.emit("status", "网络已恢复 · 自动新开仓保持停止 · 可手动重新授权")
        owner.emit(
            "log",
            "网络已恢复并完成只读核对；因断网已超过60秒或原本未授权自动交易，"
            "仅恢复观察，自动新开仓保持停止，请手动重新授权；不补发断网期间信号",
        )

    # Clear timing state but preserve the fact that this cycle recovered only in
    # local variables above.
    owner._v163b_network_started_at = 0.0
    owner._v163b_network_auto_resume = False
    owner._v163b_network_timed_out = False
    owner._v163b_network_probe_count = 0
    owner._v163b_last_network_error = ""
    return "recovered"


def _worker_v163b(self):
    _ensure_state(self)
    while not self.finished.is_set():
        try:
            kind, data = self.tasks.get(timeout=5)
        except queue.Empty:
            kind, data = "tick", None
        try:
            if self.network_paused and kind == "tick" and self.engine:
                recovery = _probe_read_only_recovery(self)
                if recovery in ("waiting", "retry"):
                    continue
                # On recovered, fall through. Engine.poll_at was reset and
                # last_bar advanced, so normal management resumes without
                # backfilling an outage-era entry signal.

            if kind == "diagnose":
                x = app.Exchange(*data[:4], demo=data[4])
                x.network_event = lambda message: self.emit("network", message)
                x.sync_time()
                self.emit("log", "网络自检：公共时间接口通过")
                if all(data[1:4]):
                    x.account()
                    self.emit("log", "网络自检：账户只读认证通过")
                else:
                    self.emit("log", "未填写完整密钥，跳过账户自检")
                continue

            if kind == "connect":
                x = app.Exchange(*data[:4], demo=data[4])
                e = app.Engine(x, self.folder, self.emit)
                x.network_event = lambda message: self.emit("network", message)
                e.connect()
                self.engine = e
                _reset_state(self)
                self.history_key = None
                self.refresh_history()
                self.emit("log", "开始加载1D / 4H / 1H / 15m / 5m指标历史K线，首次可能需数十秒")
                e.refresh_market()
            elif kind == "arm":
                if not self.engine:
                    raise Halt("先连接")
                self.engine.market = None
                self.engine.arm(data)
            elif kind == "stop" and self.engine:
                self._v163b_network_auto_resume = False
                self.engine.stop()
            elif kind == "flatten" and self.engine:
                self._v163b_network_auto_resume = False
                self.engine.flatten()
            elif kind == "ack" and self.engine:
                self.engine.acknowledge()

            if self.engine:
                self.engine.cycle()
                self.refresh_history()
                if time.monotonic() - self.public_at > 5:
                    self.emit("ticker", self.engine.x.ticker())
                    self.public_at = time.monotonic()
                self.emit(
                    "status",
                    "全自动运行 / " + ("模拟盘" if self.engine.x.demo else "实盘")
                    if self.engine.enabled
                    else "故障暂停 · 需核对"
                    if self.engine.store.data["halt"]
                    else "已停止新开仓 / 继续核对持仓",
                )
        except app.CandlePending as exc:
            message = str(exc)
            self.emit("status", "等待最新收盘K线 · 暂不新开仓")
            self.emit("candle_wait", message)
            persistent = "持续过期" in message or "连续异常" in message
            if persistent and time.monotonic() - self.candle_wait_log >= 15:
                self.emit("log", message)
                self.candle_wait_log = time.monotonic()
        except NetworkError as exc:
            # Only read-side transport failures receive the 60s grace period.
            # POST uncertainty remains fail-closed and is never retried here.
            if self.engine is not None and not _is_write_network_error(exc):
                _begin_read_only_recovery(self, exc)
            elif self.engine:
                self.engine.halt(str(exc))
            else:
                self.emit("alarm", str(exc))
        except Exception as exc:
            if self.engine:
                self.engine.halt(str(exc))
            else:
                self.emit("alarm", str(exc))
        finally:
            if kind != "tick":
                self.emit("done", None)


def _app_init_v163b(self, *args, **kwargs):
    _PREVIOUS_INIT(self, *args, **kwargs)
    _ensure_state(self)
    self.root.title(ui163.WINDOW_TITLE)
    ui163._sync_visible_version(self)
    try:
        self.signal.set("V1.6.3b · 外轨 only / RSI30–70 / 5m MACD改善 / 4H同向 · 网络60秒自动恢复")
    except Exception:
        pass
    self._v163b_network_patch_ready = True


def apply():
    if getattr(app.App, "_kaytrade_v163b_network_applied", False):
        return
    app.App.worker = _worker_v163b
    app.App.__init__ = _app_init_v163b
    app.App._kaytrade_v163b_network_applied = True


apply()

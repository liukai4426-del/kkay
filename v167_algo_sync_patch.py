"""KAYTRADE V1.6.7 pending-algo state synchronization.

Normal trading cycles no longer query four pending-algo REST families in series.
Instead:
1) subscribe to OKX business WebSocket algo channels;
2) while the stream is live, fetch one parallel REST snapshot of the four pending
   families and replay any WebSocket updates received during that snapshot;
3) serve engine algos() reads from the trusted in-memory cache;
4) on WebSocket disconnect, mark the cache untrusted immediately, reconnect and
   resync in the background; new entries remain fail-closed until resync finishes.

Read-only synchronization failures never disable the user's automatic-trading
authorization.  POST/write ambiguity and all existing idempotency protections are
untouched.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from v166_readonly_resilience_patch import apply as apply_previous
apply_previous()

import certifi
import websocket

import app
import engine
import exchange
import v165_algo_fallback_patch as fallback

VERSION = "1.6.7"
BUILD = "1670"
_TARGET = engine.INSTRUMENT
_FAMILIES = tuple(fallback._FAMILIES)
_TERMINAL_STATES = {"effective", "canceled", "order_failed", "filled"}
_INIT_WAIT_SECONDS = 45.0
_RECONNECT_MAX_SECONDS = 15.0
_INIT_LOCK = threading.Lock()

_PREVIOUS_ENGINE_CONNECT = engine.Engine.connect
_PREVIOUS_ENGINE_ARM = engine.Engine.arm
_PREVIOUS_APP_CONNECT = app.App.connect


def _ensure_state(self):
    if hasattr(self, "_v167_algo_lock"):
        return
    with _INIT_LOCK:
        if hasattr(self, "_v167_algo_lock"):
            return
        self._v167_algo_lock = threading.RLock()
        self._v167_algo_ready = threading.Event()
        self._v167_algo_stop = threading.Event()
        self._v167_algo_thread = None
        self._v167_algo_ws = None
        self._v167_algo_generation = 0
        self._v167_algo_cache = {}
        self._v167_algo_buffer = []
        self._v167_algo_trusted = False
        self._v167_algo_ever_ready = False
        self._v167_algo_ws_connected = False
        self._v167_algo_snapshot_at = 0.0
        self._v167_algo_last_error = ""
        self._v167_algo_last_notice = ""


def _notice(self, text):
    _ensure_state(self)
    message = str(text or "")
    with self._v167_algo_lock:
        if message == self._v167_algo_last_notice:
            return
        self._v167_algo_last_notice = message
    try:
        self.network_event(message)
    except Exception:
        pass


def _ws_url(self):
    # OKX publishes separate US/AU websocket hosts. EEA/global REST domains use
    # the standard ws/wspap hosts documented for the business endpoint.
    if str(getattr(self, "host", "")) == "us.okx.com":
        return (
            "wss://wsuspap.okx.com:8443/ws/v5/business"
            if bool(getattr(self, "demo", False))
            else "wss://wsus.okx.com:8443/ws/v5/business"
        )
    return (
        "wss://wspap.okx.com:8443/ws/v5/business"
        if bool(getattr(self, "demo", False))
        else "wss://ws.okx.com:8443/ws/v5/business"
    )


def _algo_key(row):
    if not isinstance(row, dict):
        return ""
    algo_id = str(row.get("algoId") or "").strip()
    if algo_id:
        return "id:" + algo_id
    client_id = str(row.get("algoClOrdId") or row.get("attachAlgoClOrdId") or "").strip()
    if client_id:
        return "cid:" + client_id
    # Defensive fallback for an OKX row that lacks IDs. Keeping such a row in
    # cache is safer than silently treating it as absent.
    pieces = (
        row.get("ordType"), row.get("instId"), row.get("side"), row.get("posSide"),
        row.get("cTime"), row.get("sz"), row.get("tpTriggerPx"), row.get("slTriggerPx"),
    )
    if any(str(x or "") for x in pieces):
        return "fallback:" + "|".join(str(x or "") for x in pieces)
    return ""


def _apply_rows_locked(self, rows):
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        inst_id = str(raw.get("instId") or "").strip()
        if inst_id and inst_id != _TARGET:
            continue
        key = _algo_key(raw)
        if not key:
            continue
        state = str(raw.get("state") or "").strip().lower()
        if state in _TERMINAL_STATES:
            self._v167_algo_cache.pop(key, None)
        else:
            self._v167_algo_cache[key] = dict(raw)


def _snapshot_rows(self):
    """Fetch the four audited pending families concurrently, not serially."""
    rows = []
    with ThreadPoolExecutor(max_workers=len(_FAMILIES), thread_name_prefix="kaytrade-algo-rest") as pool:
        jobs = {
            pool.submit(fallback._family_read, self, ord_type, label): (ord_type, label)
            for ord_type, label in _FAMILIES
        }
        for future in as_completed(jobs):
            part = future.result()
            if not isinstance(part, list):
                ord_type, label = jobs[future]
                raise exchange.APIError(f"{label}/{ord_type}只读快照结构异常")
            rows.extend(part)
    return rows


def _snapshot_until_ready(self, generation):
    delay = 2.0
    while True:
        _ensure_state(self)
        if self._v167_algo_stop.is_set():
            return
        with self._v167_algo_lock:
            if generation != self._v167_algo_generation or not self._v167_algo_ws_connected:
                return
        try:
            rows = _snapshot_rows(self)
        except Exception as exc:
            with self._v167_algo_lock:
                self._v167_algo_last_error = str(exc)
            _notice(self, f"Algo只读快照后台重试中：{str(exc)[:180]}")
            if self._v167_algo_stop.wait(delay):
                return
            delay = min(delay * 2.0, _RECONNECT_MAX_SECONDS)
            continue

        with self._v167_algo_lock:
            if generation != self._v167_algo_generation or not self._v167_algo_ws_connected:
                return
            buffered = list(self._v167_algo_buffer)
            self._v167_algo_cache = {}
            _apply_rows_locked(self, rows)
            _apply_rows_locked(self, buffered)
            self._v167_algo_buffer = []
            self._v167_algo_trusted = True
            self._v167_algo_ever_ready = True
            self._v167_algo_snapshot_at = time.time()
            self._v167_algo_last_error = ""
            self._v167_algo_ready.set()
            count = len(self._v167_algo_cache)
        _notice(self, f"Algo状态实时同步正常 · REST快照+WS · BTC待处理策略单 {count}")
        return


def _login_payload(self):
    timestamp = str(int(float(self.server_now())))
    prehash = timestamp + "GET" + "/users/self/verify"
    sign = base64.b64encode(
        hmac.new(str(self.secret).encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "op": "login",
        "args": [{
            "apiKey": str(self.key),
            "passphrase": str(self.phrase),
            "timestamp": timestamp,
            "sign": sign,
        }],
    }


def _recv_json(ws, deadline_seconds=12.0):
    deadline = time.monotonic() + float(deadline_seconds)
    while time.monotonic() < deadline:
        raw = ws.recv()
        if raw in ("pong", b"pong", "", None):
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    raise TimeoutError("OKX WebSocket握手响应超时")


def _login_and_subscribe(self, ws):
    ws.send(json.dumps(_login_payload(self), separators=(",", ":")))
    while True:
        msg = _recv_json(ws)
        event = str(msg.get("event") or "")
        if event == "login":
            if str(msg.get("code") or "0") != "0":
                raise exchange.NetworkError(
                    "Algo WebSocket登录失败：" + str(msg.get("msg") or msg.get("code") or "unknown"),
                    method="GET", path="/ws/v5/business",
                )
            break
        if event == "error":
            raise exchange.NetworkError(
                "Algo WebSocket登录错误：" + str(msg.get("msg") or msg.get("code") or "unknown"),
                method="GET", path="/ws/v5/business",
            )

    args = [
        {"channel": "orders-algo", "instType": "ANY", "instId": _TARGET},
        {"channel": "algo-advance", "instType": "SWAP", "instId": _TARGET},
    ]
    ws.send(json.dumps({"id": "v167algo", "op": "subscribe", "args": args}, separators=(",", ":")))
    subscribed = set()
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline and len(subscribed) < 2:
        msg = _recv_json(ws, max(1.0, deadline - time.monotonic()))
        if str(msg.get("event") or "") == "error":
            raise exchange.NetworkError(
                "Algo WebSocket订阅失败：" + str(msg.get("msg") or msg.get("code") or "unknown"),
                method="GET", path="/ws/v5/business",
            )
        if str(msg.get("event") or "") == "subscribe":
            channel = str((msg.get("arg") or {}).get("channel") or "")
            if channel in ("orders-algo", "algo-advance"):
                subscribed.add(channel)
    if subscribed != {"orders-algo", "algo-advance"}:
        raise TimeoutError("Algo WebSocket订阅确认不完整")


def _handle_ws_payload(self, payload):
    if not isinstance(payload, dict):
        return
    if str(payload.get("event") or "") == "error":
        raise exchange.NetworkError(
            "Algo WebSocket运行错误：" + str(payload.get("msg") or payload.get("code") or "unknown"),
            method="GET", path="/ws/v5/business",
        )
    arg = payload.get("arg") or {}
    channel = str(arg.get("channel") or "")
    if channel not in ("orders-algo", "algo-advance"):
        return
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        return
    with self._v167_algo_lock:
        if self._v167_algo_trusted:
            _apply_rows_locked(self, rows)
        else:
            self._v167_algo_buffer.extend(dict(r) for r in rows if isinstance(r, dict))


def _open_ws(self):
    return websocket.create_connection(
        _ws_url(self),
        timeout=10,
        sslopt={"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": certifi.where()},
        http_proxy_host=None,
        http_proxy_port=None,
        http_no_proxy=["*"],
        enable_multithread=True,
    )


def _algo_ws_loop(self):
    backoff = 1.0
    while not self._v167_algo_stop.is_set():
        ws = None
        try:
            ws = _open_ws(self)
            _login_and_subscribe(self, ws)
            try:
                ws.settimeout(25)
            except Exception:
                pass

            with self._v167_algo_lock:
                self._v167_algo_generation += 1
                generation = self._v167_algo_generation
                self._v167_algo_ws = ws
                self._v167_algo_ws_connected = True
                self._v167_algo_trusted = False
                self._v167_algo_ready.clear()
                self._v167_algo_buffer = []
            threading.Thread(
                target=_snapshot_until_ready,
                args=(self, generation),
                name="kaytrade-algo-snapshot",
                daemon=True,
            ).start()
            _notice(self, "Algo实时通道已连接 · 正在并行校准REST快照")
            backoff = 1.0

            while not self._v167_algo_stop.is_set():
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    ws.send("ping")
                    continue
                if raw in ("pong", b"pong", "", None):
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                _handle_ws_payload(self, json.loads(raw))
        except Exception as exc:
            with self._v167_algo_lock:
                self._v167_algo_trusted = False
                self._v167_algo_ws_connected = False
                self._v167_algo_ready.clear()
                self._v167_algo_last_error = str(exc)
            _notice(self, f"Algo实时通道重连中：{type(exc).__name__} · {str(exc)[:160]}")
        finally:
            with self._v167_algo_lock:
                self._v167_algo_ws_connected = False
                self._v167_algo_trusted = False
                self._v167_algo_ready.clear()
                self._v167_algo_ws = None
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

        if self._v167_algo_stop.wait(backoff):
            break
        backoff = min(backoff * 2.0, _RECONNECT_MAX_SECONDS)


def _start_algo_state(self):
    _ensure_state(self)
    if not all((getattr(self, "key", ""), getattr(self, "secret", ""), getattr(self, "phrase", ""))):
        return False
    with self._v167_algo_lock:
        thread = self._v167_algo_thread
        if thread is not None and thread.is_alive():
            return True
        self._v167_algo_stop.clear()
        thread = threading.Thread(target=_algo_ws_loop, args=(self,), name="kaytrade-algo-ws", daemon=True)
        self._v167_algo_thread = thread
        thread.start()
    return True


def _stop_algo_state(self):
    _ensure_state(self)
    self._v167_algo_stop.set()
    with self._v167_algo_lock:
        ws = self._v167_algo_ws
        thread = self._v167_algo_thread
        self._v167_algo_trusted = False
        self._v167_algo_ready.clear()
    if ws is not None:
        try:
            ws.close()
        except Exception:
            pass
    if thread is not None and thread is not threading.current_thread():
        try:
            thread.join(timeout=2.0)
        except Exception:
            pass


def _algo_state_status(self):
    _ensure_state(self)
    with self._v167_algo_lock:
        return {
            "trusted": bool(self._v167_algo_trusted),
            "ever_ready": bool(self._v167_algo_ever_ready),
            "ws_connected": bool(self._v167_algo_ws_connected),
            "snapshot_at": float(self._v167_algo_snapshot_at or 0.0),
            "count": len(self._v167_algo_cache),
            "last_error": str(self._v167_algo_last_error or ""),
        }


def _raise_untrusted(self):
    message = "Algo实时状态暂未完成可信同步；仅跳过本周期新开仓，自动交易保持开启，后台继续REST+WebSocket重同步"
    # Build1661's NetworkError state machine consumes this marker and therefore
    # does not turn a read-side synchronization gap into a global auto-trading stop.
    try:
        self._v166_last_readonly_failure = {
            "at": time.monotonic(),
            "message": message,
            "path": "/ws/v5/business:orders-algo",
            "code": "",
        }
    except Exception:
        pass
    raise exchange.NetworkError(message, method="GET", path="/ws/v5/business:orders-algo")


def _algos_v167(self):
    _ensure_state(self)
    _start_algo_state(self)
    with self._v167_algo_lock:
        if self._v167_algo_trusted:
            return [dict(v) for v in self._v167_algo_cache.values()]
        ever_ready = bool(self._v167_algo_ever_ready)

    # Only the first account synchronization may wait. Once the cache has ever
    # been valid, any later disconnect fails fast so the strategy loop is never
    # held hostage by a flaky REST family.
    if not ever_ready:
        self._v167_algo_ready.wait(_INIT_WAIT_SECONDS)
        with self._v167_algo_lock:
            if self._v167_algo_trusted:
                return [dict(v) for v in self._v167_algo_cache.values()]
    _raise_untrusted(self)


def _engine_connect_v167(self):
    result = _PREVIOUS_ENGINE_CONNECT(self)
    try:
        self.x.start_algo_state()
        self.emit("log", "V1.6.7 Algo同步已启动：业务WebSocket实时维护；首次REST四类策略单并行快照在后台校准")
    except Exception as exc:
        self.emit("log", "V1.6.7 Algo实时同步启动失败，后台将在首次策略单检查时重试：" + str(exc))
    return result


def _engine_arm_v167(self, settings):
    self.x.start_algo_state()
    # arm() is the one place where initial trust is mandatory before auto-entry
    # starts.  After this succeeds, normal cycles use cache-only reads.
    self.x.algos()
    return _PREVIOUS_ENGINE_ARM(self, settings)


def _app_connect_v167(self):
    old = getattr(self, "engine", None)
    if old is not None and not bool(getattr(old, "enabled", False)):
        store = getattr(old, "store", None)
        active = bool(getattr(store, "data", {}).get("active")) if store is not None else False
        if not active:
            try:
                old.x.stop_algo_state()
            except Exception:
                pass
    return _PREVIOUS_APP_CONNECT(self)


def apply():
    if getattr(exchange.Exchange, "_kaytrade_v167_algo_sync_applied", False):
        return
    exchange.Exchange.start_algo_state = _start_algo_state
    exchange.Exchange.stop_algo_state = _stop_algo_state
    exchange.Exchange.algo_state_status = _algo_state_status
    exchange.Exchange.algos = _algos_v167

    engine.Engine.connect = _engine_connect_v167
    engine.Engine.arm = _engine_arm_v167
    app.App.connect = _app_connect_v167

    exchange.Exchange._kaytrade_v167_algo_sync_applied = True
    engine.Engine._kaytrade_v167_algo_sync_applied = True
    app.App._kaytrade_v167_algo_sync_applied = True


apply()

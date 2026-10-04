"""KAYTRADE V1.9.1 Dual Environment Execution overlay.

DEMO
- isolated credentials/state/bridge/token
- AI auto execution to OKX simulated trading
- LIMIT ONLY

LIVE
- isolated credentials/state/bridge/token
- guarded real-account execution channel ON/OFF
- AI plan auto execution when explicitly enabled
- LIMIT parent entry/reduction + trigger-market TP/SL

No Paper runtime is reintroduced.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import threading
import time
from decimal import Decimal
from pathlib import Path

from v184_ui_patch import apply as apply_previous
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
import v183_okx_demo_patch as v183
import v184_ui_patch as v184
import visual
from core import INSTRUMENT
from exchange import Exchange, APIError

VERSION = "1.9.1"
BUILD = "1911"
LIMIT_ONLY = False
PAPER_RUNTIME_PRESENT = False
DEMO_BRIDGE_PORT = int(os.getenv("KAYTRADE_DEMO_BRIDGE_PORT", "17872"))
LIVE_BRIDGE_PORT = int(os.getenv("KAYTRADE_LIVE_BRIDGE_PORT", "17873"))
BRIDGE_HOST = "127.0.0.1"

ENV_META = {
    "demo": {
        "label": "OKX模拟盘",
        "short": "DEMO",
        "mode": "OKX_DEMO_EXECUTION",
        "folder": "demo",
        "descriptor": "ai_bridge_demo.json",
        "port": DEMO_BRIDGE_PORT,
    },
    "live": {
        "label": "OKX实盘",
        "short": "LIVE",
        "mode": "OKX_LIVE_EXECUTION",
        "folder": "live",
        "descriptor": "ai_bridge_live.json",
        "port": LIVE_BRIDGE_PORT,
    },
}

_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_QUIT = app.App.quit


def _env_from_mode(value):
    return "demo" if str(value) == "OKX模拟盘" else "live"


def _env_label(environment):
    return ENV_META[environment]["label"]


class LiveReadOnlyExchange:
    """Compatibility name for a real-account exchange with a hard write lock.

    The raw OKX real-account adapter is readable immediately after connection.
    POST writes are structurally blocked until the LIVE engine explicitly arms
    the wrapper. Stopping or faulting the engine closes the gate again.
    """

    def __init__(self, exchange):
        if exchange.demo:
            raise legacy_engine.Halt("LIVE Guard 只能包装 OKX 实盘连接")
        self._x = exchange
        self.demo = False
        self.host = exchange.host
        self._writes_enabled = False
        self._write_lock = threading.RLock()

    def __getattr__(self, name):
        return getattr(self._x, name)

    @property
    def writes_enabled(self):
        with self._write_lock:
            return bool(self._writes_enabled)

    def set_writes_enabled(self, enabled):
        with self._write_lock:
            self._writes_enabled = bool(enabled)

    def _require_write_gate(self, path):
        if not self.writes_enabled:
            raise APIError(
                "LIVE写入锁关闭：请先在KAYTRADE界面明确开启LIVE AI自动执行通道",
                deterministic=True,
                method="POST",
                path=path,
            )

    def post(self, path, body):
        self._require_write_gate(path)
        return self._x.post(path, body)

    def request(self, method, path, data=None, private=False):
        if str(method).upper() == "POST":
            self._require_write_gate(path)
        return self._x.request(method, path, data, private)


class DemoEngineV190(v183.OKXDemoEngineV183):
    """V1.8.4 demo execution with an explicit environment gate."""

    environment = "demo"

    def _assert_env(self, payload):
        requested = str((payload or {}).get("environment") or "demo").lower()
        if requested != "demo":
            raise legacy_engine.Halt(
                f"Environment Match Gate：请求={requested}，Engine=demo"
            )

    def publish_ai_plan(self, plan):
        self._assert_env(plan)
        if self.enabled:
            self._preflight_account()
        result = super().publish_ai_plan(plan)
        if isinstance(result, dict):
            result = dict(result)
            result["environment"] = "demo"
            result["mode"] = ENV_META["demo"]["mode"]
        return result

    def submit_ai_trade(self, proposal):
        self._assert_env(proposal)
        result = super().submit_ai_trade(proposal)
        if isinstance(result, dict):
            result = dict(result)
            result["environment"] = "demo"
            result["mode"] = ENV_META["demo"]["mode"]
        return result

    def cancel_demo_entry(self, payload):
        self._assert_env(payload)
        return super().cancel_demo_entry(payload)

    def amend_demo_entry(self, payload):
        self._assert_env(payload)
        return super().amend_demo_entry(payload)

    def amend_demo_protection(self, payload):
        self._assert_env(payload)
        return super().amend_demo_protection(payload)

    def close_demo_position(self, payload):
        self._assert_env(payload)
        return super().close_demo_position(payload)

    def ai_state(self):
        state = super().ai_state()
        state.update(
            {
                "version": VERSION,
                "build": BUILD,
                "mode": ENV_META["demo"]["mode"],
                "environment": "demo",
                "environment_label": ENV_META["demo"]["label"],
                "demo_exchange_writes": True,
                "live_ai_writes": False,
                "live_plan_review": False,
                "paper_runtime_present": False,
            }
        )
        return state


class LiveReviewEngineV190(v183.OKXDemoEngineV183):
    """V1.9.1 guarded OKX real-account AI execution engine.

    The class name is retained for compatibility with the V1.9.0 worker/UI.
    State, credentials, bridge and token remain isolated from DEMO.
    """

    environment = "live"

    def _assert_env(self, payload):
        requested = str((payload or {}).get("environment") or "").lower()
        if requested != "live":
            raise legacy_engine.Halt(
                f"Environment Match Gate：请求={requested or 'missing'}，Engine=live"
            )

    def _ensure_demo_state(self):
        if not self.store:
            return None
        # One-way V1.9.0 migration: review-only state is not promoted into orders.
        self.store.data.pop("live_review", None)
        self.store.data.pop("okx_demo_execution", None)
        self.store.data.pop("paper_execution", None)
        state = self.store.data.setdefault("okx_live_execution", {})
        state.setdefault("tiers", {"1": None, "2": None})
        state.setdefault("close_orders", [])
        state.setdefault("events", [])
        state.setdefault("results", {})
        state.setdefault("auto_execute_plans", False)
        state.setdefault("channel_enabled", False)
        state.setdefault("updated_at", time.time())
        if set(state["tiers"].keys()) != {"1", "2"}:
            state["tiers"] = {
                "1": state["tiers"].get("1"),
                "2": state["tiers"].get("2"),
            }
        self.store.save()
        return state

    def _demo_state(self):
        if not self.store:
            raise legacy_engine.Halt("KAYTRADE尚未连接OKX实盘")
        return self._ensure_demo_state()

    def _review_state(self):
        # Compatibility for the V1.9.0 dashboard.
        return self._demo_state()

    def _event(self, name, payload):
        state = self._demo_state()
        clean = dict(payload)
        if clean.get("detail"):
            clean["detail"] = str(clean["detail"]).replace("OKX模拟盘", "OKX实盘")
        row = {"time": time.time(), "event": name, **clean}
        state["events"].append(row)
        del state["events"][:-120]
        state["updated_at"] = time.time()
        self.store.save()
        self.emit(
            "log",
            f"OKX LIVE · {name} · {clean.get('detail') or clean.get('tier') or ''}",
        )
        return row

    def _preflight_account(self):
        if not self.store:
            raise legacy_engine.Halt("先连接OKX实盘")
        if getattr(self.x, "demo", True):
            raise legacy_engine.Halt("Environment Match Gate：LIVE Engine 收到 Demo Exchange")
        if self.store.data.get("halt"):
            reason = legacy_engine.normalize_halt_reason(self.store.data.get("halt"))
            raise legacy_engine.Halt(
                legacy_engine.HALT_PREFIX + reason + legacy_engine.HALT_SUFFIX
            )
        account = self.x.account()
        if account.get("uid") != self.connection_id:
            raise legacy_engine.Halt("实盘账户标识发生变化")
        if account.get("posMode") != "long_short_mode":
            raise legacy_engine.Halt("V1.9.1 LIVE 要求 OKX 双向持仓模式")
        perms = set(str(account.get("perm") or "").split(","))
        if "trade" not in perms or "withdraw" in perms:
            raise legacy_engine.Halt("LIVE API必须有交易权限且不得有提币权限")
        return account

    def _foreign_state_check(self):
        try:
            return super()._foreign_state_check()
        except legacy_engine.Halt as exc:
            raise legacy_engine.Halt(
                str(exc).replace("OKX模拟盘", "OKX实盘").replace("模拟盘", "实盘")
            ) from None

    def connect(self):
        v180.AIOnlyEngineV180.connect(self)
        self._purge_removed_paper_state()
        self._ensure_demo_state()
        self.x.set_writes_enabled(False)
        self.enabled = False
        self.stopped = True
        self.emit(
            "log",
            "V1.9.1 OKX实盘连接成功：默认写入锁关闭；需人工开启LIVE AI自动执行通道。",
        )

    def activation_check(self):
        """Read-only LIVE preflight used by the UI before opening the write gate."""
        with self._ai_lock:
            self.x.set_writes_enabled(False)
            self._preflight_account()
            self._sync_all(cancel_expired=False)
            self._foreign_state_check()
            return {
                "ok": True,
                "environment": "live",
                "mode": ENV_META["live"]["mode"],
            }

    def arm(self, _settings=None):
        with self._ai_lock:
            self.activation_check()
            state = self._demo_state()
            state["auto_execute_plans"] = True
            state["channel_enabled"] = True
            state["updated_at"] = time.time()
            self.store.save()
            self.x.set_writes_enabled(True)
            self.enabled = True
            self.stopped = False
            self.poll_at = time.monotonic()
            self.emit(
                "log",
                "LIVE AI自动执行已开启：AI完整方案可提交OKX实盘LIMIT入场；TP/SL为触发后市价保护。",
            )

    def stop(self):
        with self._ai_lock:
            state = self._demo_state() if self.store else None
            if state is not None:
                state["auto_execute_plans"] = False
                state["channel_enabled"] = False
                state["updated_at"] = time.time()
                self.store.save()
            self.enabled = False
            self.stopped = True
            self.x.set_writes_enabled(False)
            self.emit(
                "log",
                "LIVE AI自动执行已关闭：禁止新的实盘写入；交易所已生效保护单继续由OKX执行。",
            )

    def set_review_enabled(self, enabled):
        # Compatibility hook for the V1.9.0 UI button.
        if enabled:
            self.arm(None)
        else:
            self.stop()
        return {
            "version": VERSION,
            "build": BUILD,
            "mode": ENV_META["live"]["mode"],
            "environment": "live",
            "channel_enabled": bool(self.enabled),
            "live_ai_writes": True,
            "live_write_enabled": bool(self.enabled and self.x.writes_enabled),
            "manual_execution_required": False,
            "market_protection": True,
        }

    def set_auto_execute_plans(self, enabled):
        with self._ai_lock:
            if not self.enabled:
                raise legacy_engine.Halt("先启用LIVE AI自动执行通道")
            if enabled:
                self._preflight_account()
            state = self._demo_state()
            state["auto_execute_plans"] = bool(enabled)
            state["channel_enabled"] = bool(enabled)
            state["updated_at"] = time.time()
            self.store.save()
            self.x.set_writes_enabled(bool(enabled))
            self.emit(
                "log",
                "LIVE AI方案自动执行已" + (
                    "开启：完整AI方案到达即提交OKX实盘LIMIT入场" if enabled else "关闭"
                ),
            )
            return {
                "version": VERSION,
                "build": BUILD,
                "mode": ENV_META["live"]["mode"],
                "environment": "live",
                "auto_execute_plans": bool(enabled),
                "live_write_enabled": bool(enabled),
                "market_protection": True,
            }

    @staticmethod
    def _live_result(value):
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                out[key] = LiveReviewEngineV190._live_result(item)
            if out.get("mode") in ("OKX_DEMO_EXECUTION", "AI_ONLY"):
                out["mode"] = ENV_META["live"]["mode"]
            if out.get("environment") in ("OKX_DEMO", "demo"):
                out["environment"] = "live"
            return out
        if isinstance(value, list):
            return [LiveReviewEngineV190._live_result(item) for item in value]
        return value

    def publish_ai_plan(self, plan):
        self._assert_env(plan)
        result = super().publish_ai_plan(plan)
        result = self._live_result(result)
        result.update(
            version=VERSION,
            build=BUILD,
            mode=ENV_META["live"]["mode"],
            environment="live",
            live_ai_writes=True,
            live_write_enabled=bool(self.enabled and self.x.writes_enabled),
            manual_execution_required=False,
            market_protection=True,
        )
        return result

    def submit_ai_trade(self, proposal):
        self._assert_env(proposal)
        result = super().submit_ai_trade(proposal)
        return self._live_result(result)

    def cancel_demo_entry(self, payload):
        self._assert_env(payload)
        return self._live_result(super().cancel_demo_entry(payload))

    def amend_demo_entry(self, payload):
        self._assert_env(payload)
        return self._live_result(super().amend_demo_entry(payload))

    def amend_demo_protection(self, payload):
        self._assert_env(payload)
        return self._live_result(super().amend_demo_protection(payload))

    def close_demo_position(self, payload):
        self._assert_env(payload)
        return self._live_result(super().close_demo_position(payload))

    def ai_state(self):
        state = super().ai_state()
        live_state = state.pop("okx_demo_execution", None)
        if live_state is None and self.store:
            live_state = self._demo_state()
        state.update(
            {
                "version": VERSION,
                "build": BUILD,
                "mode": ENV_META["live"]["mode"],
                "environment": "live",
                "environment_label": ENV_META["live"]["label"],
                "demo_exchange_writes": False,
                "live_ai_writes": True,
                "live_write_enabled": bool(self.enabled and self.x.writes_enabled),
                "manual_execution_required": False,
                "live_plan_review": False,
                "limit_only": False,
                "limit_entry_only": True,
                "market_protection": True,
                "supported_entry_order_types": ["limit"],
                "supported_close_order_types": ["limit"],
                "supported_protection_exit_order_types": ["trigger_market"],
                "okx_live_execution": live_state,
                "auto_execute_plans": bool(
                    (live_state or {}).get("auto_execute_plans", False)
                ),
            }
        )
        return state


class _DisabledLegacyBridge:
    def __init__(self, owner):
        self.owner = owner

    def start(self):
        return None

    def stop(self):
        return None


class DualBridgeServer:
    def __init__(self, owner, environment):
        self.owner = owner
        self.environment = environment
        self.token = secrets.token_urlsafe(32)
        self.httpd = None
        self.thread = None
        self.path = Path(owner.folder) / ENV_META[environment]["descriptor"]

    def _engine(self):
        engine = (getattr(self.owner, "_v190_engines", {}) or {}).get(self.environment)
        expected = DemoEngineV190 if self.environment == "demo" else LiveReviewEngineV190
        if not isinstance(engine, expected):
            raise legacy_engine.Halt(f"{_env_label(self.environment)}尚未连接")
        return engine

    def start(self):
        environment = self.environment
        owner = self.owner
        token = self.token
        bridge = self

        class Handler(v172.BaseHTTPRequestHandler):
            server_version = "KayTradeDual/1.9.1"

            def log_message(self, _format, *_args):
                return

            def _json(self, status, payload):
                raw = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                self.send_response(status)
                self.send_header(
                    "Content-Type", "application/json; charset=utf-8"
                )
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _auth(self):
                if self.headers.get("Authorization", "") == f"Bearer {token}":
                    return True
                self._json(401, {"error": "unauthorized"})
                return False

            def do_GET(self):
                if not self._auth():
                    return
                if self.path == "/health":
                    engine = (getattr(owner, "_v190_engines", {}) or {}).get(
                        environment
                    )
                    self._json(
                        200,
                        {
                            "name": "KAYTRADE",
                            "version": VERSION,
                            "build": BUILD,
                            "mode": ENV_META[environment]["mode"],
                            "environment": environment,
                            "connected": engine is not None,
                            "ai_enabled": bool(engine and engine.enabled),
                            "demo_exchange_writes": environment == "demo",
                            "live_ai_writes": environment == "live",
                            "live_write_enabled": bool(engine and engine.enabled) if environment == "live" else False,
                            "live_plan_review": False,
                            "manual_execution_required": False,
                            "paper_runtime_present": False,
                            "limit_only": False,
                            "limit_entry_only": True,
                            "market_protection": True,
                        },
                    )
                    return
                if self.path == "/v1/state":
                    try:
                        self._json(200, bridge._engine().ai_state())
                    except legacy_engine.Halt as exc:
                        self._json(409, {"error": str(exc)})
                    return
                self._json(404, {"error": "not_found"})

            def do_POST(self):
                if not self._auth():
                    return
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                    if length <= 0 or length > 65536:
                        raise ValueError("invalid content length")
                    payload = json.loads(
                        self.rfile.read(length).decode("utf-8")
                    )
                    requested = str(payload.get("environment") or "").lower()
                    if requested != environment:
                        raise legacy_engine.Halt(
                            f"Environment Match Gate：请求={requested or 'missing'}，Bridge={environment}"
                        )
                    engine = bridge._engine()

                    routes = {
                        "/v1/plan": engine.publish_ai_plan,
                        "/v1/trade": engine.submit_ai_trade,
                        "/v1/cancel-entry": engine.cancel_demo_entry,
                        "/v1/amend-entry": engine.amend_demo_entry,
                        "/v1/amend-protection": engine.amend_demo_protection,
                        "/v1/close": engine.close_demo_position,
                    }
                    fn = routes.get(self.path)
                    if fn is None:
                        self._json(404, {"error": "not_found"})
                        return
                    self._json(200, fn(payload))
                except legacy_engine.Halt as exc:
                    self._json(409, {"error": str(exc)})
                except APIError as exc:
                    self._json(
                        502,
                        {"error": str(exc), "code": getattr(exc, "code", "")},
                    )
                except Exception as exc:
                    self._json(
                        500,
                        {"error": f"{type(exc).__name__}: {exc}"},
                    )

        preferred = ENV_META[environment]["port"]
        try:
            httpd = v172.ThreadingHTTPServer((BRIDGE_HOST, preferred), Handler)
        except OSError:
            httpd = v172.ThreadingHTTPServer((BRIDGE_HOST, 0), Handler)
        self.httpd = httpd
        port = int(httpd.server_address[1])
        descriptor = {
            "version": VERSION,
            "build": BUILD,
            "mode": ENV_META[environment]["mode"],
            "environment": environment,
            "host": BRIDGE_HOST,
            "port": port,
            "token": self.token,
            "demo_exchange_writes": environment == "demo",
            "live_ai_writes": environment == "live",
            "live_plan_review": False,
            "manual_execution_required": False,
            "paper_runtime_present": False,
            "limit_only": False,
            "limit_entry_only": True,
            "market_protection": True,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(
            self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        with os.fdopen(fd, "w") as handle:
            json.dump(descriptor, handle, ensure_ascii=False, indent=2)
        self.thread = threading.Thread(
            target=httpd.serve_forever,
            name=f"kaytrade-{environment}-bridge",
            daemon=True,
        )
        self.thread.start()
        owner.emit(
            "log",
            f"{ENV_META[environment]['short']} Bridge：127.0.0.1:{port} · 独立Token/状态",
        )

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


def _save_profile(owner, environment):
    owner._v190_profiles[environment] = {
        "host": owner.host.get(),
        "key": owner.key.get().strip(),
        "secret": owner.secret.get().strip(),
        "phrase": owner.phrase.get(),
    }


def _load_profile(owner, environment):
    profile = owner._v190_profiles[environment]
    owner.host.set(profile.get("host") or app.HOSTS[0])
    owner.key.set(profile.get("key") or "")
    owner.secret.set(profile.get("secret") or "")
    owner.phrase.set(profile.get("phrase") or "")


def _select_environment(owner, environment):
    if environment not in ENV_META:
        return
    previous = getattr(owner, "_v190_selected", "demo")
    if previous != environment:
        _save_profile(owner, previous)
    owner._v190_selected = environment
    desired = ENV_META[environment]["label"]
    if owner.mode.get() != desired:
        owner._v190_mode_guard = True
        try:
            owner.mode.set(desired)
        finally:
            owner._v190_mode_guard = False
    _load_profile(owner, environment)
    owner.engine = owner._v190_engines.get(environment)
    _update_environment_card(owner)
    _refresh_v190_dashboard(owner)


def _mode_changed(owner, *_args):
    if getattr(owner, "_v190_mode_guard", False):
        return
    _select_environment(owner, _env_from_mode(owner.mode.get()))


def _connect_current(owner):
    env = getattr(owner, "_v190_selected", _env_from_mode(owner.mode.get()))
    _save_profile(owner, env)
    owner.submit("v190_connect", (env, dict(owner._v190_profiles[env])))


def _diagnose_current(owner):
    env = getattr(owner, "_v190_selected", _env_from_mode(owner.mode.get()))
    _save_profile(owner, env)
    owner.submit("v190_diagnose", (env, dict(owner._v190_profiles[env])))


def _emit_for_env(owner, environment, kind, data):
    if kind in ("log", "alarm"):
        owner.emit(kind, f"[{ENV_META[environment]['short']}] {data}")
    elif environment == getattr(owner, "_v190_selected", "demo"):
        owner.emit(kind, data)


def _worker_v190(owner):
    while not owner.finished.is_set():
        try:
            kind, data = owner.tasks.get(timeout=2)
        except queue.Empty:
            kind, data = "v190_tick", None

        try:
            if kind == "v190_diagnose":
                env, profile = data
                raw = Exchange(
                    profile["host"],
                    profile["key"],
                    profile["secret"],
                    profile["phrase"],
                    demo=(env == "demo"),
                )
                x = raw if env == "demo" else LiveReadOnlyExchange(raw)
                x.sync_time()
                _emit_for_env(owner, env, "log", "公共时间接口通过")
                if all(
                    (
                        profile["key"],
                        profile["secret"],
                        profile["phrase"],
                    )
                ):
                    x.account()
                    _emit_for_env(owner, env, "log", "账户认证通过（未执行写入）")

            elif kind == "v190_connect":
                env, profile = data
                raw = Exchange(
                    profile["host"],
                    profile["key"],
                    profile["secret"],
                    profile["phrase"],
                    demo=(env == "demo"),
                )
                folder = (
                    Path(owner.folder)
                    / "environments"
                    / ENV_META[env]["folder"]
                )
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)

                if env == "demo":
                    engine = DemoEngineV190(
                        raw,
                        folder,
                        lambda k, d, e=env: _emit_for_env(
                            owner, e, k, d
                        ),
                    )
                else:
                    engine = LiveReviewEngineV190(
                        LiveReadOnlyExchange(raw),
                        folder,
                        lambda k, d, e=env: _emit_for_env(
                            owner, e, k, d
                        ),
                    )
                engine.connect()
                if env == "live":
                    owner._v191_live_activation_error = ""
                owner._v190_engines[env] = engine
                if owner._v190_selected == env:
                    owner.engine = engine
                try:
                    engine.refresh_market()
                except Exception:
                    pass
                _emit_for_env(
                    owner,
                    env,
                    "log",
                    "连接完成；该环境使用独立Engine / State / Bridge",
                )

            for env, engine in list(
                (getattr(owner, "_v190_engines", {}) or {}).items()
            ):
                if engine is None:
                    continue
                try:
                    engine.cycle()
                except Exception as exc:
                    try:
                        if env == "live":
                            engine.stop()
                        else:
                            engine.enabled = False
                    except Exception:
                        engine.enabled = False
                        try:
                            if env == "live" and hasattr(engine.x, "set_writes_enabled"):
                                engine.x.set_writes_enabled(False)
                        except Exception:
                            pass
                    _emit_for_env(
                        owner, env, "alarm", f"执行循环停止：{exc}"
                    )

            selected = getattr(owner, "_v190_selected", "demo")
            engine = (
                getattr(owner, "_v190_engines", {}) or {}
            ).get(selected)
            if (
                engine is not None
                and time.monotonic() - owner.public_at > 5
            ):
                owner.emit("ticker", engine.x.ticker())
                owner.public_at = time.monotonic()

        except Exception as exc:
            env = (
                data[0]
                if isinstance(data, tuple)
                and data
                and data[0] in ENV_META
                else getattr(owner, "_v190_selected", "demo")
            )
            _emit_for_env(owner, env, "alarm", str(exc))
        finally:
            if kind != "v190_tick":
                owner.emit("done", None)


def _toggle_selected(owner):
    env = getattr(owner, "_v190_selected", "demo")
    engine = owner._v190_engines.get(env)
    if engine is None:
        app.messagebox.showerror("未连接", f"先连接 {_env_label(env)}")
        return

    try:
        if env == "demo":
            if engine.enabled:
                try:
                    engine.set_auto_execute_plans(False)
                finally:
                    engine.stop()
            else:
                typed = app.simpledialog.askstring(
                    "启用DEMO AI自动执行",
                    "OKX模拟盘：AI完整方案会立即提交LIMIT挂单。\n"
                    "入场为LIMIT；TP/SL触发后按市价执行。\n\n"
                    "确认请输入 DEMO AUTO",
                    parent=owner.root,
                )
                if not (typed and typed.strip().upper() == "DEMO AUTO"):
                    return
                engine.arm(None)
                engine.set_auto_execute_plans(True)
        else:
            if engine.enabled:
                engine.set_review_enabled(False)
                _emit_for_env(owner, env, "log", "LIVE AI自动执行已由用户关闭")
            else:
                typed = app.simpledialog.askstring(
                    "启用LIVE AI自动执行",
                    "OKX实盘：开启后完整AI方案可自动提交真实账户。\n"
                    "入场/减仓仅限LIMIT；TP/SL触发后按市价执行。\n"
                    "请确认API仅有读取+交易权限且无提币权限。\n\n"
                    "确认请输入 LIVE AUTO",
                    parent=owner.root,
                )
                if not (typed and typed.strip().upper() == "LIVE AUTO"):
                    return

                # Read-only preflight first. If account mode/permissions, an old
                # halt, foreign BTC positions/orders, or a REST read blocks LIVE,
                # show the exact reason instead of failing silently inside Tk.
                engine.activation_check()
                engine.set_review_enabled(True)

                state = engine._review_state()
                write_gate = bool(
                    getattr(engine.x, "writes_enabled", False)
                )
                if not (
                    engine.enabled
                    and bool(state.get("channel_enabled"))
                    and write_gate
                ):
                    raise legacy_engine.Halt(
                        "LIVE开关状态校验失败：Engine / State / Write Gate 未同时开启"
                    )
                _emit_for_env(
                    owner,
                    env,
                    "log",
                    "LIVE AI自动执行开启成功：Engine=ON / State=ON / Write Gate=ON",
                )

    except Exception as exc:
        # Fail closed. A rejected activation must never leave the live write
        # gate half-open, and packaged macOS builds must surface the blocker.
        if env == "live":
            try:
                engine.enabled = False
                engine.stopped = True
                if hasattr(engine.x, "set_writes_enabled"):
                    engine.x.set_writes_enabled(False)
                state = engine._review_state() if engine.store else None
                if isinstance(state, dict):
                    state["channel_enabled"] = False
                    state["auto_execute_plans"] = False
                    state["updated_at"] = time.time()
                    engine.store.save()
            except Exception:
                try:
                    if hasattr(engine.x, "set_writes_enabled"):
                        engine.x.set_writes_enabled(False)
                except Exception:
                    pass
            owner._v191_live_activation_error = str(exc)
            _emit_for_env(owner, env, "alarm", f"LIVE开启失败：{exc}")
            app.messagebox.showerror(
                "LIVE AI自动执行开启失败",
                "未开启实盘写入。\n\n"
                f"原因：{exc}\n\n"
                "请按提示处理后再开启；当前 LIVE Write Gate 保持关闭。",
                parent=owner.root,
            )
        else:
            _emit_for_env(owner, env, "alarm", f"DEMO开启失败：{exc}")
            app.messagebox.showerror(
                "DEMO AI自动执行开启失败",
                str(exc),
                parent=owner.root,
            )
    finally:
        owner.update_trade_button()
        _update_environment_card(owner)
        _refresh_v190_dashboard(owner)


def _update_trade_button(owner):
    env = getattr(owner, "_v190_selected", "demo")
    engine = (getattr(owner, "_v190_engines", {}) or {}).get(env)
    running = bool(engine and engine.enabled)
    if getattr(owner, "trade_button", None):
        if env == "demo":
            text = (
                "■  关闭 DEMO AI自动执行"
                if running
                else "▶  开启 DEMO AI自动执行"
            )
        else:
            text = (
                "■  关闭 LIVE AI执行通道"
                if running
                else "▶  开启 LIVE AI执行通道"
            )
        owner.trade_button.configure(
            text=text,
            variant="danger" if running else "accent",
        )


def _install_environment_card(owner):
    dash = owner.book.pages[3].body
    card = visual.Card(dash, height=118)
    auto = getattr(owner, "_v183_auto_exec_card", None)
    kwargs = dict(fill="x", pady=(0, 12))
    if auto is not None:
        kwargs["before"] = auto
    card.pack(**kwargs)

    row = app.tk.Frame(card.body, bg=visual.PANEL)
    row.pack(fill="both", expand=True)
    left = app.tk.Frame(row, bg=visual.PANEL)
    left.pack(side="left", fill="both", expand=True)
    visual.label(
        left,
        text="交易环境",
        size=16,
        bold=True,
        color=visual.TEXT,
        bg=visual.PANEL,
    ).pack(anchor="w")
    owner._v190_env_summary = app.tk.StringVar(
        value="DEMO 未连接   |   LIVE 未连接"
    )
    visual.label(
        left,
        variable=owner._v190_env_summary,
        size=10,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(5, 0))

    buttons = app.tk.Frame(row, bg=visual.PANEL)
    buttons.pack(side="right")
    owner._v190_demo_button = visual.RoundedButton(
        buttons,
        text="OKX 模拟盘",
        command=lambda: _select_environment(owner, "demo"),
        variant="accent",
        width=150,
        height=42,
    )
    owner._v190_demo_button.pack(side="left", padx=(0, 8))
    owner._v190_live_button = visual.RoundedButton(
        buttons,
        text="OKX 实盘",
        command=lambda: _select_environment(owner, "live"),
        variant="neutral",
        width=150,
        height=42,
    )
    owner._v190_live_button.pack(side="left")
    owner._v190_environment_card = card


def _update_environment_card(owner):
    engines = getattr(owner, "_v190_engines", {}) or {}
    parts = []
    for env in ("demo", "live"):
        engine = engines.get(env)
        if engine is None:
            state = "未连接"
        elif engine.enabled:
            state = "AI ON" if env == "demo" else "AUTO ON"
        else:
            state = "AI OFF" if env == "demo" else "AUTO OFF"
        parts.append(f"{ENV_META[env]['short']} {state}")
    if getattr(owner, "_v190_env_summary", None) is not None:
        owner._v190_env_summary.set("   |   ".join(parts))

    selected = getattr(owner, "_v190_selected", "demo")
    if getattr(owner, "_v190_demo_button", None) is not None:
        owner._v190_demo_button.configure(
            variant="accent" if selected == "demo" else "neutral"
        )
    if getattr(owner, "_v190_live_button", None) is not None:
        owner._v190_live_button.configure(
            variant="danger" if selected == "live" else "neutral"
        )


def _toggle_auto_card(owner):
    _toggle_selected(owner)


def _refresh_v190_dashboard(owner):
    try:
        env = getattr(owner, "_v190_selected", "demo")
        engine = (getattr(owner, "_v190_engines", {}) or {}).get(env)
        direction = ""
        latest = {}
        tiers = {"1": None, "2": None}

        if env == "demo" and engine and engine.store:
            data = engine.store.data
            state = engine._demo_state()
            tiers = state.get("tiers") or tiers
            history = data.get("ai_plan_history") or []
            latest = history[-1] if history else {}
            channel_on = bool(
                engine.enabled and state.get("auto_execute_plans")
            )
            auto_status = (
                "已开启 · AI策略到达即提交OKX模拟盘"
                if channel_on
                else "已关闭 · AI推荐仅展示"
            )
            auto_note = (
                "DEMO：完整策略立即提交LIMIT挂单；TP/SL触发后市价保护。"
            )
        elif env == "live" and engine and engine.store:
            state = engine._review_state()
            tiers = state.get("tiers") or tiers
            history = state.get("history") or []
            latest = history[-1] if history else {}
            channel_on = bool(
                engine.enabled and state.get("channel_enabled")
            )
            auto_status = (
                "已开启 · AI方案自动提交OKX实盘"
                if channel_on
                else "已关闭 · 禁止LIVE实盘写入"
            )
            last_error = str(getattr(owner, "_v191_live_activation_error", "") or "")
            auto_note = (
                "LIVE：开关开启后AI方案自动提交实盘LIMIT入场；TP/SL触发后市价保护。"
                if not last_error or channel_on
                else f"LIVE开启失败：{last_error}"
            )
        else:
            channel_on = False
            auto_status = (
                f"等待连接 · {_env_label(env)}"
            )
            auto_note = (
                "DEMO与LIVE均支持独立自动执行；LIVE实盘默认写入锁关闭，TP/SL触发后市价保护。"
            )

        if hasattr(owner, "_v183_auto_exec_status_var"):
            owner._v183_auto_exec_status_var.set(auto_status)
            owner._v183_auto_exec_note_var.set(auto_note)
            owner._v183_auto_exec_button.configure(
                text=(
                    "关闭自动执行"
                    if channel_on and env == "demo"
                    else "关闭LIVE通道"
                    if channel_on
                    else "开启自动执行"
                    if env == "demo"
                    else "开启LIVE通道"
                ),
                variant="danger" if channel_on else "accent",
            )

        if hasattr(owner, "_v180_mode_var"):
            owner._v180_mode_var.set(
                "DEMO · AUTO ON"
                if env == "demo" and channel_on
                else "DEMO · AUTO OFF"
                if env == "demo"
                else "LIVE · AUTO ON"
                if channel_on
                else "LIVE · AUTO OFF"
            )

        if latest:
            direction = str(latest.get("direction") or "").lower()
            owner._v180_side_var.set(v180._side_cn(direction))
            owner._v180_type_var.set(
                "委托：DEMO限价"
                if env == "demo"
                else "方案：LIVE限价"
            )
            owner._v180_tier_var.set(
                f"档位：{latest.get('tier') or 1}"
            )
            lev = latest.get("leverage")
            owner._v180_leverage_var.set(
                f"杠杆：{lev}×" if lev else "杠杆：—"
            )
            entry = (
                latest.get("limit_price")
                or latest.get("suggested_entry")
            )
            owner._v180_entry_var.set(v180._fmt_px(entry))
            owner._v180_tp_var.set(
                v180._fmt_px(latest.get("take_profit"))
            )
            owner._v180_sl_var.set(
                v180._fmt_px(latest.get("stop_loss"))
            )
            owner._v180_reason_var.set(
                str(latest.get("reason") or "—")
            )
            owner._v180_advice_var.set(
                str(
                    latest.get("operation_advice")
                    or (
                        "请在OKX实盘人工执行"
                        if env == "live"
                        else "等待AI建议"
                    )
                )
            )
            owner._v180_status_chip_var.set(
                str(latest.get("status") or "RECOMMENDED")
            )
        else:
            owner._v180_side_var.set("等待 AI 方向")
            owner._v180_entry_var.set("—")
            owner._v180_status_chip_var.set("WAITING")

        accent = (
            visual.GREEN
            if direction == "long"
            else visual.RED
            if direction == "short"
            else visual.TEXT
        )
        for name in ("_v184_direction_label", "_v184_entry_value_label"):
            label = getattr(owner, name, None)
            if label is not None:
                label.configure(fg=accent)

        rows = []
        history = (
            engine.store.data.get("ai_plan_history") or []
            if env == "demo" and engine and engine.store
            else engine._review_state().get("history") or []
            if env == "live" and engine and engine.store
            else []
        )
        for item in history[-5:][::-1]:
            stamp = time.strftime(
                "%H:%M:%S",
                time.localtime(float(item.get("time") or time.time())),
            )
            side = str(item.get("direction") or "WAIT").upper()
            entry = (
                item.get("limit_price")
                or item.get("suggested_entry")
            )
            rows.append(
                f"{stamp}  {ENV_META[env]['short']}  "
                f"T{item.get('tier') or 1}  {side:<5} LMT  "
                f"{v180._fmt_px(entry):>10}  "
                f"{str(item.get('status') or '')[:24]}"
            )
        owner._v180_history_var.set(
            "\n".join(rows)
            if rows
            else f"暂无 {_env_label(env)} AI 方案记录"
        )

        if env == "demo":
            owner._v180_tier1_var.set(v183._tier_ui_text(tiers.get("1")))
            owner._v180_tier2_var.set(v183._tier_ui_text(tiers.get("2")))
            active = [
                x
                for x in (tiers.get("1"), tiers.get("2"))
                if v183._active(x)
            ]
            if active:
                item = active[0]
                owner._v180_order_state_var.set(
                    str(
                        item.get("order_state")
                        or item.get("status")
                        or "—"
                    ).upper()
                )
                owner._v180_order_type_var.set("DEMO限价")
                owner._v180_order_px_var.set(
                    v180._fmt_px(item.get("limit_price"))
                )
                owner._v180_fill_px_var.set(
                    v180._fmt_px(item.get("entry_price"))
                )
                owner._v180_size_var.set(
                    f"{sum(v183._position_size(x) for x in active):g} 张"
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
                owner._v180_position_badge_var.set(
                    "DEMO 持仓/委托"
                )
            else:
                owner._v180_order_state_var.set("等待DEMO委托")
                owner._v180_order_type_var.set("DEMO限价")
                owner._v180_order_px_var.set("—")
                owner._v180_fill_px_var.set("—")
                owner._v180_size_var.set("—")
                owner._v180_position_tp_var.set("—")
                owner._v180_position_sl_var.set("—")
                owner._v180_proposal_var.set("—")
                owner._v180_position_badge_var.set("DEMO 空仓")
        else:
            def live_tier(item):
                if not isinstance(item, dict):
                    return "等待 AI 方案"
                return (
                    f"LIVE实盘 · {item.get('status','—')}\n"
                    f"方向：{str(item.get('direction') or '').upper()}   "
                    f"入场：{v180._fmt_px(item.get('limit_price'))}   "
                    f"数量：{float(item.get('size') or 0):g}张\n"
                    f"TP：{v180._fmt_px(item.get('take_profit'))}   "
                    f"SL：{v180._fmt_px(item.get('stop_loss'))}"
                )
            owner._v180_tier1_var.set(live_tier(tiers.get("1")))
            owner._v180_tier2_var.set(live_tier(tiers.get("2")))
            ready = [
                x
                for x in (tiers.get("1"), tiers.get("2"))
                if isinstance(x, dict)
            ]
            if ready:
                item = ready[0]
                owner._v180_order_state_var.set(
                    "待人工执行"
                    if item.get("status") == "READY_FOR_MANUAL_EXECUTION"
                    else "方案展示"
                )
                owner._v180_order_type_var.set("LIVE限价入场")
                owner._v180_order_px_var.set(
                    v180._fmt_px(item.get("limit_price"))
                )
                owner._v180_fill_px_var.set("—")
                owner._v180_size_var.set(
                    f"{sum(float(x.get('size') or 0) for x in ready):g} 张"
                )
                owner._v180_position_tp_var.set(
                    v180._fmt_px(item.get("take_profit"))
                )
                owner._v180_position_sl_var.set(
                    v180._fmt_px(item.get("stop_loss"))
                )
                owner._v180_proposal_var.set(
                    str(item.get("plan_id") or "—")[:22]
                )
                owner._v180_position_badge_var.set(
                    "LIVE 自动执行"
                )
            else:
                owner._v180_order_state_var.set("等待LIVE方案")
                owner._v180_order_type_var.set("LIVE限价入场")
                owner._v180_order_px_var.set("—")
                owner._v180_fill_px_var.set("—")
                owner._v180_size_var.set("—")
                owner._v180_position_tp_var.set("—")
                owner._v180_position_sl_var.set("—")
                owner._v180_proposal_var.set("—")
                owner._v180_position_badge_var.set(
                    "LIVE 实盘"
                )

        _update_environment_card(owner)
        owner.update_trade_button()

    except Exception:
        pass

    try:
        if owner.root.winfo_exists():
            owner.root.after(
                650, lambda: _refresh_v190_dashboard(owner)
            )
    except Exception:
        pass


def _app_init_v190(owner, *args, **kwargs):
    _PREVIOUS_APP_INIT(owner, *args, **kwargs)

    owner._v190_profiles = {
        "demo": {
            "host": owner.host.get(),
            "key": owner.key.get().strip(),
            "secret": owner.secret.get().strip(),
            "phrase": owner.phrase.get(),
        },
        "live": {
            "host": owner.host.get(),
            "key": "",
            "secret": "",
            "phrase": "",
        },
    }
    owner._v190_engines = {"demo": None, "live": None}
    owner._v190_selected = _env_from_mode(owner.mode.get())
    owner._v190_mode_guard = False

    try:
        owner.ai_bridge.stop()
    except Exception:
        pass

    owner._v190_bridges = {
        "demo": DualBridgeServer(owner, "demo"),
        "live": DualBridgeServer(owner, "live"),
    }
    owner._v190_bridges["demo"].start()
    owner._v190_bridges["live"].start()

    owner.mode.trace_add("write", lambda *_: _mode_changed(owner))
    _install_environment_card(owner)

    try:
        auto = getattr(owner, "_v183_auto_exec_card", None)
        if auto is not None:
            auto.pack_forget()
            auto.pack(
                fill="x",
                pady=(0, 12),
                after=owner._v190_environment_card,
            )
    except Exception:
        pass
    try:
        owner.quote.pack_forget()
        owner.quote.pack(
            fill="x",
            pady=(0, 10),
            before=owner._v190_environment_card,
        )
    except Exception:
        pass

    owner.root.title(
        "KAYTRADE 1.9.1 · LIVE EXECUTION · Build 1911"
    )
    owner.signal.set(
        "V1.9.1 Build1911｜DEMO/LIVE独立自动执行｜LIMIT入场 + 触发市价TP/SL"
    )
    owner._v190_dual_ready = True
    _select_environment(owner, owner._v190_selected)


def _quit_v190(owner):
    for engine in (
        getattr(owner, "_v190_engines", {}) or {}
    ).values():
        if engine is not None:
            engine.enabled = False
    for bridge in (
        getattr(owner, "_v190_bridges", {}) or {}
    ).values():
        try:
            bridge.stop()
        except Exception:
            pass
    _PREVIOUS_APP_QUIT(owner)


def apply():
    if getattr(model, "_kaytrade_v190_applied", False):
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
        v183,
        v184,
    ):
        module.VERSION = VERSION
        module.BUILD = BUILD

    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = (
        "KAYTRADE 1.9.1 · LIVE EXECUTION · Build 1911"
    )

    v172.AIBridgeServer = _DisabledLegacyBridge
    app.Engine = DemoEngineV190
    legacy_engine.AIOnlyEngine = DemoEngineV190

    app.App.worker = _worker_v190
    app.App.connect = _connect_current
    app.App.diagnose = _diagnose_current
    app.App.toggle_auto = _toggle_selected
    app.App.arm = _toggle_selected
    app.App.stop = _toggle_selected
    app.App.update_trade_button = _update_trade_button
    app.App.__init__ = _app_init_v190
    app.App.quit = _quit_v190

    v183._toggle_auto_execute_v183 = _toggle_auto_card
    v180._refresh_v180_dashboard = _refresh_v190_dashboard
    v183._refresh_v183_dashboard = _refresh_v190_dashboard
    v184._refresh_v184 = _refresh_v190_dashboard

    model._kaytrade_v190_applied = True
    model._kaytrade_v191_applied = True
    app.App._kaytrade_v190_applied = True
    app.App._kaytrade_v191_applied = True


apply()

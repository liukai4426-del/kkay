"""KAYTRADE V1.8.5 Dual Environment overlay.

Two isolated OKX execution environments:
- demo: OKX simulated trading
- live: OKX real trading

Isolation boundaries:
- separate in-memory credentials;
- separate Engine instances;
- separate state folders;
- separate AI Bridge ports/tokens/descriptors;
- every bridge write requires an explicit matching environment;
- engine environment must match Exchange.demo before every write path.

No local Paper runtime is reintroduced.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import threading
import time
from pathlib import Path
from decimal import Decimal

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
from exchange import Exchange, APIError

VERSION = "1.8.5"
BUILD = "1850"
LIMIT_ONLY = True
PAPER_RUNTIME_PRESENT = False
DEMO_BRIDGE_PORT = int(os.getenv("KAYTRADE_DEMO_BRIDGE_PORT", "17872"))
LIVE_BRIDGE_PORT = int(os.getenv("KAYTRADE_LIVE_BRIDGE_PORT", "17873"))
BRIDGE_HOST = "127.0.0.1"

ENV_META = {
    "demo": {
        "label": "OKX模拟盘",
        "short": "DEMO",
        "mode": "OKX_DEMO_EXECUTION",
        "state_key": "okx_demo_execution",
        "folder": "demo",
        "descriptor": "ai_bridge_demo.json",
        "port": DEMO_BRIDGE_PORT,
    },
    "live": {
        "label": "OKX实盘",
        "short": "LIVE",
        "mode": "OKX_LIVE_EXECUTION",
        "state_key": "okx_live_execution",
        "folder": "live",
        "descriptor": "ai_bridge_live.json",
        "port": LIVE_BRIDGE_PORT,
    },
}

_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_QUIT = app.App.quit
_PREVIOUS_EMIT = app.App.emit


def _env_from_mode(value):
    return "demo" if str(value) == "OKX模拟盘" else "live"


def _env_label(environment):
    return ENV_META[environment]["label"]


def _result_env(result, environment):
    if isinstance(result, dict):
        out = dict(result)
        out["environment"] = environment
        out["mode"] = ENV_META[environment]["mode"]
        out["version"] = VERSION
        out["build"] = BUILD
        return out
    return result


class OKXDualEngineV185(v183.OKXDemoEngineV183):
    """One isolated execution engine bound permanently to demo or live."""

    def __init__(self, exchange, folder, emit, environment):
        if environment not in ENV_META:
            raise ValueError("environment must be demo or live")
        expected_demo = environment == "demo"
        if bool(exchange.demo) != expected_demo:
            raise legacy_engine.Halt("Exchange环境与Engine环境不一致")
        self.environment = environment
        self.environment_label = _env_label(environment)
        self.environment_mode = ENV_META[environment]["mode"]
        self._state_key = ENV_META[environment]["state_key"]
        super().__init__(exchange, folder, emit)

    def connect(self):
        # Bypass the V1.8.4 demo-only connect wrapper; keep AI-only/base account init.
        v180.AIOnlyEngineV180.connect(self)
        self._purge_removed_paper_state()
        self._ensure_demo_state()
        self.emit(
            "log",
            f"V1.8.5 {self.environment_label} Runtime 已连接；状态目录与另一环境完全隔离。",
        )

    def _ensure_demo_state(self):
        if not self.store:
            return None
        state = self.store.data.setdefault(self._state_key, {})
        state.setdefault("environment", self.environment)
        state.setdefault("tiers", {"1": None, "2": None})
        state.setdefault("close_orders", [])
        state.setdefault("events", [])
        state.setdefault("results", {})
        state.setdefault("auto_execute_plans", False)
        state.setdefault("updated_at", time.time())
        if set(state["tiers"].keys()) != {"1", "2"}:
            state["tiers"] = {
                "1": state["tiers"].get("1"),
                "2": state["tiers"].get("2"),
            }
        # Remove a wrong-environment state key if an old manual file was copied.
        other_key = (
            ENV_META["live"]["state_key"]
            if self.environment == "demo"
            else ENV_META["demo"]["state_key"]
        )
        self.store.data.pop(other_key, None)
        self.store.save()
        return state

    def _demo_state(self):
        if not self.store:
            raise legacy_engine.Halt("KAYTRADE尚未连接")
        return self._ensure_demo_state()

    def _event(self, name, payload):
        state = self._demo_state()
        row = {"time": time.time(), "event": name, "environment": self.environment, **dict(payload)}
        state["events"].append(row)
        del state["events"][:-120]
        state["updated_at"] = time.time()
        self.store.save()
        self.emit(
            "log",
            f"{self.environment_label} · {name} · {payload.get('detail') or payload.get('tier') or ''}",
        )
        return row

    def _assert_environment(self, payload):
        requested = str((payload or {}).get("environment") or "").strip().lower()
        if requested not in ENV_META:
            raise legacy_engine.Halt("交易请求必须显式包含 environment=demo 或 live")
        if requested != self.environment:
            raise legacy_engine.Halt(
                f"Environment Match Gate：请求={requested}，Engine={self.environment}，已拒绝"
            )
        if bool(self.x.demo) != (self.environment == "demo"):
            raise legacy_engine.Halt("Environment Match Gate：Exchange环境与Engine环境不一致")
        return requested

    def _preflight_account(self):
        if not self.store:
            raise legacy_engine.Halt("先测试连接")
        if bool(self.x.demo) != (self.environment == "demo"):
            raise legacy_engine.Halt("Environment Match Gate：Exchange环境与Engine环境不一致")
        if self.store.data.get("halt"):
            reason = legacy_engine.normalize_halt_reason(self.store.data.get("halt"))
            raise legacy_engine.Halt(
                legacy_engine.HALT_PREFIX + reason + legacy_engine.HALT_SUFFIX
            )
        account = self.x.account()
        if account.get("uid") != self.connection_id:
            raise legacy_engine.Halt("账户标识发生变化")
        if account.get("posMode") != "long_short_mode":
            raise legacy_engine.Halt("V1.8.5 要求 OKX 双向持仓模式")
        perms = set(str(account.get("perm") or "").split(","))
        if "trade" not in perms or "withdraw" in perms:
            raise legacy_engine.Halt("API必须有交易权限且不得有提币权限")
        return account

    def _submit_open(self, proposal, proposal_id):
        result = super()._submit_open(proposal, proposal_id)
        tier = str(int(proposal.get("tier") or 1))
        item = self._demo_state()["tiers"].get(tier)
        if isinstance(item, dict):
            item["environment"] = self.environment
            item["okx_demo"] = self.environment == "demo"
            self.store.save()
        return _result_env(result, self.environment)

    def publish_ai_plan(self, plan):
        self._assert_environment(plan)
        return _result_env(super().publish_ai_plan(plan), self.environment)

    def submit_ai_trade(self, proposal):
        self._assert_environment(proposal)
        return _result_env(super().submit_ai_trade(proposal), self.environment)

    def set_auto_execute_plans(self, enabled):
        result = super().set_auto_execute_plans(enabled)
        return _result_env(result, self.environment)

    def cancel_demo_entry(self, payload):
        self._assert_environment(payload)
        return _result_env(super().cancel_demo_entry(payload), self.environment)

    def amend_demo_entry(self, payload):
        self._assert_environment(payload)
        return _result_env(super().amend_demo_entry(payload), self.environment)

    def amend_demo_protection(self, payload):
        self._assert_environment(payload)
        return _result_env(super().amend_demo_protection(payload), self.environment)

    def close_demo_position(self, payload):
        self._assert_environment(payload)
        return _result_env(super().close_demo_position(payload), self.environment)

    def arm(self, _settings=None):
        with self._ai_lock:
            self._preflight_account()
            self._ensure_demo_state()
            self._sync_all(cancel_expired=False)
            self._foreign_state_check()
            self.enabled = True
            self.stopped = False
            self.poll_at = time.monotonic()
            self.emit(
                "log",
                f"{self.environment_label} AI自动执行已启用；完整AI方案会立即提交交易所LIMIT挂单。",
            )

    def stop(self):
        with self._ai_lock:
            self.enabled = False
            self.stopped = True
            self.emit(
                "log",
                f"{self.environment_label} AI执行已停止；既有交易所订单不会迁移到另一环境。",
            )

    def ai_state(self):
        state = super().ai_state()
        env_state = self._demo_state() if self.store else {}
        state.update(
            {
                "version": VERSION,
                "build": BUILD,
                "mode": self.environment_mode,
                "environment": self.environment,
                "environment_label": self.environment_label,
                "demo_exchange_writes": self.environment == "demo",
                "live_ai_writes": self.environment == "live",
                "paper_runtime_present": False,
                "limit_only": True,
                "auto_execute_plans": bool(env_state.get("auto_execute_plans", False)),
                "execution_state": env_state,
            }
        )
        state.pop("okx_demo_execution", None)
        return state


class _DisabledLegacyBridge:
    """Prevents the old single-environment bridge from being started by V1.7.2 init."""

    def __init__(self, owner):
        self.owner = owner

    def start(self):
        return None

    def stop(self):
        return None


class _DualBridgeHandler(v172.BaseHTTPRequestHandler):
    server_version = "KayTradeDual/1.8.5"

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

    def _engine(self):
        engine = (getattr(self.bridge.owner, "_v185_engines", {}) or {}).get(
            self.bridge.environment
        )
        if not isinstance(engine, OKXDualEngineV185):
            raise legacy_engine.Halt(
                f"{_env_label(self.bridge.environment)}尚未连接"
            )
        if engine.environment != self.bridge.environment:
            raise legacy_engine.Halt("Environment Match Gate：Bridge与Engine不一致")
        return engine

    def do_GET(self):
        if not self._require_auth():
            return
        if self.path == "/health":
            engine = (getattr(self.bridge.owner, "_v185_engines", {}) or {}).get(
                self.bridge.environment
            )
            self._json(
                200,
                {
                    "name": "KAYTRADE",
                    "version": VERSION,
                    "build": BUILD,
                    "mode": ENV_META[self.bridge.environment]["mode"],
                    "environment": self.bridge.environment,
                    "connected": isinstance(engine, OKXDualEngineV185),
                    "ai_enabled": bool(engine and engine.enabled),
                    "demo_exchange_writes": self.bridge.environment == "demo",
                    "live_ai_writes": self.bridge.environment == "live",
                    "paper_runtime_present": False,
                    "limit_only": True,
                },
            )
            return
        if self.path == "/v1/state":
            try:
                self._json(200, self._engine().ai_state())
            except legacy_engine.Halt as exc:
                self._json(409, {"error": str(exc)})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        if not self._require_auth():
            return
        routes = {
            "/v1/trade": "submit_ai_trade",
            "/v1/plan": "publish_ai_plan",
            "/v1/cancel-entry": "cancel_demo_entry",
            "/v1/amend-entry": "amend_demo_entry",
            "/v1/amend-protection": "amend_demo_protection",
            "/v1/close": "close_demo_position",
        }
        if self.path not in routes:
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > 65536:
                raise ValueError("invalid content length")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            requested = str(payload.get("environment") or "").strip().lower()
            if requested != self.bridge.environment:
                raise legacy_engine.Halt(
                    f"Environment Match Gate：请求={requested or 'missing'}，Bridge={self.bridge.environment}"
                )
            engine = self._engine()
            result = getattr(engine, routes[self.path])(payload)
            self._json(200, result)
        except legacy_engine.Halt as exc:
            self._json(409, {"error": str(exc)})
        except APIError as exc:
            self._json(502, {"error": str(exc), "code": getattr(exc, "code", "")})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


class DualBridgeServer:
    def __init__(self, owner, environment):
        self.owner = owner
        self.environment = environment
        self.token = secrets.token_urlsafe(32)
        self.httpd = None
        self.thread = None
        self.path = Path(owner.folder) / ENV_META[environment]["descriptor"]

    def start(self):
        preferred = ENV_META[self.environment]["port"]
        try:
            httpd = v172.ThreadingHTTPServer(
                (BRIDGE_HOST, preferred), _DualBridgeHandler
            )
        except OSError:
            httpd = v172.ThreadingHTTPServer((BRIDGE_HOST, 0), _DualBridgeHandler)
        httpd.bridge = self
        self.httpd = httpd
        port = int(httpd.server_address[1])
        descriptor = {
            "version": VERSION,
            "build": BUILD,
            "mode": ENV_META[self.environment]["mode"],
            "environment": self.environment,
            "host": BRIDGE_HOST,
            "port": port,
            "token": self.token,
            "paper_runtime_present": False,
            "limit_only": True,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(descriptor, handle, ensure_ascii=False, indent=2)
        self.thread = threading.Thread(
            target=httpd.serve_forever,
            name=f"kaytrade-{self.environment}-bridge",
            daemon=True,
        )
        self.thread.start()
        self.owner.emit(
            "log",
            f"{_env_label(self.environment)} AI Bridge：127.0.0.1:{port} · 独立Token/端口",
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


def _profile(owner, environment):
    return owner._v185_profiles[environment]


def _save_visible_profile(owner, environment):
    if environment not in ENV_META:
        return
    owner._v185_profiles[environment] = {
        "host": owner.host.get(),
        "key": owner.key.get().strip(),
        "secret": owner.secret.get().strip(),
        "phrase": owner.phrase.get(),
    }


def _load_visible_profile(owner, environment):
    profile = _profile(owner, environment)
    owner.host.set(profile.get("host") or app.HOSTS[0])
    owner.key.set(profile.get("key") or "")
    owner.secret.set(profile.get("secret") or "")
    owner.phrase.set(profile.get("phrase") or "")


def _select_environment(owner, environment):
    if environment not in ENV_META:
        return
    previous = getattr(owner, "_v185_selected", "demo")
    if previous != environment:
        _save_visible_profile(owner, previous)
    owner._v185_selected = environment
    desired_mode = ENV_META[environment]["label"]
    if owner.mode.get() != desired_mode:
        owner._v185_switch_guard = True
        try:
            owner.mode.set(desired_mode)
        finally:
            owner._v185_switch_guard = False
    _load_visible_profile(owner, environment)
    owner.engine = owner._v185_engines.get(environment)
    _update_environment_card(owner)
    _refresh_v185_dashboard(owner)
    engine = owner.engine
    if engine is not None:
        try:
            owner.emit(
                "account",
                {
                    "environment": engine.environment_label,
                    "mode": engine.x.account().get("posMode"),
                    "equity": engine.x.balance()[0],
                    "positions": engine.x.positions(),
                    "orders": engine.x.orders(),
                },
            )
            owner.emit("ticker", engine.x.ticker())
        except Exception as exc:
            owner.emit("alarm", f"[{ENV_META[environment]['short']}] 切换后读取失败：{exc}")
    else:
        owner.environment.set(f"当前看板：{ENV_META[environment]['label']} · 未连接")
        owner.equity.set("USDT 权益：—")


def _mode_changed(owner, *_args):
    if getattr(owner, "_v185_switch_guard", False):
        return
    _select_environment(owner, _env_from_mode(owner.mode.get()))


def _env_emit(owner, environment, kind, data):
    selected = getattr(owner, "_v185_selected", "demo")
    if kind in ("log", "alarm"):
        owner.emit(kind, f"[{ENV_META[environment]['short']}] {data}")
        return
    if environment == selected:
        owner.emit(kind, data)


def _connect_current(owner):
    env = getattr(owner, "_v185_selected", _env_from_mode(owner.mode.get()))
    _save_visible_profile(owner, env)
    profile = dict(_profile(owner, env))
    owner.submit("v185_connect", (env, profile))


def _diagnose_current(owner):
    env = getattr(owner, "_v185_selected", _env_from_mode(owner.mode.get()))
    _save_visible_profile(owner, env)
    profile = dict(_profile(owner, env))
    owner.submit("v185_diagnose", (env, profile))


def _worker_v185(owner):
    while not owner.finished.is_set():
        try:
            kind, data = owner.tasks.get(timeout=2)
        except queue.Empty:
            kind, data = "v185_tick", None
        try:
            if kind == "v185_diagnose":
                env, profile = data
                x = Exchange(
                    profile["host"],
                    profile["key"],
                    profile["secret"],
                    profile["phrase"],
                    demo=(env == "demo"),
                )
                x.sync_time()
                _env_emit(owner, env, "log", "网络自检：公共时间接口通过")
                if all((profile["key"], profile["secret"], profile["phrase"])):
                    x.account()
                    _env_emit(owner, env, "log", "账户只读认证通过")
            elif kind == "v185_connect":
                env, profile = data
                x = Exchange(
                    profile["host"],
                    profile["key"],
                    profile["secret"],
                    profile["phrase"],
                    demo=(env == "demo"),
                )
                folder = Path(owner.folder) / "environments" / ENV_META[env]["folder"]
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                engine = OKXDualEngineV185(
                    x,
                    folder,
                    lambda k, d, e=env: _env_emit(owner, e, k, d),
                    env,
                )
                engine.connect()
                owner._v185_engines[env] = engine
                if owner._v185_selected == env:
                    owner.engine = engine
                try:
                    engine.refresh_market()
                except Exception:
                    pass
                _env_emit(owner, env, "log", "连接完成；该环境使用独立Engine/State/Bridge")
            # Cycle both environments independently, regardless of which dashboard is selected.
            for env, engine in list((getattr(owner, "_v185_engines", {}) or {}).items()):
                if engine is None:
                    continue
                try:
                    engine.cycle()
                except Exception as exc:
                    engine.enabled = False
                    _env_emit(owner, env, "alarm", f"执行循环已停止：{exc}")
            selected = getattr(owner, "_v185_selected", "demo")
            engine = (getattr(owner, "_v185_engines", {}) or {}).get(selected)
            if engine and time.monotonic() - owner.public_at > 5:
                owner.emit("ticker", engine.x.ticker())
                owner.public_at = time.monotonic()
        except Exception as exc:
            env = data[0] if isinstance(data, tuple) and data and data[0] in ENV_META else getattr(owner, "_v185_selected", "demo")
            _env_emit(owner, env, "alarm", str(exc))
        finally:
            if kind != "v185_tick":
                owner.emit("done", None)


def _enable_selected(owner):
    env = getattr(owner, "_v185_selected", "demo")
    engine = owner._v185_engines.get(env)
    if not isinstance(engine, OKXDualEngineV185):
        app.messagebox.showerror("未连接", f"先连接 {_env_label(env)}")
        return
    if bool(engine.x.demo) != (env == "demo"):
        app.messagebox.showerror("环境异常", "Exchange与当前环境不一致，已拒绝")
        return

    phrase = "DEMO AUTO" if env == "demo" else "LIVE AUTO"
    title = "启用模拟盘AI自动执行" if env == "demo" else "启用实盘AI自动执行"
    warning = (
        "AI完整策略到达后会立即向OKX模拟盘提交LIMIT挂单。"
        if env == "demo"
        else
        "这是OKX真实账户自动交易。启用后AI完整策略会立即提交真实LIMIT订单，不逐笔确认。"
    )
    typed = app.simpledialog.askstring(
        title,
        f"KAYTRADE V1.8.5 · {_env_label(env)}\n\n"
        f"{warning}\n"
        "入场/TP/SL/减仓均为LIMIT ONLY；TP/SL禁止-1市价哨兵。\n"
        "Demo与Live使用独立Engine、State、Bridge和Token，不会迁移订单/仓位。\n\n"
        f"确认启用请输入 {phrase}",
        parent=owner.root,
    )
    if typed and typed.strip().upper() == phrase:
        try:
            engine.arm(None)
            engine.set_auto_execute_plans(True)
            owner.update_trade_button()
            _update_environment_card(owner)
            _refresh_v185_dashboard(owner)
        except Exception as exc:
            engine.enabled = False
            app.messagebox.showerror("启用失败", str(exc))


def _stop_selected(owner):
    env = getattr(owner, "_v185_selected", "demo")
    engine = owner._v185_engines.get(env)
    if engine:
        engine.set_auto_execute_plans(False)
        engine.stop()
    owner.update_trade_button()
    _update_environment_card(owner)


def _toggle_selected(owner):
    env = getattr(owner, "_v185_selected", "demo")
    engine = owner._v185_engines.get(env)
    if engine and engine.enabled:
        _stop_selected(owner)
    else:
        _enable_selected(owner)


def _update_trade_button_v185(owner):
    env = getattr(owner, "_v185_selected", "demo")
    engine = (getattr(owner, "_v185_engines", {}) or {}).get(env)
    running = bool(engine and engine.enabled)
    if getattr(owner, "trade_button", None):
        owner.trade_button.configure(
            text=(
                f"■  停止 {ENV_META[env]['short']} AI自动执行"
                if running
                else f"▶  启用 {ENV_META[env]['short']} AI自动执行"
            ),
            variant="danger" if running else "accent",
        )


def _install_environment_card(owner):
    dash = owner.book.pages[3].body
    card = visual.Card(dash, height=118)
    before = getattr(owner, "_v183_auto_exec_card", None)
    if before is not None:
        card.pack(fill="x", pady=(0, 12), before=before)
    else:
        card.pack(fill="x", pady=(0, 12))

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
    owner._v185_env_summary = app.tk.StringVar(value="Demo 未连接 · Live 未连接")
    visual.label(
        left,
        variable=owner._v185_env_summary,
        size=10,
        color=visual.MUTED,
        bg=visual.PANEL,
    ).pack(anchor="w", pady=(5, 0))

    buttons = app.tk.Frame(row, bg=visual.PANEL)
    buttons.pack(side="right")
    owner._v185_demo_button = visual.RoundedButton(
        buttons,
        text="OKX 模拟盘",
        command=lambda: _select_environment(owner, "demo"),
        variant="accent",
        width=150,
        height=42,
    )
    owner._v185_demo_button.pack(side="left", padx=(0, 8))
    owner._v185_live_button = visual.RoundedButton(
        buttons,
        text="OKX 实盘",
        command=lambda: _select_environment(owner, "live"),
        variant="neutral",
        width=150,
        height=42,
    )
    owner._v185_live_button.pack(side="left")
    owner._v185_environment_card = card


def _update_environment_card(owner):
    engines = getattr(owner, "_v185_engines", {}) or {}
    parts = []
    for env in ("demo", "live"):
        e = engines.get(env)
        if e is None:
            state = "未连接"
        elif e.enabled:
            state = "已连接 · AI ON"
        else:
            state = "已连接 · AI OFF"
        parts.append(f"{ENV_META[env]['short']} {state}")
    if getattr(owner, "_v185_env_summary", None) is not None:
        owner._v185_env_summary.set("   |   ".join(parts))

    selected = getattr(owner, "_v185_selected", "demo")
    if getattr(owner, "_v185_demo_button", None) is not None:
        owner._v185_demo_button.configure(
            variant="accent" if selected == "demo" else "neutral"
        )
    if getattr(owner, "_v185_live_button", None) is not None:
        owner._v185_live_button.configure(
            variant="danger" if selected == "live" else "neutral"
        )


def _refresh_v185_dashboard(owner):
    try:
        env = getattr(owner, "_v185_selected", "demo")
        engine = (getattr(owner, "_v185_engines", {}) or {}).get(env)
        data = engine.store.data if engine and engine.store else {}
        state = engine._demo_state() if engine and engine.store else {}
        tiers = state.get("tiers") or {}
        history = data.get("ai_plan_history") or []
        latest = history[-1] if history else {}
        auto_on = bool(state.get("auto_execute_plans", False))

        if hasattr(owner, "_v183_auto_exec_status_var"):
            owner._v183_auto_exec_status_var.set(
                (
                    f"已开启 · AI策略到达即提交{_env_label(env)}"
                    if auto_on and engine and engine.enabled
                    else f"已关闭 · {_env_label(env)} AI推荐仅展示"
                )
            )
            owner._v183_auto_exec_note_var.set(
                f"当前环境：{_env_label(env)}；完整策略立即提交该环境LIMIT挂单。Demo/Live不会互相迁移。"
            )

        if hasattr(owner, "_v180_mode_var"):
            owner._v180_mode_var.set(
                f"{ENV_META[env]['short']} · {'AUTO ON' if auto_on and engine and engine.enabled else 'AUTO OFF'}"
            )

        direction = ""
        if latest:
            direction = str(latest.get("direction") or "").lower()
            owner._v180_side_var.set(v180._side_cn(direction))
            owner._v180_type_var.set(f"委托：{ENV_META[env]['short']}限价")
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

        accent = (
            visual.GREEN if direction == "long"
            else visual.RED if direction == "short"
            else visual.TEXT
        )
        for name in ("_v184_direction_label", "_v184_entry_value_label"):
            label = getattr(owner, name, None)
            if label is not None:
                label.configure(fg=accent)

        rows = []
        for item in history[-5:][::-1]:
            stamp = time.strftime(
                "%H:%M:%S",
                time.localtime(float(item.get("time") or time.time())),
            )
            side = str(item.get("direction") or "WAIT").upper()
            entry = item.get("limit_price") or item.get("suggested_entry")
            rows.append(
                f"{stamp}  {ENV_META[env]['short']}  T{item.get('tier') or 1}  "
                f"{side:<5} LMT  {v180._fmt_px(entry):>10}  "
                f"{str(item.get('status') or '')[:18]}"
            )
        owner._v180_history_var.set(
            "\n".join(rows) if rows else f"暂无 {_env_label(env)} AI 方案记录"
        )

        t1, t2 = tiers.get("1"), tiers.get("2")
        owner._v180_tier1_var.set(v183._tier_ui_text(t1))
        owner._v180_tier2_var.set(v183._tier_ui_text(t2))

        active = [x for x in (t1, t2) if v183._active(x)]
        if active:
            item = next(
                (x for x in active if float(x.get("filled_size") or 0) > 0),
                active[0],
            )
            owner._v180_order_state_var.set(
                str(item.get("order_state") or item.get("status") or "—").upper()
            )
            owner._v180_order_type_var.set(f"{ENV_META[env]['short']}限价")
            owner._v180_order_px_var.set(v180._fmt_px(item.get("limit_price")))
            owner._v180_fill_px_var.set(v180._fmt_px(item.get("entry_price")))
            owner._v180_size_var.set(
                f"{sum(v183._position_size(x) for x in active):g} 张"
            )
            owner._v180_position_tp_var.set(v180._fmt_px(item.get("take_profit")))
            owner._v180_position_sl_var.set(v180._fmt_px(item.get("stop_loss")))
            owner._v180_proposal_var.set(str(item.get("proposal_id") or "—")[:22])
            owner._v180_position_badge_var.set(
                f"{ENV_META[env]['short']} 持仓/委托"
            )
        else:
            owner._v180_order_state_var.set(f"等待{ENV_META[env]['short']}委托")
            owner._v180_order_type_var.set(f"{ENV_META[env]['short']}限价")
            owner._v180_order_px_var.set("—")
            owner._v180_fill_px_var.set("—")
            owner._v180_size_var.set("—")
            owner._v180_position_tp_var.set("—")
            owner._v180_position_sl_var.set("—")
            owner._v180_proposal_var.set("—")
            owner._v180_position_badge_var.set(f"{ENV_META[env]['short']} 空仓")

        _update_environment_card(owner)
        owner.update_trade_button()
    except Exception:
        pass

    try:
        if owner.root.winfo_exists():
            owner.root.after(650, lambda: _refresh_v185_dashboard(owner))
    except Exception:
        pass


def _app_init_v185(owner, *args, **kwargs):
    _PREVIOUS_APP_INIT(owner, *args, **kwargs)

    owner._v185_profiles = {
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
    owner._v185_engines = {"demo": None, "live": None}
    owner._v185_selected = _env_from_mode(owner.mode.get())
    owner._v185_switch_guard = False

    # The old single bridge created during inherited init is intentionally inert.
    try:
        owner.ai_bridge.stop()
    except Exception:
        pass
    owner._v185_bridges = {
        "demo": DualBridgeServer(owner, "demo"),
        "live": DualBridgeServer(owner, "live"),
    }
    owner._v185_bridges["demo"].start()
    owner._v185_bridges["live"].start()

    owner.mode.trace_add("write", lambda *_: _mode_changed(owner))
    _install_environment_card(owner)

    try:
        # Exact overview hierarchy.
        auto = getattr(owner, "_v183_auto_exec_card", None)
        env_card = owner._v185_environment_card
        if auto is not None:
            env_card.pack_forget()
            env_card.pack(fill="x", pady=(0, 12), before=auto)
        owner.quote.pack_forget()
        owner.quote.pack(fill="x", pady=(0, 10), before=env_card)
    except Exception:
        pass

    owner.root.title("KAYTRADE 1.8.5 · DUAL ENVIRONMENT · Build 1850")
    owner.signal.set(
        "V1.8.5 Build1850｜DEMO + LIVE 双环境隔离｜AI自动执行｜LIMIT ONLY｜无Paper Runtime"
    )
    owner._v185_dual_ready = True
    _select_environment(owner, owner._v185_selected)


def _quit_v185(owner):
    for engine in (getattr(owner, "_v185_engines", {}) or {}).values():
        if engine is not None:
            engine.enabled = False
    for bridge in (getattr(owner, "_v185_bridges", {}) or {}).values():
        try:
            bridge.stop()
        except Exception:
            pass
    _PREVIOUS_APP_QUIT(owner)


def apply():
    if getattr(model, "_kaytrade_v185_applied", False):
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
    ui166.WINDOW_TITLE = "KAYTRADE 1.8.5 · DUAL ENVIRONMENT · Build 1850"

    # Prevent inherited init from launching the old single-environment bridge.
    v172.AIBridgeServer = _DisabledLegacyBridge

    app.Engine = OKXDualEngineV185
    legacy_engine.AIOnlyEngine = OKXDualEngineV185

    app.App.worker = _worker_v185
    app.App.connect = _connect_current
    app.App.diagnose = _diagnose_current
    app.App.toggle_auto = _toggle_selected
    app.App.arm = _enable_selected
    app.App.stop = _stop_selected
    app.App.update_trade_button = _update_trade_button_v185
    app.App.__init__ = _app_init_v185
    app.App.quit = _quit_v185

    v180._refresh_v180_dashboard = _refresh_v185_dashboard
    v183._refresh_v183_dashboard = _refresh_v185_dashboard

    model._kaytrade_v185_applied = True
    app.App._kaytrade_v185_applied = True


apply()

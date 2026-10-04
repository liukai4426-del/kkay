"""KAYTRADE V1.9.0 single-environment OKX LIVE overlay.

This overlay is intentionally built on the known-good V1.8.4 Build1840
execution runtime. It changes only the account environment from OKX Demo to
OKX LIVE while preserving the existing execution contract:

- AI-only execution;
- LIMIT entry / TP / SL / reductions;
- Tier 1 + Tier 2 same-direction support;
- 60-minute incomplete-entry cancellation;
- no local Paper runtime.

LIVE safety gates retained:
- the connection itself performs no write;
- AI execution starts only after explicit user confirmation;
- API must include trade permission and must NOT include withdraw permission;
- OKX account must be long_short_mode;
- foreign BTC positions/orders/algos block arming.
"""
from __future__ import annotations

import json
import os
import threading
import time

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
from exchange import APIError

VERSION = "1.9.0"
BUILD = "1900"
LIVE_EXECUTION = True
PAPER_RUNTIME_PRESENT = False
LIMIT_ONLY = True

_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_EMIT = app.App.emit
_PREVIOUS_V184_REFRESH = v184._refresh_v184


def _live_result(value):
    if isinstance(value, dict):
        out = {key: _live_result(item) for key, item in value.items()}
        if out.get("mode") in ("OKX_DEMO_EXECUTION", "AI_ONLY"):
            out["mode"] = "OKX_LIVE_EXECUTION"
        if out.get("environment") in ("OKX_DEMO", "demo"):
            out["environment"] = "OKX_LIVE"
        if "demo_exchange_writes" in out:
            out["demo_exchange_writes"] = False
        if "live_ai_writes" in out:
            out["live_ai_writes"] = True
        return out
    if isinstance(value, list):
        return [_live_result(item) for item in value]
    return value


class OKXLiveEngineV190(v183.OKXDemoEngineV183):
    """Build1840 execution runtime bound to an OKX real account."""

    def connect(self):
        if getattr(self.x, "demo", True):
            raise legacy_engine.Halt("V1.9.0 仅允许 OKX 实盘连接")
        # V1.8.4 connection path is read-only; it does not submit an order.
        super().connect()
        self.emit(
            "log",
            "V1.9.0 Build1900：OKX实盘连接成功；连接阶段未执行任何账户写入。",
        )

    def _preflight_account(self):
        if not self.store:
            raise legacy_engine.Halt("先连接OKX实盘")
        if getattr(self.x, "demo", True):
            raise legacy_engine.Halt("V1.9.0 LIVE Runtime 拒绝 OKX 模拟盘连接")
        if self.store.data.get("halt"):
            reason = legacy_engine.normalize_halt_reason(self.store.data.get("halt"))
            raise legacy_engine.Halt(
                legacy_engine.HALT_PREFIX + reason + legacy_engine.HALT_SUFFIX
            )
        account = self.x.account()
        if account.get("uid") != self.connection_id:
            raise legacy_engine.Halt("实盘账户标识发生变化")
        if account.get("posMode") != "long_short_mode":
            raise legacy_engine.Halt("V1.9.0 实盘要求 OKX 双向持仓模式")
        perms = set(str(account.get("perm") or "").split(","))
        if "trade" not in perms:
            raise legacy_engine.Halt("实盘API必须开启交易权限")
        if "withdraw" in perms:
            raise legacy_engine.Halt("实盘API不得开启提币权限")
        return account

    def _foreign_state_check(self):
        try:
            return super()._foreign_state_check()
        except legacy_engine.Halt as exc:
            raise legacy_engine.Halt(
                str(exc).replace("OKX模拟盘", "OKX实盘").replace("模拟盘", "实盘")
            ) from None

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
                "V1.9.0 OKX实盘 AI执行已启用：完整AI方案到达后按V1.8.4规则提交实盘LIMIT订单。",
            )

    def stop(self):
        with self._ai_lock:
            self.enabled = False
            self.stopped = True
            self.emit(
                "log",
                "OKX实盘 AI执行通道已停止；不再接受新的AI开仓，已有交易所订单/保护继续核对。",
            )

    def ai_state(self):
        state = _live_result(super().ai_state())
        state.update(
            version=VERSION,
            build=BUILD,
            mode="OKX_LIVE_EXECUTION",
            environment="OKX_LIVE",
            demo_exchange_writes=False,
            live_ai_writes=True,
            paper_runtime_present=False,
            limit_only=True,
        )
        if "okx_demo_execution" in state:
            state["okx_live_execution"] = state.pop("okx_demo_execution")
        return state

    def publish_ai_plan(self, plan):
        return _live_result(super().publish_ai_plan(plan))

    def submit_ai_trade(self, proposal):
        return _live_result(super().submit_ai_trade(proposal))

    def set_auto_execute_plans(self, enabled):
        return _live_result(super().set_auto_execute_plans(enabled))

    def cancel_demo_entry(self, payload):
        return _live_result(super().cancel_demo_entry(payload))

    def amend_demo_entry(self, payload):
        return _live_result(super().amend_demo_entry(payload))

    def amend_demo_protection(self, payload):
        return _live_result(super().amend_demo_protection(payload))

    def close_demo_position(self, payload):
        return _live_result(super().close_demo_position(payload))


def _ensure_v190_runtime(owner):
    current = getattr(owner, "engine", None)
    if current is None:
        raise legacy_engine.Halt("先连接OKX实盘")
    if isinstance(current, OKXLiveEngineV190):
        return current
    x = getattr(current, "x", None)
    if x is None:
        raise legacy_engine.Halt("当前连接缺少Exchange对象，无法切换到OKX LIVE Runtime")
    if getattr(x, "demo", True):
        raise legacy_engine.Halt("当前连接是OKX模拟盘；V1.9.0要求重新连接实盘")
    try:
        current.enabled = False
    except Exception:
        pass
    replacement = OKXLiveEngineV190(x, owner.folder, owner.emit)
    replacement.connect()
    owner.engine = replacement
    owner.emit("log", "V1.9.0：当前连接已切换到 OKXLiveEngineV190")
    return replacement


def _rewrite_live_text(data):
    if not isinstance(data, str):
        return data
    text = str(data)
    replacements = (
        ("V1.8.4", VERSION),
        ("Build1840", f"Build{BUILD}"),
        ("OKX Demo", "OKX LIVE"),
        ("OKX DEMO", "OKX LIVE"),
        ("OKX模拟盘", "OKX实盘"),
        ("模拟盘", "实盘"),
        ("demo", "live"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _app_emit_v190(self, kind, data):
    return _PREVIOUS_APP_EMIT(self, kind, _rewrite_live_text(data))


def _update_trade_button_v190(self):
    button = getattr(self, "trade_button", None)
    if not button:
        return
    running = bool(self.engine and self.engine.enabled)
    button.configure(
        text="■  停止 OKX LIVE AI通道" if running else "▶  启用 OKX LIVE AI通道",
        variant="danger" if running else "accent",
    )


def _app_arm_v190(self):
    if not self.engine:
        app.messagebox.showerror("未连接", "先连接 OKX 实盘")
        return
    if getattr(self.engine.x, "demo", True):
        app.messagebox.showerror(
            "环境错误",
            "当前仍是OKX模拟盘连接；请重新连接真实账户。",
        )
        return
    typed = app.simpledialog.askstring(
        "启用 OKX LIVE AI执行",
        "KAYTRADE V1.9.0 Build1900\n\n"
        "当前环境：OKX真实账户。\n"
        "AI完整策略到达后会按V1.8.4原执行规则向真实账户提交LIMIT订单。\n"
        "TP和SL保持V1.8.4的LIMIT保护，不改交易逻辑。\n"
        "API必须有交易权限，并且不得有提币权限。\n"
        "存在非KAYTRADE管理的BTC仓位/普通挂单/策略挂单时将拒绝开启。\n\n"
        "确认启用实盘AI执行请输入 LIVE",
        parent=self.root,
    )
    if typed and typed.strip().upper() == "LIVE":
        v172._start_ai_enable(self)


def _app_init_v190(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    self.mode.set("真实账户")
    try:
        self.environment.set("账户：未连接 · OKX实盘")
    except Exception:
        pass
    self.root.title("KAYTRADE 1.9.0 · OKX LIVE · Build 1900")
    try:
        self.signal.set(
            "V1.9.0 Build1900｜OKX LIVE ONLY｜基于V1.8.4执行逻辑｜LIMIT ONLY"
        )
        self._v180_mode_var.set("OKX LIVE")
        self._v180_position_badge_var.set("OKX LIVE 空仓")
        self._v183_auto_exec_status_var.set("等待连接 · 实盘自动执行默认开启")
        self._v183_auto_exec_note_var.set(
            "完整AI策略到达后立即提交OKX实盘LIMIT挂单；不等待价格触达。"
        )
    except Exception:
        pass
    label = getattr(self, "_v170_version_label", None)
    if label is not None:
        try:
            label.configure(text="BTC / USDT   ·   V1.9.0 Build1900 · OKX LIVE")
        except Exception:
            pass
    self._v190_okx_live_ready = True
    self._v183_paper_runtime_present = False
    self.update_trade_button()


def _refresh_v190(owner):
    _PREVIOUS_V184_REFRESH(owner)
    try:
        engine = getattr(owner, "engine", None)
        state = engine._demo_state() if isinstance(engine, OKXLiveEngineV190) and engine.store else {}
        auto_on = bool(state.get("auto_execute_plans", True))
        owner._v180_mode_var.set(
            "OKX LIVE · AUTO ON" if auto_on else "OKX LIVE · MANUAL"
        )
        owner._v183_auto_exec_status_var.set(
            "已开启 · AI策略到达即提交OKX实盘"
            if auto_on else "已关闭 · AI推荐仅展示"
        )
        owner._v183_auto_exec_note_var.set(
            "完整策略立即发送OKX实盘LIMIT挂单；TP / SL沿用V1.8.4限价保护。"
            if auto_on else "自动执行关闭：AI方案只显示，不向OKX实盘发送新订单。"
        )
    except Exception:
        pass


def _bridge_get_v190(self):
    if not self._require_auth():
        return
    if self.path == "/health":
        self._json(
            200,
            {
                "name": "KAYTRADE",
                "version": VERSION,
                "build": BUILD,
                "mode": "OKX_LIVE_EXECUTION",
                "demo_exchange_writes": False,
                "live_ai_writes": True,
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
                "mode": "OKX_LIVE_EXECUTION",
                "connected": False,
                "ai_enabled": False,
                "demo_exchange_writes": False,
                "live_ai_writes": True,
                "paper_runtime_present": False,
                "limit_only": True,
            }
            self._json(200, payload)
        except Exception as exc:
            self._json(409, {"error": str(exc)})
        return
    self._json(404, {"error": "not_found"})


def _bridge_post_v190(self):
    if not self._require_auth():
        return
    allowed = {
        "/v1/trade",
        "/v1/plan",
        "/v1/live/cancel-entry",
        "/v1/live/amend-entry",
        "/v1/live/amend-protection",
        "/v1/live/close",
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
        if not isinstance(engine, OKXLiveEngineV190):
            raise legacy_engine.Halt("当前Runtime不是 V1.9.0 OKX LIVE Execution")
        routes = {
            "/v1/trade": engine.submit_ai_trade,
            "/v1/plan": engine.publish_ai_plan,
            "/v1/live/cancel-entry": engine.cancel_demo_entry,
            "/v1/live/amend-entry": engine.amend_demo_entry,
            "/v1/live/amend-protection": engine.amend_demo_protection,
            "/v1/live/close": engine.close_demo_position,
        }
        self._json(200, routes[self.path](payload))
    except legacy_engine.Halt as exc:
        self._json(409, {"error": str(exc)})
    except APIError as exc:
        self._json(502, {"error": str(exc), "code": getattr(exc, "code", "")})
    except Exception as exc:
        self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


def _bridge_start_v190(self):
    port = v172.AI_BRIDGE_PORT
    try:
        httpd = v172.ThreadingHTTPServer(
            (v172.AI_BRIDGE_HOST, port), v172._BridgeHandler
        )
    except OSError:
        httpd = v172.ThreadingHTTPServer(
            (v172.AI_BRIDGE_HOST, 0), v172._BridgeHandler
        )
    httpd.bridge = self
    self.httpd = httpd
    actual_port = int(httpd.server_address[1])
    descriptor = {
        "version": VERSION,
        "build": BUILD,
        "mode": "OKX_LIVE_EXECUTION",
        "host": v172.AI_BRIDGE_HOST,
        "port": actual_port,
        "token": self.token,
        "demo_exchange_writes": False,
        "live_ai_writes": True,
        "paper_runtime_present": False,
        "limit_only": True,
    }
    self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(
        self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
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
        f"AI Bridge已启动：127.0.0.1:{actual_port} · V1.9.0 OKX LIVE交易所执行",
    )


def apply():
    if getattr(model, "_kaytrade_v190_live_applied", False):
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
    ui166.WINDOW_TITLE = "KAYTRADE 1.9.0 · OKX LIVE · Build 1900"

    app.Engine = OKXLiveEngineV190
    legacy_engine.AIOnlyEngine = OKXLiveEngineV190
    v172.AIOnlyEngine = OKXLiveEngineV190
    v172._ensure_ai_only_engine = _ensure_v190_runtime

    app.App.__init__ = _app_init_v190
    app.App.emit = _app_emit_v190
    app.App.arm = _app_arm_v190
    app.App.update_trade_button = _update_trade_button_v190

    v172._BridgeHandler.do_GET = _bridge_get_v190
    v172._BridgeHandler.do_POST = _bridge_post_v190
    v172.AIBridgeServer.start = _bridge_start_v190

    v184._refresh_v184 = _refresh_v190
    v183._refresh_v183_dashboard = _refresh_v190
    v180._refresh_v180_dashboard = _refresh_v190

    model._kaytrade_v190_live_applied = True
    app.App._kaytrade_v190_live_applied = True


apply()

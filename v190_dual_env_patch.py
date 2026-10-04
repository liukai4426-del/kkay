"""KAYTRADE V1.9.0 dual OKX execution overlay.

Build1900 keeps the V1.8.4 LIMIT-only execution contract and adds a strictly
separated OKX Demo / OKX Live runtime. AI submits only trade plans; the desktop
application chooses the active environment and the program performs the write.

Safety / separation contract:
- Exchange environment is taken only from the connected Exchange object.
- AI payloads cannot select or override Demo/Live.
- Demo and Live use different Engine Store files (host + demo flag + uid).
- Demo auto-execution defaults ON after channel enable.
- Live auto-execution resets OFF on every Live connection.
- Live writes require an in-memory LIVE session authorization plus channel ON.
- LIMIT ONLY remains enforced by the V1.8.4 engine and exchange firewall.
"""
from __future__ import annotations

import json
import os
import threading
import time
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
from exchange import APIError

VERSION = "1.9.0"
BUILD = "1900"
LIMIT_ONLY = True

DEFAULT_RISK_LIMITS = {
    "max_leverage": "20",
    "max_notional_usdt": "3500",
    "max_estimated_stop_loss_usdt": "100",
}


def _env_code(engine):
    return "DEMO" if getattr(engine.x, "demo", False) else "LIVE"


def _env_mode(engine):
    return "OKX_DEMO_EXECUTION" if getattr(engine.x, "demo", False) else "OKX_LIVE_EXECUTION"


def _env_name(engine):
    return "OKX模拟盘" if getattr(engine.x, "demo", False) else "OKX实盘"


def _state_key(engine):
    return "okx_demo_execution" if getattr(engine.x, "demo", False) else "okx_live_execution"


def _rewrite_env_text(engine, value):
    if not isinstance(value, str):
        return value
    if getattr(engine.x, "demo", False):
        return value.replace("V1.8.4", VERSION).replace("Build1840", f"Build{BUILD}")
    return (
        value.replace("V1.8.4", VERSION)
        .replace("Build1840", f"Build{BUILD}")
        .replace("OKX模拟盘", "OKX实盘")
        .replace("OKX Demo", "OKX Live")
        .replace("OKX DEMO", "OKX LIVE")
        .replace("模拟盘", "实盘")
    )


def _rewrite_result(engine, value):
    if isinstance(value, list):
        return [_rewrite_result(engine, x) for x in value]
    if not isinstance(value, dict):
        return _rewrite_env_text(engine, value)
    out = {k: _rewrite_result(engine, v) for k, v in value.items()}
    out["version"] = VERSION
    out["build"] = BUILD
    if "mode" in out:
        out["mode"] = _env_mode(engine)
    if "environment" in out:
        out["environment"] = "OKX_DEMO" if engine.x.demo else "OKX_LIVE"
    return out


class OKXDualExecutionEngineV190(v183.OKXDemoEngineV183):
    """One execution engine class, with the environment fixed by its Exchange."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._live_session_authorized = False

    def connect(self):
        # Skip the V1.8.4 Demo-only connect message, but preserve its clean
        # exchange-backed initialization.
        v183.v180.AIOnlyEngineV180.connect(self)
        self._purge_removed_paper_state()
        state = self._ensure_demo_state()
        self._live_session_authorized = False
        if not self.x.demo:
            # Real-money execution must never survive a reconnect/restart.
            state["auto_execute_plans"] = False
            state["updated_at"] = time.time()
            self.store.save()
        self.emit(
            "log",
            f"[{_env_code(self)}] V1.9.0 Build1900 已连接：{_env_name(self)} · LIMIT ONLY · "
            + ("AI方案自动执行默认开启。" if self.x.demo else "真实资金自动执行默认关闭。"),
        )
        owner = getattr(self.emit, "__self__", None)
        registry = getattr(owner, "_v190_env_engines", None)
        if isinstance(registry, dict):
            registry[_env_code(self)] = self

    def _ensure_demo_state(self):
        if not self.store:
            return None
        key = _state_key(self)
        state = self.store.data.setdefault(key, {})
        state.setdefault("tiers", {"1": None, "2": None})
        state.setdefault("close_orders", [])
        state.setdefault("events", [])
        state.setdefault("results", {})
        state.setdefault("auto_execute_plans", True if self.x.demo else False)
        state.setdefault("risk_limits", dict(DEFAULT_RISK_LIMITS))
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
            raise legacy_engine.Halt("KAYTRADE尚未连接")
        return self._ensure_demo_state()

    def _event(self, name, payload):
        state = self._demo_state()
        clean = dict(payload)
        clean["detail"] = _rewrite_env_text(self, clean.get("detail", ""))
        row = {"time": time.time(), "event": name, **clean}
        state["events"].append(row)
        del state["events"][:-120]
        state["updated_at"] = time.time()
        self.store.save()
        self.emit("log", f"[{_env_code(self)}] {name} · {clean.get('detail') or clean.get('tier') or ''}")
        return row

    def _preflight_account(self):
        if not self.store:
            raise legacy_engine.Halt("先测试连接")
        if self.store.data.get("halt"):
            reason = legacy_engine.normalize_halt_reason(self.store.data.get("halt"))
            raise legacy_engine.Halt(legacy_engine.HALT_PREFIX + reason + legacy_engine.HALT_SUFFIX)
        account = self.x.account()
        if account.get("uid") != self.connection_id:
            raise legacy_engine.Halt("账户标识发生变化")
        if account.get("posMode") != "long_short_mode":
            raise legacy_engine.Halt("AI执行要求 OKX 双向持仓模式")
        perms = set(str(account.get("perm") or "").split(","))
        if "trade" not in perms or "withdraw" in perms:
            raise legacy_engine.Halt("API必须有交易权限且不得有提币权限")
        if not self.x.demo and not self._live_session_authorized:
            raise legacy_engine.Halt("LIVE写权限未授权；请在KAYTRADE界面输入 LIVE 启用本次实盘会话")
        return account

    def authorize_live_session(self, token):
        if self.x.demo:
            return True
        if str(token or "").strip().upper() != "LIVE":
            raise legacy_engine.Halt("实盘授权口令错误")
        self._live_session_authorized = True
        self.emit("log", "[LIVE] 本次实盘会话写权限已人工授权；AI自动执行仍保持独立开关。")
        return True

    def clear_live_session_authorization(self):
        self._live_session_authorized = False
        if not self.x.demo and self.store:
            self._demo_state()["auto_execute_plans"] = False
            self.store.save()

    def _risk_limits(self):
        state = self._demo_state()
        limits = state.setdefault("risk_limits", dict(DEFAULT_RISK_LIMITS))
        return {
            "max_leverage": Decimal(str(limits.get("max_leverage") or DEFAULT_RISK_LIMITS["max_leverage"])),
            "max_notional_usdt": Decimal(str(limits.get("max_notional_usdt") or DEFAULT_RISK_LIMITS["max_notional_usdt"])),
            "max_estimated_stop_loss_usdt": Decimal(str(limits.get("max_estimated_stop_loss_usdt") or DEFAULT_RISK_LIMITS["max_estimated_stop_loss_usdt"])),
        }

    def _validate_demo_open(self, proposal, replace_tier=None):
        limits = self._risk_limits()
        leverage = v183._d(proposal.get("leverage", limits["max_leverage"]), "leverage")
        if leverage > limits["max_leverage"]:
            raise legacy_engine.Halt(f"{_env_code(self)} leverage 必须≤{limits['max_leverage']}x")
        return super()._validate_demo_open(proposal, replace_tier=replace_tier)

    def _risk_totals(self, candidate=None, replace_tier=None):
        notional = Decimal("0")
        estimated = Decimal("0")
        for key, item in self._demo_state()["tiers"].items():
            if key == str(replace_tier) or not v183._active(item):
                continue
            notional += Decimal(str(item.get("notional_usdt") or 0))
            estimated += Decimal(str(item.get("estimated_stop_loss_usdt") or 0))
        if candidate:
            notional += candidate["notional"]
            estimated += candidate["estimated_loss"]
        limits = self._risk_limits()
        if notional > limits["max_notional_usdt"]:
            raise legacy_engine.Halt(
                f"{_env_code(self)} 两档合计名义仓位 {notional:.4f} USDT 超过 {limits['max_notional_usdt']} USDT"
            )
        if estimated > limits["max_estimated_stop_loss_usdt"]:
            raise legacy_engine.Halt(
                f"{_env_code(self)} 两档合计估算止损 {estimated:.4f} USDT 超过 {limits['max_estimated_stop_loss_usdt']} USDT"
            )
        return notional, estimated

    def _submit_open(self, proposal, proposal_id):
        result = super()._submit_open(proposal, proposal_id)
        key, item = self._tier(proposal.get("tier") or 1, require=True)
        item["okx_demo"] = bool(self.x.demo)
        item["environment"] = "OKX_DEMO" if self.x.demo else "OKX_LIVE"
        item["version"] = VERSION
        self.store.save()
        return _rewrite_result(self, result)

    def _amend_pending_from_plan(self, current, proposal):
        return _rewrite_result(self, super()._amend_pending_from_plan(current, proposal))

    def publish_ai_plan(self, plan):
        if isinstance(plan, dict) and any(k in plan for k in ("environment", "execution_environment", "okx_environment")):
            raise legacy_engine.Halt("AI方案不得指定 Demo/Live；执行环境只能由KAYTRADE当前连接决定")
        result = super().publish_ai_plan(plan)
        if isinstance(result, dict):
            result = _rewrite_result(self, result)
            result["mode"] = _env_mode(self)
            result["environment"] = "OKX_DEMO" if self.x.demo else "OKX_LIVE"
            plan_row = result.get("plan")
            if isinstance(plan_row, dict) and plan_row.get("detail"):
                plan_row["detail"] = _rewrite_env_text(self, plan_row["detail"])
        return result

    def submit_ai_trade(self, proposal):
        if isinstance(proposal, dict) and any(k in proposal for k in ("environment", "execution_environment", "okx_environment")):
            raise legacy_engine.Halt("AI交易请求不得指定 Demo/Live；执行环境只能由KAYTRADE当前连接决定")
        return _rewrite_result(self, super().submit_ai_trade(proposal))

    def close_position(self, payload):
        return _rewrite_result(self, super().close_demo_position(payload))

    def cancel_entry(self, payload):
        return _rewrite_result(self, super().cancel_demo_entry(payload))

    def amend_entry(self, payload):
        return _rewrite_result(self, super().amend_demo_entry(payload))

    def amend_protection(self, payload):
        return _rewrite_result(self, super().amend_demo_protection(payload))

    def set_auto_execute_plans(self, enabled):
        if enabled and not self.enabled:
            raise legacy_engine.Halt(f"先启用{_env_name(self)} AI执行通道")
        if enabled and not self.x.demo and not self._live_session_authorized:
            raise legacy_engine.Halt("LIVE自动执行需要先完成本次实盘会话授权")
        result = super().set_auto_execute_plans(enabled)
        self.emit(
            "log",
            f"[{_env_code(self)}] AI方案自动执行已"
            + ("开启：完整策略到达即提交交易所LIMIT挂单" if enabled else "关闭：AI方案仅展示"),
        )
        return _rewrite_result(self, result)

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
                f"[{_env_code(self)}] AI执行通道已启用："
                + ("Demo自动执行按当前开关运行。" if self.x.demo else "LIVE通道已开；真实资金自动执行仍由独立开关控制。"),
            )

    def stop(self):
        with self._ai_lock:
            self.enabled = False
            self.stopped = True
            if not self.x.demo:
                self._demo_state()["auto_execute_plans"] = False
                self.store.save()
            self.emit(
                "log",
                f"[{_env_code(self)}] AI执行通道已停止；不再接受新开仓，已有交易所订单/保护单保留并在下次连接时继续核对。",
            )

    def ai_state(self):
        state = super().ai_state()
        execution = state.pop("okx_demo_execution", None)
        if execution is None and self.store:
            execution = self._demo_state()
        state.update(
            version=VERSION,
            build=BUILD,
            mode=_env_mode(self),
            environment="OKX_DEMO" if self.x.demo else "OKX_LIVE",
            active_environment=_env_code(self),
            demo_exchange_writes=bool(self.x.demo),
            live_ai_writes=bool((not self.x.demo) and self.enabled and self._live_session_authorized),
            live_session_authorized=bool(self._live_session_authorized) if not self.x.demo else False,
            auto_execute_plans=bool(self._demo_state().get("auto_execute_plans", self.x.demo)) if self.store else False,
            risk_limits={k: str(v) for k, v in self._risk_limits().items()} if self.store else dict(DEFAULT_RISK_LIMITS),
            execution_state=execution,
        )
        state[_state_key(self)] = execution
        return state


_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_EMIT = app.App.emit


def _app_emit_v190(self, kind, data):
    engine = getattr(self, "engine", None)
    if engine is not None and isinstance(engine, OKXDualExecutionEngineV190):
        data = _rewrite_env_text(engine, data)
    return _PREVIOUS_APP_EMIT(self, kind, data)


def _cache_credentials_for_mode(owner, mode):
    cache = getattr(owner, "_v190_credentials", None)
    if cache is None:
        return
    cache[mode] = (owner.key.get(), owner.secret.get(), owner.phrase.get())


def _restore_credentials_for_mode(owner, mode):
    values = getattr(owner, "_v190_credentials", {}).get(mode, ("", "", ""))
    owner.key.set(values[0])
    owner.secret.set(values[1])
    owner.phrase.set(values[2])


def _mode_changed_v190(owner, *_):
    old = getattr(owner, "_v190_last_mode", "OKX模拟盘")
    new = owner.mode.get()
    if old == new:
        return
    current = getattr(owner, "engine", None)
    if isinstance(current, OKXDualExecutionEngineV190) and current.enabled:
        try:
            current.stop()
            owner.emit("log", f"[{_env_code(current)}] 环境切换：已自动停止该环境的新单写权限；已有订单转只读核对。")
        except Exception as exc:
            owner.emit("alarm", "环境切换停止写权限失败：" + str(exc))
    _cache_credentials_for_mode(owner, old)
    _restore_credentials_for_mode(owner, new)
    owner._v190_last_mode = new
    try:
        owner.signal.set(
            "V1.9.0 Build1900｜"
            + ("OKX DEMO" if new == "OKX模拟盘" else "OKX LIVE · REAL MONEY")
            + "｜LIMIT ONLY｜AI不能选择执行环境"
        )
    except Exception:
        pass


def _update_trade_button_v190(owner):
    button = getattr(owner, "trade_button", None)
    if not button:
        return
    engine = getattr(owner, "engine", None)
    running = bool(engine and engine.enabled)
    live = bool(engine and not engine.x.demo)
    if running:
        text = "■  停止 OKX Live AI通道" if live else "■  停止 OKX Demo AI通道"
    else:
        text = "▶  启用 OKX Live AI通道" if owner.mode.get() == "真实账户" else "▶  启用 OKX Demo AI通道"
    button.configure(text=text, variant="danger" if running else "accent")


def _ensure_v190_runtime(owner):
    current = getattr(owner, "engine", None)
    if current is None:
        raise legacy_engine.Halt("先连接OKX账户")
    if isinstance(current, OKXDualExecutionEngineV190):
        return current
    x = getattr(current, "x", None)
    if x is None:
        raise legacy_engine.Halt("当前连接缺少Exchange对象")
    try:
        current.enabled = False
    except Exception:
        pass
    replacement = OKXDualExecutionEngineV190(x, owner.folder, owner.emit)
    replacement.connect()
    owner.engine = replacement
    owner.emit("log", f"[{_env_code(replacement)}] Runtime已升级为 V1.9.0 双环境执行器。")
    return replacement


def _app_arm_v190(owner):
    if not owner.engine:
        app.messagebox.showerror("未连接", "先测试连接")
        return
    if owner.network_paused:
        app.messagebox.showerror("网络暂停", "等待网络恢复并核对账户后再启用AI通道")
        return
    if owner.host.get() != owner.engine.x.host or (owner.mode.get() == "OKX模拟盘") != owner.engine.x.demo:
        app.messagebox.showerror("连接不一致", "账户环境已修改，请重新测试连接")
        return

    engine = _ensure_v190_runtime(owner)
    live = not engine.x.demo
    if live:
        typed = app.simpledialog.askstring(
            "启用 OKX LIVE AI执行",
            "KAYTRADE V1.9.0 Build1900\n\n"
            "当前连接：OKX REAL ACCOUNT / REAL MONEY\n"
            "AI只生成交易方案，KAYTRADE程序会在自动执行开启时直接向OKX实盘发送LIMIT订单。\n"
            "Entry、TP、SL、减仓均保持 LIMIT ONLY；禁止OKX -1市价哨兵。\n"
            "本次启动后，LIVE自动执行默认OFF，开启执行通道后仍需单独打开“AI方案自动执行”。\n\n"
            "确认启用本次实盘写权限请输入 LIVE",
            parent=owner.root,
        )
        if not typed or typed.strip().upper() != "LIVE":
            return
        try:
            engine.authorize_live_session(typed)
        except Exception as exc:
            app.messagebox.showerror("实盘授权失败", str(exc))
            return
    else:
        typed = app.simpledialog.askstring(
            "启用 OKX Demo AI执行",
            "KAYTRADE V1.9.0 Build1900\n\n"
            "AI完整策略到达后，程序按当前自动执行开关向OKX模拟盘提交LIMIT挂单。\n"
            "确认启用请输入 DEMO",
            parent=owner.root,
        )
        if not typed or typed.strip().upper() != "DEMO":
            return
    v172._start_ai_enable(owner)


def _toggle_auto_execute_v190(owner):
    engine = getattr(owner, "engine", None)
    if not isinstance(engine, OKXDualExecutionEngineV190):
        app.messagebox.showerror("未连接", "先连接并启用 V1.9.0 AI执行通道")
        return
    try:
        current = bool(engine._demo_state().get("auto_execute_plans", engine.x.demo))
        engine.set_auto_execute_plans(not current)
        _refresh_v190_dashboard(owner)
    except Exception as exc:
        app.messagebox.showerror("自动执行设置失败", str(exc))


def _refresh_v190_dashboard(owner):
    engine = getattr(owner, "engine", None)
    store = getattr(engine, "store", None) if isinstance(engine, OKXDualExecutionEngineV190) else None
    data = getattr(store, "data", {}) if store is not None else {}
    restore = None
    had_alias = False
    if engine is not None and not engine.x.demo and store is not None:
        had_alias = "okx_demo_execution" in data
        restore = data.get("okx_demo_execution")
        data["okx_demo_execution"] = data.get("okx_live_execution") or {}
    try:
        v184._refresh_v184(owner)
    finally:
        if engine is not None and not engine.x.demo and store is not None:
            if had_alias:
                data["okx_demo_execution"] = restore
            else:
                data.pop("okx_demo_execution", None)

    try:
        if not isinstance(engine, OKXDualExecutionEngineV190):
            return
        state = engine._demo_state()
        auto_on = bool(state.get("auto_execute_plans", engine.x.demo))
        code = _env_code(engine)
        if hasattr(owner, "_v180_mode_var"):
            owner._v180_mode_var.set(
                f"OKX {code} · " + ("AUTO ON" if auto_on else "AUTO OFF")
            )
        if hasattr(owner, "_v183_auto_exec_status_var"):
            owner._v183_auto_exec_status_var.set(
                f"{code} · 已开启 · AI策略到达即提交交易所"
                if auto_on else
                f"{code} · 已关闭 · AI推荐仅展示"
            )
            owner._v183_auto_exec_note_var.set(
                "真实资金：完整策略将直接提交OKX实盘LIMIT挂单；执行环境只能由本界面选择。"
                if (auto_on and not engine.x.demo) else
                "完整策略立即提交OKX模拟盘LIMIT挂单；不等待价格触达。"
                if auto_on else
                "自动执行关闭：AI方案只显示，不向当前环境发送新订单。"
            )
            owner._v183_auto_exec_button.configure(
                text="关闭自动执行" if auto_on else "开启自动执行",
                variant="danger" if auto_on else "accent",
                command=lambda: _toggle_auto_execute_v190(owner),
            )
        owner.root.title(
            f"KAYTRADE {VERSION} · OKX {code}"
            + (" · REAL MONEY" if code == "LIVE" else "")
            + f" · Build {BUILD}"
        )
    except Exception:
        pass


def _readonly_cross_env_loop(owner):
    """Keep the disconnected environment observable without granting writes."""
    while not owner.finished.wait(10):
        current = getattr(owner, "engine", None)
        for code, engine in list(getattr(owner, "_v190_env_engines", {}).items()):
            if engine is current or not isinstance(engine, OKXDualExecutionEngineV190) or not engine.store:
                continue
            try:
                with engine._ai_lock:
                    # cancel_expired=False is deliberate: non-current environments
                    # are read-only and must never cancel/amend/place orders.
                    engine._sync_all(cancel_expired=False)
            except Exception as exc:
                owner.emit("log", f"[{code}] 只读核对暂时失败：{type(exc).__name__}: {exc}")


def _app_init_v190(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    self._v190_env_engines = {}
    current = getattr(self, "engine", None)
    if isinstance(current, OKXDualExecutionEngineV190):
        self._v190_env_engines[_env_code(current)] = current
    self._v190_readonly_thread = threading.Thread(
        target=_readonly_cross_env_loop,
        args=(self,),
        name="kaytrade-v190-readonly-cross-env",
        daemon=True,
    )
    self._v190_readonly_thread.start()
    self._v190_credentials = {
        "OKX模拟盘": (self.key.get(), self.secret.get(), self.phrase.get()),
        "真实账户": ("", "", ""),
    }
    self._v190_last_mode = self.mode.get()
    self.mode.trace_add("write", lambda *_: _mode_changed_v190(self))
    self.root.title(f"KAYTRADE {VERSION} · OKX DEMO · Build {BUILD}")
    try:
        self.signal.set("V1.9.0 Build1900｜OKX DEMO / LIVE完全隔离｜LIMIT ONLY｜AI不能选择执行环境")
        label = getattr(self, "_v170_version_label", None)
        if label is not None:
            label.configure(text="BTC / USDT   ·   V1.9.0 Build1900 · DUAL OKX")
        if getattr(self, "_v183_auto_exec_button", None) is not None:
            self._v183_auto_exec_button.configure(command=lambda: _toggle_auto_execute_v190(self))
    except Exception:
        pass
    self._v190_dual_env_ready = True
    self.update_trade_button()


def _bridge_get_v190(self):
    if not self._require_auth():
        return
    engine = self.bridge.owner.engine
    if self.path == "/health":
        env = _env_code(engine) if isinstance(engine, OKXDualExecutionEngineV190) else "DISCONNECTED"
        self._json(200, {
            "name": "KAYTRADE",
            "version": VERSION,
            "build": BUILD,
            "mode": _env_mode(engine) if isinstance(engine, OKXDualExecutionEngineV190) else "DUAL_OKX",
            "active_environment": env,
            "environment_selected_by_ai": False,
            "paper_runtime_present": False,
            "limit_only": True,
        })
        return
    if self.path == "/v1/state":
        try:
            payload = engine.ai_state() if isinstance(engine, OKXDualExecutionEngineV190) else {
                "version": VERSION,
                "build": BUILD,
                "mode": "DUAL_OKX",
                "connected": False,
                "active_environment": "DISCONNECTED",
                "environment_selected_by_ai": False,
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
        "/v1/cancel-entry",
        "/v1/amend-entry",
        "/v1/amend-protection",
        "/v1/close",
    }
    if self.path not in allowed:
        self._json(404, {"error": "not_found"})
        return
    try:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 65536:
            raise ValueError("invalid content length")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if isinstance(payload, dict) and any(k in payload for k in ("environment", "execution_environment", "okx_environment")):
            raise legacy_engine.Halt("AI不得指定 Demo/Live；执行环境由KAYTRADE桌面端当前连接决定")
        engine = self.bridge.owner.engine
        if not isinstance(engine, OKXDualExecutionEngineV190):
            raise legacy_engine.Halt("当前Runtime不是 V1.9.0 Dual OKX Execution")
        routes = {
            "/v1/trade": engine.submit_ai_trade,
            "/v1/plan": engine.publish_ai_plan,
            "/v1/cancel-entry": engine.cancel_entry,
            "/v1/amend-entry": engine.amend_entry,
            "/v1/amend-protection": engine.amend_protection,
            "/v1/close": engine.close_position,
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
        httpd = v172.ThreadingHTTPServer((v172.AI_BRIDGE_HOST, port), v172._BridgeHandler)
    except OSError:
        httpd = v172.ThreadingHTTPServer((v172.AI_BRIDGE_HOST, 0), v172._BridgeHandler)
    httpd.bridge = self
    self.httpd = httpd
    actual_port = int(httpd.server_address[1])
    descriptor = {
        "version": VERSION,
        "build": BUILD,
        "mode": "DUAL_OKX",
        "host": v172.AI_BRIDGE_HOST,
        "port": actual_port,
        "token": self.token,
        "environment_selected_by_ai": False,
        "paper_runtime_present": False,
        "limit_only": True,
        "routes": ["/v1/state", "/v1/plan", "/v1/trade", "/v1/cancel-entry", "/v1/amend-entry", "/v1/amend-protection", "/v1/close"],
    }
    self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(descriptor, handle, ensure_ascii=False, indent=2)
    self.thread = threading.Thread(target=httpd.serve_forever, name="kaytrade-ai-bridge", daemon=True)
    self.thread.start()
    self.owner.emit("log", f"AI Bridge已启动：127.0.0.1:{actual_port} · V1.9.0 Dual OKX · 环境由桌面端锁定")


def apply():
    if getattr(model, "_kaytrade_v190_dual_applied", False):
        return

    # Promote version metadata through the existing overlay stack.
    v183.VERSION = VERSION
    v183.BUILD = BUILD
    v184.VERSION = VERSION
    v184.BUILD = BUILD
    for module in (model, runtime, v168, b1681, v170, v1701, v171, v172, v180):
        module.VERSION = VERSION
        module.BUILD = BUILD
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = f"KAYTRADE {VERSION} · DUAL OKX · Build {BUILD}"

    app.Engine = OKXDualExecutionEngineV190
    legacy_engine.AIOnlyEngine = OKXDualExecutionEngineV190
    v172.AIOnlyEngine = OKXDualExecutionEngineV190
    v172._ensure_ai_only_engine = _ensure_v190_runtime

    app.App.__init__ = _app_init_v190
    app.App.emit = _app_emit_v190
    app.App.arm = _app_arm_v190
    app.App.update_trade_button = _update_trade_button_v190

    v183._refresh_v183_dashboard = _refresh_v190_dashboard
    v180._refresh_v180_dashboard = _refresh_v190_dashboard

    v172._BridgeHandler.do_GET = _bridge_get_v190
    v172._BridgeHandler.do_POST = _bridge_post_v190
    v172.AIBridgeServer.start = _bridge_start_v190

    model._kaytrade_v190_dual_applied = True
    app.App._kaytrade_v190_dual_applied = True


apply()

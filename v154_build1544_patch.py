"""KAYTRADE V1.5.4 Build 1544 overlay.

Build 1544 keeps the verified Build 1543 safety stack but disables Path C
completely for new trading decisions. Existing exchange/local legs from an
older build are still reconciled normally; no new Path-C opportunity or add-on
is created. Consecutive-loss pause remains disabled and the first-signal LIMIT
strategy remains unchanged.
"""
from __future__ import annotations

import math
from dataclasses import asdict

from v154_build1543_patch import apply as apply_previous
apply_previous()

import app
import engine
import v154_build1543_patch as v1543

VERSION = "1.5.4"
BUILD = "154.4"
PATH_C_ENABLED = False


def _settings_without_path_c(owner):
    """Build settings from UI without reserving capital for the disabled add-on."""
    SettingsClass = app.Settings
    values = asdict(SettingsClass())
    for key, var in owner.fields.items():
        if key not in values:
            continue
        try:
            values[key] = float(var.get())
        except Exception:
            raise engine.Halt(key + "必须是有效数字") from None

    for key in ("leverage", "cooldown_minutes"):
        value = float(values[key])
        if int(value) != value:
            raise engine.Halt(key + "必须是整数")
        values[key] = int(value)

    first = float(values.get("first_signal_notional") or 0.0)
    if not math.isfinite(first) or first <= 0:
        raise engine.Halt("第一信号仓位必须是有限正数")

    # Keep the second value only for backward-compatible display/settings data.
    # It is not used for sizing and is not included in the capital-limit check.
    values["first_signal_notional"] = first
    values["second_signal_notional"] = first * 2.0
    values["third_signal_notional"] = max(float(values.get("third_signal_notional") or 1.0), 1e-9)
    values["consecutive_losses"] = 3  # compatibility-only; loss pause remains disabled
    values["score_threshold"] = 6.0
    values["max_initial_notional"] = first
    values["max_notional"] = first

    settings = SettingsClass(**values).validate()
    if abs(float(settings.stop_atr) - 1.0) > 1e-9:
        raise engine.Halt("V1.5.4固定使用1.0×1H ATR止损")
    if first > float(settings.capital) * float(settings.leverage) + 1e-9:
        raise engine.Halt("第一信号仓位超过策略资金×杠杆上限")
    return settings


def _path_c_disabled(_self):
    """Hard stop for all new Path-C opportunity/add-on creation."""
    return None


def _mark_path_c_disabled(owner):
    try:
        owner.root.title("KAYTRADE 1.5.4 · BTC 策略控制台 · Build 1544")
    except Exception:
        pass
    try:
        text = str(owner.signal.get() or "")
        text = text.replace("路径A首仓1×；路径C第二信号2×加仓", "单一第一信号仓位；路径C已关闭")
        owner.signal.set(text)
    except Exception:
        pass
    label = getattr(owner, "_v1543_second_signal_entry", None)
    if label is not None:
        try:
            parent = label.master
            for child in parent.winfo_children():
                try:
                    text = str(child.cget("text") or "")
                except Exception:
                    continue
                if text.startswith("第二信号仓位 / 加仓资金"):
                    child.configure(text="第二信号仓位 / 加仓资金 USDT（路径C已关闭，仅兼容显示）")
        except Exception:
            pass
    owner._v1544_path_c_enabled = False
    owner._v1544_ready = True


def apply():
    if getattr(engine.Engine, "_kaytrade_v154_build1544_applied", False):
        return

    # Build1543's cycle closes over its module global `_maybe_path_c`; replacing
    # that global guarantees the inherited cycle cannot create/submit an add-on.
    v1543._maybe_path_c = _path_c_disabled
    v1543._settings_from_ui = _settings_without_path_c

    previous_cycle = engine.Engine.cycle
    previous_app_init = app.App.__init__
    previous_app_arm = app.App.arm

    def cycle(self):
        result = previous_cycle(self)
        if self.store:
            active = self.store.data.get("active")
            if isinstance(active, dict):
                active["version"] = VERSION
                active["build"] = BUILD
                active["path_c_enabled"] = False
                # A stale candidate without an exchange leg is safe to discard.
                has_path_c_leg = any(bool(x.get("path_c")) for x in (active.get("legs") or []))
                if not has_path_c_leg and active.get("path_c_opportunity") is not None:
                    active["path_c_opportunity"] = None
                self.store.save()
        return result

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        _mark_path_c_disabled(self)

    def app_arm(self):
        original = app.simpledialog.askstring
        def askstring(title, prompt, *args, **kwargs):
            text = str(prompt)
            text += (
                "\n\nV1.5.4 Build 1544：路径C已关闭，不创建第二信号、不执行2×加仓。"
                "当前仅按第一信号LIMIT开仓；连续亏损停开仍保持取消。"
            )
            return original(title, text, *args, **kwargs)
        app.simpledialog.askstring = askstring
        try:
            return previous_app_arm(self)
        finally:
            app.simpledialog.askstring = original

    engine.Engine.cycle = cycle
    app.App.__init__ = app_init
    app.App.arm = app_arm
    engine.Engine._kaytrade_v154_build1544_applied = True
    app.App._kaytrade_v154_build1544_applied = True


apply()

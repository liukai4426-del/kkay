"""KAYTRADE V1.6.0 production overlay.

Changes from the verified V1.5.6 Build1561 stack:
- middle_rsi requires the existing formal 5m macd_improving state for both long
  and short, and that gate is checked again in the pre-submit execution path;
- lower_band / upper_band open one single initial LIMIT order at 2x the user's
  base fixed position; middle_rsi remains 1x.

No new MACD definition, no cross/zero-axis/histogram-sign condition, no Path C,
no cooldown, no consecutive-loss pause, and no change to 1H ATR stop / 2R TP.
"""
from __future__ import annotations

import os
import traceback
from dataclasses import replace
from pathlib import Path

from v156_build1561_patch import apply as apply_previous
apply_previous()

import app
import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v152_strategy_patch as v152_runtime
import v153_runtime_patch as v153_runtime
import v153_backtest_core as v153_bt
import v154_runtime_patch as v154_runtime
import v154_limit_log_fix as v154_limit
import v154_record_card_boll_fix as v154_record
import v156_build1561_patch as v1561
import v160_model as model

VERSION = "1.6.0"
BUILD = "1600"
PATH_C_ENABLED = False
COOLDOWN_ENABLED = False
CONSECUTIVE_LOSS_PAUSE_ENABLED = False
MIDDLE_POSITION_MULTIPLIER = 1.0
OUTER_POSITION_MULTIPLIER = 2.0

# V1.6.0 is the only runtime hook in the package.  Suppress the inherited
# Build1561 success probe so CI only sees the final V1.6.0 state.
v1561._write_probe = lambda: None

# Route every inherited live/backtest facade through the V1.6.0 model wrapper.
v152_runtime.model = model
v152_runtime.V152_VERSION = VERSION
v153_runtime.model = model
v153_runtime.VERSION = VERSION
v153_bt.model = model
v154_runtime.model = model
v154_limit.model = model
if hasattr(v154_record, "model"):
    v154_record.model = model

_PREVIOUS_PREPARE = v138._prepare_order
_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_APP_ARM = app.App.arm
_PREVIOUS_ENGINE_ARM = engine.Engine.arm
_PREVIOUS_CYCLE = engine.Engine.cycle


def _path_from(market, score):
    for source in (
        (market or {}).get("opportunity") if isinstance(market, dict) else None,
        (score or {}).get("opportunity") if isinstance(score, dict) else None,
    ):
        if isinstance(source, dict):
            path = str(source.get("signal_path") or source.get("path") or "")
            if path:
                return path
    return ""


def _outer_settings(settings):
    """Create the one-call 2x sizing view without changing saved UI settings."""
    base_notional = float(getattr(settings, "max_notional"))
    updates = {"max_notional": base_notional * OUTER_POSITION_MULTIPLIER}
    if hasattr(settings, "max_initial_notional"):
        updates["max_initial_notional"] = float(getattr(settings, "max_initial_notional")) * OUTER_POSITION_MULTIPLIER
    if hasattr(settings, "second_signal_notional"):
        # Compatibility metadata only; Path C remains hard-off.
        updates["second_signal_notional"] = float(getattr(settings, "first_signal_notional", base_notional)) * OUTER_POSITION_MULTIPLIER
    return replace(settings, **updates)


def _outer_tier(original_tier):
    def tier(total):
        signal_tier, multiplier, level = original_tier(total)
        if signal_tier and multiplier:
            return signal_tier, OUTER_POSITION_MULTIPLIER, level
        return signal_tier, multiplier, level
    return tier


def _prepare_order_v160(self, side, score, market, equity, available, remaining):
    """Apply the requested path-dependent base size before inherited checks.

    The inherited V1.5.x preparation chain still performs all hard blockers and
    calls model.execution_checks() before a LIMIT write.  For outer paths only,
    both the risk multiplier and the fixed-notional cap are doubled for this
    single order.  No second/add-on order is created.
    """
    path = _path_from(market, score)

    if path == model.MIDDLE_PATH and not model._macd_improving(score or {}):
        self.emit("log", "V1.6.0实际开仓前复核：中轨5m macd_improving未通过，禁止提交订单；机会保留")
        return None

    if path not in model.OUTER_PATHS:
        prepared = _PREVIOUS_PREPARE(self, side, score, market, equity, available, remaining)
        if not prepared:
            return prepared
        plan, tier, multiplier, level = prepared
        plan["version"] = VERSION
        plan["build"] = BUILD
        plan["boll_signal_path"] = path or plan.get("boll_signal_path")
        plan["base_position_multiplier"] = MIDDLE_POSITION_MULTIPLIER
        plan["boll_position_multiplier"] = MIDDLE_POSITION_MULTIPLIER
        plan["position_rule"] = "middle_1x"
        return plan, tier, MIDDLE_POSITION_MULTIPLIER, "开仓信号 · 中轨1×基础仓位"

    original_settings = self.settings
    original_tier = v137._tier
    base_notional = float(getattr(original_settings, "max_notional"))
    try:
        self.settings = _outer_settings(original_settings)
        v137._tier = _outer_tier(original_tier)
        prepared = _PREVIOUS_PREPARE(self, side, score, market, equity, available, remaining)
    finally:
        v137._tier = original_tier
        self.settings = original_settings

    if not prepared:
        return prepared
    plan, tier, _multiplier, _level = prepared
    plan["version"] = VERSION
    plan["build"] = BUILD
    plan["boll_signal_path"] = path
    plan["base_position_notional"] = base_notional
    plan["requested_position_notional"] = base_notional * OUTER_POSITION_MULTIPLIER
    plan["base_position_multiplier"] = 1.0
    plan["boll_position_multiplier"] = OUTER_POSITION_MULTIPLIER
    plan["position_multiplier"] = OUTER_POSITION_MULTIPLIER
    plan["position_rule"] = "outer_band_2x_single_order"
    return plan, tier, OUTER_POSITION_MULTIPLIER, "开仓信号 · 外轨2×基础仓位"


def _probe_target():
    return os.environ.get("KAYTRADE_STARTUP_PROBE", "").strip()


def _write_probe():
    target = _probe_target()
    if not target:
        return
    try:
        Path(target).write_text(
            f"PASS KAYTRADE {VERSION} Build {BUILD} middle_macd=required outer_band=2x_single_order "
            "limit_entry=on stop=1h_atr_1x tp=2r cooldown=off path_c=off loss_pause=off\n",
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


def _rewrite_start_log(data):
    if not isinstance(data, str) or not data.startswith("自动交易启动；"):
        return data
    return (
        "自动交易启动；V1.6.0：5m BOLL中轨做多/做空均必须通过正式模型现有macd_improving；"
        "中轨=1×基础固定仓位；5m BOLL下轨/上轨=首次开仓单张LIMIT订单直接2×基础固定仓位；"
        "不新增MACD金叉/零轴/柱体正负条件；固定6分；1×1H ATR止损；整仓2R；"
        "Path C关闭；平仓冷却关闭；连续亏损停开关闭"
    )


def apply():
    if getattr(engine.Engine, "_kaytrade_v160_applied", False):
        return

    v138._prepare_order = _prepare_order_v160

    def app_init(self, *args, **kwargs):
        try:
            _PREVIOUS_APP_INIT(self, *args, **kwargs)
            try:
                self.root.title("KAYTRADE 1.6.0 · BTC 策略控制台 · Build 1600")
                self.signal.set("V1.6.0 · 中轨MACD必要 / 外轨2×基础仓位")
            except Exception:
                pass
            self._v160_ready = True
            _write_probe()
        except Exception:
            _write_probe_error()
            raise

    def app_arm(self):
        original = app.simpledialog.askstring
        def askstring(title, prompt, *args, **kwargs):
            text = str(prompt)
            text += (
                "\n\nV1.6.0：中轨开仓必须通过正式模型现有5m macd_improving；"
                "中轨使用1×基础固定仓位；上下外轨首次开仓直接使用2×基础固定仓位，"
                "只提交一张LIMIT订单，不是先1×再加仓。"
            )
            return original(title, text, *args, **kwargs)
        app.simpledialog.askstring = askstring
        try:
            return _PREVIOUS_APP_ARM(self)
        finally:
            app.simpledialog.askstring = original

    def engine_arm(self, settings):
        original_emit = self.emit
        def emit(kind, data):
            return original_emit(kind, _rewrite_start_log(data))
        self.emit = emit
        try:
            return _PREVIOUS_ENGINE_ARM(self, settings)
        finally:
            self.emit = original_emit

    def cycle(self):
        result = _PREVIOUS_CYCLE(self)
        if self.store:
            active = self.store.data.get("active")
            if isinstance(active, dict):
                changed = active.get("version") != VERSION or active.get("build") != BUILD
                active["version"] = VERSION
                active["build"] = BUILD
                active["path_c_enabled"] = False
                active["cooldown_enabled"] = False
                active["consecutive_loss_pause_enabled"] = False
                active["middle_macd_required"] = True
                active["outer_band_position_multiplier"] = OUTER_POSITION_MULTIPLIER
                if changed:
                    self.store.save()
        return result

    app.App.__init__ = app_init
    app.App.arm = app_arm
    engine.Engine.arm = engine_arm
    engine.Engine.cycle = cycle
    engine.Engine._kaytrade_v160_applied = True
    app.App._kaytrade_v160_applied = True


apply()

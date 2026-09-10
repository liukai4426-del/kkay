"""KAYTRADE V1.3.5 runtime refinements: 1-10 score threshold and 5s startup buffer."""
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

STARTUP_BUFFER_SECONDS = 5.0
OLD_THRESHOLD_LABEL = '自动开仓评分阈值 3.5—10（0.5步进）'
NEW_THRESHOLD_LABEL = '自动开仓评分阈值 1—10（0.5步进）'


def _validate_settings(self):
    """V1.3.5 validation with score threshold widened from 3.5-10 to 1-10."""
    from engine import Halt

    if not 1.0 <= self.score_threshold <= 10.0 or abs(self.score_threshold * 2 - round(self.score_threshold * 2)) > 1e-9:
        raise Halt('评分阈值必须是1—10之间、以0.5为步进')
    for name, value in asdict(self).items():
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise Halt(name + ' 必须是有限正数')
    if int(self.leverage) != self.leverage or not 1 <= self.leverage <= 10:
        raise Halt('杠杆范围1—10倍（整数）')
    if int(self.consecutive_losses) != self.consecutive_losses or self.consecutive_losses > 20:
        raise Halt('连续亏损上限必须是1—20的整数')
    if self.daily_loss > self.capital or self.max_notional > self.capital * self.leverage:
        raise Halt('日亏损/名义仓位超出资金与杠杆范围')
    if not .6 <= self.stop_atr <= 3:
        raise Halt('ATR止损倍数范围0.6—3')
    if abs(self.reward_r - 2.0) > 1e-9:
        raise Halt('V1.3.5最终止盈固定为2R')
    if abs(self.fee_bps - 2.0) > 1e-9 or abs(self.taker_fee_bps - 5.0) > 1e-9 or abs(self.slippage_bps - 5.0) > 1e-9:
        raise Halt('V1.3.5成本参数固定：Maker 2bps / Taker 5bps / 滑点预算5bps')
    return self


def _widgets(root):
    out = []
    try:
        children = root.winfo_children()
    except Exception:
        return out
    for child in children:
        out.append(child)
        out.extend(_widgets(child))
    return out


def apply():
    """Apply the V1.3.5 refinements once, before the desktop UI is instantiated."""
    import app
    import engine

    if getattr(engine.Engine, '_kaytrade_v135_patch_applied', False):
        return

    # 1) Execution threshold can be selected from 1 to 10, preserving 0.5-point steps.
    engine.Settings.validate = _validate_settings

    original_app_init = app.App.__init__

    def app_init(self, *args, **kwargs):
        original_app_init(self, *args, **kwargs)

        # app.py V1.3.5 originally clamps persisted values to >=3.5. Restore the
        # newly supported 1-10 value after the original UI has been constructed.
        try:
            path = Path(self.settings_path)
            if path.exists():
                saved = json.loads(path.read_text())
                if 'score_threshold' in saved:
                    value = float(saved['score_threshold'])
                    value = max(1.0, min(10.0, round(value * 2) / 2))
                    self.fields['score_threshold'].set(f'{value:g}')
        except Exception:
            pass

        # Keep the visible execution-parameter description aligned with validation.
        for widget in _widgets(self.root):
            try:
                if widget.cget('text') == OLD_THRESHOLD_LABEL:
                    widget.configure(text=NEW_THRESHOLD_LABEL)
            except Exception:
                pass

    app.App.__init__ = app_init

    # 2) Every manual enable of automatic trading gets a hard 5-second no-entry
    # buffer in the engine layer. Reconciliation and market refresh may continue,
    # but cycle() is run with entry permission temporarily disabled during buffer.
    original_engine_init = engine.Engine.__init__
    original_arm = engine.Engine.arm
    original_cycle = engine.Engine.cycle
    original_stop = engine.Engine.stop

    def engine_init(self, *args, **kwargs):
        original_engine_init(self, *args, **kwargs)
        self.startup_buffer_until = 0.0

    def arm(self, settings):
        result = original_arm(self, settings)
        self.startup_buffer_until = time.monotonic() + STARTUP_BUFFER_SECONDS
        self.emit('log', '自动交易已开启；启动缓冲5秒，期间只更新行情/核对仓位，不允许自动开仓')
        return result

    def cycle(self):
        until = float(getattr(self, 'startup_buffer_until', 0.0) or 0.0)
        if self.enabled and until:
            now = time.monotonic()
            if now < until:
                self.enabled = False
                try:
                    return original_cycle(self)
                finally:
                    # Do not revive trading if the user stopped it or a permanent
                    # safety lock was written while reconciliation was running.
                    if not self.stopped and self.store and not self.store.data.get('halt'):
                        self.enabled = True
            self.startup_buffer_until = 0.0
            self.emit('log', '启动缓冲5秒结束；自动开仓现已启用')
        return original_cycle(self)

    def stop(self):
        self.startup_buffer_until = 0.0
        return original_stop(self)

    engine.Engine.__init__ = engine_init
    engine.Engine.arm = arm
    engine.Engine.cycle = cycle
    engine.Engine.stop = stop
    engine.Engine._kaytrade_v135_patch_applied = True

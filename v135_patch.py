"""KAYTRADE V1.3.5 runtime refinements: threshold/startup safety, explicit 50123 handling, and relaxed 15m setup gate."""
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


def _is_okx_50123(exc):
    code = str(getattr(exc, 'code', '') or '')
    text = str(exc or '')
    return code == '50123' or 'OKX 50123' in text or '错误码 50123' in text


def _remove_15m_setup_gate(original_signal, strategy_module):
    """Return a signal wrapper where 15m Setup remains scored but never vetoes an entry."""
    def signal(*args, **kwargs):
        result = original_signal(*args, **kwargs)
        scores = result.get('scores', {})
        for side, score in scores.items():
            structure = score.get('structure', {}) or {}
            trigger = float((score.get('layers', {}) or {}).get('trigger', 0.0) or 0.0)
            hard_trigger = trigger >= .5
            blocked = bool(structure.get('blocked'))
            gate = hard_trigger and not blocked
            total = float(score.get('total', 0.0) or 0.0)
            required = float(score.get('required', result.get('threshold', 3.5)) or 0.0)
            eligible = gate and total >= required
            score['gate'] = gate
            score['eligible'] = eligible
            score['position_multiplier'] = strategy_module._position_multiplier(total) if eligible else 0.0

            # 15m Setup is informational/scoring only in this refinement. It must
            # never appear as a hard-stop reason, even when its score is zero.
            if blocked:
                score['reason'] = f'前方强结构距离 {structure.get("front_r", 0):.2f}R < 1R，禁止开仓'
            elif not hard_trigger:
                score['reason'] = f'5m Trigger {trigger:g}/1.0 < 0.5，等待入场触发'
            elif eligible:
                score['reason'] = (
                    f"{score.get('level', '信号')} · {total:g}/{result.get('score_max', 10):g} 达标；"
                    f"按 {score['position_multiplier']:g}× 仓位进入执行与风险检查"
                )
            else:
                score['reason'] = (
                    f"{score.get('level', '信号')} · {total:g}/{result.get('score_max', 10):g}，"
                    f"未达开仓阈值 {required:g}"
                )

        qualified = [side for side, score in scores.items() if score.get('eligible')]
        if len(qualified) == 1:
            selected = qualified[0]
        elif len(qualified) == 2 and scores[qualified[0]].get('total') != scores[qualified[1]].get('total'):
            selected = max(qualified, key=lambda side: scores[side].get('total', 0))
        else:
            selected = '观望'
        result['side'] = selected
        result['why'] = scores[selected]['reason'] if selected != '观望' else ' / '.join(
            side + ': ' + score.get('reason', '') for side, score in scores.items()
        )
        return result

    return signal


def apply():
    """Apply the V1.3.5 refinements once, before the desktop UI is instantiated."""
    import app
    import engine
    import strategy

    if getattr(engine.Engine, '_kaytrade_v135_patch_applied', False):
        return

    # 1) Execution threshold can be selected from 1 to 10, preserving 0.5-point steps.
    engine.Settings.validate = _validate_settings

    # 2) 15m Setup remains part of the score, but it is no longer a hard entry
    # gate. 5m Trigger and the forward-structure safety block remain hard gates.
    original_strategy_signal = strategy.signal
    relaxed_signal = _remove_15m_setup_gate(original_strategy_signal, strategy)
    strategy.signal = relaxed_signal
    engine.signal = relaxed_signal

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

    # 3) Every manual enable of automatic trading gets a hard 5-second no-entry
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

    def run_original_cycle(self):
        try:
            return original_cycle(self)
        except Exception as exc:
            if not _is_okx_50123(exc):
                raise

            # OKX 50123 is a deterministic authorization rejection. The parent
            # order was not accepted, so keeping the pre-POST local "active"
            # placeholder would only cause pointless 51603 reconciliation loops.
            p = self.store.data.get('active') if self.store else None
            cleared = False
            if isinstance(p, dict) and not p.get('order_id') and not p.get('filled'):
                snapshot = {
                    'client_id': p.get('client_id', ''),
                    'side': p.get('side', ''),
                    'score': p.get('score'),
                    'submitted': p.get('submitted'),
                    'reason': 'OKX 50123 explicit API permission rejection',
                }
                self.store.data['active'] = None
                self.store.save()
                self.store.record('OKX明确拒绝开仓', snapshot)
                cleared = True

            self.enabled = False
            self.startup_buffer_until = 0.0
            detail = '；已清理本地未成交记录，不进入51603订单核对' if cleared else ''
            raise engine.Halt(
                'OKX 50123：API Key没有BTC/对应交易市场的下单权限；本次开仓被OKX明确拒绝，订单未创建'
                + detail + '；已停止新开仓。请在OKX修正API交易权限后重新测试连接并启动'
            ) from None

    def cycle(self):
        until = float(getattr(self, 'startup_buffer_until', 0.0) or 0.0)
        if self.enabled and until:
            now = time.monotonic()
            if now < until:
                self.enabled = False
                try:
                    return run_original_cycle(self)
                finally:
                    # Do not revive trading if the user stopped it or a permanent
                    # safety lock was written while reconciliation was running.
                    if not self.stopped and self.store and not self.store.data.get('halt'):
                        self.enabled = True
            self.startup_buffer_until = 0.0
            self.emit('log', '启动缓冲5秒结束；自动开仓现已启用')
        return run_original_cycle(self)

    def stop(self):
        self.startup_buffer_until = 0.0
        return original_stop(self)

    engine.Engine.__init__ = engine_init
    engine.Engine.arm = arm
    engine.Engine.cycle = cycle
    engine.Engine.stop = stop
    engine.Engine._kaytrade_v135_patch_applied = True

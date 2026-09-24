"""V1.5.2 validation/safety wiring applied after the strategy runtime.

Keeps the first entry-model validation isolated at 1.0 x 1H ATR, preserves a
historical max-loss-streak counter, and emits deduplicated state/block diagnostics.
"""
from __future__ import annotations

import math

from v152_strategy_patch import apply as apply_v152
apply_v152()

import app
import engine
import v137_strategy_patch as v137


def apply():
    if getattr(engine.Engine, '_kaytrade_v152_safety_applied', False):
        return

    previous_refresh = engine.Engine.refresh_market
    previous_arm = engine.Engine.arm
    previous_settings = app.App.settings
    previous_app_init = app.App.__init__
    previous_finalize = v137._finalize_cycle

    def app_settings(self):
        settings = previous_settings(self)
        if abs(float(settings.stop_atr) - 1.0) > 1e-9:
            raise engine.Halt('V1.5.2首轮新入场模型验证固定使用1.0×1H ATR止损；1.2/1.5仅用于后续独立A/B测试')
        return settings

    def engine_arm(self, settings):
        if abs(float(settings.stop_atr) - 1.0) > 1e-9:
            raise engine.Halt('V1.5.2首轮验证固定1.0×1H ATR止损，禁止混入ATR参数实验')
        return previous_arm(self, settings)

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        try:
            if 'stop_atr' in self.fields:
                self.fields['stop_atr'].set('1.0')
        except Exception:
            pass
        for widget in getattr(__import__('v138_strategy_patch'), '_widgets')(self.root):
            try:
                text = str(widget.cget('text') or '')
            except Exception:
                text = ''
            try:
                if '1小时 ATR 止损倍数 0.6—3' in text:
                    widget.configure(text='1小时 ATR 止损倍数 1.0（V1.5.2首轮固定）')
            except Exception:
                pass

    def _enhance_market(data):
        if not isinstance(data, dict):
            return data
        out = data
        sl = out.get('estimated_sl')
        tp = out.get('estimated_tp')
        extras = []
        try:
            if sl is not None and math.isfinite(float(sl)):
                extras.append(f'预计SL {float(sl):,.2f}')
            if tp is not None and math.isfinite(float(tp)):
                extras.append(f'预计TP {float(tp):,.2f}')
        except (TypeError, ValueError):
            pass
        front = out.get('front_space_status')
        if front:
            if front == '空间未知':
                extras.append('前方空间 未知')
            else:
                try:
                    extras.append(f"前方空间 {float(out.get('front_r')):.2f}R")
                except (TypeError, ValueError):
                    extras.append('前方空间 '+str(front))
        try:
            cost = float(out.get('cost_r'))
            if math.isfinite(cost):
                extras.append(f'成本 {cost:.2f}R')
        except (TypeError, ValueError):
            pass
        if extras:
            current = str(out.get('why') or '')
            suffix = '｜' + '｜'.join(extras)
            if suffix not in current:
                out['why'] = current + suffix
        return out

    def refresh_market(self):
        original_emit = self.emit
        captured = {'market': None}
        def emit(kind, data):
            if kind == 'market':
                data = _enhance_market(data)
                captured['market'] = data
            return original_emit(kind, data)
        self.emit = emit
        try:
            result = previous_refresh(self)
        finally:
            self.emit = original_emit

        market = captured['market'] or self.market
        if not self.store or not isinstance(market, dict):
            return result
        state = self.store.data
        direction = market.get('direction')
        score = (market.get('scores') or {}).get(direction) if direction in ('做多','做空') else None
        blockers = list(((score or {}).get('confirmations') or {}).get('blockers') or [])
        status = str(market.get('status') or '')
        opportunity_id = str(market.get('opportunity_id') or '')
        if blockers:
            detail = '；'.join(blockers)
        elif status in ('等待趋势','等待回调','等待5m确认','等待1m触发','机会失效','评分及限制检查'):
            detail = str((score or {}).get('reason') or market.get('why') or status)
        else:
            detail = ''
        signature = f'{opportunity_id}|{direction}|{status}|{detail}'
        if detail and state.get('v152_last_block_signature') != signature:
            state['v152_last_block_signature'] = signature
            self.store.save()
            self.store.record('V1.5.2未开仓原因', {
                'opportunity_id': opportunity_id, 'direction': direction,
                'status': status, 'blockers': blockers, 'detail': detail,
            })
            original_emit('log', f'V1.5.2未开仓：{status}｜{detail}')
        elif not detail and state.get('v152_last_block_signature'):
            state['v152_last_block_signature'] = ''
            self.store.save()
        return result

    def finalize_cycle(self, p):
        result = previous_finalize(self, p)
        if self.store:
            state = self.store.data
            current = int(state.get('streak') or 0)
            historical = int(state.get('max_streak') or 0)
            if current > historical:
                state['max_streak'] = current
                self.store.save()
        return result

    app.App.settings = app_settings
    app.App.__init__ = app_init
    engine.Engine.arm = engine_arm
    engine.Engine.refresh_market = refresh_market
    v137._finalize_cycle = finalize_cycle
    engine.Engine._kaytrade_v152_safety_applied = True


apply()

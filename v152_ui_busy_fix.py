"""V1.5.2 UI operation-lock hotfix.

The account-connect and auto-trading authorization actions must finish as soon as
their own control work is complete. Expensive 4H/1H/15m/5m/1m candle warm-up
belongs to the subsequent strategy tick and must not keep the GUI ``busy`` flag
latched. Trading writes stay serialized by the single worker thread and all
existing fail-closed engine/exchange guards remain unchanged.
"""
from __future__ import annotations

import queue
import time

from v152_safety_patch import apply as apply_v152_safety
apply_v152_safety()

import app
import engine
from candles import CandlePending
from exchange import Exchange, NetworkError

V152_UI_BUSY_FIX = True


def _active_local(engine_obj):
    try:
        return bool(engine_obj.store and engine_obj.store.data.get('active'))
    except Exception:
        return False


def _should_cycle(kind, engine_obj):
    """Return whether the worker may enter the potentially expensive engine cycle.

    connect/arm are deliberately excluded so their UI operation lock can release
    promptly. Before the user has armed once, idle ticks also stay out of market
    warm-up unless a persisted active order/position must be reconciled.
    """
    if engine_obj is None:
        return False
    if kind in ('connect', 'arm'):
        return False
    if kind == 'tick' and getattr(engine_obj, 'settings', None) is None and not _active_local(engine_obj):
        return False
    return True


def worker_v152(self):
    while not self.finished.is_set():
        try:
            kind, data = self.tasks.get(timeout=5)
        except queue.Empty:
            kind, data = 'tick', None

        try:
            if self.network_paused and kind == 'tick' and self.engine:
                if time.monotonic() - self.probe_at < 30:
                    continue
                self.probe_at = time.monotonic()
                self.engine.x.sync_time()
                if self.engine.x.account().get('uid') != self.engine.connection_id:
                    raise engine.Halt('恢复后的账户标识不匹配')
                self.engine.x.balance()
                self.engine.x.positions()
                self.engine.x.orders()
                self.engine.x.algos()
                self.recovery_count += 1
                self.emit('network', f'恢复核对 {self.recovery_count}/2 · 不开仓')
                if self.recovery_count < 2:
                    continue
                if self.engine.store.data['active']:
                    self.engine.reconcile()
                self.engine.store.data['last_bar'] = int(self.engine.market_now() // 300) * 300000 - 300000
                self.engine.store.save()
                self.network_paused = False
                self.emit('log', '网络已恢复并核对账户，仅恢复观察；请核对解除故障锁后重新授权，不补发错过的订单')

            if kind == 'diagnose':
                x = Exchange(*data[:4], demo=data[4])
                x.network_event = lambda message: self.emit('network', message)
                x.sync_time()
                self.emit('log', '网络自检：公共时间接口通过')
                if all(data[1:4]):
                    x.account()
                    self.emit('log', '网络自检：账户只读认证通过')
                else:
                    self.emit('log', '未填写完整密钥，跳过账户自检')
                continue

            if kind == 'connect':
                x = Exchange(*data[:4], demo=data[4])
                e = engine.Engine(x, self.folder, self.emit)
                x.network_event = lambda message: self.emit('network', message)
                e.connect()
                self.engine = e
                self.network_paused = False
                self.recovery_count = 0
                self.history_key = None
                self.refresh_history()
                self.emit('log', 'V1.5.2连接核对完成；行情历史将在启动自动交易后由后台策略循环加载')

            elif kind == 'arm':
                if not self.engine:
                    raise engine.Halt('先连接')
                self.engine.market = None
                self.engine.arm(data)
                if self.engine.enabled:
                    self.emit('log', 'V1.5.2启动授权完成；指标历史加载期间保持等待，不使用旧K线开仓')

            elif kind == 'stop' and self.engine:
                self.engine.stop()

            elif kind == 'flatten' and self.engine:
                self.engine.flatten()

            elif kind == 'ack' and self.engine:
                self.engine.acknowledge()

            if self.engine:
                if _should_cycle(kind, self.engine):
                    self.engine.cycle()
                self.refresh_history()
                # Do not add an unrelated ticker GET to connect/arm before their
                # ``done`` event. The next idle tick will resume public updates.
                if kind not in ('connect', 'arm') and time.monotonic() - self.public_at > 5:
                    self.emit('ticker', self.engine.x.ticker())
                    self.public_at = time.monotonic()
                self.emit(
                    'status',
                    '全自动运行 / ' + ('模拟盘' if self.engine.x.demo else '实盘')
                    if self.engine.enabled
                    else '故障暂停 · 需核对'
                    if self.engine.store.data['halt']
                    else '已停止新开仓 / 继续核对持仓',
                )

        except CandlePending as exc:
            message = str(exc)
            self.emit('status', '等待最新收盘K线 · 暂不新开仓')
            self.emit('candle_wait', message)
            persistent = '持续过期' in message or '连续异常' in message
            if persistent and time.monotonic() - self.candle_wait_log >= 15:
                self.emit('log', message)
                self.candle_wait_log = time.monotonic()

        except NetworkError as exc:
            self.network_paused = True
            self.recovery_count = 0
            self.probe_at = time.monotonic()
            if self.engine:
                self.engine.halt(str(exc))
            else:
                self.emit('alarm', str(exc))

        except Exception as exc:
            if self.engine:
                self.engine.halt(str(exc))
            else:
                self.emit('alarm', str(exc))

        finally:
            # This is the only release signal consumed by App.drain(), so every
            # user-submitted action must reach it even on error/continue paths.
            if kind != 'tick':
                self.emit('done', None)


def apply():
    if getattr(app.App, '_kaytrade_v152_ui_busy_fix_applied', False):
        return
    app.App.worker = worker_v152
    app.App._kaytrade_v152_ui_busy_fix_applied = True


apply()

"""V1.5.2 UI operation-lock and event-pump hotfix.

The account-connect and auto-trading authorization actions must finish as soon as
their own control work is complete. Expensive 4H/1H/15m/5m/1m candle warm-up
belongs to the subsequent strategy tick and must not keep the GUI ``busy`` flag
latched.

V1.4.7+ strategy payloads intentionally removed the old 1D ``d`` field and use
``q`` for 4H instead. The legacy Tk event renderer still indexed ``data['d']``;
once the first V1.5.2 market event arrived this raised KeyError on the Tk thread,
stopped future drain scheduling, left the live BTC quote at "waiting", and could
leave later GUI actions permanently busy. This hotfix adapts V1.5.2 market events
to the four-column legacy renderer (4H/1H/15m/5m) and makes the UI event pump
self-rescheduling if a rendering exception ever occurs again.

Trading writes stay serialized by the single worker thread and all existing
fail-closed engine/exchange guards remain unchanged.
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

_PREVIOUS_EMIT = app.App.emit
_PREVIOUS_DRAIN = app.App.drain
_PREVIOUS_INIT = app.App.__init__


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


def _market_for_legacy_ui(data):
    """Adapt V1.5.2's 4H/1H/15m/5m payload to the inherited four-column renderer.

    The old first column key is named ``d`` because it used to mean 1D. V1.5.2
    has no 1D strategy data, so the UI first column now deliberately displays 4H
    and receives a copy of ``q`` under the compatibility key ``d``.
    """
    if not isinstance(data, dict):
        return data
    out = dict(data)
    if 'd' not in out:
        out['d'] = out.get('q') or {}
    return out


def emit_v152_ui(self, kind, data):
    if kind == 'market':
        data = _market_for_legacy_ui(data)
    return _PREVIOUS_EMIT(self, kind, data)


def drain_v152_ui(self):
    """Never allow one display-only event to permanently kill Tk event draining."""
    try:
        return _PREVIOUS_DRAIN(self)
    except Exception as exc:
        # The engine/worker is separate from Tk rendering. A rendering exception
        # must not silently freeze all later ticker/done/status events.
        self._v152_ui_drain_error = f'{type(exc).__name__}: {exc}'
        try:
            self.status.set('界面行情显示异常 · 已自动恢复事件循环')
        except Exception:
            pass

        # It is safe to release a purely read/control UI lock if its completion
        # event was lost to a renderer exception. Never auto-release while a
        # flatten write is still being reconciled.
        pending = getattr(self, '_v140_pending_action', None)
        flatten_waiting = bool(
            getattr(self, 'engine', None)
            and getattr(self.engine, '_v140_flatten_waiting', False)
        )
        if getattr(self, 'busy', False) and not flatten_waiting and pending in (None, 'connect', 'arm', 'ack'):
            self.busy = False
            try:
                self.update_trade_button()
            except Exception:
                pass

        try:
            self.root.after(150, self.drain)
        except Exception:
            pass
        return None


def app_init_v152_ui(self, *args, **kwargs):
    _PREVIOUS_INIT(self, *args, **kwargs)
    # The inherited Treeview still calls its first data column "d" internally.
    # Relabel it to what V1.5.2 actually renders: 4H.
    try:
        self.matrix.heading('d', text='4小时', anchor='w')
        self._v152_indicator_first_column = '4H'
    except Exception:
        pass


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
                self.public_at = 0
                self.emit('log', 'V1.5.2连接核对完成；实时BTC价格由空闲行情轮询更新，指标历史将在启动自动交易后加载')

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
    app.App.emit = emit_v152_ui
    app.App.drain = drain_v152_ui
    app.App.__init__ = app_init_v152_ui
    app.App.worker = worker_v152
    app.App._kaytrade_v152_ui_busy_fix_applied = True


apply()

"""V1.3.5 auto-entry guard: separate safe skips/stops from true integrity fault locks.

This patch is intentionally applied after the existing V1.3.5 strategy/execution
patches.  It never weakens fail-closed handling for ambiguous writes, live exposure,
order-state mismatches, or TP/SL protection failures.
"""
import time

from v135_patch import apply as apply_v135_patch
apply_v135_patch()
from v135_execution_patch import apply as apply_v135_execution_patch
apply_v135_execution_patch()
from v135_flatten_noop_hotfix import apply as apply_flatten_noop
apply_flatten_noop()

import app
import engine
import exchange


class EntrySkip(RuntimeError):
    """A market/risk condition that skips this entry without creating a fault lock."""
    def __init__(self, message, consume_bar=False):
        super().__init__(message)
        self.consume_bar = bool(consume_bar)


# These are pre-order conditions: no trading write has been submitted yet.
_ENTRY_SKIP_RULES = (
    ('买卖价差过大', False),
    ('风险预算不足以满足最小下单量', True),
    ('下单数量不足以安全拆分为两笔止盈', True),
    ('无效信号或ATR', True),
    ('止盈止损价格非法', True),
)

# These conditions must stop automatic entries and require a fresh user start, but
# they do not prove an ambiguous write or account-state integrity conflict.
_SOFT_HALT_PATTERNS = (
    '检测到睡眠或长时间停顿',
    '只读请求失败，不会下单',
    '行情过期，禁止开仓',
    '时钟同步延迟过高',
)

# Locks produced by older builds even though no order/exposure existed.  They may
# be migrated only when current exchange reads prove positions/orders/algos empty
# AND there is no local active parent placeholder.
_LEGACY_FALSE_LOCK_PATTERNS = (
    '没有可识别的本程序仓位/活动订单',
    '没有可识别的本程序仓位',
    '当前已无本程序BTC仓位，无需再次平仓',
    '开仓订单已取消且没有仓位，无需平仓',
    '买卖价差过大',
    '风险预算不足以满足最小下单量',
    '下单数量不足以安全拆分为两笔止盈',
    '无效信号或ATR',
    '止盈止损价格非法',
    '行情过期，禁止开仓',
    '时钟同步延迟过高',
    '检测到睡眠或长时间停顿',
    '首版要求专用子账户、双向持仓模式',
    'API需要交易权限，且不得有提币权限',
    'BTC存在非本程序管理的仓位或挂单',
    'OKX 50123明确拒绝',
    'OKX明确拒绝',
)


def _matches(text, patterns):
    value = str(text or '')
    return any(token in value for token in patterns)


def _log_once(self, key, message, interval=15.0):
    now = time.monotonic()
    cache = getattr(self, '_v135_entry_guard_logs', None)
    if not isinstance(cache, dict):
        cache = {}
        self._v135_entry_guard_logs = cache
    if now - float(cache.get(key, 0.0) or 0.0) >= interval:
        cache[key] = now
        self.emit('log', message)


def apply():
    if getattr(engine.Engine, '_kaytrade_v135_auto_entry_guard_applied', False):
        return

    # ---- 1) Entry-plan conditions: skip instead of poisoning persistent state. ----
    original_make_plan = engine.make_plan

    def make_plan(*args, **kwargs):
        try:
            return original_make_plan(*args, **kwargs)
        except engine.Halt as exc:
            message = str(exc)
            for token, consume_bar in _ENTRY_SKIP_RULES:
                if token in message:
                    raise EntrySkip(message, consume_bar=consume_bar) from None
            raise

    engine.make_plan = make_plan

    # ---- 2) Persistent halt writer: soften read-only/sleep/stale-market stops. ----
    original_halt = engine.Engine.halt

    def halt(self, reason):
        message = str(reason or '')
        if _matches(message, _SOFT_HALT_PATTERNS):
            self.enabled = False
            self.stopped = True
            if hasattr(self, 'startup_buffer_until'):
                self.startup_buffer_until = 0.0
            _log_once(self, 'soft:'+message, message+'；已停止自动新开仓，但未写入永久故障锁；请重新授权后再启动')
            return False
        return original_halt(self, reason)

    engine.Engine.halt = halt

    # ---- 3) Startup/preflight checks are read-only: block start, do not create halt. ----
    original_arm = engine.Engine.arm

    def arm(self, settings):
        had_lock = bool(self.store and self.store.data.get('halt'))
        try:
            return original_arm(self, settings)
        except exchange.NetworkError:
            # App's network-pause path handles this; Engine.halt above keeps GET failures soft.
            raise
        except (engine.Halt, exchange.APIError) as exc:
            # Existing hard locks must still surface unchanged.  A pre-existing active
            # placeholder is also never softened because it may represent a real write.
            if had_lock or (self.store and self.store.data.get('halt')) or (self.store and self.store.data.get('active')):
                raise
            self.enabled = False
            self.stopped = True
            if hasattr(self, 'startup_buffer_until'):
                self.startup_buffer_until = 0.0
            self.emit('log', '自动交易启动检查未通过：'+str(exc)+'；未开仓，未写入永久故障锁')
            return None

    engine.Engine.arm = arm

    # ---- 4) Runtime entry cycle: transient pre-order conditions never become halt. ----
    original_cycle = engine.Engine.cycle

    def cycle(self):
        try:
            return original_cycle(self)
        except EntrySkip as exc:
            if exc.consume_bar and self.store and isinstance(getattr(self, 'market', None), dict):
                bar = self.market.get('bar')
                if bar:
                    self.store.data['last_bar'] = bar
                    self.store.save()
            _log_once(self, 'skip:'+str(exc), '本轮自动开仓跳过：'+str(exc)+'；未写入故障锁')
            return None
        except exchange.APIError as exc:
            # Only clearly read/pre-order freshness errors are softened here.  POST
            # ambiguity/rejections are handled below or by the existing hardening layer.
            message = str(exc)
            if not getattr(exc, 'write_rejected', False) and str(getattr(exc, 'method', '') or '') != 'POST' \
                    and _matches(message, ('行情过期，禁止开仓', '时钟同步延迟过高')):
                self.enabled = False
                self.stopped = True
                if hasattr(self, 'startup_buffer_until'):
                    self.startup_buffer_until = 0.0
                _log_once(self, 'market-read:'+message, message+'；已停止自动新开仓，未写入永久故障锁；请重新授权')
                return None
            raise
        except engine.Halt as exc:
            message = str(exc)
            # v135_patch has already proved explicit entry/set-leverage POST rejection
            # deterministic.  It also clears the parent placeholder when appropriate.
            # With no active placeholder and no existing halt there is nothing ambiguous
            # to acknowledge, so stop auto-entry without manufacturing a permanent lock.
            if _matches(message, ('OKX 50123明确拒绝', 'OKX明确拒绝')) \
                    and self.store and not self.store.data.get('active') and not self.store.data.get('halt'):
                self.enabled = False
                self.stopped = True
                if hasattr(self, 'startup_buffer_until'):
                    self.startup_buffer_until = 0.0
                self.emit('log', message+'；已停止自动新开仓，但未写入永久故障锁；修正原因后重新测试连接/启动即可')
                return None
            raise

    engine.Engine.cycle = cycle

    # ---- 5) Migrate only known old false locks after current exposure is proven empty. ----
    original_connect = engine.Engine.connect

    def connect(self):
        result = original_connect(self)
        if not self.store:
            return result
        reason = engine.normalize_halt_reason(self.store.data.get('halt', ''))
        if reason and _matches(reason, _LEGACY_FALSE_LOCK_PATTERNS) and not self.store.data.get('active'):
            positions = self.x.positions()
            orders = self.x.orders()
            algos = self.x.algos()
            if not positions and not orders and not algos:
                self.store.data['halt'] = ''
                self.store.save()
                self.store.record('V1.3.5自动迁移旧误锁', {'reason': reason})
                self.emit('log', '已确认BTC仓位、普通委托和全部策略委托为空；自动清理旧版误故障锁：'+reason)
        return result

    engine.Engine.connect = connect

    # ---- 6) Keep UI text aligned with the actual final strategy/runtime. ----
    original_emit = app.App.emit

    def app_emit(self, kind, data):
        if kind == 'log' and isinstance(data, str) and '请核对解除故障锁后重新授权' in data:
            if getattr(self, 'engine', None) and self.engine.store and not self.engine.store.data.get('halt'):
                data = data.replace('请核对解除故障锁后重新授权', '自动开仓保持停止，请重新授权')
        return original_emit(self, kind, data)

    app.App.emit = app_emit

    original_app_arm = app.App.arm

    def app_arm(self):
        original_ask = app.simpledialog.askstring

        def askstring(title, prompt, *args, **kwargs):
            text = str(prompt)
            text = text.replace('3.5–4.5=1×仓位 / 5.0–6.5=1.5× / 7.0–10=2×',
                                '1.0–4.5=1×仓位 / 5.0–6.5=1.5× / 7.0–10=2×')
            text = text.replace('15m Setup≥0.5、5m Trigger≥0.5；前方结构<1R禁止开仓。',
                                '15m Setup参与评分；5m Trigger≥0.5；前方结构<1R禁止开仓。')
            return original_ask(title, text, *args, **kwargs)

        app.simpledialog.askstring = askstring
        try:
            return original_app_arm(self)
        finally:
            app.simpledialog.askstring = original_ask

    app.App.arm = app_arm

    engine.Engine._kaytrade_v135_auto_entry_guard_applied = True


apply()

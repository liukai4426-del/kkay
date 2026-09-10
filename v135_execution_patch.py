"""V1.3.5 execution hardening: fail-closed order lifecycle, safe flattening, and clearer state tracking."""
import time
from decimal import Decimal, ROUND_DOWN

UNKNOWN_WRITE_TIMEOUT = 60.0
KNOWN_ORDER_MISSING_TIMEOUT = 30.0
ORDER_MISSING_LOG_INTERVAL = 15.0
CANCEL_CONFIRM_TIMEOUT = 20.0
CLOSE_CONFIRM_TIMEOUT = 20.0
PROTECTION_CONFIRM_TIMEOUT = 15.0
LIVE_ALGO_STATES = {'live', 'effective', 'partially_effective'}


def _explicit_rejection(exc):
    return bool(getattr(exc, 'write_rejected', False))


def _halt_once(self, reason):
    import engine
    current = engine.normalize_halt_reason(self.store.data.get('halt', '')) if self.store else ''
    if current != reason:
        self.halt(reason)


def _set_phase(self, p, phase, **extra):
    if not isinstance(p, dict):
        return
    changed = p.get('phase') != phase
    p['phase'] = phase
    p['phase_at'] = time.time()
    if extra:
        p.update(extra)
        changed = True
    if changed and self.store:
        self.store.save()


def _position_rows(self, p, positions):
    rows = [r for r in positions if r.get('mgnMode') == 'isolated' and r.get('posSide') == p.get('posSide')
            and Decimal(str(r.get('pos') or '0')).copy_abs() > 0]
    if len(rows) != 1:
        raise RuntimeError('本程序仓位不是唯一的同方向逐仓仓位')
    return rows


def _position_size(self, p, positions):
    import engine
    rows = _position_rows(self, p, positions)
    raw = Decimal(str(rows[0].get('pos') or '0')).copy_abs()
    meta = self.x.instrument()
    lot = Decimal(str(meta['lotSz']))
    units = (raw / lot).to_integral_value(rounding=ROUND_DOWN)
    normalized = units * lot
    if normalized <= 0 or normalized != raw:
        raise engine.Halt('BTC仓位数量不符合当前合约lotSz，禁止自动平仓，请核对OKX')
    if normalized > Decimal(str(p['sz'])):
        raise engine.Halt('BTC仓位数量超过本程序记录，禁止自动平仓，请核对OKX')
    return format(normalized, 'f'), rows[0]


def _lookup_parent_order(self, p):
    """Reconcile a parent order without ever auto-releasing a genuinely ambiguous write."""
    import engine
    try:
        return self.x.order(p.get('client_id', ''), p.get('order_id', ''))
    except Exception as exc:
        if str(getattr(exc, 'code', '')) != '51603':
            raise

    now = time.time()
    p['order_missing_checks'] = int(p.get('order_missing_checks') or 0) + 1
    p['order_missing_last'] = now
    last_log = float(p.get('order_missing_log_at') or 0)
    if now - last_log >= ORDER_MISSING_LOG_INTERVAL:
        p['order_missing_log_at'] = now
        self.emit('log', '51603：父订单暂不可查询；正在交叉核对当前委托、历史订单与BTC仓位，不会重复下单')
    self.store.save()

    pending = self.x.orders()
    found = next((row for row in pending if self._same_parent_order(row, p)), None)
    if found:
        if not p.get('order_id') and found.get('ordId'):
            p['order_id'] = str(found['ordId'])
        _set_phase(self, p, 'SUBMITTED')
        return found

    history = self.x.recent_orders() if hasattr(self.x, 'recent_orders') else []
    found = next((row for row in history if self._same_parent_order(row, p)), None)
    if found:
        if not p.get('order_id') and found.get('ordId'):
            p['order_id'] = str(found['ordId'])
        state = str(found.get('state') or '')
        _set_phase(self, p, 'FILLED' if state == 'filled' else 'CANCELED' if state in ('canceled', 'mmp_canceled') else 'SUBMITTED')
        return found

    positions = self.x.positions()
    same = [r for r in positions if r.get('mgnMode') == 'isolated' and r.get('posSide') == p.get('posSide')
            and Decimal(str(r.get('pos') or '0')).copy_abs() > 0]
    if same:
        if len(same) != 1 or Decimal(str(same[0].get('pos') or '0')).copy_abs() > Decimal(str(p['sz'])):
            raise engine.Halt('51603：订单查询不到且BTC仓位与本地记录不一致；已停止新开仓，请立即核对OKX')
        _set_phase(self, p, 'FILLED')
        self.emit('log', '51603交叉核对发现本程序方向BTC逐仓仓位：按已成交继续管理保护单，不重复开仓')
        return {'state': 'filled', 'accFillSz': str(Decimal(str(same[0]['pos'])).copy_abs()),
                'ordId': str(p.get('order_id') or ''), 'clOrdId': p.get('client_id', '')}

    age = now - float(p.get('submitted', now))
    has_ordid = bool(p.get('order_id'))
    timeout = KNOWN_ORDER_MISSING_TIMEOUT if has_ordid else UNKNOWN_WRITE_TIMEOUT
    if age < timeout:
        return None

    if has_ordid:
        raise engine.Halt(
            f'父订单已有ordId，但持续{int(timeout)}秒无法在当前委托、历史订单或BTC仓位中匹配；'
            '禁止重复开仓，请核对OKX')

    _set_phase(self, p, 'SUBMIT_UNKNOWN')
    raise engine.Halt(
        f'开仓POST结果持续{int(timeout)}秒无法确认：无ordId，且当前委托、历史订单、BTC仓位均未匹配；'
        '本地占位继续保留，禁止重复提交。请在OKX核对后再解除故障锁')


def _cancel_pending_entry(self, p, reason):
    import engine
    if p.get('filled') or p.get('cancel_requested'):
        return
    p['cancel_requested'] = time.time()
    p['cancel_reason'] = reason
    _set_phase(self, p, 'CANCEL_PENDING')
    cancel = {'instId': engine.INSTRUMENT}
    if p.get('order_id'):
        cancel['ordId'] = str(p['order_id'])
    else:
        cancel['clOrdId'] = p['client_id']
    try:
        self.x.post('/api/v5/trade/cancel-order', cancel)
    except Exception as exc:
        if _explicit_rejection(exc):
            p['cancel_rejected'] = {'time': time.time(), 'code': str(getattr(exc, 'code', '') or ''), 'message': str(exc)}
            _set_phase(self, p, 'CANCEL_REJECTED')
            self.store.record('限价开仓撤单被拒绝', {'client_id': p.get('client_id'), **p['cancel_rejected']})
            _halt_once(self, '限价开仓撤单被OKX明确拒绝；不自动重试撤单，继续只读核对订单/仓位，请在OKX处理')
            return
        p['cancel_result_unknown'] = True
        _set_phase(self, p, 'CANCEL_UNKNOWN')
        raise
    self.store.record('撤销限价开仓', {'client_id': p['client_id'], 'reason': reason})
    self.emit('log', '[ORDER] CANCEL_SENT：已发送限价开仓撤单；等待OKX确认终态，不会重复发送')


def _submit_position_close(self, p, positions, mode='manual', reason='手动平仓'):
    """Submit exactly one market close after parent entry is terminal."""
    import engine
    config = {
        'manual': ('close_id', 'close_requested', 'close_unknown', 'cl', 'CLOSING_MANUAL'),
        'partial': ('partial_close_id', 'partial_close_requested', 'partial_close_unknown', 'pc', 'CLOSING_PARTIAL'),
        'emergency': ('emergency_close_id', 'emergency_close_requested', 'emergency_close_unknown', 'ec', 'CLOSING_EMERGENCY'),
    }
    if mode not in config:
        raise engine.Halt('未知平仓模式')
    id_key, requested_key, unknown_key, prefix, phase = config[mode]
    if p.get(id_key):
        return False
    size, _ = _position_size(self, p, positions)
    close_id = prefix + __import__('uuid').uuid4().hex[:28]
    p[id_key] = close_id
    p[requested_key] = time.time()
    p[unknown_key] = False
    _set_phase(self, p, phase)
    body = {'instId': engine.INSTRUMENT, 'tdMode': 'isolated', 'posSide': p['posSide'],
            'side': 'sell' if p['posSide'] == 'long' else 'buy', 'ordType': 'market',
            'sz': size, 'clOrdId': close_id}
    try:
        reply = self.x.post('/api/v5/trade/order', body)
    except Exception as exc:
        if _explicit_rejection(exc):
            rejected_id = p.pop(id_key, '')
            p.pop(requested_key, None)
            p[unknown_key] = False
            rejection = {'time': time.time(), 'code': str(getattr(exc, 'code', '') or ''),
                         'message': str(exc), 'client_id': rejected_id, 'reason': reason}
            if mode == 'partial':
                p['partial_close_blocked'] = True
                p['last_partial_close_rejection'] = rejection
                _set_phase(self, p, 'PARTIAL_CLOSE_REJECTED')
                self.store.record('部分成交安全平仓被拒绝', rejection)
                _halt_once(self, '部分成交仓位的市价安全平仓被OKX明确拒绝；不会自动重复提交，请人工核对并处理仓位')
            elif mode == 'emergency':
                p['emergency_close_blocked'] = True
                p['last_emergency_close_rejection'] = rejection
                _set_phase(self, p, 'EMERGENCY_CLOSE_REJECTED')
                self.store.record('保护异常安全平仓被拒绝', rejection)
                _halt_once(self, 'TP/SL保护异常后的市价安全平仓被OKX明确拒绝；不会自动重复提交，请立即人工处理仓位')
            else:
                p['last_close_rejection'] = rejection
                _set_phase(self, p, 'CLOSE_REJECTED')
                self.store.record('手动平仓被OKX明确拒绝', rejection)
                _halt_once(self, '手动市价平仓被OKX明确拒绝；未创建本次平仓单，可在修正原因并核对仓位后再次手动提交')
            return False
        p[unknown_key] = True
        _set_phase(self, p, {'partial':'PARTIAL_CLOSE_UNKNOWN','emergency':'EMERGENCY_CLOSE_UNKNOWN'}.get(mode,'CLOSE_UNKNOWN'))
        raise
    row = reply[0] if isinstance(reply, list) and reply else {}
    if mode == 'emergency':
        p['emergency_close_order_id'] = str(row.get('ordId') or '')
        p['emergency_close_ack_at'] = time.time()
        self.store.save()
    event = {'manual':'手动平仓请求','partial':'部分成交安全平仓请求','emergency':'保护异常自动安全平仓请求'}[mode]
    self.store.record(event, {'client_id': p.get('client_id'), 'close_id': close_id,
                              'ordId': str(row.get('ordId') or ''), 'size': size, 'reason': reason})
    label = '保护异常一次性安全平仓' if mode == 'emergency' else reason
    self.emit('log', '[ORDER] CLOSE_SENT：'+label+'市价请求已发送；等待仓位归零确认，禁止重复发送')
    return True


def _reconcile(self):
    import engine
    state = self.store.data
    p = state.get('active')
    if not p:
        return
    order = self._lookup_parent_order(p)
    if order is None:
        return
    status = str(order.get('state') or '')
    filled = Decimal(str(order.get('accFillSz') or '0')).copy_abs()
    positions = self.x.positions()

    for r in positions:
        pos = Decimal(str(r.get('pos') or '0')).copy_abs()
        if r.get('mgnMode') != 'isolated' or r.get('posSide') != p['posSide'] or pos > Decimal(str(p['sz'])):
            raise engine.Halt('仓位与程序记录不一致，请立即在OKX核对')

    if status in ('live', 'partially_filled'):
        _set_phase(self, p, 'PARTIAL' if status == 'partially_filled' or filled > 0 else 'SUBMITTED')
        if p.get('cancel_requested'):
            if p.get('cancel_rejected'):
                self.emit('position', positions)
                return
            if time.time() - float(p['cancel_requested']) > CANCEL_CONFIRM_TIMEOUT:
                _halt_once(self, '限价撤单结果长时间无法确认；不会再次发送撤单或开仓，请到OKX核对')
            return
        if filled > 0 or positions:
            self._cancel_pending_entry(p, '限价单出现部分成交，先撤销剩余开仓数量')
            return
        if p.get('cancel_on_reconcile') or p.get('manual_flatten_requested'):
            self._cancel_pending_entry(p, '手动停止/平仓要求：先确认撤销剩余开仓委托')
            return
        if time.time() >= float(p.get('expires', p['submitted'] + 300)):
            self._cancel_pending_entry(p, '限价挂单已等待1根5m K线')
        return

    if status in ('canceled', 'mmp_canceled') and filled <= 0:
        if positions:
            raise engine.Halt('限价订单已取消但BTC仓位非空')
        p['phase'] = 'CANCELED'
        self.store.record('限价单未成交', p)
        state['active'] = None
        self.store.save()
        self.emit('log', '[ORDER] CANCELED：限价开仓未成交/已撤销；不计为交易，该信号不重试')
        return

    if status in ('canceled', 'mmp_canceled') and filled > 0 and positions:
        _set_phase(self, p, 'PARTIAL_CANCELED')
        if p.get('partial_close_id'):
            if time.time() - float(p.get('partial_close_requested', time.time())) > CLOSE_CONFIRM_TIMEOUT:
                _halt_once(self, '部分成交安全平仓结果长时间无法确认；禁止重复平仓，请立即到OKX核对')
            return
        if p.get('partial_close_blocked'):
            self.emit('position', positions)
            return
        self._submit_position_close(p, positions, mode='partial', reason='部分成交安全平仓')
        return

    if status not in ('filled', 'canceled', 'mmp_canceled'):
        if time.time() - float(p.get('submitted', time.time())) > 20:
            _halt_once(self, '父订单状态长时间不确定；禁止重复开仓，请到OKX核对')
        return
    if filled <= 0:
        raise engine.Halt('成交状态异常')

    if not p.get('filled'):
        p['filled'] = True
        p['filled_sz'] = float(filled)
        p['filled_at'] = time.time()
    _set_phase(self, p, 'FILLED')

    if not positions:
        if time.time() - float(p.get('filled_at', p['submitted'])) < 10:
            return
        equity, _ = self.x.balance()
        pnl = equity - p['equity_before']
        self._reset_streak_day()
        state['streak'] = state['streak'] + 1 if pnl < 0 else 0
        p['phase'] = 'DONE'
        self.store.record('仓位归零', dict(client_id=p['client_id'], equity_change=pnl, side=p['side'],
                     px=p['px'], sz=p['sz'], score=p.get('score'), phase='DONE'))
        state['active'] = None
        state['last_close'] = time.time()
        self.store.save()
        self.emit('log', f'[ORDER] DONE：仓位已归零；本轮USDT净权益变化 {pnl:+.4f}；中国时间连续亏损 {state["streak"]}/{self.settings.consecutive_losses}')
        return

    for key, requested_key, label in (
        ('emergency_close_id','emergency_close_requested','保护异常安全平仓'),
        ('close_id','close_requested','手动市价平仓'),
    ):
        if p.get(key):
            if time.time() - float(p.get(requested_key, time.time())) > CLOSE_CONFIRM_TIMEOUT:
                _halt_once(self, label+'结果长时间无法确认；不会重复提交，请到OKX核对')
            self.emit('position', positions)
            return

    if p.get('manual_flatten_requested'):
        self._submit_position_close(p, positions, mode='manual', reason='手动平仓')
        return

    if not all(k in p for k in ('tp1','tp2','tp1_sz','tp2_sz','tp1_id','tp2_id','sl_id')):
        raise engine.Halt('检测到旧版活动仓位；V1.3.5不能接管旧TP/SL结构，请先在OKX完成或人工处理该仓位')

    qty, posrow = _position_size(self, p, positions)
    qty_d = Decimal(qty)
    full = Decimal(str(p['sz']))
    remaining = Decimal(str(p['tp2_sz']))
    if qty_d not in (full, remaining):
        raise engine.Halt('分批止盈后的仓位数量与计划不一致，请立即到OKX核对')

    algos = self.x.algos()
    def live(cid):
        return next((a for a in algos if isinstance(a, dict) and a.get('algoClOrdId') == cid
                     and str(a.get('state') or '') in LIVE_ALGO_STATES
                     and a.get('posSide') == p['posSide'] and a.get('side') != p['exchange_side']
                     and a.get('tdMode') == 'isolated'), None)
    t1 = live(p['tp1_id']); t2 = live(p['tp2_id']); sl = live(p['sl_id'])
    tick = Decimal(str(self.x.instrument()['tickSz']))
    def px_equal(a, b):
        try:
            return (Decimal(str(a)) - Decimal(str(b))).copy_abs() <= tick / 2
        except Exception:
            return False
    tp2_ok = bool(t2 and px_equal(t2.get('tpTriggerPx') or 0, p['tp2'])
                  and Decimal(str(t2.get('sz') or '0')) >= Decimal(str(p['tp2_sz']))
                  and str(t2.get('tpOrdPx') or '') == '-1')
    sl_ok = bool(sl and str(sl.get('slOrdPx') or '') == '-1' and str(sl.get('amendPxOnTriggerType') or '') == '1')

    if qty_d == full:
        tp1_ok = bool(t1 and px_equal(t1.get('tpTriggerPx') or 0, p['tp1'])
                      and Decimal(str(t1.get('sz') or '0')) >= Decimal(str(p['tp1_sz']))
                      and str(t1.get('tpOrdPx') or '') == '-1')
        sl_price_ok = sl_ok and px_equal(sl.get('slTriggerPx') or 0, p['sl'])
        protection_ok = tp1_ok and tp2_ok and sl_price_ok
    else:
        if not p.get('tp1_done'):
            p['tp1_done'] = True
            p['tp1_seen_at'] = time.time()
            _set_phase(self, p, 'TP1_DONE')
            self.emit('log', '[ORDER] TP1_DONE：已确认剩余50%仓位；正在核对保本SL')
        entry_avg = Decimal(str(posrow.get('avgPx') or p['px']))
        sl_px = Decimal(str(sl.get('slTriggerPx') or '0')) if sl else Decimal('0')
        tolerance = max(tick, entry_avg * Decimal('0.00000001'))
        be_ok = sl_ok and (sl_px - entry_avg).copy_abs() <= tolerance
        protection_ok = tp2_ok and be_ok
        if protection_ok and not p.get('breakeven_verified'):
            p['breakeven_verified'] = True
            self.store.save()
            self.emit('log', f'[ORDER] BREAKEVEN：已核对剩余50%保本SL={sl_px}（成交均价 {entry_avg}）')

    if protection_ok:
        if not p.get('protected'):
            p['protected'] = True
            _set_phase(self, p, 'PROTECTED' if not p.get('tp1_done') else 'TP1_PROTECTED')
            self.emit('log', '[ORDER] PROTECTED：TP1/TP2均为触发后市价止盈，SL为市价止损，保护结构已核对')
    else:
        grace = float(p.get('tp1_seen_at', p.get('filled_at', p['submitted'])))
        if time.time() - grace > PROTECTION_CONFIRM_TIMEOUT:
            reason = 'V1.3.5分批TP/保本SL保护状态无法核实；已停止新开仓，请立即到OKX核对'
            _halt_once(self, reason)
            if not p.get('emergency_close_id') and not p.get('emergency_close_blocked') \
                    and not p.get('close_id') and not p.get('partial_close_id'):
                self._submit_position_close(p, positions, mode='emergency', reason='TP/SL保护超过15秒无法核实')
    self.emit('position', positions)


def _flatten(self):
    import engine
    self.stop()
    p = self.store.data.get('active') if self.store else None
    if not p:
        raise engine.Halt('没有可识别的本程序仓位/活动订单')
    if p.get('emergency_close_id'):
        raise engine.Halt('已有保护异常自动安全平仓请求；为防重复平仓，当前禁止再次发送')
    if p.get('partial_close_id'):
        raise engine.Halt('已有部分成交安全平仓请求结果未确认；为防重复平仓，当前禁止再次发送')
    if p.get('close_id'):
        raise engine.Halt('已有手动平仓请求，禁止重复发送；请等待仓位归零或到OKX核对')

    order = self._lookup_parent_order(p)
    if order is None:
        raise engine.Halt('父开仓订单结果仍在核对中；为防重复/反向仓位，暂不发送手动平仓')
    status = str(order.get('state') or '')
    filled = Decimal(str(order.get('accFillSz') or '0')).copy_abs()
    positions = self.x.positions()

    if status in ('live', 'partially_filled'):
        p['manual_flatten_requested'] = True
        p['cancel_on_reconcile'] = True
        self.store.save()
        if not p.get('cancel_requested'):
            self._cancel_pending_entry(p, '用户要求平仓：先撤销尚未终态的开仓委托')
        self.emit('log', '[ORDER] FLATTEN_WAIT：开仓委托尚未终态，已先执行撤单；确认终态后再平已成交仓位')
        return

    if status in ('canceled', 'mmp_canceled') and filled <= 0:
        if positions:
            raise engine.Halt('父开仓订单显示未成交取消，但BTC仓位非空；禁止自动平仓，请核对OKX')
        raise engine.Halt('开仓订单已取消且没有仓位，无需平仓')

    if not positions:
        raise engine.Halt('当前已无本程序BTC仓位，无需再次平仓')

    p['manual_flatten_requested'] = True
    self.store.save()
    self._submit_position_close(p, positions, mode='manual', reason='手动平仓')


def _record_flat_trade_if_proven(self, p, order):
    """Record PnL only when an exchange order proves the parent actually filled."""
    if not order or Decimal(str(order.get('accFillSz') or '0')).copy_abs() <= 0:
        return
    equity, _ = self.x.balance()
    pnl = equity - float(p.get('equity_before', equity))
    self._reset_streak_day()
    self.store.data['streak'] = self.store.data.get('streak', 0) + 1 if pnl < 0 else 0
    self.store.record('人工核对平仓', dict(client_id=p.get('client_id',''), equity_change=pnl,
                      side=p.get('side',''), px=p.get('px'), sz=p.get('sz'), score=p.get('score')))


def _acknowledge(self):
    """Unlock only after current, historical and position/algo evidence all reconcile."""
    import engine
    if self.enabled:
        raise engine.Halt('先停止自动开仓')

    positions = self.x.positions()
    pending = self.x.orders()
    algos = self.x.algos()
    if positions or pending or algos:
        raise engine.Halt('账户仍有BTC仓位/普通委托/策略委托；请先在OKX人工处理')

    p = self.store.data.get('active') if self.store else None
    if p:
        direct = None
        try:
            direct = self.x.order(p.get('client_id',''), p.get('order_id',''))
        except Exception as exc:
            if str(getattr(exc,'code','')) != '51603':
                raise

        history = self.x.recent_orders() if hasattr(self.x, 'recent_orders') else []
        historical = next((row for row in history if self._same_parent_order(row, p)), None)
        evidence = direct or historical
        if evidence:
            status = str(evidence.get('state') or '')
            if status not in ('filled','canceled','mmp_canceled'):
                raise engine.Halt('原开仓订单仍不是终态，不可解除故障锁')
            self._record_flat_trade_if_proven(p, evidence)
        else:
            age = time.time() - float(p.get('submitted', time.time()))
            if not p.get('order_id') and age < UNKNOWN_WRITE_TIMEOUT:
                raise engine.Halt(f'开仓POST结果仍在{int(UNKNOWN_WRITE_TIMEOUT)}秒安全核对窗口内；暂不可清除本地占位')
            if p.get('order_id') and age < KNOWN_ORDER_MISSING_TIMEOUT:
                raise engine.Halt(f'已取得ordId但仍在{int(KNOWN_ORDER_MISSING_TIMEOUT)}秒核对窗口内；暂不可解除故障锁')
            self.store.record('人工确认无交易所订单', {
                'client_id': p.get('client_id',''), 'order_id': p.get('order_id',''),
                'phase': p.get('phase',''), 'age_seconds': age,
                'checked': ['orders-pending','orders-history','order-detail','positions','algo-pending'],
            })

    self.store.data['active'] = None
    self.store.data['halt'] = ''
    self.store.save()
    self.emit('log', '故障锁已解除：已交叉核对当前委托、历史订单、BTC仓位与全部策略委托；每日权益基准、连续亏损计数与信号去重仍保留')


def apply():
    import engine
    import exchange

    if getattr(engine.Engine, '_kaytrade_v135_execution_patch_applied', False):
        return

    original_store_save = engine.Store.save
    def store_save(self):
        p = self.data.get('active')
        if isinstance(p, dict) and p.get('submitted'):
            if not p.get('phase'):
                p['phase'] = 'SUBMITTING'
                p['phase_at'] = time.time()
            if p.get('order_id') and p.get('phase') in ('SUBMITTING', 'SUBMIT_UNKNOWN'):
                p['phase'] = 'SUBMITTED'
                p['phase_at'] = time.time()
        return original_store_save(self)
    engine.Store.save = store_save

    original_post = exchange.Exchange.post
    def post(self, path, body):
        try:
            return original_post(self, path, body)
        except Exception as exc:
            if path != '/api/v5/account/set-leverage' or _explicit_rejection(exc):
                raise
            try:
                infos = self.get('/api/v5/account/leverage-info',
                                 {'instId': body.get('instId'), 'mgnMode': body.get('mgnMode')}, True)
                expected_side = str(body.get('posSide') or '')
                expected_lever = float(body.get('lever'))
                if any(str(i.get('posSide') or '') == expected_side
                       and str(i.get('mgnMode') or '') == str(body.get('mgnMode') or '')
                       and float(i.get('lever') or 0) == expected_lever for i in infos):
                    self.network_event('杠杆写入响应未知，但回读确认已生效')
                    return [{'sCode': '0', 'confirmedByReadback': '1'}]
            except Exception:
                pass
            raise exc
    exchange.Exchange.post = post

    original_arm = engine.Engine.arm
    def arm(self, settings):
        self.x.sync_time()
        self.x.instrument()
        self.x.ticker()
        result = original_arm(self, settings)
        if self.enabled:
            self.emit('log', '自动交易健康检查通过：OKX时间、合约状态、行情、账户模式、API Trade权限、BTC仓位/普通委托/全部Algo均已核对')
        return result
    engine.Engine.arm = arm

    original_cycle = engine.Engine.cycle
    def cycle(self):
        try:
            result = original_cycle(self)
        except Exception as exc:
            p = self.store.data.get('active') if self.store else None
            if isinstance(p, dict) and not p.get('order_id') and not _explicit_rejection(exc) \
                    and str(getattr(exc, 'path', '') or '') == '/api/v5/trade/order':
                p['write_result_unknown'] = True
                p['last_submit_error'] = str(exc)
                _set_phase(self, p, 'SUBMIT_UNKNOWN')
            raise
        p = self.store.data.get('active') if self.store else None
        if isinstance(p, dict) and p.get('order_id') and p.get('phase') in (None, 'SUBMITTING', 'SUBMIT_UNKNOWN'):
            _set_phase(self, p, 'SUBMITTED')
        return result
    engine.Engine.cycle = cycle

    engine.Engine._lookup_parent_order = _lookup_parent_order
    engine.Engine._cancel_pending_entry = _cancel_pending_entry
    engine.Engine._submit_position_close = _submit_position_close
    engine.Engine.reconcile = _reconcile
    engine.Engine.flatten = _flatten
    engine.Engine.acknowledge = _acknowledge
    engine.Engine._execution_halt_once = _halt_once
    engine.Engine._kaytrade_v135_execution_patch_applied = True

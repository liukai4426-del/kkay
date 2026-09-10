"""KAYTRADE V1.3.5 runtime refinements and safety hotfixes."""
import copy
import json
import math
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

STARTUP_BUFFER_SECONDS = 5.0
OLD_THRESHOLD_LABEL = '自动开仓评分阈值 3.5—10（0.5步进）'
NEW_THRESHOLD_LABEL = '自动开仓评分阈值 1—10（0.5步进）'
DAILY_STOP_TEXT = '达到中国时间日内权益回撤上限'


class DailyRiskStop(RuntimeError):
    """Daily drawdown is a trading stop, not a system/integrity fault."""


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


def _is_explicit_rejection(exc):
    """Only a parsed POST rejection may prove that an exchange write was not accepted."""
    return bool(getattr(exc, 'write_rejected', False))


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


def _china_day():
    return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')


def _daily(self, equity):
    """Latch a daily new-entry stop without turning it into a permanent fault lock."""
    import engine

    if not math.isfinite(equity) or equity <= 0:
        raise engine.Halt('账户权益无效')
    state = self.store.data
    day = _china_day()
    if state.get('day') != day:
        state.update(day=day, peak=equity, daily_stop_day='', daily_notice_day='')
    peak = float(state.get('peak') or equity)
    state['peak'] = max(equity, peak)
    self.store.save()

    if state.get('daily_stop_day') == day:
        raise DailyRiskStop('中国时间本日已达到权益回撤上限；停止新开仓，原有TP/SL继续生效，次日自动恢复')

    remaining = self.settings.daily_loss - max(0.0, state['peak'] - equity)
    if remaining <= 0:
        state['daily_stop_day'] = day
        self.store.save()
        raise DailyRiskStop('中国时间本日已达到权益回撤上限；停止新开仓，原有TP/SL继续生效，次日自动恢复')
    return remaining


def _check_latest(bar, timestamp, now, step):
    """Use OKX candle start timestamps; distinguish normal close settlement from true lag."""
    import candles

    expected = candles.expected_bar(now, step)
    if timestamp == expected:
        return

    latest_close = datetime.fromtimestamp((timestamp + step) / 1000, timezone.utc).strftime('%H:%M:%S UTC')
    expected_close = datetime.fromtimestamp((expected + step) / 1000, timezone.utc).strftime('%H:%M:%S UTC')
    current = datetime.fromtimestamp(now, timezone.utc).strftime('%H:%M:%S UTC')
    boundary = expected + step

    if timestamp == expected - step and 0 <= now * 1000 - boundary <= 45000:
        raise candles.CandlePending(
            f'{bar} 刚收盘，等待OKX确认最新K线：上一根已确认收盘 {latest_close}，'
            f'本应确认收盘 {expected_close}，交易所时间 {current}；最多等待45秒，不使用未确认K线'
        )

    lag = max(0.0, (expected - timestamp) / 1000)
    raise candles.CandleLag(
        f'{bar} K线持续过期/时间异常：上一根已确认收盘 {latest_close}，'
        f'本应确认收盘 {expected_close}，交易所时间 {current}，落后 {lag:.0f}秒；不使用旧信号'
    )


def _market_split_tp_post(original_post):
    """Force split TP to market and validate every successful trade-order acknowledgement."""
    def post(self, path, body):
        payload = body
        if path == '/api/v5/trade/order' and isinstance(body, dict) and body.get('attachAlgoOrds'):
            payload = copy.deepcopy(body)
            tps = [item for item in payload.get('attachAlgoOrds', []) if item.get('tpTriggerPx') is not None]
            if len(tps) >= 2:
                for item in tps:
                    item['tpOrdKind'] = 'condition'
                    item['tpOrdPx'] = '-1'

        reply = original_post(self, path, payload)
        if path != '/api/v5/trade/order':
            return reply

        # A HTTP/OKX success without a usable ordId is not proof of failure.
        # Treat response-integrity problems as UNKNOWN, preserving the caller's
        # pre-write idempotency marker and forbidding an automatic retry.
        import exchange
        row = reply[0] if isinstance(reply, list) and reply else None
        if not isinstance(row, dict):
            raise exchange.APIError(
                'OKX交易请求响应为空/结构异常；写入结果需核对，禁止重复提交',
                method='POST', path=path)
        order_id = str(row.get('ordId') or '')
        if not order_id:
            raise exchange.APIError(
                'OKX交易请求响应缺少ordId；写入结果需核对，禁止重复提交',
                method='POST', path=path)
        sent_client = str(payload.get('clOrdId') or '') if isinstance(payload, dict) else ''
        returned_client = str(row.get('clOrdId') or '')
        if sent_client and returned_client and sent_client != returned_client:
            raise exchange.APIError(
                'OKX交易请求返回的clOrdId与本地请求不一致；写入结果需核对，禁止重复提交',
                method='POST', path=path)
        return reply
    return post


def apply():
    """Apply the V1.3.5 refinements once, before the desktop UI is instantiated."""
    import app
    import candles
    import engine
    import exchange
    import strategy

    if getattr(engine.Engine, '_kaytrade_v135_patch_applied', False):
        return

    # 1) Execution threshold can be selected from 1 to 10, preserving 0.5-point steps.
    engine.Settings.validate = _validate_settings

    # 2) 15m Setup remains part of score but no longer vetoes an entry.
    original_strategy_signal = strategy.signal
    relaxed_signal = _remove_15m_setup_gate(original_strategy_signal, strategy)
    strategy.signal = relaxed_signal
    engine.signal = relaxed_signal

    # 3) Preserve confirmed-candle safety but distinguish normal <=45s settlement from real lag.
    candles.check_latest = _check_latest
    engine.check_latest = _check_latest

    # 4) V1.3.5 requires market exits. In OKX split-TP mode, both condition TP
    # legs use tpOrdPx=-1 while the SL already uses slOrdPx=-1. The wrapper also
    # validates ordId/clOrdId for parent, partial-close and manual-close orders.
    original_exchange_post = exchange.Exchange.post
    exchange.Exchange.post = _market_split_tp_post(original_exchange_post)

    original_app_init = app.App.__init__

    def app_init(self, *args, **kwargs):
        original_app_init(self, *args, **kwargs)
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
        for widget in _widgets(self.root):
            try:
                if widget.cget('text') == OLD_THRESHOLD_LABEL:
                    widget.configure(text=NEW_THRESHOLD_LABEL)
            except Exception:
                pass

    app.App.__init__ = app_init

    # 5) Daily drawdown is a day-level stop, not a permanent fault lock.
    engine.Engine.daily = _daily

    original_engine_init = engine.Engine.__init__
    original_connect = engine.Engine.connect
    original_arm = engine.Engine.arm
    original_cycle = engine.Engine.cycle
    original_stop = engine.Engine.stop
    original_reconcile = engine.Engine.reconcile
    original_flatten = engine.Engine.flatten

    def engine_init(self, *args, **kwargs):
        original_engine_init(self, *args, **kwargs)
        self.startup_buffer_until = 0.0

    def connect(self):
        result = original_connect(self)
        if self.store:
            reason = str(self.store.data.get('halt') or '')
            if DAILY_STOP_TEXT in reason:
                self.store.data['halt'] = ''
                if self.store.data.get('day') == _china_day():
                    self.store.data['daily_stop_day'] = _china_day()
                self.store.save()
                self.emit('log', '旧版日内回撤故障锁已迁移为“当日停止新开仓”；权益峰值基准保留，次日自动恢复')
        return result

    def arm(self, settings):
        try:
            result = original_arm(self, settings)
        except DailyRiskStop as exc:
            self.enabled = False
            self.startup_buffer_until = 0.0
            day = _china_day()
            if self.store and self.store.data.get('daily_notice_day') != day:
                self.store.data['daily_notice_day'] = day
                self.store.save()
                self.emit('log', str(exc))
            return None
        self.startup_buffer_until = time.monotonic() + STARTUP_BUFFER_SECONDS
        self.emit('log', '自动交易已开启；启动缓冲5秒，期间只更新行情/核对仓位，不允许自动开仓')
        return result

    def _handle_daily_stop(self, exc):
        day = _china_day()
        if self.store and self.store.data.get('daily_notice_day') != day:
            self.store.data['daily_notice_day'] = day
            self.store.save()
            self.emit('log', str(exc))
        return None

    def _handle_explicit_rejection(self, exc):
        p = self.store.data.get('active') if self.store else None
        cleared = False
        # The parent order placeholder is written immediately before POST. If
        # OKX explicitly rejects that POST and no ordId/fill exists, there is no
        # exchange order to reconcile and keeping active would create a 51603 loop.
        if isinstance(p, dict) and not p.get('order_id') and not p.get('filled'):
            snapshot = {
                'client_id': p.get('client_id', ''),
                'side': p.get('side', ''),
                'score': p.get('score'),
                'submitted': p.get('submitted'),
                'code': str(getattr(exc, 'code', '') or ''),
                'reason': str(exc),
            }
            self.store.data['active'] = None
            self.store.save()
            self.store.record('OKX明确拒绝开仓', snapshot)
            cleared = True

        self.enabled = False
        self.startup_buffer_until = 0.0
        code = str(getattr(exc, 'code', '') or '')
        detail = '；已清理本地未成交占位，不进入51603订单核对' if cleared else '；本地活动状态保留，禁止重复提交'
        if code == '50123':
            message = 'OKX 50123：API Key没有BTC/对应交易市场的下单权限；本次写入请求被OKX明确拒绝，没有产生本次新开仓订单'
        else:
            message = 'OKX明确拒绝交易请求'+(f'（错误码 {code}）' if code else '')+'：'+str(exc)
        raise engine.Halt(message + detail + '；已停止新开仓，请核对原因后重新测试连接/启动') from None

    def run_original_cycle(self):
        try:
            return original_cycle(self)
        except DailyRiskStop as exc:
            return _handle_daily_stop(self, exc)
        except Exception as exc:
            if _is_explicit_rejection(exc):
                return _handle_explicit_rejection(self, exc)
            raise

    def cycle(self):
        until = float(getattr(self, 'startup_buffer_until', 0.0) or 0.0)
        if self.enabled and until:
            now = time.monotonic()
            if now < until:
                self.enabled = False
                try:
                    return run_original_cycle(self)
                finally:
                    if not self.stopped and self.store and not self.store.data.get('halt'):
                        self.enabled = True
            self.startup_buffer_until = 0.0
            self.emit('log', '启动缓冲5秒结束；自动开仓现已启用')
        return run_original_cycle(self)

    def reconcile(self):
        result = original_reconcile(self)
        p = self.store.data.get('active') if self.store else None
        # Verify once, immediately after the base engine has confirmed the full
        # protection set. This catches an accidental regression back to limit TP.
        if p and p.get('protected') and p.get('filled') and not p.get('market_tp_verified') and not p.get('tp1_done'):
            algos = self.x.algos()
            by_id = {a.get('algoClOrdId'): a for a in algos if isinstance(a, dict) and a.get('state') == 'live'}
            t1 = by_id.get(p.get('tp1_id')); t2 = by_id.get(p.get('tp2_id'))
            if not t1 or not t2:
                return result
            if str(t1.get('tpOrdPx') or '') != '-1' or str(t2.get('tpOrdPx') or '') != '-1':
                self.halt('V1.3.5检测到TP不是市价执行；已锁住新开仓，请立即核对OKX保护单')
                return result
            p['market_tp_verified'] = True
            self.store.save()
            self.emit('log', '已核对TP1/TP2均为触发后市价止盈（tpOrdPx=-1），SL为市价止损；分批保护结构有效')
        return result

    def flatten(self):
        try:
            return original_flatten(self)
        except Exception as exc:
            # Manual close persists close_id before POST to prevent duplicates.
            # If OKX explicitly rejects that POST, the close order definitely was
            # not created, so release only close_id and let the user retry after
            # fixing the cause. Unknown network/write outcomes keep close_id.
            if _is_explicit_rejection(exc) and self.store:
                p = self.store.data.get('active')
                if isinstance(p, dict) and p.get('close_id'):
                    rejected_id = p.get('close_id')
                    p.pop('close_id', None)
                    p.pop('close_requested', None)
                    p['last_close_rejection'] = {
                        'time': time.time(),
                        'code': str(getattr(exc, 'code', '') or ''),
                        'message': str(exc),
                        'client_id': rejected_id,
                    }
                    self.store.save()
                    self.store.record('手动平仓被OKX明确拒绝', p['last_close_rejection'])
                    self.emit('log', '手动市价平仓被OKX明确拒绝；未创建平仓单，已释放本地close_id，可在修正原因后再次手动提交')
            raise

    def stop(self):
        self.startup_buffer_until = 0.0
        return original_stop(self)

    engine.Engine.__init__ = engine_init
    engine.Engine.connect = connect
    engine.Engine.arm = arm
    engine.Engine.cycle = cycle
    engine.Engine.reconcile = reconcile
    engine.Engine.flatten = flatten
    engine.Engine.stop = stop
    engine.Engine._kaytrade_v135_patch_applied = True
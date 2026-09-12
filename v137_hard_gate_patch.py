"""KAYTRADE V1.3.7 layered-entry rule completion.

Implements the recorded-but-missing V1.3.7 rules without weakening the existing
OKX reconciliation / fail-closed safety layer:
- fixed 4.0/10 minimum score
- 1m Trigger = KDJ cross OR EMA20 reclaim OR reversal candle; EMA direction is not a trigger and adds no score
- 5m MACD must agree with the intended side (hard gate) and adds +1
- 1H aligned trend +2, neutral 0, opposite trend = hard block
- forward strong structure must leave >=1.3R
- higher score wins when both sides qualify; equal scores wait
- latest CLOSED 1m candle is the dedupe key; max one new entry/add-on per 1m bar
- entry LIMIT order expires after 60 seconds
- signal-price drift check uses 0.3 x 1H ATR
- Tier1/Tier2/Tier3 each at most once per cycle; no backfilling lower tiers
- all 1D indicators removed from scoring and filtering
"""
import math
import time
import uuid

from core import indicators, ema
from v137_cancel_fix import apply as apply_v137_cancel_fix
apply_v137_cancel_fix()

import app
import engine
import strategy
import v135_patch
import v137_strategy_patch as v137

ONE_MINUTE_STEP = 60_000
V137_THRESHOLD = 4.0
FRONT_MIN_R = 1.3
EXPECTED_COST_MIN = 1.20


def _tier(total):
    value = float(total or 0.0)
    if value >= 7.5:
        return 3, 2.0, '三级超强信号'
    if value >= 5.5:
        return 2, 1.5, '二级强信号'
    if value >= 4.0:
        return 1, 1.0, '一级开仓信号'
    return 0, 0.0, '未达开仓线'


def _tier_limit(tier):
    return {1: 1, 2: 1, 3: 1}.get(int(tier or 0), 0)


def _allow_entry(active, tier, bar):
    tier = int(tier or 0)
    if tier <= 0:
        return False, '评分未达4.0'
    if not isinstance(active, dict):
        return True, ''
    counts = active.get('tier_counts') or {}
    highest = int(active.get('highest_tier') or 0)
    if active.get('last_entry_bar') == bar:
        return False, '本根已收盘1m K线已提交过一次开仓/加仓'
    if tier < highest:
        return False, '评分已从更高级别回落，不回补低Tier仓位'
    if int(counts.get(str(tier), 0) or 0) >= 1:
        return False, '当前Tier本周期已经开过一次'
    if tier == 1 and active.get('legs'):
        return False, 'Tier1只允许空仓首笔，不在已有持仓后回补'
    if sum(int(counts.get(str(x), 0) or 0) for x in (1, 2, 3)) >= 3:
        return False, '本周期最多3次入场事件'
    return True, ''


def _ema_series(values, period):
    return ema([float(v) for v in values], period)


def _macd(rows):
    close = [float(r['c']) for r in rows]
    e12 = _ema_series(close, 12)
    e26 = _ema_series(close, 26)
    dif = [a-b for a, b in zip(e12, e26)]
    dea = _ema_series(dif, 9)
    hist = [2*(a-b) for a, b in zip(dif, dea)]
    return {'dif': dif[-1], 'dea': dea[-1], 'hist': hist[-1], 'prev_hist': hist[-2]}


def _trend_components(rows, current, previous, buy):
    macd = _macd(rows)
    if buy:
        macd_ok = macd['dif'] > macd['dea'] and macd['hist'] > 0 and macd['hist'] >= macd['prev_hist']
        boll_ok = float(rows[-1]['c']) > float(current['middle']) and float(current['middle']) > float(previous['middle'])
    else:
        macd_ok = macd['dif'] < macd['dea'] and macd['hist'] < 0 and macd['hist'] <= macd['prev_hist']
        boll_ok = float(rows[-1]['c']) < float(current['middle']) and float(current['middle']) < float(previous['middle'])
    return macd_ok, boll_ok, macd


def _one_minute_trigger(rows, current, previous, buy):
    ema_reclaim = strategy._ema_reclaim(rows, current, previous, buy)
    kdj = current['cross_up'] if buy else current['cross_down']
    reversal = strategy._reversal(rows, buy)
    valid = bool(ema_reclaim or kdj or reversal)
    return valid, {
        'ema_reclaim': bool(ema_reclaim),
        'kdj': bool(kdj),
        'reversal': bool(reversal),
        'ema_direction': False,
    }


def _trend_state(data, current, buy):
    candle = data[-1]
    aligned = (
        candle['c'] > current['ema200'] and current['ema20'] > current['ema50'] and current['up']
    ) if buy else (
        candle['c'] < current['ema200'] and current['ema20'] < current['ema50'] and current['down']
    )
    opposite = (
        candle['c'] < current['ema200'] and current['ema20'] < current['ema50'] and current['down']
    ) if buy else (
        candle['c'] > current['ema200'] and current['ema20'] > current['ema50'] and current['up']
    )
    return bool(aligned), bool(opposite)


def _rsi_penalty(m15, m5, buy):
    a, b = float(m15['rsi']), float(m5['rsi'])
    if buy:
        if a > 80 and b > 80:
            return -2.0
        if a > 75 and b > 75:
            return -1.0
    else:
        if a < 20 and b < 20:
            return -2.0
        if a < 25 and b < 25:
            return -1.0
    return 0.0


def _zones(rows, current, lookback):
    return strategy._zones(rows, float(current['atr']), lookback)


def _nearest(zones, price, kind, ahead=None, strong_only=False):
    if strong_only:
        zones = [z for z in zones if z.get('strong')]
    return strategy._nearest(zones, price, kind, ahead)


def strict_signal(hour, quarter, five, one, stop_atr=1.0, four=None):
    four = hour if four is None else four
    h, m, f, o, q = indicators(hour), indicators(quarter), indicators(five), indicators(one), indicators(four)
    pf, pm, po = indicators(five[:-1]), indicators(quarter[:-1]), indicators(one[:-1])
    price = float(one[-1]['c'])
    hz, mz, qz = _zones(hour, h, 120), _zones(quarter, m, 160), _zones(four, q, 180)
    results = {}

    for side in ('做多', '做空'):
        buy = side == '做多'
        trigger_valid, trigger_detail = _one_minute_trigger(one, o, po, buy)
        macd5, boll5, macd5_values = _trend_components(five, f, pf, buy)
        macd15, boll15, macd15_values = _trend_components(quarter, m, pm, buy)
        trend1_aligned, trend1_opposite = _trend_state(hour, h, buy)
        trend4_aligned, trend4_opposite = _trend_state(four, q, buy)

        setup, setup_detail, volume_ratio = strategy._setup(quarter, m, buy)
        favorable_kind = 'support' if buy else 'resistance'
        adverse_kind = 'resistance' if buy else 'support'
        ahead = 'above' if buy else 'below'

        fav15 = _nearest(mz, price, favorable_kind)
        favorable15 = .5 if fav15 and abs(float(fav15['price'])-price)/float(m['atr']) <= .25 else 0.0

        bad1 = _nearest(hz, price, adverse_kind, ahead)
        bad4 = _nearest(qz, price, adverse_kind, ahead)
        structure1 = -1.0 if bad1 and abs(float(bad1['price'])-price)/float(h['atr']) <= .25 else 0.0
        structure4 = -1.5 if bad4 and abs(float(bad4['price'])-price)/float(q['atr']) <= .25 else 0.0

        forward = []
        for tf, zs in (('1H', hz), ('15m', mz)):
            z = _nearest(zs, price, adverse_kind, ahead, strong_only=True)
            if z:
                forward.append((abs(float(z['price'])-price), tf, z))
        front = min(forward, key=lambda x: x[0]) if forward else None
        risk = float(m['atr']) * float(stop_atr)
        front_r = front[0] / risk if front and risk > 0 else math.inf
        space_ok = front_r >= FRONT_MIN_R

        rsi = _rsi_penalty(m, f, buy)
        trend1_score = 2.0 if trend1_aligned else 0.0
        trend4_score = 1.0 if trend4_aligned else (-1.0 if trend4_opposite else 0.0)
        raw = (
            setup + favorable15 +
            (1.0 if macd5 else 0.0) + (1.0 if boll5 else 0.0) +
            (1.0 if macd15 else 0.0) + (1.0 if boll15 else 0.0) +
            trend1_score + trend4_score + rsi + structure1 + structure4
        )
        total = max(0.0, min(10.0, round(raw*2)/2))
        tier, multiplier, level = _tier(total)
        gate = bool(trigger_valid and macd5 and not trend1_opposite and space_ok)
        eligible = bool(gate and tier > 0)

        if not trigger_valid:
            reason = '1m Trigger无效：KDJ / EMA20回收 / 反转K线均未触发，禁止开仓'
        elif not macd5:
            reason = '5m MACD与开仓方向不一致，禁止开仓'
        elif trend1_opposite:
            reason = '1H趋势与开仓方向相反，硬性禁止开仓'
        elif not space_ok:
            reason = f'前方有效空间仅 {front_r:.2f}R < 1.3R，禁止开仓'
        elif tier <= 0:
            reason = f'{total:g}/10，未达固定开仓门槛4.0'
        else:
            reason = f'{level} · {total:g}/10；1m Trigger、5m MACD、1H方向与1.3R空间硬条件全部通过'

        items = [
            ('1H趋势（顺势）', trend1_score, 2.0),
            ('4H趋势', trend4_score, 1.0),
            ('15m有利支撑/阻力结构', favorable15, .5),
            ('15m Setup（BOLL/量/KDJ/反转）', setup, 1.5),
            ('5m MACD方向确认', 1.0 if macd5 else 0.0, 1.0),
            ('5m BOLL方向', 1.0 if boll5 else 0.0, 1.0),
            ('15m MACD', 1.0 if macd15 else 0.0, 1.0),
            ('15m BOLL', 1.0 if boll15 else 0.0, 1.0),
            ('5m+15m RSI过热/过冷惩罚', rsi, 0),
            ('靠近1H不利支撑/阻力', structure1, 0),
            ('靠近4H不利支撑/阻力', structure4, 0),
        ]
        results[side] = {
            'total': total, 'raw': raw, 'gate': gate, 'eligible': eligible,
            'required': V137_THRESHOLD, 'signal_tier': tier,
            'position_multiplier': multiplier if eligible else 0.0,
            'level': f'{level} · {multiplier:g}×仓位' if tier else level,
            'reason': reason, 'items': items,
            'layers': {
                'trigger': 1.0 if trigger_valid else 0.0,
                'setup': setup, 'macd_5m': 1.0 if macd5 else 0.0,
                'boll_5m': 1.0 if boll5 else 0.0,
                'macd_15m': 1.0 if macd15 else 0.0,
                'boll_15m': 1.0 if boll15 else 0.0,
                'trend_1h': trend1_score, 'trend_4h': trend4_score,
                'structure_15m': favorable15, 'rsi_penalty': rsi,
                'structure_penalty_1h': structure1, 'structure_penalty_4h': structure4,
            },
            'confirmations': {
                '1m_trigger': trigger_valid, 'trigger': trigger_detail,
                '5m_macd': macd5, '5m_boll': boll5,
                '15m_macd': macd15, '15m_boll': boll15,
                '1H_aligned': trend1_aligned, '1H_countertrend': trend1_opposite,
                '4H_aligned': trend4_aligned, '4H_countertrend': trend4_opposite,
                'front_r': front_r, 'front_space_ok': space_ok,
                'setup': setup_detail, 'volume_ratio_15m': volume_ratio,
                'macd_5m_values': macd5_values, 'macd_15m_values': macd15_values,
            },
            'structure': {
                'front_r': front_r,
                'front': ({'timeframe': front[1], **front[2]} if front else None),
                'favorable_15m': fav15,
                'adverse_1h': bad1, 'adverse_4h': bad4,
                'blocked': not space_ok,
            },
        }

    qualified = [side for side in ('做多', '做空') if results[side]['eligible']]
    if len(qualified) == 1:
        selected = qualified[0]
    elif len(qualified) == 2:
        a, b = results[qualified[0]]['total'], results[qualified[1]]['total']
        selected = '观望' if abs(a-b) <= 1e-9 else max(qualified, key=lambda s: results[s]['total'])
    else:
        selected = '观望'
    why = results[selected]['reason'] if selected != '观望' else ' / '.join(s+': '+results[s]['reason'] for s in ('做多','做空'))
    return {'q': q, 'h': h, 'm': m, 'f': f, 'o': o, 'side': selected, 'why': why, 'scores': results, 'threshold': V137_THRESHOLD, 'score_max': 10.0}


def _expected_1m(self):
    return int(self.market_now()*1000)//ONE_MINUTE_STEP*ONE_MINUTE_STEP-ONE_MINUTE_STEP


def _refresh_market(self):
    q = self.x.candles('4H')
    h = self.x.candles('1H')
    m = self.x.candles('15m')
    f = self.x.candles('5m')
    o = self.x.candles('1m')
    self._verify_latest('4H', q[-1]['t'], 14_400_000)
    self._verify_latest('1H', h[-1]['t'], 3_600_000)
    self._verify_latest('15m', m[-1]['t'], 900_000)
    self._verify_latest('5m', f[-1]['t'], 300_000)
    self._verify_latest('1m', o[-1]['t'], ONE_MINUTE_STEP)
    value = strict_signal(h, m, f, o, self.settings.stop_atr if self.settings else 1.0, four=q)
    self.market = dict(value, bar=o[-1]['t'], bar1m=o[-1]['t'], bar5m=f[-1]['t'], bar15=m[-1]['t'], bar1h=h[-1]['t'], bar4h=q[-1]['t'], close=o[-1]['c'])
    self.market_at = time.time()
    self.market_monotonic = time.monotonic()
    self._candle_recovered()
    self.emit('market', self.market)


def _prepare_order(self, side, score, market, equity, available, remaining):
    tier, multiplier, level = _tier(score['total'])
    ticker = self.x.ticker()
    self.emit('ticker', ticker)
    if abs(float(ticker['last']) - float(market['close'])) > .3 * float(market['h']['atr']):
        self.emit('log', '价格偏离1m信号收盘价超过0.3×1H ATR，本轮跳过，不追价')
        return None
    meta = self.x.instrument()
    v137._CURRENT_CONTEXT.update(bar=market['bar'], tier=tier, level=level, score=float(score['total']))
    try:
        plan = v137._v137_make_plan(self.settings, side, ticker, meta, market['m']['atr'], available, remaining, multiplier)
    except engine.Halt as exc:
        message = str(exc)
        if any(x in message for x in ('买卖价差过大', '风险预算不足以满足最小下单量', '无效信号或ATR', '止盈止损价格非法')):
            self.emit('log', 'V1.3.7本轮自动开仓跳过：' + message)
            return None
        raise
    if float(plan.get('expected_fee_multiple') or 0) < EXPECTED_COST_MIN:
        self.emit('log', f"V1.3.7成本过滤：2R TP仅为预计手续费 {float(plan.get('expected_fee_multiple') or 0):.2f} 倍（最低{EXPECTED_COST_MIN:.2f}倍）；未提交OKX订单")
        return None
    return plan, tier, multiplier, level


def _set_leverage(self, plan):
    self.x.post('/api/v5/account/set-leverage', {'instId': engine.INSTRUMENT, 'lever': str(self.settings.leverage), 'mgnMode': 'isolated', 'posSide': plan['posSide']})
    infos = self.x.get('/api/v5/account/leverage-info', {'instId': engine.INSTRUMENT, 'mgnMode': 'isolated'}, True)
    if not any(i.get('posSide') == plan['posSide'] and float(i['lever']) == self.settings.leverage and i.get('mgnMode') == 'isolated' for i in infos):
        raise engine.Halt('逐仓杠杆回读不一致')
    return True


def _submit_initial(self, market, score, equity, available, remaining):
    prepared = _prepare_order(self, market['side'], score, market, equity, available, remaining)
    if not prepared:
        return
    plan, tier, multiplier, level = prepared
    if not _set_leverage(self, plan) or not self.enabled:
        return
    self._verify_latest('1m', market['bar'], ONE_MINUTE_STEP)
    cid, algo = 'mac'+uuid.uuid4().hex[:28], 'br'+uuid.uuid4().hex[:28]
    state = self.store.data
    before_last = state.get('last_bar')
    leg = dict(plan, client_id=cid, order_id='', bracket_id=algo, submitted=time.time(), expires=time.time()+60,
               filled=False, state='pending', cancel_requested=False, cancel_on_reconcile=False, tier=tier,
               score=float(score['total']), signal_bar=market['bar'], counted=True, protected=False)
    root = dict(plan, client_id=cid, order_id='', bracket_id=algo, submitted=leg['submitted'], expires=leg['expires'],
                equity_before=equity, filled=False, protected=False, score=float(score['total']), signal_bar=market['bar'],
                signal_tier=tier, signal_level=level, v137=True, version='1.3.7', legs=[leg],
                tier_counts={'1':0,'2':0,'3':0}, highest_tier=tier, last_entry_bar=market['bar'])
    root['tier_counts'][str(tier)] = 1
    state['last_bar'] = market['bar']
    state['active'] = root
    self.store.save()
    body = {'instId':engine.INSTRUMENT,'tdMode':'isolated','side':plan['exchange_side'],'posSide':plan['posSide'],
            'ordType':'limit','px':plan['px'],'sz':plan['sz'],'clOrdId':cid,
            'attachAlgoOrds':[{'attachAlgoClOrdId':algo,'tpOrdKind':'condition','tpTriggerPx':plan['tp'],'tpOrdPx':'-1','tpTriggerPxType':'last',
                               'slTriggerPx':plan['sl'],'slOrdPx':'-1','slTriggerPxType':'last'}]}
    try:
        reply = self.x.post('/api/v5/trade/order', body)
    except Exception as exc:
        if getattr(exc, 'write_rejected', False):
            state['active'] = None
            state['last_bar'] = before_last
            self.store.save()
            self.enabled = False
            self.stopped = True
            self.emit('log', f'OKX明确拒绝V1.3.7限价开仓：{exc}；已释放占位并停止新开仓')
            return
        raise
    row = reply[0] if reply else {}
    oid = str(row.get('ordId') or '') if isinstance(row, dict) else ''
    if not oid:
        raise engine.Halt('V1.3.7开仓响应缺少ordId；本地占位已保留，禁止重复提交')
    leg['order_id'] = oid
    root['order_id'] = oid
    root['okx_ack_at'] = time.time()
    self.store.save()
    self.store.record('V1.3.7提交1m限价开仓', {'tier':tier,'score':score['total'],'client_id':cid,'order_id':oid,'plan':plan})
    self.emit('log', f'V1.3.7 {level}：层级硬条件全部通过，已提交60秒有效限价{market["side"]}；整仓TP=2R，SL=1R')
    self.emit('plan', plan)


def _submit_addon(self, p, market, score, equity, available, remaining):
    tier, multiplier, level = _tier(score['total'])
    allowed, _ = _allow_entry(p, tier, market['bar'])
    if not allowed:
        return
    current_risk = sum(float(leg.get('estimated_loss') or 0) for leg in p.get('legs', []) if leg.get('state') == 'filled')
    risk_remaining = float(remaining) - current_risk
    if risk_remaining <= 0:
        return
    prepared = _prepare_order(self, p['side'], score, market, equity, available, risk_remaining)
    if not prepared:
        return
    plan, tier, multiplier, level = prepared
    meta = self.x.instrument()
    current_notional = sum(float(leg.get('notional') or 0) for leg in p.get('legs', []) if leg.get('state') == 'filled')
    plan = v137._cap_plan(plan, meta, float(self.settings.max_notional)-current_notional)
    if not _set_leverage(self, plan) or not self.enabled:
        return
    self._verify_latest('1m', market['bar'], ONE_MINUTE_STEP)
    cid, algo = 'mac'+uuid.uuid4().hex[:28], 'br'+uuid.uuid4().hex[:28]
    state = self.store.data
    before_last = state.get('last_bar')
    leg = dict(plan, client_id=cid, order_id='', bracket_id=algo, submitted=time.time(), expires=time.time()+60,
               filled=False, state='pending', cancel_requested=False, cancel_on_reconcile=False, tier=tier,
               score=float(score['total']), signal_bar=market['bar'], counted=True, protected=False)
    state['last_bar'] = market['bar']
    p.setdefault('legs', []).append(leg)
    p.setdefault('tier_counts', {'1':0,'2':0,'3':0})[str(tier)] = int(p['tier_counts'].get(str(tier), 0) or 0)+1
    p['highest_tier'] = max(int(p.get('highest_tier') or 0), tier)
    p['last_entry_bar'] = market['bar']
    self.store.save()
    body = {'instId':engine.INSTRUMENT,'tdMode':'isolated','side':plan['exchange_side'],'posSide':plan['posSide'],
            'ordType':'limit','px':plan['px'],'sz':plan['sz'],'clOrdId':cid,
            'attachAlgoOrds':[{'attachAlgoClOrdId':algo,'tpOrdKind':'condition','tpTriggerPx':plan['tp'],'tpOrdPx':'-1','tpTriggerPxType':'last',
                               'slTriggerPx':plan['sl'],'slOrdPx':'-1','slTriggerPxType':'last'}]}
    try:
        reply = self.x.post('/api/v5/trade/order', body)
    except Exception as exc:
        if getattr(exc, 'write_rejected', False):
            p['legs'].remove(leg)
            p['tier_counts'][str(tier)] = max(0, int(p['tier_counts'][str(tier)])-1)
            p['highest_tier'] = max([int(k) for k,v in p['tier_counts'].items() if int(v)>0] or [0])
            p['last_entry_bar'] = max([int(x.get('signal_bar') or 0) for x in p['legs']] or [0])
            state['last_bar'] = before_last
            self.store.save()
            self.enabled = False
            self.stopped = True
            self.emit('log', f'OKX明确拒绝V1.3.7加仓：{exc}；已释放加仓占位并停止新开仓')
            return
        raise
    row = reply[0] if reply else {}
    oid = str(row.get('ordId') or '') if isinstance(row, dict) else ''
    if not oid:
        raise engine.Halt('V1.3.7加仓响应缺少ordId；本地占位已保留，禁止重复提交')
    leg['order_id'] = oid
    leg['okx_ack_at'] = time.time()
    self.store.save()
    self.store.record('V1.3.7提交1m加仓', {'tier':tier,'score':score['total'],'client_id':cid,'order_id':oid,'plan':plan})
    self.emit('log', f'V1.3.7 {level}：已提交本周期该Tier唯一一次加仓；限价有效60秒')
    self.emit('plan', plan)


def _cycle(self):
    now = time.monotonic()
    if self.poll_at and now-self.poll_at > 60 and self.enabled:
        self.halt('检测到睡眠或长时间停顿，必须人工重新检查后启动')
    self.poll_at = now
    if not self.store:
        return
    p = self.store.data.get('active')
    if isinstance(p, dict) and p.get('v137'):
        self.reconcile()
    expected = _expected_1m(self)
    if not self.market or self.market.get('bar') != expected:
        self.refresh_market()
    if not self.enabled:
        return
    if float(getattr(self, 'startup_buffer_until', 0) or 0) > time.monotonic():
        return
    self.startup_buffer_until = 0.0
    market = self.market
    self._verify_latest('1m', market['bar'], ONE_MINUTE_STEP)
    try:
        equity, available = self.x.balance()
        remaining = self.daily(equity)
    except v135_patch.DailyRiskStop as exc:
        self.enabled = False
        self.stopped = True
        self.emit('log', str(exc))
        return
    self._reset_streak_day()
    state = self.store.data
    if state.get('streak', 0) >= self.settings.consecutive_losses:
        return
    p = state.get('active')
    if isinstance(p, dict) and p.get('v137'):
        v137._ensure_v137_state(self, p)
        if p.get('emergency_close_id') or any(leg.get('state') == 'pending' for leg in p.get('legs', [])) or not p.get('protected'):
            return
        if market.get('side') == '观望' or market.get('side') != p.get('side'):
            return
        score = (market.get('scores') or {}).get(p['side']) or {}
        if not score.get('gate') or float(score.get('total') or 0) < V137_THRESHOLD:
            return
        return _submit_addon(self, p, market, score, equity, available, remaining)

    if time.time() - float(state.get('last_close') or 0) < self.settings.cooldown_minutes*60:
        return
    if self.x.positions() or self.x.orders() or self.x.algos():
        raise engine.Halt('出现非本程序仓位或挂单，停止自动开仓')
    if market.get('side') == '观望' or state.get('last_bar') == market.get('bar'):
        return
    score = (market.get('scores') or {}).get(market['side']) or {}
    if not score.get('gate') or float(score.get('total') or 0) < V137_THRESHOLD:
        return
    return _submit_initial(self, market, score, equity, available, remaining)


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
    if getattr(engine.Engine, '_kaytrade_v137_hard_gate_applied', False):
        return

    v137.V137_THRESHOLD = V137_THRESHOLD
    v137._tier = _tier
    v137._tier_limit = _tier_limit
    v137._allow_entry = _allow_entry
    engine.Settings.validate = v137._validate_settings
    strategy.signal = strict_signal
    engine.signal = strict_signal
    engine.Engine.refresh_market = _refresh_market
    engine.Engine.cycle = _cycle

    previous_reconcile = engine.Engine.reconcile
    previous_app_init = app.App.__init__
    previous_app_settings = app.App.settings
    previous_app_arm = app.App.arm

    def reconcile(self):
        original_emit = self.emit
        def emit(kind, data):
            if isinstance(data, str):
                data = data.replace('等待1根5m K线', '超过60秒1m信号有效期')
                data = data.replace('下一根有效5m信号', '下一根有效1m信号')
            return original_emit(kind, data)
        self.emit = emit
        try:
            return previous_reconcile(self)
        finally:
            self.emit = original_emit
    engine.Engine.reconcile = reconcile

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        if 'score_threshold' in self.fields:
            self.fields['score_threshold'].set('4.0')
        for w in _widgets(self.root):
            try:
                text = str(w.cget('text') or '')
            except Exception:
                text = ''
            try:
                if text.startswith('自动开仓评分阈值'):
                    w.configure(text='自动开仓评分阈值 4.0（固定）')
            except Exception:
                pass
            if isinstance(w, app.RoundedEntry) and getattr(w, 'variable', None) is self.fields.get('score_threshold'):
                try:
                    w.variable.set('4.0')
                    w.entry.configure(state='disabled', disabledbackground=app.FIELD, disabledforeground=app.MUTED)
                except Exception:
                    pass

    def app_settings(self):
        if hasattr(self, 'fields') and 'score_threshold' in self.fields:
            self.fields['score_threshold'].set('4.0')
        return previous_app_settings(self)

    def app_arm(self):
        original = app.simpledialog.askstring
        def askstring(title, prompt, *args, **kwargs):
            text = str(prompt)
            text += ('\n\nV1.3.7层级式开仓：有效1m Trigger → 5m MACD同向 → 1H不能逆势 → '
                     '前方空间≥1.3R → 最终评分≥4.0 → 多空高分仲裁；同分等待。'
                     '\nTier1/Tier2/Tier3每周期各最多一次；每根已收盘1m最多一次；限价60秒未成交撤销。')
            return original(title, text, *args, **kwargs)
        app.simpledialog.askstring = askstring
        try:
            return previous_app_arm(self)
        finally:
            app.simpledialog.askstring = original

    app.App.__init__ = app_init
    app.App.settings = app_settings
    app.App.arm = app_arm
    engine.Engine._kaytrade_v137_hard_gate_applied = True


apply()

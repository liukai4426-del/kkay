"""KAYTRADE V1.3.8 final layered hard-gate scoring overlay.

Loaded after v138_user_update so the user-confirmed V1.3.8 execution/risk model
remains intact while the recorded V1.3.7 layered entry rules become the final
V1.3.8 signal engine:
- only two score bands: 6.0-7.5 opening / 8.0-10 strong
- 1m Trigger = KDJ cross OR EMA20 reclaim OR reversal candle; EMA direction alone does nothing
- 5m MACD direction is mandatory and adds +1
- 1H aligned trend +2; 1H opposite trend is a hard block
- forward strong structure must leave >=1.3R
- 4H aligned +1; 4H opposite -1, not a hard block
- 5m BOLL +1, 15m MACD +1, 15m BOLL +1
- 15m favorable structure +0.5, 15m setup up to +1.5
- RSI overheat/oversold penalty and 1H/4H adverse-structure penalties retained
- all 1D indicators are removed from scoring, filtering and market refresh
- higher score wins when both sides qualify; equal score waits

Execution remains provided by v138_strategy_patch + v138_user_update:
- latest CLOSED 1m dedupe
- LIMIT entry, ~60s expiry
- first/second signal position relationship 1:2
- TP=2R / SL=1R using 15m ATR
- 3 consecutive losses -> six-hour new-entry pause
- existing reconciliation / fail-closed safety behavior
"""
import math
import time

from core import indicators, ema
from v138_user_update import apply as apply_v138_user_update
apply_v138_user_update()

import app
import engine
import strategy
import v138_strategy_patch as v138
import v138_user_update as update

ONE_MINUTE_STEP = 60_000
FRONT_MIN_R = 1.3


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
    return bool(macd_ok), bool(boll_ok), macd


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


def _trend_state(rows, current, buy):
    candle = rows[-1]
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


def strict_signal(hour, quarter, five, one, stop_atr=1.0, four=None, day=None):
    """Final V1.3.8 scoring engine. `day` is accepted only for call compatibility and is ignored."""
    four = hour if four is None else four
    h = indicators(hour)
    m = indicators(quarter)
    f = indicators(five)
    o = indicators(one)
    q = indicators(four)
    pf = indicators(five[:-1])
    pm = indicators(quarter[:-1])
    po = indicators(one[:-1])
    price = float(one[-1]['c'])
    hz = _zones(hour, h, 120)
    mz = _zones(quarter, m, 160)
    qz = _zones(four, q, 180)
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
        tier, multiplier, level = update._two_tier(total)
        gate = bool(trigger_valid and macd5 and not trend1_opposite and space_ok)
        eligible = bool(gate and tier > 0)

        if not trigger_valid:
            reason = '1m Trigger无效：KDJ / EMA20回收 / 反转K线均未触发，禁止开仓'
        elif not macd5:
            reason = '5m MACD与开仓方向不一致，禁止开仓'
        elif trend1_opposite:
            reason = '1H趋势与开仓方向相反，Hard Block：禁止开仓'
        elif not space_ok:
            reason = f'前方有效空间仅 {front_r:.2f}R < 1.3R，禁止开仓'
        elif tier <= 0:
            reason = f'{total:g}/10，未达V1.3.8开仓信号6.0分'
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
            'total': total,
            'raw': raw,
            'gate': gate,
            'eligible': eligible,
            'required': update.V138_ENTRY_THRESHOLD,
            'signal_tier': tier,
            'position_multiplier': multiplier if eligible else 0.0,
            'level': f'{level} · {multiplier:g}×第一仓位' if tier else level,
            'reason': reason,
            'items': items,
            'layers': {
                'trigger': 1.0 if trigger_valid else 0.0,
                'setup': setup,
                'macd_5m': 1.0 if macd5 else 0.0,
                'boll_5m': 1.0 if boll5 else 0.0,
                'macd_15m': 1.0 if macd15 else 0.0,
                'boll_15m': 1.0 if boll15 else 0.0,
                'trend_1h': trend1_score,
                'trend_4h': trend4_score,
                'structure_15m': favorable15,
                'rsi_penalty': rsi,
                'structure_penalty_1h': structure1,
                'structure_penalty_4h': structure4,
            },
            'confirmations': {
                '1m_trigger': trigger_valid,
                'trigger': trigger_detail,
                '5m_macd': macd5,
                '5m_boll': boll5,
                '15m_macd': macd15,
                '15m_boll': boll15,
                '1H_aligned': trend1_aligned,
                '1H_countertrend': trend1_opposite,
                '4H_aligned': trend4_aligned,
                '4H_countertrend': trend4_opposite,
                'front_r': front_r,
                'front_space_ok': space_ok,
                'setup': setup_detail,
                'volume_ratio_15m': volume_ratio,
                'macd_5m_values': macd5_values,
                'macd_15m_values': macd15_values,
            },
            'structure': {
                'front_r': front_r,
                'front': ({'timeframe': front[1], **front[2]} if front else None),
                'favorable_15m': fav15,
                'adverse_1h': bad1,
                'adverse_4h': bad4,
                'blocked': not space_ok,
            },
        }

    qualified = [side for side in ('做多', '做空') if results[side]['eligible']]
    if len(qualified) == 1:
        selected = qualified[0]
    elif len(qualified) == 2:
        a = results[qualified[0]]['total']
        b = results[qualified[1]]['total']
        selected = '观望' if abs(a-b) <= 1e-9 else max(qualified, key=lambda s: results[s]['total'])
    else:
        selected = '观望'
    why = results[selected]['reason'] if selected != '观望' else ' / '.join(s+': '+results[s]['reason'] for s in ('做多','做空'))
    return {'q': q, 'h': h, 'm': m, 'f': f, 'o': o, 'side': selected, 'why': why,
            'scores': results, 'threshold': update.V138_ENTRY_THRESHOLD, 'score_max': 10.0}


def _refresh_market(self):
    # 1D is intentionally not fetched: V1.3.8 final removes all daily indicators.
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
    self.market = dict(value, bar=o[-1]['t'], bar1m=o[-1]['t'], bar5m=f[-1]['t'], bar15=m[-1]['t'],
                       bar1h=h[-1]['t'], bar4h=q[-1]['t'], close=o[-1]['c'])
    self.market_at = time.time()
    self.market_monotonic = time.monotonic()
    self._candle_recovered()
    self.emit('market', self.market)


def apply():
    if getattr(engine.Engine, '_kaytrade_v138_hard_gate_final_applied', False):
        return

    # Keep the already-confirmed two-tier threshold and 1:2 sizing from v138_user_update.
    v138.V138_THRESHOLD = update.V138_ENTRY_THRESHOLD
    v138.v138_signal = strict_signal
    strategy.signal = strict_signal
    engine.signal = strict_signal
    engine.Engine.refresh_market = _refresh_market

    previous_app_arm = app.App.arm

    def app_arm(self):
        original = app.simpledialog.askstring

        def askstring(title, prompt, *args, **kwargs):
            text = str(prompt)
            text += (
                '\n\nV1.3.8最终开仓链条：有效1m Trigger → 5m MACD同向 → 1H不能逆势 → '
                '前方有效空间≥1.3R → 最终评分≥6.0 → 多空高分仲裁；同分等待。'
                '\n评分仅两档：6.0–7.5开仓信号；8.0–10强信号。第二仓位固定为第一仓位2倍且不可编辑。'
                '\n每根已收盘1m最多一次；限价约60秒未成交撤销；连续亏损3次暂停新开仓6小时。'
            )
            return original(title, text, *args, **kwargs)

        app.simpledialog.askstring = askstring
        try:
            return previous_app_arm(self)
        finally:
            app.simpledialog.askstring = original

    app.App.arm = app_arm
    engine.Engine._kaytrade_v138_hard_gate_final_applied = True


apply()

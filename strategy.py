"""V1.1 closed-candle extreme-RSI reversal scores; integer points, no model."""
from core import indicators

def signal(hour, quarter, threshold=7):
    h, m = indicators(hour), indicators(quarter)
    p = indicators(quarter[:-1])
    c, prev = quarter[-1], quarter[-2]
    results = {}
    for side in ('做多', '做空'):
        buy = side == '做多'
        r15, r1 = m['rsi'], h['rsi']
        gate = (r15 <= 20 and r1 <= 25) if buy else (r15 >= 75 and r1 >= 70)
        r15pts = (2 if r15 <= 15 else 1 if r15 <= 20 else 0) if buy else (2 if r15 >= 80 else 1 if r15 >= 75 else 0)
        r1pts = (2 if r1 <= 20 else 1 if r1 <= 25 else 0) if buy else (2 if r1 >= 75 else 1 if r1 >= 70 else 0)
        # J extreme and K/D cross use the SAME closed candle, as specified.
        cross = (m['j'] <= 0 and m['cross_up']) if buy else (m['j'] >= 100 and m['cross_down'])
        reversal = (c['c'] > c['o'] and c['c'] > prev['h']) if buy else (c['c'] < c['o'] and c['c'] < prev['l'])
        deviation = (m['ema20']-c['c']) if buy else (c['c']-m['ema20'])
        reclaim = (prev['c'] <= p['ema20'] and c['c'] > m['ema20']) if buy else (prev['c'] >= p['ema20'] and c['c'] < m['ema20'])
        items = [
            ('15m RSI 极值', r15pts, 2), ('1H RSI 极值', r1pts, 2),
            ('布林轨外收盘', int(c['c'] < m['lower'] if buy else c['c'] > m['upper']), 1),
            ('J极值 + K/D交叉', 2*int(cross), 2),
            ('反转K线确认', int(reversal), 1),
            ('EMA偏离 ≥ 0.7×15m ATR', int(m['atr'] > 0 and deviation >= .7*m['atr']), 1),
            ('重新穿越EMA20', int(reclaim), 1)]
        total = sum(i[1] for i in items)
        reason = ('RSI硬条件未同时满足' if not gate else f'评分 {total}/10，未达 {threshold:g}' if total < threshold else '评分达标；仍需执行风控检查')
        results[side] = dict(total=total, gate=gate, eligible=gate and total >= threshold, items=items, reason=reason)
    side = next((s for s in results if results[s]['eligible']), '观望')
    why = results[side]['reason'] if side != '观望' else ' / '.join(s+': '+r['reason'] for s,r in results.items())
    return dict(h=h, m=m, side=side, why=why, scores=results, threshold=threshold)

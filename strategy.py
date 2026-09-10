"""V1.2.3 closed-candle reversal score: 10 points, no RSI hard gate."""
from core import indicators


def _period(rows, current, previous, buy):
    c, prev = rows[-1], rows[-2]
    kdj = (current['j'] <= 30 and current['cross_up']) if buy else (current['j'] >= 70 and current['cross_down'])
    ema = (prev['c'] <= previous['ema20'] and c['c'] > current['ema20']) if buy else (prev['c'] >= previous['ema20'] and c['c'] < current['ema20'])
    reversal = (c['c'] > c['o'] and c['c'] > prev['h']) if buy else (c['c'] < c['o'] and c['c'] < prev['l'])
    return dict(kdj=kdj, ema=ema, reversal=reversal, confirmed=kdj or ema or reversal)


def signal(hour, quarter, five=None, threshold=7):
    # Backwards-compatible fallback is kept for tests/tools that still call signal(h, m).
    five = quarter if five is None else five
    h, m, f = indicators(hour), indicators(quarter), indicators(five)
    ph, pm, pf = indicators(hour[:-1]), indicators(quarter[:-1]), indicators(five[:-1])
    hc, mc = hour[-1], quarter[-1]
    results = {}

    for side in ('做多', '做空'):
        buy = side == '做多'
        hf = _period(hour, h, ph, buy)
        mf = _period(quarter, m, pm, buy)
        ff = _period(five, f, pf, buy)

        rsi_extreme = (m['rsi'] <= 20 and h['rsi'] <= 25) if buy else (m['rsi'] >= 75 and h['rsi'] >= 70)
        boll_rsi = (mc['l'] <= m['lower'] and m['rsi'] < 30) if buy else (mc['h'] >= m['upper'] and m['rsi'] > 70)

        # The three single-point items complete the 10-point positive score.
        kdj_point = mf['kdj']
        ema_point = ff['ema']
        reversal_point = mf['reversal']

        strong_opposite = (
            hc['c'] < h['ema200'] and h['ema20'] < h['ema50'] and h['down']
        ) if buy else (
            hc['c'] > h['ema200'] and h['ema20'] > h['ema50'] and h['up']
        )

        # 1H is an environment confirmation rather than a requirement for an identical 5m pattern.
        hour_support = (not strong_opposite) and (
            hf['confirmed'] or
            (h['rsi'] <= 45 or hc['c'] >= h['ema20']) if buy else
            (h['rsi'] >= 55 or hc['c'] <= h['ema20'])
        )

        # Stair-step confirmation is counted once per side, never 1+2+3.
        if ff['confirmed'] and mf['confirmed'] and hour_support:
            multi = 3
        elif ff['confirmed'] and mf['confirmed']:
            multi = 2
        elif ff['confirmed']:
            multi = 1
        else:
            multi = 0

        items = [
            ('15m + 1H RSI 极值组合', 2 * int(rsi_extreme), 2),
            ('15m BOLL触轨 + RSI超买/超卖', 2 * int(boll_rsi), 2),
            ('15m KDJ J值 + K/D交叉确认', int(kdj_point), 1),
            ('5m 重新穿越 EMA20', int(ema_point), 1),
            ('15m 反转K线确认', int(reversal_point), 1),
            ('5m / 15m / 1H 多周期确认', multi, 3),
            ('1H 强逆势惩罚', -2 * int(strong_opposite), 0),
        ]
        raw = sum(item[1] for item in items)
        total = max(0, min(10, raw))
        eligible = total >= threshold
        reason = (f'评分 {total}/10 达标；仍需执行风控检查' if eligible
                  else f'评分 {total}/10，未达 {threshold:g}')
        results[side] = dict(total=total, raw=raw, gate=True, eligible=eligible,
                             items=items, reason=reason,
                             confirmations={'5m': ff['confirmed'], '15m': mf['confirmed'],
                                            '1H': hour_support, 'strong_opposite': strong_opposite})

    qualified = [side for side, row in results.items() if row['eligible']]
    if len(qualified) == 1:
        side = qualified[0]
    elif len(qualified) == 2 and results[qualified[0]]['total'] != results[qualified[1]]['total']:
        side = max(qualified, key=lambda s: results[s]['total'])
    else:
        side = '观望'
    why = results[side]['reason'] if side != '观望' else ' / '.join(s + ': ' + r['reason'] for s, r in results.items())
    return dict(h=h, m=m, f=f, side=side, why=why, scores=results, threshold=threshold)

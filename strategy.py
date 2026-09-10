"""V1.2.3 closed-candle scoring: per-signal multi-timeframe confluence, 19-point maximum."""
import math
from core import indicators


def _period(rows, current, previous, buy):
    c, prev = rows[-1], rows[-2]
    kdj = (current['j'] <= 30 and current['cross_up']) if buy else (current['j'] >= 70 and current['cross_down'])
    ema = (prev['c'] <= previous['ema20'] and c['c'] > current['ema20']) if buy else (prev['c'] >= previous['ema20'] and c['c'] < current['ema20'])
    reversal = (c['c'] > c['o'] and c['c'] > prev['h']) if buy else (c['c'] < c['o'] and c['c'] < prev['l'])
    return dict(kdj=kdj, ema=ema, reversal=reversal, confirmed=kdj or ema or reversal)


def _volume_boll_return(rows, current, buy, multiplier=1.3):
    """Confirm 15m Bollinger rejection using current volume versus prior 20 closed bars."""
    if len(rows) < 21:
        return False, 0.0
    history=[]
    for row in rows[-21:-1]:
        try:
            value=float(row['v'])
        except (KeyError, TypeError, ValueError):
            return False, 0.0
        if not math.isfinite(value) or value < 0:
            return False, 0.0
        history.append(value)
    try:
        current_volume=float(rows[-1]['v'])
    except (KeyError, TypeError, ValueError):
        return False, 0.0
    average=sum(history)/len(history)
    if not math.isfinite(current_volume) or current_volume < 0 or average <= 0:
        return False, 0.0
    candle=rows[-1]
    returned=(candle['l'] <= current['lower'] and candle['c'] > current['lower']) if buy else (candle['h'] >= current['upper'] and candle['c'] < current['upper'])
    ratio=current_volume/average
    return returned and ratio >= multiplier, ratio


def _ladder(five_ok, quarter_ok, hour_ok):
    """Each independent signal gets its own 5m/+1, 5m+15m/+2, 5m+15m+1H/+3 ladder."""
    if five_ok and quarter_ok and hour_ok:
        return 3
    if five_ok and quarter_ok:
        return 2
    if five_ok:
        return 1
    return 0


def _level(total):
    if total >= 15:
        return '高共振信号'
    if total >= 11:
        return '强信号'
    if total >= 8:
        return '普通信号'
    return '未达开仓线'


def signal(hour, quarter, five=None, threshold=8):
    five = quarter if five is None else five
    h, m, f = indicators(hour), indicators(quarter), indicators(five)
    ph, pm, pf = indicators(hour[:-1]), indicators(quarter[:-1]), indicators(five[:-1])
    hc, mc, fc = hour[-1], quarter[-1], five[-1]
    results = {}

    for side in ('做多', '做空'):
        buy = side == '做多'
        hf = _period(hour, h, ph, buy)
        mf = _period(quarter, m, pm, buy)
        ff = _period(five, f, pf, buy)

        rsi5 = f['rsi'] <= 20 if buy else f['rsi'] >= 75
        rsi15 = m['rsi'] <= 20 if buy else m['rsi'] >= 75
        rsi1h = h['rsi'] <= 25 if buy else h['rsi'] >= 70
        rsi_score = _ladder(rsi5, rsi15, rsi1h)

        boll5 = fc['l'] <= f['lower'] if buy else fc['h'] >= f['upper']
        boll15 = mc['l'] <= m['lower'] if buy else mc['h'] >= m['upper']
        boll1h = hc['l'] <= h['lower'] if buy else hc['h'] >= h['upper']
        boll_score = _ladder(boll5, boll15, boll1h)

        kdj_score = _ladder(ff['kdj'], mf['kdj'], hf['kdj'])
        ema_score = _ladder(ff['ema'], mf['ema'], hf['ema'])
        reversal_score = _ladder(ff['reversal'], mf['reversal'], hf['reversal'])

        boll_rsi = (mc['l'] <= m['lower'] and m['rsi'] < 30) if buy else (mc['h'] >= m['upper'] and m['rsi'] > 70)
        volume_boll, volume_ratio = _volume_boll_return(quarter, m, buy)

        strong_opposite = (
            hc['c'] < h['ema200'] and h['ema20'] < h['ema50'] and h['down']
        ) if buy else (
            hc['c'] > h['ema200'] and h['ema20'] > h['ema50'] and h['up']
        )

        items = [
            ('RSI 多周期共振（5m/15m/1H）', rsi_score, 3),
            ('BOLL 多周期共振（5m/15m/1H）', boll_score, 3),
            ('KDJ 多周期共振（5m/15m/1H）', kdj_score, 3),
            ('EMA20 多周期共振（5m/15m/1H）', ema_score, 3),
            ('反转K线多周期共振（5m/15m/1H）', reversal_score, 3),
            ('15m RSI + BOLL 组合增强', 2 * int(boll_rsi), 2),
            ('15m BOLL外破回归 + 成交量≥20均量×1.3', 2 * int(volume_boll), 2),
            ('1H 强逆势惩罚', -3 * int(strong_opposite), 0),
        ]
        raw = sum(item[1] for item in items)
        total = max(0, min(19, raw))
        level = _level(total)
        eligible = total >= threshold
        reason = (f'{level} · 评分 {total}/19 达标；仍需执行风控检查' if eligible
                  else f'{level} · 评分 {total}/19，未达开仓阈值 {threshold:g}')
        results[side] = dict(total=total, raw=raw, gate=True, eligible=eligible, level=level,
                             items=items, reason=reason,
                             confirmations={
                                 '5m': any((rsi5, boll5, ff['kdj'], ff['ema'], ff['reversal'])),
                                 '15m': any((rsi15, boll15, mf['kdj'], mf['ema'], mf['reversal'])),
                                 '1H': any((rsi1h, boll1h, hf['kdj'], hf['ema'], hf['reversal'])),
                                 'strong_opposite': strong_opposite,
                                 'volume_ratio_15m': volume_ratio,
                                 'signal_confluence': {
                                     'RSI': {'5m': rsi5, '15m': rsi15, '1H': rsi1h, 'score': rsi_score},
                                     'BOLL': {'5m': boll5, '15m': boll15, '1H': boll1h, 'score': boll_score},
                                     'KDJ': {'5m': ff['kdj'], '15m': mf['kdj'], '1H': hf['kdj'], 'score': kdj_score},
                                     'EMA20': {'5m': ff['ema'], '15m': mf['ema'], '1H': hf['ema'], 'score': ema_score},
                                     '反转K线': {'5m': ff['reversal'], '15m': mf['reversal'], '1H': hf['reversal'], 'score': reversal_score},
                                 },
                             })

    qualified = [side for side, row in results.items() if row['eligible']]
    if len(qualified) == 1:
        side = qualified[0]
    elif len(qualified) == 2 and results[qualified[0]]['total'] != results[qualified[1]]['total']:
        side = max(qualified, key=lambda s: results[s]['total'])
    else:
        side = '观望'
    why = results[side]['reason'] if side != '观望' else ' / '.join(s + ': ' + r['reason'] for s, r in results.items())
    return dict(h=h, m=m, f=f, side=side, why=why, scores=results, threshold=threshold)

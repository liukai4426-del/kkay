"""KAYTRADE V1.3.6: 5m-led trend pullback/breakout scoring with Bollinger confirmation."""
import math
from core import indicators, ema

SCORE_MAX = 13.0
ENTRY_SCORE_MAX = 10.0
DEFAULT_THRESHOLD = 6.0
FIVE_MAX = 6.0
FIFTEEN_MAX = 4.0


def _round_half(value):
    return round(float(value) * 2) / 2


def _volume_ratio(rows, lookback=20):
    if len(rows) < lookback + 1:
        return 0.0
    history=[]
    for row in rows[-lookback-1:-1]:
        try:
            value=float(row.get('v',0))
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(value) or value < 0:
            return 0.0
        history.append(value)
    average=sum(history)/len(history) if history else 0.0
    current=float(rows[-1].get('v',0) or 0)
    return current/average if average > 0 and math.isfinite(current) and current >= 0 else 0.0


def _swing_points(rows, lookback=120, radius=2):
    start=max(0,len(rows)-lookback)
    points=[]
    for i in range(start+radius,len(rows)-radius):
        left=rows[i-radius:i]
        right=rows[i+1:i+radius+1]
        row=rows[i]
        low=float(row['l']); high=float(row['h'])
        left_l=min(float(r['l']) for r in left); right_l=min(float(r['l']) for r in right)
        left_h=max(float(r['h']) for r in left); right_h=max(float(r['h']) for r in right)
        if low <= min(left_l,right_l) and (low < left_l or low < right_l):
            points.append({'kind':'support','price':low,'i':i})
        if high >= max(left_h,right_h) and (high > left_h or high > right_h):
            points.append({'kind':'resistance','price':high,'i':i})
    return points


def _zones(rows, atr, lookback=120):
    atr=float(atr)
    if not math.isfinite(atr) or atr <= 0:
        return []
    tolerance=.25*atr
    clusters=[]
    for point in _swing_points(rows,lookback):
        matches=[z for z in clusters if z['kind']==point['kind'] and abs(z['center']-point['price'])<=tolerance]
        if matches:
            zone=min(matches,key=lambda z:abs(z['center']-point['price']))
            zone['points'].append(point)
            zone['center']=sum(p['price'] for p in zone['points'])/len(zone['points'])
            zone['last_i']=max(zone['last_i'],point['i'])
        else:
            clusters.append({'kind':point['kind'],'center':point['price'],'points':[point],'last_i':point['i']})
    out=[]
    for zone in clusters:
        tests=len(zone['points'])
        if tests < 2:
            continue
        center=zone['center']
        later=rows[zone['last_i']+1:]
        broken=(any(float(r['c']) < center-.30*atr for r in later) if zone['kind']=='support'
                else any(float(r['c']) > center+.30*atr for r in later))
        if broken:
            continue
        age=max(0,len(rows)-1-zone['last_i'])
        recency=max(0.0,1.0-age/max(1,lookback))
        out.append({'kind':zone['kind'],'price':center,'tests':tests,'age':age,
                    'recency':recency,'strong':tests>=3 and recency>=.25})
    return out


def _nearest(zones, price, kind, ahead=None):
    candidates=[]
    for zone in zones:
        if zone['kind'] != kind:
            continue
        if ahead=='above' and zone['price'] <= price:
            continue
        if ahead=='below' and zone['price'] >= price:
            continue
        candidates.append(zone)
    return min(candidates,key=lambda z:abs(z['price']-price)) if candidates else None


def _pullback_reclaim(rows, current, buy):
    """Recent 5m pullback touches EMA20/EMA50 zone, stays structurally intact, then closes back through EMA20."""
    if len(rows) < 210:
        return False, {}
    close=[float(r['c']) for r in rows]
    e20=ema(close,20); e50=ema(close,50)
    atr=float(current['atr']); tolerance=.25*atr; damage=.35*atr
    start=max(0,len(rows)-5)
    touched=False; closest=math.inf
    for i in range(start,len(rows)-1):
        row=rows[i]
        low=float(row['l']); high=float(row['h'])
        lo=min(e20[i],e50[i]); hi=max(e20[i],e50[i])
        distance=0.0 if low <= hi and high >= lo else min(abs(low-hi),abs(high-lo))
        closest=min(closest,distance/max(atr,1e-12))
        if low <= hi+tolerance and high >= lo-tolerance:
            touched=True
    recent=rows[start:]
    if buy:
        intact=min(float(r['l']) for r in recent) >= min(e50[start:])-damage
        reclaimed=float(rows[-1]['c']) > e20[-1]
    else:
        intact=max(float(r['h']) for r in recent) <= max(e50[start:])+damage
        reclaimed=float(rows[-1]['c']) < e20[-1]
    return bool(touched and intact and reclaimed), {'touched':touched,'intact':intact,'reclaimed':reclaimed,'closest_atr':closest}


def _breakout_trigger(rows, buy, lookback=3):
    if len(rows) < lookback+1:
        return False, {}
    current=rows[-1]; prior=rows[-lookback-1:-1]
    if buy:
        level=max(float(r['h']) for r in prior)
        passed=float(current['c']) > level and float(current['c']) > float(current['o'])
    else:
        level=min(float(r['l']) for r in prior)
        passed=float(current['c']) < level and float(current['c']) < float(current['o'])
    return passed, {'level':level,'close':float(current['c']),'lookback':lookback}


def _bollinger_score(rows, current, previous, buy, intraday='5m'):
    width=float(current['upper'])-float(current['lower'])
    prev_width=float(previous['upper'])-float(previous['lower'])
    mid_up=float(current['middle']) > float(previous['middle'])
    mid_down=float(current['middle']) < float(previous['middle'])
    close=float(rows[-1]['c']); atr=max(float(current['atr']),1e-12)
    recent=rows[-5:-1] if len(rows)>=5 else rows[:-1]
    if buy:
        touched_mid=any(float(r['l']) <= float(current['middle'])+.25*atr for r in recent)
        trend_pullback=mid_up and close >= float(current['middle']) and touched_mid
        expansion=mid_up and close >= float(current['upper']) and width >= prev_width*1.02
        regime=mid_up and close >= float(current['middle']) and width >= prev_width*.95
    else:
        touched_mid=any(float(r['h']) >= float(current['middle'])-.25*atr for r in recent)
        trend_pullback=mid_down and close <= float(current['middle']) and touched_mid
        expansion=mid_down and close <= float(current['lower']) and width >= prev_width*1.02
        regime=mid_down and close <= float(current['middle']) and width >= prev_width*.95
    passed=(trend_pullback or expansion) if intraday=='5m' else regime
    return bool(passed), {'trend_pullback':trend_pullback,'expansion':expansion,'regime':regime,
                          'width_ratio':width/prev_width if prev_width>0 else math.inf,
                          'middle':float(current['middle'])}


def _ema_alignment(current, previous, buy, strict_5m=False):
    if buy:
        order=(current['ema5']>current['ema10']>current['ema20']) if strict_5m else current['ema20']>current['ema50']
        slope=current['ema20']>previous['ema20']
    else:
        order=(current['ema5']<current['ema10']<current['ema20']) if strict_5m else current['ema20']<current['ema50']
        slope=current['ema20']<previous['ema20']
    return bool(order and slope), {'order':order,'slope':slope}


def _momentum(current, previous, buy):
    if buy:
        rsi_ok=current['rsi']>=50 and current['rsi']>=previous['rsi']
        kdj_ok=current['cross_up'] or current['k']>current['d']
    else:
        rsi_ok=current['rsi']<=50 and current['rsi']<=previous['rsi']
        kdj_ok=current['cross_down'] or current['k']<current['d']
    return bool(rsi_ok and kdj_ok), {'rsi_ok':rsi_ok,'kdj_ok':kdj_ok,'rsi':current['rsi']}


def _five_score(rows, current, previous, buy):
    reclaim,reclaim_detail=_pullback_reclaim(rows,current,buy)
    breakout,breakout_detail=_breakout_trigger(rows,buy)
    boll,boll_detail=_bollinger_score(rows,current,previous,buy,'5m')
    ema_ok,ema_detail=_ema_alignment(current,previous,buy,True)
    momentum,momentum_detail=_momentum(current,previous,buy)
    volume_ratio=_volume_ratio(rows)
    score=(2.0 if reclaim else 0.0)+(2.0 if breakout else 0.0)+(1.0 if boll else 0.0)+(.5 if ema_ok else 0.0)+(.5 if momentum else 0.0)
    return min(FIVE_MAX,score), {
        'pullback_reclaim':reclaim,'pullback_detail':reclaim_detail,
        'breakout_trigger':breakout,'breakout_detail':breakout_detail,
        'boll':boll,'boll_detail':boll_detail,'ema':ema_ok,'ema_detail':ema_detail,
        'momentum':momentum,'momentum_detail':momentum_detail,
        'volume_confirm':volume_ratio>=1.2,'volume_ratio':volume_ratio,
        'components':{
            'pullback_reclaim':2.0 if reclaim else 0.0,
            'breakout_trigger':2.0 if breakout else 0.0,
            'boll':1.0 if boll else 0.0,
            'ema':.5 if ema_ok else 0.0,
            'momentum':.5 if momentum else 0.0,
        },
    }


def _fifteen_market_structure(rows, buy):
    points=_swing_points(rows,100,2)
    supports=[p for p in points if p['kind']=='support'][-2:]
    resistances=[p for p in points if p['kind']=='resistance'][-2:]
    if len(supports)<2 or len(resistances)<2:
        return False, {'supports':supports,'resistances':resistances}
    if buy:
        passed=supports[-1]['price']>supports[-2]['price'] and resistances[-1]['price']>resistances[-2]['price']
    else:
        passed=supports[-1]['price']<supports[-2]['price'] and resistances[-1]['price']<resistances[-2]['price']
    return passed, {'supports':supports,'resistances':resistances}


def _fifteen_position(rows, current, buy):
    price=float(rows[-1]['c']); atr=float(current['atr'])
    zones=_zones(rows,atr,120)
    kind='support' if buy else 'resistance'
    nearest=_nearest(zones,price,kind)
    distance=abs(nearest['price']-price)/atr if nearest and atr>0 else math.inf
    return bool(nearest and distance<=.35), {'nearest':nearest,'distance_atr':distance,'zones':zones}


def _fifteen_score(rows, current, previous, buy):
    structure,structure_detail=_fifteen_market_structure(rows,buy)
    position,position_detail=_fifteen_position(rows,current,buy)
    boll,boll_detail=_bollinger_score(rows,current,previous,buy,'15m')
    ema_ok,ema_detail=_ema_alignment(current,previous,buy,False)
    momentum,momentum_detail=_momentum(current,previous,buy)
    score=(1.0 if structure else 0.0)+(.5 if position else 0.0)+(1.0 if boll else 0.0)+(1.0 if ema_ok else 0.0)+(.5 if momentum else 0.0)
    return min(FIFTEEN_MAX,score), {
        'structure':structure,'structure_detail':structure_detail,
        'position':position,'position_detail':position_detail,
        'boll':boll,'boll_detail':boll_detail,'ema':ema_ok,'ema_detail':ema_detail,
        'momentum':momentum,'momentum_detail':momentum_detail,
        'components':{
            'structure':1.0 if structure else 0.0,
            'position':.5 if position else 0.0,
            'boll':1.0 if boll else 0.0,
            'ema':1.0 if ema_ok else 0.0,
            'momentum':.5 if momentum else 0.0,
        },
    }


def _trend_adjust(rows, current, previous, buy, weight):
    candle=rows[-1]
    bullish=current['ema20']>current['ema50'] and float(candle['c'])>current['ema200'] and current['ema20']>previous['ema20']
    bearish=current['ema20']<current['ema50'] and float(candle['c'])<current['ema200'] and current['ema20']<previous['ema20']
    if not bullish and not bearish:
        return 0.0,'中性',{'bullish':False,'bearish':False}
    aligned=(bullish and buy) or (bearish and not buy)
    return (float(weight) if aligned else -float(weight)),('顺势' if aligned else '逆势'),{'bullish':bullish,'bearish':bearish}


def _forward_structure(hour, quarter, h, m, buy, stop_atr=1.0):
    price=float(quarter[-1]['c'])
    hz=_zones(hour,float(h['atr']),120)
    mz=_zones(quarter,float(m['atr']),160)
    opposite='resistance' if buy else 'support'
    ahead='above' if buy else 'below'
    candidates=[]
    for timeframe,zones in (('1H',hz),('15m',mz)):
        strong=[z for z in zones if z.get('strong')]
        zone=_nearest(strong,price,opposite,ahead)
        if zone:
            candidates.append((abs(zone['price']-price),timeframe,zone))
    front=min(candidates,key=lambda item:item[0]) if candidates else None
    risk=float(m['atr'])*float(stop_atr)
    front_r=front[0]/risk if front and risk>0 else math.inf
    return {
        'blocked':front_r<1.0,
        'warning':1.0<=front_r<1.5,
        'front_r':front_r,
        'front':({'timeframe':front[1],**front[2]} if front else None),
        'zones_1h':hz,'zones_15m':mz,
    }


def _level(total):
    if total >= 10:
        return '高共振信号'
    if total >= 8:
        return '强信号'
    if total >= DEFAULT_THRESHOLD:
        return '普通信号'
    return '未达开仓线'


def _position_multiplier(total):
    """V1.3.6 deliberately keeps all qualified entries at the configured base risk (1x)."""
    return 1.0


def signal(hour, quarter, five=None, threshold=DEFAULT_THRESHOLD, stop_atr=1.0, four=None, day=None):
    five=quarter if five is None else five
    four=hour if four is None else four
    day=four if day is None else day
    h=indicators(hour); ph=indicators(hour[:-1])
    m=indicators(quarter); pm=indicators(quarter[:-1])
    f=indicators(five); pf=indicators(five[:-1])
    q=indicators(four); pq=indicators(four[:-1])
    d=indicators(day)
    required=float(threshold)
    results={}
    for side in ('做多','做空'):
        buy=side=='做多'
        five_score,five_detail=_five_score(five,f,pf,buy)
        fifteen_score,fifteen_detail=_fifteen_score(quarter,m,pm,buy)
        trend_1h,state_1h,detail_1h=_trend_adjust(hour,h,ph,buy,1.0)
        trend_4h,state_4h,detail_4h=_trend_adjust(four,q,pq,buy,2.0)
        base=_round_half(five_score+fifteen_score)
        trend=_round_half(trend_1h+trend_4h)
        total=max(0.0,min(SCORE_MAX,_round_half(base+trend)))
        structure=_forward_structure(hour,quarter,h,m,buy,stop_atr)
        mandatory=bool(five_detail['pullback_reclaim'] and five_detail['breakout_trigger'])
        five_gate=five_score>=4.0
        gate=mandatory and five_gate and not structure['blocked']
        eligible=bool(gate and total>=required)
        level=_level(total)
        if not five_detail['pullback_reclaim']:
            reason='5m回调收回未成立，禁止开仓'
        elif not five_detail['breakout_trigger']:
            reason='5m突破触发未成立，禁止开仓'
        elif not five_gate:
            reason=f'5m入场分 {five_score:g}/6 < 4，禁止开仓'
        elif structure['blocked']:
            reason=f'前方强结构仅 {structure["front_r"]:.2f}R < 1R，禁止开仓'
        elif total < required:
            reason=f'入场分 {base:g}/10 · 趋势修正 {trend:+g} · 最终 {total:g}/13，未达阈值 {required:g}'
        else:
            caution=' · 前方结构空间偏紧' if structure['warning'] else ''
            reason=f'{level} · 入场分 {base:g}/10 · 趋势修正 {trend:+g} · 最终 {total:g}/13 达标{caution}'
        c5=five_detail['components']; c15=fifteen_detail['components']
        items=[
            ('5m 回调收回（硬条件）',c5['pullback_reclaim'],2.0),
            ('5m 突破触发（硬条件）',c5['breakout_trigger'],2.0),
            ('5m BOLL趋势/回调',c5['boll'],1.0),
            ('5m EMA5/10/20结构',c5['ema'],.5),
            ('5m RSI/KDJ动量',c5['momentum'],.5),
            ('15m 高低点趋势结构',c15['structure'],1.0),
            ('15m 支撑/压力位置',c15['position'],.5),
            ('15m BOLL趋势/位置',c15['boll'],1.0),
            ('15m EMA20/50趋势',c15['ema'],1.0),
            ('15m RSI/KDJ动量',c15['momentum'],.5),
            ('1H 趋势修正',trend_1h,1.0),
            ('4H 趋势修正',trend_4h,2.0),
        ]
        results[side]={
            'total':total,'entry_score':base,'five_score':five_score,'fifteen_score':fifteen_score,
            'trend_adjust':trend,'trend_1h':trend_1h,'trend_4h':trend_4h,
            'trend_state_1h':state_1h,'trend_state_4h':state_4h,
            'gate':gate,'mandatory_trigger':mandatory,'eligible':eligible,'required':required,'level':level,
            'position_multiplier':1.0 if eligible else 0.0,'items':items,'reason':reason,'structure':structure,
            'layers':{'5m':five_score,'15m':fifteen_score,'1h':trend_1h,'4h':trend_4h},
            'confirmations':{
                'pullback_reclaim':five_detail['pullback_reclaim'],
                'breakout_trigger':five_detail['breakout_trigger'],
                'volume_confirm':five_detail['volume_confirm'],
                'volume_ratio_5m':five_detail['volume_ratio'],
                'five':five_detail,'fifteen':fifteen_detail,
                'trend_1h':detail_1h,'trend_4h':detail_4h,'structure':structure,
            },
        }
    qualified=[side for side,row in results.items() if row['eligible']]
    if len(qualified)==1:
        side=qualified[0]
    elif len(qualified)==2 and results[qualified[0]]['total']!=results[qualified[1]]['total']:
        side=max(qualified,key=lambda s:results[s]['total'])
    else:
        side='观望'
    why=results[side]['reason'] if side!='观望' else ' / '.join(s+': '+row['reason'] for s,row in results.items())
    return {
        'd':d,'q':q,'h':h,'m':m,'f':f,'side':side,'why':why,'scores':results,
        'threshold':required,'score_max':SCORE_MAX,'entry_score_max':ENTRY_SCORE_MAX,
    }

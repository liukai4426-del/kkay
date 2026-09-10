"""V1.3 layered BTC intraday scoring: 1H environment -> structure -> 15m setup -> 5m trigger."""
import math
from core import indicators

SCORE_MAX = 18
NORMAL_THRESHOLD = 8
COUNTERTREND_THRESHOLD = 11


def _volume_boll_return(rows, current, buy, multiplier=1.3):
    """15m rejection: current candle returns inside Bollinger on >=1.3x prior-20 volume."""
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


def _reversal(rows, buy):
    if len(rows) < 2:
        return False
    c, prev=rows[-1],rows[-2]
    return (c['c'] > c['o'] and c['c'] > prev['h']) if buy else (c['c'] < c['o'] and c['c'] < prev['l'])


def _ema_reclaim(rows, current, previous, buy):
    if len(rows) < 2:
        return False
    c, prev=rows[-1],rows[-2]
    return (prev['c'] <= previous['ema20'] and c['c'] > current['ema20']) if buy else (prev['c'] >= previous['ema20'] and c['c'] < current['ema20'])


def _environment(current, candle, buy):
    strong_opposite=(candle['c'] < current['ema200'] and current['ema20'] < current['ema50'] and current['down']) if buy else (candle['c'] > current['ema200'] and current['ema20'] > current['ema50'] and current['up'])
    if strong_opposite:
        return -3, True, {'ema_order':False,'ema200':False,'slope':False}
    ema_order=current['ema20'] > current['ema50'] if buy else current['ema20'] < current['ema50']
    ema200=candle['c'] > current['ema200'] if buy else candle['c'] < current['ema200']
    slope=current['up'] if buy else current['down']
    return int(ema_order)+int(ema200)+int(slope), False, {'ema_order':ema_order,'ema200':ema200,'slope':slope}


def _setup(rows, current, buy):
    candle=rows[-1]
    rsi=current['rsi'] <= 30 if buy else current['rsi'] >= 70
    boll=candle['l'] <= current['lower'] if buy else candle['h'] >= current['upper']
    combo=rsi and boll
    volume_boll, volume_ratio=_volume_boll_return(rows,current,buy)
    kdj=(current['j'] <= 30 and current['cross_up']) if buy else (current['j'] >= 70 and current['cross_down'])
    reversal=_reversal(rows,buy)
    components={
        'rsi':2*int(rsi),
        'boll':int(boll),
        'combo':int(combo),
        'volume_boll':2*int(volume_boll),
        'kdj':int(kdj),
        'reversal':int(reversal),
    }
    return sum(components.values()), components, volume_ratio


def _trigger(rows, current, previous, buy):
    candle=rows[-1]
    ema_reclaim=_ema_reclaim(rows,current,previous,buy)
    kdj=current['cross_up'] if buy else current['cross_down']
    reversal=_reversal(rows,buy)
    ema_direction=(candle['c'] > current['ema20'] and current['ema20'] > previous['ema20']) if buy else (candle['c'] < current['ema20'] and current['ema20'] < previous['ema20'])
    components={'ema_reclaim':int(ema_reclaim),'kdj':int(kdj),'reversal':int(reversal),'ema_direction':int(ema_direction)}
    return sum(components.values()), components


def _swing_points(rows, lookback, radius=3):
    start=max(0,len(rows)-lookback)
    points=[]
    for i in range(start+radius,len(rows)-radius):
        left=rows[i-radius:i]; right=rows[i+1:i+radius+1]; row=rows[i]
        low=row['l']; high=row['h']
        if low <= min(r['l'] for r in left+right) and (low < min(r['l'] for r in left) or low < min(r['l'] for r in right)):
            points.append({'kind':'support','price':float(low),'i':i})
        if high >= max(r['h'] for r in left+right) and (high > max(r['h'] for r in left) or high > max(r['h'] for r in right)):
            points.append({'kind':'resistance','price':float(high),'i':i})
    return points


def _zones(rows, atr, lookback):
    if not math.isfinite(atr) or atr <= 0:
        return []
    tolerance=.25*atr
    clusters=[]
    for p in _swing_points(rows,lookback):
        same=[z for z in clusters if z['kind']==p['kind'] and abs(z['center']-p['price']) <= tolerance]
        if same:
            z=min(same,key=lambda x:abs(x['center']-p['price']))
            z['points'].append(p); z['center']=sum(x['price'] for x in z['points'])/len(z['points']); z['last_i']=max(z['last_i'],p['i'])
        else:
            clusters.append({'kind':p['kind'],'center':p['price'],'points':[p],'last_i':p['i']})
    out=[]
    for z in clusters:
        tests=len(z['points'])
        if tests < 2:
            continue
        center=z['center']; after=rows[z['last_i']+1:]
        broken=any(r['c'] < center-.3*atr for r in after) if z['kind']=='support' else any(r['c'] > center+.3*atr for r in after)
        if broken:
            continue
        age=max(0,len(rows)-1-z['last_i']); recency=max(0.0,1.0-age/max(1,lookback))
        out.append({'kind':z['kind'],'price':center,'tests':tests,'age':age,'recency':recency,'strong':tests>=3 and recency>=.25})
    return out


def _nearest(zones, price, kind, ahead=None):
    candidates=[]
    for z in zones:
        if z['kind'] != kind:
            continue
        if ahead=='above' and z['price'] <= price:
            continue
        if ahead=='below' and z['price'] >= price:
            continue
        candidates.append(z)
    return min(candidates,key=lambda z:abs(z['price']-price)) if candidates else None


def _structure_context(hour, quarter, h, m, buy, stop_atr=1.0):
    price=float(quarter[-1]['c'])
    hz=_zones(hour,float(h['atr']),120)
    mz=_zones(quarter,float(m['atr']),160)
    favorable='support' if buy else 'resistance'
    opposite='resistance' if buy else 'support'
    hnear=_nearest(hz,price,favorable); mnear=_nearest(mz,price,favorable)
    hdist=abs(hnear['price']-price)/h['atr'] if hnear else math.inf
    mdist=abs(mnear['price']-price)/m['atr'] if mnear else math.inf
    score=0
    if hnear and hdist <= .25:
        score=3 if hnear['strong'] else (2 if hnear.get('recency',0)>=.25 else 1)
    if mnear and mdist <= .25:
        score=max(score,1)
    overlap=bool(hnear and mnear and hdist<=.25 and mdist<=.25 and abs(hnear['price']-mnear['price']) <= max(.25*h['atr'],.25*m['atr']))
    if overlap:
        score=3
    ahead='above' if buy else 'below'
    forward=[]
    for tf,zones in (('1H',hz),('15m',mz)):
        z=_nearest([item for item in zones if item.get('strong')],price,opposite,ahead)
        if z:
            forward.append((abs(z['price']-price),tf,z))
    front=min(forward,key=lambda x:x[0]) if forward else None
    risk=float(m['atr'])*float(stop_atr)
    front_r=front[0]/risk if front and risk>0 else math.inf
    blocked=front_r < 1.0
    penalty=-2 if 1.0 <= front_r < 1.3 else 0
    warning=1.3 <= front_r < 1.5
    return {
        'score':score,'penalty':penalty,'blocked':blocked,'warning':warning,'front_r':front_r,
        'favorable_1h':hnear,'favorable_15m':mnear,
        'front':({'timeframe':front[1],**front[2]} if front else None),
        'overlap':overlap,'zones_1h':hz,'zones_15m':mz,
    }


def _level(total):
    if total >= 14:
        return '高共振信号'
    if total >= 11:
        return '强信号'
    if total >= 8:
        return '普通信号'
    return '未达开仓线'


def signal(hour, quarter, five=None, threshold=8, stop_atr=1.0):
    five=quarter if five is None else five
    h,m,f=indicators(hour),indicators(quarter),indicators(five)
    ph,pm,pf=indicators(hour[:-1]),indicators(quarter[:-1]),indicators(five[:-1])
    hc=hour[-1]
    results={}
    for side in ('做多','做空'):
        buy=side=='做多'
        env,strong_opposite,env_detail=_environment(h,hc,buy)
        setup,setup_detail,volume_ratio=_setup(quarter,m,buy)
        trigger,trigger_detail=_trigger(five,f,pf,buy)
        structure=_structure_context(hour,quarter,h,m,buy,stop_atr)
        raw=env+setup+trigger+structure['score']+structure['penalty']
        total=max(0,min(SCORE_MAX,int(raw)))
        required=max(float(threshold),COUNTERTREND_THRESHOLD if strong_opposite else float(threshold))
        hard_setup=setup>=2
        hard_trigger=trigger>=1
        gate=hard_setup and hard_trigger and not structure['blocked']
        eligible=gate and total>=required
        level=_level(total)
        if structure['blocked']:
            reason=f'前方强结构距离 {structure["front_r"]:.2f}R < 1R，禁止开仓'
        elif not hard_setup:
            reason=f'15m Setup {setup}/8 < 2，禁止开仓'
        elif not hard_trigger:
            reason=f'5m Trigger {trigger}/4 < 1，等待入场触发'
        elif strong_opposite and total<COUNTERTREND_THRESHOLD:
            reason=f'1H强逆势已扣3分；最终 {total}/{SCORE_MAX}，逆势需≥{COUNTERTREND_THRESHOLD}'
        elif eligible:
            reason=f'{level} · {total}/{SCORE_MAX} 达标；进入执行与风险检查'
        else:
            reason=f'{level} · {total}/{SCORE_MAX}，未达开仓阈值 {required:g}'
        items=[
            ('1H 趋势环境',env,3),
            ('15m RSI 极值',setup_detail['rsi'],2),
            ('15m BOLL 外轨',setup_detail['boll'],1),
            ('15m RSI + BOLL 组合',setup_detail['combo'],1),
            ('15m BOLL外破回归 + 成交量≥20均量×1.3',setup_detail['volume_boll'],2),
            ('15m KDJ 转向',setup_detail['kdj'],1),
            ('15m 反转K线',setup_detail['reversal'],1),
            ('15m/1H 支撑阻力结构',structure['score'],3),
            ('5m EMA20 重新突破',trigger_detail['ema_reclaim'],1),
            ('5m KDJ 方向交叉',trigger_detail['kdj'],1),
            ('5m 反转K线突破',trigger_detail['reversal'],1),
            ('5m EMA20 短线方向确认',trigger_detail['ema_direction'],1),
            ('前方结构空间惩罚',structure['penalty'],0),
        ]
        results[side]={
            'total':total,'raw':raw,'gate':gate,'eligible':eligible,'level':level,'required':required,
            'items':items,'reason':reason,'structure':structure,
            'layers':{'environment':env,'setup':setup,'trigger':trigger,'structure':structure['score'],'front_penalty':structure['penalty']},
            'confirmations':{
                '5m':trigger>=1,'15m':setup>=2,'1H':env>0,'strong_opposite':strong_opposite,
                'volume_ratio_15m':volume_ratio,'environment':env_detail,
                'setup':setup_detail,'trigger':trigger_detail,'structure':structure,
            },
        }
    qualified=[s for s,r in results.items() if r['eligible']]
    if len(qualified)==1:
        side=qualified[0]
    elif len(qualified)==2 and results[qualified[0]]['total'] != results[qualified[1]]['total']:
        side=max(qualified,key=lambda s:results[s]['total'])
    else:
        side='观望'
    why=results[side]['reason'] if side!='观望' else ' / '.join(s+': '+r['reason'] for s,r in results.items())
    return {'h':h,'m':m,'f':f,'side':side,'why':why,'scores':results,'threshold':threshold,'score_max':SCORE_MAX}

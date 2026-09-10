"""V1.3.2 layered BTC intraday scoring: 4H structure -> 1H environment -> 15m setup -> 5m trigger."""
import math
from core import indicators

SCORE_MAX = 10.0
NORMAL_THRESHOLD = 4.0


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
    """1H direction uses three independent 0.5-point confirmations; strong countertrend is -1.5 only."""
    strong_opposite=(candle['c'] < current['ema200'] and current['ema20'] < current['ema50'] and current['down']) if buy else (candle['c'] > current['ema200'] and current['ema20'] > current['ema50'] and current['up'])
    if strong_opposite:
        return -1.5, True, {'ema_order':False,'ema200':False,'slope':False}
    ema_order=current['ema20'] > current['ema50'] if buy else current['ema20'] < current['ema50']
    ema200=candle['c'] > current['ema200'] if buy else candle['c'] < current['ema200']
    slope=current['up'] if buy else current['down']
    return .5*(int(ema_order)+int(ema200)+int(slope)), False, {'ema_order':ema_order,'ema200':ema200,'slope':slope}


def _rsi_resonance(m, f, buy):
    """RSI earns points only when 5m and 15m hit the same extreme together."""
    return (m['rsi'] <= 30 and f['rsi'] <= 30) if buy else (m['rsi'] >= 70 and f['rsi'] >= 70)


def _setup(rows, current, buy):
    """15m setup keeps detailed components in 0.5 steps and caps correlated evidence at 2.0."""
    candle=rows[-1]
    boll=candle['l'] <= current['lower'] if buy else candle['h'] >= current['upper']
    volume_boll, volume_ratio=_volume_boll_return(rows,current,buy)
    kdj=(current['j'] <= 30 and current['cross_up']) if buy else (current['j'] >= 70 and current['cross_down'])
    reversal=_reversal(rows,buy)
    components={
        'boll':.5*int(boll),
        'volume_boll':1.0*int(volume_boll),
        'kdj':.5*int(kdj),
        'reversal':.5*int(reversal),
    }
    return min(2.0,sum(components.values())), components, volume_ratio


def _trigger(rows, current, previous, buy):
    """5m only times entry; four 0.5 triggers are capped at 1.5."""
    candle=rows[-1]
    ema_reclaim=_ema_reclaim(rows,current,previous,buy)
    kdj=current['cross_up'] if buy else current['cross_down']
    reversal=_reversal(rows,buy)
    ema_direction=(candle['c'] > current['ema20'] and current['ema20'] > previous['ema20']) if buy else (candle['c'] < current['ema20'] and current['ema20'] < previous['ema20'])
    components={'ema_reclaim':.5*int(ema_reclaim),'kdj':.5*int(kdj),'reversal':.5*int(reversal),'ema_direction':.5*int(ema_direction)}
    return min(1.5,sum(components.values())), components


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


def _structure_context(hour, quarter, h, m, buy, stop_atr=1.0, four=None, q=None):
    """Score favorable 1H/15m/4H zones; preserve V1.3 forward 1H/15m safety filter."""
    price=float(quarter[-1]['c'])
    hz=_zones(hour,float(h['atr']),120)
    mz=_zones(quarter,float(m['atr']),160)
    qz=_zones(four,float(q['atr']),180) if four is not None and q is not None else []
    favorable='support' if buy else 'resistance'
    opposite='resistance' if buy else 'support'
    hnear=_nearest(hz,price,favorable); mnear=_nearest(mz,price,favorable); qnear=_nearest(qz,price,favorable)
    hdist=abs(hnear['price']-price)/h['atr'] if hnear else math.inf
    mdist=abs(mnear['price']-price)/m['atr'] if mnear else math.inf
    qdist=abs(qnear['price']-price)/q['atr'] if qnear and q else math.inf
    h_score=1.0 if hnear and hdist <= .25 else 0.0
    m_score=.5 if mnear and mdist <= .25 else 0.0
    q_score=1.5 if qnear and qdist <= .25 else 0.0
    overlap=bool(hnear and mnear and hdist<=.25 and mdist<=.25 and abs(hnear['price']-mnear['price']) <= max(.25*h['atr'],.25*m['atr']))
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
    penalty=-1.0 if 1.0 <= front_r < 1.3 else 0.0
    warning=1.3 <= front_r < 1.5
    return {
        'score':min(1.5,h_score+m_score),'score_1h':h_score,'score_15m':m_score,'score_4h':q_score,
        'penalty':penalty,'blocked':blocked,'warning':warning,'front_r':front_r,
        'favorable_4h':qnear,'favorable_1h':hnear,'favorable_15m':mnear,
        'front':({'timeframe':front[1],**front[2]} if front else None),
        'overlap':overlap,'zones_4h':qz,'zones_1h':hz,'zones_15m':mz,
    }


def _ema_dynamic_support(hour, h, ph, price, buy):
    """1H EMA20/EMA50 dynamic support/resistance; multiple EMA hits still score only 0.5."""
    atr=float(h['atr'])
    if not math.isfinite(atr) or atr <= 0:
        return 0.0, None
    tolerance=.20*atr
    candidates=(('EMA20',float(h['ema20']),float(ph['ema20'])),('EMA50',float(h['ema50']),float(ph['ema50'])))
    for name,value,previous in candidates:
        slope_ok=value>previous if buy else value<previous
        close_ok=price >= value-.05*atr if buy else price <= value+.05*atr
        if slope_ok and close_ok and abs(price-value) <= tolerance:
            return .5, name
    return 0.0, None


def _level(total):
    if total >= 8.5:
        return '高共振信号'
    if total >= 7.0:
        return '强信号'
    if total >= 5.5:
        return '较强信号'
    if total >= 4.0:
        return '普通信号'
    return '未达开仓线'


def signal(hour, quarter, five=None, threshold=4.0, stop_atr=1.0, four=None):
    five=quarter if five is None else five
    four=hour if four is None else four
    h,m,f,q=indicators(hour),indicators(quarter),indicators(five),indicators(four)
    ph,pm,pf=indicators(hour[:-1]),indicators(quarter[:-1]),indicators(five[:-1])
    hc=hour[-1]
    price=float(quarter[-1]['c'])
    results={}
    for side in ('做多','做空'):
        buy=side=='做多'
        env,strong_opposite,env_detail=_environment(h,hc,buy)
        setup,setup_detail,volume_ratio=_setup(quarter,m,buy)
        trigger,trigger_detail=_trigger(five,f,pf,buy)
        structure=_structure_context(hour,quarter,h,m,buy,stop_atr,four,q)
        ema_support,ema_name=_ema_dynamic_support(hour,h,ph,price,buy)
        rsi_resonance=1.5 if _rsi_resonance(m,f,buy) else 0.0
        raw=env+structure['score_4h']+structure['score']+ema_support+rsi_resonance+setup+trigger+structure['penalty']
        total=max(0.0,min(SCORE_MAX,round(raw*2)/2))
        required=float(threshold)
        hard_setup=setup>=.5
        hard_trigger=trigger>=.5
        gate=hard_setup and hard_trigger and not structure['blocked']
        eligible=gate and total>=required
        level=_level(total)
        if structure['blocked']:
            reason=f'前方强结构距离 {structure["front_r"]:.2f}R < 1R，禁止开仓'
        elif not hard_setup:
            reason=f'15m Setup {setup:g}/2 < 0.5，禁止开仓'
        elif not hard_trigger:
            reason=f'5m Trigger {trigger:g}/1.5 < 0.5，等待入场触发'
        elif eligible:
            reason=f'{level} · {total:g}/{SCORE_MAX:g} 达标；进入执行与风险检查'
        else:
            reason=f'{level} · {total:g}/{SCORE_MAX:g}，未达开仓阈值 {required:g}'
        items=[
            ('1H 趋势环境',env,1.5),
            ('4H 对应支撑/阻力',structure['score_4h'],1.5),
            ('1H 支撑/阻力结构',structure['score_1h'],1.0),
            ('15m 支撑/阻力结构',structure['score_15m'],.5),
            ('1H EMA20/EMA50 动态支撑/压力',ema_support,.5),
            ('5m + 15m RSI 极值共振',rsi_resonance,1.5),
            ('15m BOLL 外轨',setup_detail['boll'],.5),
            ('15m BOLL外破回归 + 成交量≥20均量×1.3',setup_detail['volume_boll'],1.0),
            ('15m KDJ 转向',setup_detail['kdj'],.5),
            ('15m 反转K线',setup_detail['reversal'],.5),
            ('5m EMA20 重新突破',trigger_detail['ema_reclaim'],.5),
            ('5m KDJ 方向交叉',trigger_detail['kdj'],.5),
            ('5m 反转K线突破',trigger_detail['reversal'],.5),
            ('5m EMA20 短线方向确认',trigger_detail['ema_direction'],.5),
            ('前方结构空间惩罚',structure['penalty'],0),
        ]
        results[side]={
            'total':total,'raw':raw,'gate':gate,'eligible':eligible,'level':level,'required':required,
            'items':items,'reason':reason,'structure':structure,
            'layers':{'environment':env,'structure_4h':structure['score_4h'],'structure':structure['score'],
                      'ema_support':ema_support,'rsi_resonance':rsi_resonance,'setup':setup,'trigger':trigger,
                      'front_penalty':structure['penalty']},
            'confirmations':{
                '5m':trigger>=.5,'15m':setup>=.5,'1H':env>0,'4H_structure':structure['score_4h']>0,
                'strong_opposite':strong_opposite,'rsi_resonance':rsi_resonance>0,'ema_support':ema_name,
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
    return {'q':q,'h':h,'m':m,'f':f,'side':side,'why':why,'scores':results,'threshold':threshold,'score_max':SCORE_MAX}

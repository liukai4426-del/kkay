from pathlib import Path


def replace_once(text, old, new, label):
    count=text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected 1 match, got {count}')
    return text.replace(old,new,1)

strategy = r'''"""V1.3 layered BTC intraday scoring: 1H environment -> structure -> 15m setup -> 5m trigger."""
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
        score=3 if hnear['strong'] else 2
    if mnear and mdist <= .25:
        score=max(score,1)
    overlap=bool(hnear and mnear and hdist<=.25 and mdist<=.25 and abs(hnear['price']-mnear['price']) <= max(.25*h['atr'],.25*m['atr']))
    if overlap:
        score=3
    ahead='above' if buy else 'below'
    forward=[]
    for tf,zones in (('1H',hz),('15m',mz)):
        z=_nearest(zones,price,opposite,ahead)
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
'''
Path('strategy.py').write_text(strategy)

# Engine: execution, cost filter, limit-entry lifecycle, JST loss streak.
p=Path('engine.py'); text=p.read_text()
text=replace_once(text,"from datetime import datetime, timezone\n","from datetime import datetime, timezone\nfrom zoneinfo import ZoneInfo\n",'zoneinfo import')
text=text.replace("not 8<=self.score_threshold<=19","not 8<=self.score_threshold<=18")
text=text.replace("评分阈值必须是8—19的整数","评分阈值必须是8—18的整数")
text=replace_once(text,"    limit=float(rounded((ask if buy else bid)*(1+d*s.slippage_bps/10000),meta['tickSz'],buy))\n","    # V1.3 entry is a normal limit order: buy at best bid / sell at best ask; never chase beyond the saved price.\n    limit=float(rounded(bid if buy else ask,meta['tickSz'],not buy))\n",'limit price')
old="""    return dict(side=side,posSide='long' if buy else 'short',exchange_side='buy' if buy else 'sell',\n        px=str(limit),sz=quantity,sl=sl,tp=tp,btc=btc,notional=btc*limit,estimated_loss=btc*per_btc)\n"""
new="""    expected_roundtrip_cost=limit*((2*s.fee_bps+s.slippage_bps)/10000)\n    tp_distance=dist*s.reward_r\n    cost_multiple=tp_distance/expected_roundtrip_cost if expected_roundtrip_cost>0 else math.inf\n    return dict(side=side,posSide='long' if buy else 'short',exchange_side='buy' if buy else 'sell',\n        px=str(limit),sz=quantity,sl=sl,tp=tp,btc=btc,notional=btc*limit,estimated_loss=btc*per_btc,\n        expected_roundtrip_cost=btc*expected_roundtrip_cost,cost_multiple=cost_multiple)\n"""
text=replace_once(text,old,new,'plan cost metadata')
text=replace_once(text,"self.data={'active':None,'last_bar':0,'last_close':0,'streak':0,'day':'','peak':0,'halt':''}","self.data={'active':None,'last_bar':0,'last_close':0,'streak':0,'streak_day':'','streak_notice_day':'','day':'','peak':0,'halt':''}",'store state')
text=replace_once(text,"        if self.store.data['streak']>=settings.consecutive_losses:\n            raise Halt('连续亏损已达上限')\n        self.enabled=True; self.stopped=False; self.poll_at=time.monotonic()\n","        self._reset_streak_day()\n        self.enabled=True; self.stopped=False; self.poll_at=time.monotonic()\n        if self.store.data['streak']>=settings.consecutive_losses:\n            self.emit('log',f'日本时间本日已连续亏损 {self.store.data[\"streak\"]} 次：仅停止新开仓，次日自动恢复')\n",'arm streak')
text=text.replace("自动交易启动；5m入场、15m结构、1H环境；SL/TP使用15m ATR；最终评分达阈值才允许进入风控；每根5m信号最多一次","自动交易启动；1H环境→15m/1H结构→15m Setup→5m Trigger；限价开仓、市场价退出；SL/TP使用15m ATR；每根5m信号最多一次")
old_stop="""    def stop(self):\n        self.enabled=False; self.stopped=True\n        self.emit('log','已停止新开仓；继续监控本程序仓位。交易所TP/SL不撤销；已发送请求无法撤回')\n\n    def daily(self,equity):\n"""
new_stop="""    def stop(self):\n        self.enabled=False; self.stopped=True\n        p=self.store.data.get('active') if self.store else None\n        if p and not p.get('filled') and not p.get('cancel_requested'):\n            self._cancel_pending_entry(p,'手动停止自动交易')\n        self.emit('log','已停止新开仓；未成交限价开仓会撤销，已有仓位继续监控且交易所TP/SL不撤销')\n\n    def _reset_streak_day(self):\n        if not self.store:\n            return ''\n        day=datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m-%d')\n        state=self.store.data\n        if state.get('streak_day')!=day:\n            state.update(streak_day=day,streak=0,streak_notice_day='')\n            self.store.save()\n        return day\n\n    def daily(self,equity):\n"""
text=replace_once(text,old_stop,new_stop,'stop/streak helper')
text=text.replace("state.update(day=day,peak=equity,streak=0)","state.update(day=day,peak=equity)")
text=replace_once(text,"value=signal(h,m,f,self.settings.score_threshold if self.settings else 8)","value=signal(h,m,f,self.settings.score_threshold if self.settings else 8,self.settings.stop_atr if self.settings else 1.0)",'signal arguments')
old_streak="""        remaining=self.daily(equity)\n        state=self.store.data; s=self.settings\n        if state['streak']>=s.consecutive_losses:\n            raise Halt('达到连续亏损次数上限')\n"""
new_streak="""        remaining=self.daily(equity)\n        self._reset_streak_day()\n        state=self.store.data; s=self.settings\n        if state['streak']>=s.consecutive_losses:\n            if state.get('streak_notice_day')!=state.get('streak_day'):\n                state['streak_notice_day']=state.get('streak_day',''); self.store.save()\n                self.emit('log',f'日本时间本日连续净亏损已达 {s.consecutive_losses} 次：停止新开仓；已有仓位/TP/SL继续管理，次日自动恢复')\n            return\n"""
text=replace_once(text,old_streak,new_streak,'cycle streak')
old_plan="""        plan=make_plan(s,market['side'],ticker,self.x.instrument(),market['m']['atr'],available,remaining)\n        # Check settings explicitly; set only isolated leverage for this side, never account mode.\n"""
new_plan="""        plan=make_plan(s,market['side'],ticker,self.x.instrument(),market['m']['atr'],available,remaining)\n        # Expected TP space must cover at least 2x estimated round-trip execution cost.\n        if plan['cost_multiple'] < 2:\n            state['last_bar']=market['bar']; self.store.save()\n            self.emit('log',f\"预计TP空间仅为往返成本 {plan['cost_multiple']:.2f} 倍（最低2倍），本轮跳过\")\n            return\n        # Check settings explicitly; set only isolated leverage for this side, never account mode.\n"""
text=replace_once(text,old_plan,new_plan,'cost filter')
text=replace_once(text,"state['active']=dict(plan,client_id=cid,algo_id=aid,submitted=time.time(),equity_before=equity,filled=False,\n                             score=score['total'],score_items=score.get('items',[]))","state['active']=dict(plan,client_id=cid,algo_id=aid,submitted=time.time(),expires=time.time()+300,equity_before=equity,filled=False,cancel_requested=False,\n                             score=score['total'],score_items=score.get('items',[]))",'active limit metadata')
text=text.replace("'ordType':'fok'","'ordType':'limit'")
text=text.replace("已提交逐仓FOK开仓请求（{score.get('level','信号')} · 评分 {score['total']}/19），附带TP/SL；尚不代表成交或保护单生效","已提交逐仓限价开仓请求（{score.get('level','信号')} · 评分 {score['total']}/18），最多等待1根5m K线；附带市场价TP/SL")
start=text.index("    def reconcile(self):")
qty=text.index("        qty=sum(abs(float(r['pos']))",start)
new_reconcile=r'''    def _cancel_pending_entry(self,p,reason):
        if p.get('filled') or p.get('cancel_requested'):
            return
        p['cancel_requested']=time.time(); p['cancel_reason']=reason; self.store.save()
        # Persist before the write: an uncertain cancel response must never trigger a duplicate order.
        self.x.post('/api/v5/trade/cancel-order',{'instId':INSTRUMENT,'clOrdId':p['client_id']})
        self.store.record('撤销限价开仓',{'client_id':p['client_id'],'reason':reason})
        self.emit('log','已发送限价开仓撤单请求：'+reason+'；等待交易所确认')

    def reconcile(self):
        state=self.store.data; p=state['active']
        if not p:
            return
        order=self.x.order(p['client_id'])
        status=order.get('state')
        filled=float(order.get('accFillSz') or 0)
        positions=self.x.positions()
        if any(r.get('mgnMode')!='isolated' or r.get('posSide')!=p['posSide'] or abs(float(r['pos']))>float(p['sz'])+1e-10 for r in positions):
            raise Halt('仓位与程序记录不一致，请立即在OKX核对')
        if status in ('live','partially_filled'):
            if filled>0 or positions:
                self._cancel_pending_entry(p,'限价单出现部分成交，撤销剩余数量并核对保护')
                return
            if p.get('cancel_requested'):
                if time.time()-float(p['cancel_requested'])>15:
                    raise Halt('限价撤单状态长时间无法核实；请到OKX核对，禁止重复开仓')
                return
            if time.time() >= float(p.get('expires',p['submitted']+300)):
                self._cancel_pending_entry(p,'限价挂单已等待1根5m K线')
            return
        if status=='canceled' and filled<=0:
            if positions:
                raise Halt('限价订单已取消但仓位非空')
            state['active']=None; self.store.save()
            self.store.record('限价单未成交',p)
            self.emit('log','限价开仓未成交/已撤销；不计为交易，不触发平仓冷却，该信号不重试')
            return
        if status not in ('filled','canceled'):
            if time.time()-p['submitted']>15:
                raise Halt('订单状态长时间不确定，禁止重复开仓；请到OKX核对')
            return
        if filled<=0:
            raise Halt('成交状态异常')
        p['filled']=True; p['filled_sz']=filled; self.store.save()
        if not positions:
            if time.time()-p['submitted']<10:
                return
            equity,_=self.x.balance()
            pnl=equity-p['equity_before']
            self._reset_streak_day()
            state['streak']=state['streak']+1 if pnl<0 else 0
            state['active']=None; state['last_close']=time.time(); self.store.save()
            self.store.record('仓位归零',dict(client_id=p['client_id'],equity_change=pnl,side=p['side'],px=p['px'],sz=p['sz'],score=p.get('score')))
            self.emit('log',f'仓位已归零；本轮USDT净权益变化 {pnl:+.4f}；日本时间连续亏损 {state["streak"]}/{self.settings.consecutive_losses}')
            return
'''
text=text[:start]+new_reconcile+text[qty:]
text=replace_once(text,"                self.store.data['streak']=self.store.data['streak']+1 if pnl<0 else 0\n","                self._reset_streak_day()\n                self.store.data['streak']=self.store.data['streak']+1 if pnl<0 else 0\n",'ack streak reset')
p.write_text(text)

# Exchange: cancellation is the only new write route; POSTs still never retry.
p=Path('exchange.py'); text=p.read_text()
text=replace_once(text,"allowed={'/api/v5/trade/order','/api/v5/account/set-leverage'}","allowed={'/api/v5/trade/order','/api/v5/trade/cancel-order','/api/v5/account/set-leverage'}",'cancel allowlist')
p.write_text(text)

# UI: version, layered scoring, 18-point scale, JST streak wording, limit-entry wording.
p=Path('app.py'); text=p.read_text()
text=text.replace("OKX Local 1.2.3 · BTC 策略控制台","OKX Local 1.3 · BTC 策略控制台")
text=text.replace("BTC / USDT   ·   V1.2.3 评分机制版","BTC / USDT   ·   V1.3 分层结构策略版")
text=text.replace("'consecutive_losses':'连续亏损停机次数'","'consecutive_losses':'日本时间日内连续亏损停开次数（默认3）'")
text=text.replace("'slippage_bps':'FOK限价偏移 / SL滑点预算 bps'","'slippage_bps':'价差 / 市价退出滑点预算 bps'")
text=text.replace("'score_threshold':'自动开仓评分阈值 8—19（默认8）'","'score_threshold':'自动开仓评分阈值 8—18（默认8）'")
text=text.replace("V1.2.3 最高19分 · ≥8开仓 · 8–10普通 / 11–14强 / 15+高共振","V1.3 最高18分 · 1H环境 → 结构 → 15m Setup → 5m Trigger · ≥8开仓")
text=text.replace("— / 19","— / 18")
text=text.replace("maximum=19","maximum=18")
text=text.replace("/19","/18")
text=text.replace("最高19分","最高18分")
text=text.replace("8–10普通 / 11–14强 / 15+高共振；1H强逆势 -3分。","8–10普通 / 11–13强 / 14+高共振；1H强逆势 -3分且最终需≥11。15m Setup≥2、5m Trigger≥1；前方结构<1R禁止开仓。")
text=text.replace("if defaults.get('score_threshold') == 7:\n                    defaults['score_threshold'] = 8","if defaults.get('score_threshold') == 7:\n                    defaults['score_threshold'] = 8\n                if defaults.get('score_threshold',8) > 18:\n                    defaults['score_threshold'] = 18")
p.write_text(text)

# Keep old network/history suites, skip only obsolete V1.2.3 score assertions.
p=Path('test_v11.py'); text=p.read_text()
text=replace_once(text,"class Scores(unittest.TestCase):","@unittest.skip('V1.2.3 scoring replaced by V1.3 layered tests')\nclass Scores(unittest.TestCase):",'skip old scores')
p.write_text(text)

# Update execution tests from FOK to one-candle limit lifecycle and temporary streak guard.
p=Path('test_engine.py'); text=p.read_text()
text=text.replace("self.assertEqual(b['tdMode'],'isolated'); self.assertEqual(b['ordType'],'fok')","self.assertEqual(b['tdMode'],'isolated'); self.assertEqual(b['ordType'],'limit')")
text=text.replace("        with self.assertRaises(Halt): self.e.cycle()\n        self.assertEqual(len([x for x in self.x.writes if x[0].endswith('/order')]),1)","        self.e.cycle()\n        self.assertEqual(len([x for x in self.x.writes if x[0].endswith('/order')]),1)",1)
text=text.replace("    def test_streak_blocks(self):\n        self.e.store.data['streak']=3\n        with self.assertRaises(Halt): self.e.cycle()\n","    def test_streak_blocks(self):\n        self.e._reset_streak_day(); self.e.store.data['streak']=3; self.e.store.save()\n        before=len(self.x.writes); self.e.cycle()\n        self.assertEqual(len(self.x.writes),before); self.assertTrue(self.e.enabled)\n")
text=text.replace("    def test_fok_cancel_cooldown(self):\n        self.active(); self.x.ord={'state':'canceled','accFillSz':'0'}; self.e.cycle()\n        self.assertIsNone(self.e.store.data['active']); self.assertGreater(self.e.store.data['last_close'],0)\n","    def test_limit_cancel_is_not_a_trade(self):\n        self.active(); self.x.ord={'state':'canceled','accFillSz':'0'}; self.e.cycle()\n        self.assertIsNone(self.e.store.data['active']); self.assertEqual(self.e.store.data['last_close'],0)\n")
p.write_text(text)

# Dedicated V1.3 tests.
Path('test_v13.py').write_text(r'''import unittest
from strategy import _environment, _setup, _trigger, _level, SCORE_MAX
from engine import Settings, make_plan
import test_engine


class V13ScoreTests(unittest.TestCase):
    def test_levels_and_max(self):
        self.assertEqual(SCORE_MAX,18)
        self.assertEqual((_level(8),_level(10)),('普通信号','普通信号'))
        self.assertEqual((_level(11),_level(13)),('强信号','强信号'))
        self.assertEqual((_level(14),_level(18)),('高共振信号','高共振信号'))

    def test_environment_aligned_and_countertrend(self):
        aligned={'ema20':110,'ema50':100,'ema200':90,'up':True,'down':False}
        self.assertEqual(_environment(aligned,{'c':120},True)[0],3)
        opposite={'ema20':90,'ema50':100,'ema200':110,'up':False,'down':True}
        score,strong,_=_environment(opposite,{'c':80},True)
        self.assertEqual(score,-3); self.assertTrue(strong)

    def test_setup_caps_at_eight(self):
        rows=[{'o':100,'h':101,'l':99,'c':100,'v':100} for _ in range(21)]
        rows[-2]={'o':100,'h':101,'l':99,'c':100,'v':100}
        rows[-1]={'o':95,'h':102,'l':89,'c':102,'v':140}
        current={'rsi':20,'lower':90,'upper':110,'j':20,'cross_up':True,'cross_down':False}
        total,parts,ratio=_setup(rows,current,True)
        self.assertEqual(total,8); self.assertGreaterEqual(ratio,1.3)
        self.assertEqual(sum(parts.values()),8)

    def test_trigger_is_recovery_not_oversold_requirement(self):
        rows=[{'o':100,'h':101,'l':99,'c':100},{'o':99,'h':103,'l':98,'c':103}]
        current={'ema20':101,'cross_up':True,'cross_down':False}
        previous={'ema20':100}
        total,parts=_trigger(rows,current,previous,True)
        self.assertEqual(total,4); self.assertEqual(sum(parts.values()),4)

    def test_limit_plan_and_cost_ratio(self):
        p=make_plan(Settings(), '做多', test_engine.TICK, test_engine.META, 200, 100, 3)
        self.assertEqual(float(p['px']),59999.9)
        self.assertGreaterEqual(p['cost_multiple'],1.99)

    def test_default_three_loss_guard(self):
        self.assertEqual(Settings().consecutive_losses,3)


if __name__=='__main__': unittest.main(verbosity=2)
''')

# Release note.
Path('RELEASE-1.3.md').write_text('''# OKX Local V1.3\n\n- 分层评分：1H 环境、15m/1H 支撑阻力、15m Setup、5m Trigger；最高18分。\n- 正常市场最终评分 >=8；强逆势先扣3分且最终 >=11。\n- 硬条件：15m Setup >=2、5m Trigger >=1；前方强反向结构 <1R 禁止开仓，1.0–1.3R 扣2分。\n- 支撑阻力：1H回看120根、15m回看160根；Swing左右各3根；0.25 ATR聚类；至少2次测试，3次为强结构；0.3 ATR收盘突破判失效。\n- 开仓改为普通限价委托，最多等待1根5m K线；未成交自动撤销，不算一笔交易。\n- 平仓继续使用市场价：程序主动平仓为market；交易所附带TP/SL使用 -1 市价执行。\n- 日本时间自然日连续3笔净亏损后，仅停止当日新开仓；已有仓位继续管理，次日自动恢复。\n- 增加交易成本过滤：理论TP空间至少覆盖预计往返成本2倍。\n- V1.2.2 的K线、网络、仓位和保护单 fail-closed 安全机制继续保留。\n\n测试版仍未完成真实资金端到端验收，不保证盈利、限价成交或止损成交价格。\n''')

# Build workflow for the V1.3 branch/artifact.
p=Path('.github/workflows/build-mac.yml'); text=p.read_text()
text=text.replace('name: Build Intel Mac App 1.2.3 Per-Signal 19-Point Scoring','name: Build Intel Mac App 1.3 Layered Structure Strategy')
text=text.replace('- codex/v1-2-3-scoring','- codex/v1-3-strategy')
text=text.replace('Test order engine, V1.2.3 19-point per-signal scoring, 15m ATR, network and stability guards','Test V1.3 layered scoring, limit execution, 3-loss guard, network and stability guards')
text=text.replace('python -m unittest -v test_engine test_v11 test_candles test_v122','python -m unittest -v test_engine test_v11 test_v13 test_candles test_v122')
text=text.replace('          cp RELEASE-1.2.3.md installer/RELEASE-1.2.3.md','          cp RELEASE-1.2.3.md installer/RELEASE-1.2.3.md\n          cp RELEASE-1.3.md installer/RELEASE-1.3.md')
text=text.replace('OKXLocal-1.2.3','OKXLocal-1.3')
p.write_text(text)

print('V1.3 patch applied')

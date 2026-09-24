"""KAYTRADE V1.3.8 strategy/runtime update.

- LIMIT entries for long and short
- isolated leverage 1-50x
- 1m Trigger replaces 5m Trigger and is both score (+1 max) and hard gate
- 5m+15m RSI becomes overheat/oversold penalty
- adverse 1H/4H structures deduct -1/-1.5
- 5m and 15m MACD momentum + Bollinger trend each add up to +2
- entry/add-on cadence and dedupe use the latest CLOSED 1m candle
"""
import math
import time
import uuid
from dataclasses import asdict

from v137_cancel_fix import apply as apply_v137_cancel_fix
apply_v137_cancel_fix()

import app
import engine
import strategy
import v135_patch
import v137_strategy_patch as v137
from core import indicators, ema

V138_THRESHOLD=3.5
ONE_MINUTE_STEP=60000
EXPECTED_COST_MIN=1.20


def _validate_settings(self):
    if abs(float(self.score_threshold)-V138_THRESHOLD)>1e-9:
        raise engine.Halt('V1.3.8自动开仓评分门槛固定为3.5，不可修改')
    for name,value in asdict(self).items():
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise engine.Halt(name+' 必须是有限正数')
    if int(self.leverage)!=self.leverage or not 1<=self.leverage<=50:
        raise engine.Halt('杠杆范围1—50倍（整数）')
    if int(self.consecutive_losses)!=self.consecutive_losses or self.consecutive_losses>20:
        raise engine.Halt('连续亏损上限必须是1—20的整数')
    if self.daily_loss>self.capital or self.max_notional>self.capital*self.leverage:
        raise engine.Halt('日亏损/名义仓位超出资金与杠杆范围')
    if not .6<=self.stop_atr<=3:
        raise engine.Halt('ATR止损倍数范围0.6—3')
    if abs(self.reward_r-2.0)>1e-9:
        raise engine.Halt('V1.3.8止盈固定为2R')
    if abs(self.fee_bps-2.0)>1e-9 or abs(self.taker_fee_bps-5.0)>1e-9 or abs(self.slippage_bps-5.0)>1e-9:
        raise engine.Halt('V1.3.8成本参数固定：Maker 2bps / Taker 5bps / 滑点预算5bps')
    return self


def _ema_series(values,period):
    return ema([float(v) for v in values],period)


def _macd(rows):
    close=[float(r['c']) for r in rows]
    e12=_ema_series(close,12); e26=_ema_series(close,26)
    dif=[a-b for a,b in zip(e12,e26)]
    dea=_ema_series(dif,9)
    hist=[2*(a-b) for a,b in zip(dif,dea)]
    return {'dif':dif[-1],'dea':dea[-1],'hist':hist[-1],'prev_hist':hist[-2]}


def _trend_score(rows,current,previous,buy):
    macd=_macd(rows)
    if buy:
        macd_ok=macd['dif']>macd['dea'] and macd['hist']>0 and macd['hist']>=macd['prev_hist']
        boll_ok=float(rows[-1]['c'])>float(current['middle']) and float(current['middle'])>float(previous['middle'])
    else:
        macd_ok=macd['dif']<macd['dea'] and macd['hist']<0 and macd['hist']<=macd['prev_hist']
        boll_ok=float(rows[-1]['c'])<float(current['middle']) and float(current['middle'])<float(previous['middle'])
    return float(int(macd_ok)+int(boll_ok)),{'macd':macd_ok,'boll':boll_ok,'macd_values':macd}


def _rsi_penalty(m15,m5,buy):
    a=float(m15['rsi']); b=float(m5['rsi'])
    if buy:
        if a>80 and b>80:return -2.0
        if a>75 and b>75:return -1.0
    else:
        if a<20 and b<20:return -2.0
        if a<25 and b<25:return -1.0
    return 0.0


def _adverse_structure(rows,current,price,buy,weight,lookback):
    zones=strategy._zones(rows,float(current['atr']),lookback)
    kind='resistance' if buy else 'support'
    ahead='above' if buy else 'below'
    near=strategy._nearest(zones,price,kind,ahead)
    distance=(abs(float(near['price'])-price)/float(current['atr'])) if near else math.inf
    hit=bool(near and distance<=.25)
    return (-float(weight) if hit else 0.0),near,distance,zones


def _favorable_15m(rows,current,price,buy):
    zones=strategy._zones(rows,float(current['atr']),160)
    kind='support' if buy else 'resistance'
    near=strategy._nearest(zones,price,kind)
    distance=(abs(float(near['price'])-price)/float(current['atr'])) if near else math.inf
    return (.5 if near and distance<=.25 else 0.0),near,distance,zones


def v138_signal(hour,quarter,five,one,stop_atr=1.0,four=None,day=None):
    four=hour if four is None else four
    day=four if day is None else day
    h,m,f,o,q,d=indicators(hour),indicators(quarter),indicators(five),indicators(one),indicators(four),indicators(day)
    po=indicators(one[:-1]); pf=indicators(five[:-1]); pm=indicators(quarter[:-1])
    price=float(one[-1]['c']); hc=hour[-1]; qc=four[-1]
    results={}
    for side in ('做多','做空'):
        buy=side=='做多'
        daily_ema,daily_name,daily_detail=strategy._daily_ema_zone(day,d,price,buy)
        setup,setup_detail,volume_ratio=strategy._setup(quarter,m,buy)
        trigger,trigger_detail=strategy._trigger(one,o,po,buy)
        trend5,trend5_detail=_trend_score(five,f,pf,buy)
        trend15,trend15_detail=_trend_score(quarter,m,pm,buy)
        favorable15,fav15_zone,fav15_dist,_=_favorable_15m(quarter,m,price,buy)
        structure1,zone1,dist1,zones1=_adverse_structure(hour,h,price,buy,1.0,120)
        structure4,zone4,dist4,zones4=_adverse_structure(four,q,price,buy,1.5,180)
        trend1,opp1,trend1_detail=strategy._trend_penalty(h,hc,buy)
        trend4,opp4,trend4_detail=strategy._trend_penalty(q,qc,buy)
        rsi_penalty=_rsi_penalty(m,f,buy)
        forward=strategy._structure_context(hour,quarter,h,m,buy,stop_atr,four,q)
        front_penalty=float(forward.get('penalty') or 0.0)
        raw=daily_ema+favorable15+setup+trigger+trend5+trend15+rsi_penalty+structure1+structure4+trend1+trend4+front_penalty
        total=max(0.0,min(10.0,round(raw*2)/2))
        tier,multiplier,level=v137._tier(total)
        gate=trigger>0 and not bool(forward.get('blocked'))
        eligible=gate and tier>0
        if forward.get('blocked'):
            reason=f'前方强结构距离 {float(forward.get("front_r") or 0):.2f}R < 1R，禁止开仓'
        elif trigger<=0:
            reason='1m Trigger 0/1.0，禁止开仓/加仓'
        elif eligible:
            reason=f'{level} · {total:g}/10 达标；1m Trigger {trigger:g}>0'
        else:
            reason=f'{total:g}/10，未达固定开仓门槛3.5'
        items=[
            ('1D EMA5/10/20 支撑/压力',daily_ema,3.0),
            ('15m 有利支撑/阻力结构',favorable15,.5),
            ('15m Setup（BOLL/量/KDJ/反转）',setup,1.5),
            ('1m Trigger（EMA/KDJ/反转）',trigger,1.0),
            ('5m 趋势（MACD量能+BOLL）',trend5,2.0),
            ('15m 趋势（MACD量能+BOLL）',trend15,2.0),
            ('5m+15m RSI过热/超卖惩罚',rsi_penalty,0),
            ('1H 不利支撑/阻力结构',structure1,0),
            ('4H 不利支撑/阻力结构',structure4,0),
            ('1H 反向趋势惩罚',trend1,0),
            ('4H 反向趋势惩罚',trend4,0),
            ('前方结构空间惩罚',front_penalty,0),
        ]
        structure=dict(forward)
        structure.update(adverse_1h=zone1,adverse_1h_distance_atr=dist1,adverse_4h=zone4,adverse_4h_distance_atr=dist4,
                         favorable_15m=fav15_zone,favorable_15m_distance_atr=fav15_dist,zones_1h=zones1,zones_4h=zones4)
        results[side]={
            'total':total,'raw':raw,'gate':gate,'eligible':eligible,'level':f'{level} · {multiplier:g}×仓位' if tier else level,
            'required':V138_THRESHOLD,'position_multiplier':multiplier if eligible else 0.0,'signal_tier':tier,
            'items':items,'reason':reason,'structure':structure,
            'layers':{'daily_ema':daily_ema,'structure_15m':favorable15,'setup':setup,'trigger':trigger,'trend_5m':trend5,'trend_15m':trend15,
                      'rsi_penalty':rsi_penalty,'structure_penalty_1h':structure1,'structure_penalty_4h':structure4,
                      'trend_penalty_1h':trend1,'trend_penalty_4h':trend4,'front_penalty':front_penalty},
            'confirmations':{'1m':trigger>0,'trigger':trigger_detail,'setup':setup_detail,'volume_ratio_15m':volume_ratio,
                             'trend_5m':trend5_detail,'trend_15m':trend15_detail,'rsi_5m':f['rsi'],'rsi_15m':m['rsi'],
                             'daily_ema':daily_name,'daily_ema_detail':daily_detail,'trend_1h':trend1_detail,'trend_4h':trend4_detail,
                             '1H_countertrend':opp1,'4H_countertrend':opp4,'structure':structure},
        }
    qualified=[side for side,row in results.items() if row['eligible']]
    if len(qualified)==1:selected=qualified[0]
    elif len(qualified)==2 and results[qualified[0]]['total']!=results[qualified[1]]['total']:
        selected=max(qualified,key=lambda side:results[side]['total'])
    else:selected='观望'
    why=results[selected]['reason'] if selected!='观望' else ' / '.join(side+': '+row['reason'] for side,row in results.items())
    return {'d':d,'q':q,'h':h,'m':m,'f':f,'o':o,'side':selected,'why':why,'scores':results,'threshold':V138_THRESHOLD,'score_max':10.0}


def _expected_1m(self):
    return int(self.market_now()*1000)//ONE_MINUTE_STEP*ONE_MINUTE_STEP-ONE_MINUTE_STEP


def _refresh_market(self):
    try:
        d=self.x.candles('1Dutc'); q=self.x.candles('4H'); h=self.x.candles('1H'); m=self.x.candles('15m'); f=self.x.candles('5m'); o=self.x.candles('1m')
        self._verify_latest('1D',d[-1]['t'],86400000); self._verify_latest('4H',q[-1]['t'],14400000)
        self._verify_latest('1H',h[-1]['t'],3600000); self._verify_latest('15m',m[-1]['t'],900000)
        self._verify_latest('5m',f[-1]['t'],300000); self._verify_latest('1m',o[-1]['t'],ONE_MINUTE_STEP)
    except Exception:
        raise
    value=v138_signal(h,m,f,o,self.settings.stop_atr if self.settings else 1.0,four=q,day=d)
    self.market=dict(value,bar=o[-1]['t'],bar1m=o[-1]['t'],bar5m=f[-1]['t'],bar15=m[-1]['t'],bar1h=h[-1]['t'],bar4h=q[-1]['t'],bar1d=d[-1]['t'],close=o[-1]['c'])
    self.market_at=time.time(); self.market_monotonic=time.monotonic(); self._candle_recovered(); self.emit('market',self.market)


def _prepare_order(self,side,score,market,equity,available,remaining):
    tier,multiplier,level=v137._tier(score['total'])
    ticker=self.x.ticker(); self.emit('ticker',ticker)
    if abs(float(ticker['last'])-float(market['close']))>.3*float(market['h']['atr']):
        self.emit('log','价格偏离1m触发价超过0.3 ATR，本轮不追价'); return None
    meta=self.x.instrument(); v137._CURRENT_CONTEXT.update(bar=market['bar'],tier=tier,level=level,score=float(score['total']))
    try:plan=v137._v137_make_plan(self.settings,side,ticker,meta,market['m']['atr'],available,remaining,multiplier)
    except engine.Halt as exc:
        message=str(exc)
        if any(x in message for x in ('买卖价差过大','风险预算不足以满足最小下单量','无效信号或ATR','止盈止损价格非法')):
            self.emit('log','V1.3.8本轮自动开仓跳过：'+message); return None
        raise
    if float(plan.get('expected_fee_multiple') or 0)<EXPECTED_COST_MIN:
        self.emit('log',f"V1.3.8成本过滤：2R TP仅为预计手续费 {float(plan.get('expected_fee_multiple') or 0):.2f} 倍（最低{EXPECTED_COST_MIN:.2f}倍）；未提交OKX订单")
        return None
    return plan,tier,multiplier,level


def _set_leverage(self,plan):
    try:
        self.x.post('/api/v5/account/set-leverage',{'instId':engine.INSTRUMENT,'lever':str(self.settings.leverage),'mgnMode':'isolated','posSide':plan['posSide']})
    except Exception as exc:
        if getattr(exc,'write_rejected',False):
            self.enabled=False; self.stopped=True; self.startup_buffer_until=0.0
            self.emit('log',f'OKX明确拒绝设置逐仓杠杆：{exc}；未提交开仓订单，自动新开仓已停止'); return False
        raise
    infos=self.x.get('/api/v5/account/leverage-info',{'instId':engine.INSTRUMENT,'mgnMode':'isolated'},True)
    if not any(i.get('posSide')==plan['posSide'] and float(i['lever'])==self.settings.leverage and i.get('mgnMode')=='isolated' for i in infos):
        raise engine.Halt('逐仓杠杆回读不一致')
    return True


def _submit_initial(self,market,score,equity,available,remaining):
    prepared=_prepare_order(self,market['side'],score,market,equity,available,remaining)
    if not prepared:return
    plan,tier,multiplier,level=prepared
    if not _set_leverage(self,plan) or not self.enabled:return
    self._verify_latest('1m',market['bar'],ONE_MINUTE_STEP)
    cid='mac'+uuid.uuid4().hex[:28]; algo='br'+uuid.uuid4().hex[:28]; state=self.store.data; before_last=state.get('last_bar')
    leg=dict(plan,client_id=cid,order_id='',bracket_id=algo,submitted=time.time(),expires=time.time()+60,
             filled=False,state='pending',cancel_requested=False,cancel_on_reconcile=False,tier=tier,score=float(score['total']),
             signal_bar=market['bar'],counted=True,protected=False)
    root=dict(plan,client_id=cid,order_id='',bracket_id=algo,submitted=leg['submitted'],expires=leg['expires'],equity_before=equity,
              filled=False,protected=False,score=float(score['total']),signal_bar=market['bar'],signal_tier=tier,signal_level=level,
              v137=True,v138=True,version='1.3.8',legs=[leg],tier_counts={'1':0,'2':0,'3':0},highest_tier=tier,last_entry_bar=market['bar'])
    root['tier_counts'][str(tier)]=1; state['last_bar']=market['bar']; state['active']=root; self.store.save()
    body={'instId':engine.INSTRUMENT,'tdMode':'isolated','side':plan['exchange_side'],'posSide':plan['posSide'],'ordType':'limit','px':plan['px'],'sz':plan['sz'],'clOrdId':cid,
          'attachAlgoOrds':[{'attachAlgoClOrdId':algo,'tpOrdKind':'condition','tpTriggerPx':plan['tp'],'tpOrdPx':'-1','tpTriggerPxType':'last',
                             'slTriggerPx':plan['sl'],'slOrdPx':'-1','slTriggerPxType':'last'}]}
    try:reply=self.x.post('/api/v5/trade/order',body)
    except Exception as exc:
        if getattr(exc,'write_rejected',False):
            state['active']=None; state['last_bar']=before_last; self.store.save(); self.enabled=False; self.stopped=True
            self.emit('log',f'OKX明确拒绝V1.3.8限价开仓：{exc}；未产生新订单，已释放占位；自动新开仓已停止'); return
        raise
    row=reply[0] if reply else {}; oid=str(row.get('ordId') or '') if isinstance(row,dict) else ''
    if not oid:raise engine.Halt('V1.3.8开仓响应缺少ordId；本地占位已保留，禁止重复提交')
    leg['order_id']=oid; root['order_id']=oid; root['okx_ack_at']=time.time(); self.store.save()
    self.store.record('V1.3.8提交限价开仓',{'tier':tier,'score':score['total'],'client_id':cid,'order_id':oid,'plan':plan})
    self.emit('log',f'V1.3.8 {level}：已提交限价{market["side"]}；1m Trigger有效；{multiplier:g}×风险仓位，整仓TP=2R，SL=1R')
    self.emit('plan',plan)


def _submit_addon(self,p,market,score,equity,available,remaining):
    tier,multiplier,level=v137._tier(score['total']); allowed,_=v137._allow_entry(p,tier,market['bar'])
    if not allowed:return
    current_risk=sum(float(leg.get('estimated_loss') or 0) for leg in p.get('legs',[]) if leg.get('state')=='filled')
    risk_remaining=float(remaining)-current_risk
    if risk_remaining<=0:return
    prepared=_prepare_order(self,p['side'],score,market,equity,available,risk_remaining)
    if not prepared:return
    plan,tier,multiplier,level=prepared; meta=self.x.instrument()
    current_notional=sum(float(leg.get('notional') or 0) for leg in p.get('legs',[]) if leg.get('state')=='filled')
    plan=v137._cap_plan(plan,meta,float(self.settings.max_notional)-current_notional)
    if not _set_leverage(self,plan) or not self.enabled:return
    self._verify_latest('1m',market['bar'],ONE_MINUTE_STEP)
    cid='mac'+uuid.uuid4().hex[:28]; algo='br'+uuid.uuid4().hex[:28]; state=self.store.data; before_last=state.get('last_bar')
    leg=dict(plan,client_id=cid,order_id='',bracket_id=algo,submitted=time.time(),expires=time.time()+60,filled=False,state='pending',
             cancel_requested=False,cancel_on_reconcile=False,tier=tier,score=float(score['total']),signal_bar=market['bar'],counted=True,protected=False)
    state['last_bar']=market['bar']; p.setdefault('legs',[]).append(leg); p.setdefault('tier_counts',{'1':0,'2':0,'3':0})[str(tier)]=int(p['tier_counts'].get(str(tier),0) or 0)+1
    p['highest_tier']=max(int(p.get('highest_tier') or 0),tier); p['last_entry_bar']=market['bar']; p['v138']=True; p['version']='1.3.8'; self.store.save()
    body={'instId':engine.INSTRUMENT,'tdMode':'isolated','side':plan['exchange_side'],'posSide':plan['posSide'],'ordType':'limit','px':plan['px'],'sz':plan['sz'],'clOrdId':cid,
          'attachAlgoOrds':[{'attachAlgoClOrdId':algo,'tpOrdKind':'condition','tpTriggerPx':plan['tp'],'tpOrdPx':'-1','tpTriggerPxType':'last',
                             'slTriggerPx':plan['sl'],'slOrdPx':'-1','slTriggerPxType':'last'}]}
    try:reply=self.x.post('/api/v5/trade/order',body)
    except Exception as exc:
        if getattr(exc,'write_rejected',False):
            p['legs'].remove(leg); p['tier_counts'][str(tier)]=max(0,int(p['tier_counts'][str(tier)])-1); p['highest_tier']=max([int(k) for k,v in p['tier_counts'].items() if int(v)>0] or [0]);
            p['last_entry_bar']=max([int(x.get('signal_bar') or 0) for x in p['legs']] or [0]); state['last_bar']=before_last; self.store.save(); self.enabled=False; self.stopped=True
            self.emit('log',f'OKX明确拒绝V1.3.8加仓：{exc}；本次未产生订单，已释放加仓占位；自动新开仓已停止'); return
        raise
    row=reply[0] if reply else {}; oid=str(row.get('ordId') or '') if isinstance(row,dict) else ''
    if not oid:raise engine.Halt('V1.3.8加仓响应缺少ordId；本地占位已保留，禁止重复提交')
    leg['order_id']=oid; leg['okx_ack_at']=time.time(); self.store.save()
    self.store.record('V1.3.8提交加仓',{'tier':tier,'score':score['total'],'client_id':cid,'order_id':oid,'plan':plan})
    self.emit('log',f'V1.3.8 {level}：已提交第 {p["tier_counts"][str(tier)]}/{v137._tier_limit(tier)} 次该等级限价加仓；1m Trigger有效')
    self.emit('plan',plan)


def _cycle(self):
    now=time.monotonic()
    if self.poll_at and now-self.poll_at>60 and self.enabled:self.halt('检测到睡眠或长时间停顿，必须人工重新检查后启动')
    self.poll_at=now
    if not self.store:return
    p=self.store.data.get('active')
    if isinstance(p,dict) and p.get('v137'):
        self.reconcile(); p=self.store.data.get('active')
    expected=_expected_1m(self)
    if not self.market or self.market.get('bar')!=expected:self.refresh_market()
    if not self.enabled:return
    until=float(getattr(self,'startup_buffer_until',0) or 0)
    if until>time.monotonic():return
    self.startup_buffer_until=0.0
    market=self.market; self._verify_latest('1m',market['bar'],ONE_MINUTE_STEP)
    try:
        equity,available=self.x.balance(); remaining=self.daily(equity)
    except v135_patch.DailyRiskStop as exc:
        self.enabled=False; self.stopped=True; self.emit('log',str(exc)); return
    self._reset_streak_day(); state=self.store.data
    if state.get('streak',0)>=self.settings.consecutive_losses:return
    p=state.get('active')
    if isinstance(p,dict) and p.get('v137'):
        v137._ensure_v137_state(self,p); p['v138']=True; p['version']='1.3.8'; self.store.save()
        if p.get('emergency_close_id') or any(leg.get('state')=='pending' for leg in p.get('legs',[])) or not p.get('protected'):return
        if market.get('side')=='观望' or market.get('side')!=p.get('side'):return
        score=(market.get('scores') or {}).get(p['side']) or {}
        if float((score.get('layers') or {}).get('trigger') or 0)<=0 or not score.get('gate') or float(score.get('total') or 0)<V138_THRESHOLD:return
        return _submit_addon(self,p,market,score,equity,available,remaining)
    if time.time()-float(state.get('last_close') or 0)<self.settings.cooldown_minutes*60:return
    if self.x.positions() or self.x.orders() or self.x.algos():raise engine.Halt('出现非本程序仓位或挂单，停止自动开仓')
    if market.get('side')=='观望' or state.get('last_bar')==market.get('bar'):return
    score=(market.get('scores') or {}).get(market['side']) or {}
    if not score.get('gate') or float(score.get('total') or 0)<V138_THRESHOLD:return
    return _submit_initial(self,market,score,equity,available,remaining)


def _widgets(root):
    out=[]
    try:children=root.winfo_children()
    except Exception:return out
    for child in children:out.append(child); out.extend(_widgets(child))
    return out


def apply():
    if getattr(engine.Engine,'_kaytrade_v138_applied',False):return
    engine.Settings.validate=_validate_settings
    engine.Engine.refresh_market=_refresh_market
    engine.Engine.cycle=_cycle
    previous_reconcile=engine.Engine.reconcile
    previous_app_init=app.App.__init__; previous_app_settings=app.App.settings; previous_app_arm=app.App.arm

    def reconcile(self):
        original_emit=self.emit
        def emit(kind,data):
            if isinstance(data,str):data=data.replace('V1.3.7','V1.3.8').replace('等待1根5m K线','超过当前1m信号有效期').replace('下一根有效5m信号','下一根有效1m信号')
            return original_emit(kind,data)
        self.emit=emit
        try:return previous_reconcile(self)
        finally:self.emit=original_emit
    engine.Engine.reconcile=reconcile

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:self.root.title('KAYTRADE 1.3.8 · BTC 策略控制台')
        except Exception:pass
        if 'score_threshold' in self.fields:self.fields['score_threshold'].set('3.5')
        for w in _widgets(self.root):
            try:text=str(w.cget('text') or '')
            except Exception:text=''
            try:
                if text=='逐仓杠杆 1—10倍':w.configure(text='逐仓杠杆 1—50倍')
                elif 'V1.3.7 策略更新版' in text:w.configure(text=text.replace('V1.3.7 策略更新版','V1.3.8 1m触发版'))
                elif 'V1.3.6 运行稳定性版' in text:w.configure(text=text.replace('V1.3.6 运行稳定性版','V1.3.8 1m触发版'))
            except Exception:pass

    def app_settings(self):
        if 'score_threshold' in self.fields:self.fields['score_threshold'].set('3.5')
        return previous_app_settings(self)

    def app_arm(self):
        original=app.simpledialog.askstring
        def askstring(title,prompt,*args,**kwargs):
            text=str(prompt).replace('逐仓10倍','逐仓50倍')
            text=text.replace('每根已收盘5m最多新增1笔','每根已收盘1m最多新增1笔')
            text=text.replace('做多/做空均要求5m Trigger>0','做多/做空均要求1m Trigger>0')
            text=text.replace('5m Trigger≥0.5','1m Trigger>0')
            text += '\nV1.3.8：1m Trigger为硬门槛并计分；5m/15m MACD+BOLL趋势各最高+2；RSI过热/超卖、1H/4H不利结构按规则扣分。'
            return original(title,text,*args,**kwargs)
        app.simpledialog.askstring=askstring
        try:return previous_app_arm(self)
        finally:app.simpledialog.askstring=original

    app.App.__init__=app_init; app.App.settings=app_settings; app.App.arm=app_arm
    engine.Engine._kaytrade_v138_applied=True


apply()

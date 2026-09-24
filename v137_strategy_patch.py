"""KAYTRADE V1.3.7 strategy update.

Changes:
- fixed 3.5/10 entry threshold
- tiered entries: 3.5-5.0 once, 5.5-7.0 once, 7.5-10.0 up to twice
- long/short both require 5m Trigger > 0
- full-position TP at 2R, SL at 1R; no split take-profit / breakeven shift
- keep V1.3.6 expected-cost floor and fail-closed ambiguous-write behavior
"""
import copy
import math
import time
import uuid
from dataclasses import asdict
from decimal import Decimal, ROUND_DOWN

from v136_entry_fix import apply as apply_v136_entry_fix
apply_v136_entry_fix()

import app
import engine
import exchange
import strategy
import v136_runtime
import v136_entry_fix

V137_THRESHOLD = 3.5
EXPECTED_COST_MIN = 1.20
_CURRENT_CONTEXT = {}


def _tier(total):
    value=float(total or 0.0)
    if value >= 7.5:
        return 3, 2.0, '三级超强信号'
    if value >= 5.5:
        return 2, 1.5, '二级强信号'
    if value >= 3.5:
        return 1, 1.0, '一级开仓信号'
    return 0, 0.0, '未达开仓线'


def _tier_limit(tier):
    return {1:1, 2:1, 3:2}.get(int(tier or 0), 0)


def _allow_entry(active, tier, bar):
    tier=int(tier or 0)
    if tier <= 0:
        return False, '评分未达3.5'
    if not isinstance(active,dict):
        return True, ''
    counts=active.get('tier_counts') or {}
    highest=int(active.get('highest_tier') or 0)
    if active.get('last_entry_bar') == bar:
        return False, '本根5m已提交过一次开仓/加仓'
    if tier < highest:
        return False, '评分已从更高级别回落，不补低级别仓位'
    used=int(counts.get(str(tier),0) or 0)
    if used >= _tier_limit(tier):
        return False, '当前等级允许次数已用完'
    if tier == 1 and active.get('legs'):
        return False, '一级信号只允许空仓首笔'
    return True, ''


def _validate_settings(self):
    if abs(float(self.score_threshold)-V137_THRESHOLD)>1e-9:
        raise engine.Halt('V1.3.7自动开仓评分门槛固定为3.5，不可修改')
    for name,value in asdict(self).items():
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise engine.Halt(name+' 必须是有限正数')
    if int(self.leverage)!=self.leverage or not 1<=self.leverage<=10:
        raise engine.Halt('杠杆范围1—10倍（整数）')
    if int(self.consecutive_losses)!=self.consecutive_losses or self.consecutive_losses>20:
        raise engine.Halt('连续亏损上限必须是1—20的整数')
    if self.daily_loss>self.capital or self.max_notional>self.capital*self.leverage:
        raise engine.Halt('日亏损/名义仓位超出资金与杠杆范围')
    if not .6<=self.stop_atr<=3:
        raise engine.Halt('ATR止损倍数范围0.6—3')
    if abs(self.reward_r-2.0)>1e-9:
        raise engine.Halt('V1.3.7止盈固定为2R')
    if abs(self.fee_bps-2.0)>1e-9 or abs(self.taker_fee_bps-5.0)>1e-9 or abs(self.slippage_bps-5.0)>1e-9:
        raise engine.Halt('V1.3.7成本参数固定：Maker 2bps / Taker 5bps / 滑点预算5bps')
    return self


def _v137_make_plan(s,side,ticker,meta,atr,available,daily_remaining,position_multiplier=1.0):
    s.validate()
    if side not in ('做多','做空') or not math.isfinite(float(atr)) or float(atr)<=0:
        raise engine.Halt('无效信号或ATR')
    multiplier=float(position_multiplier)
    if multiplier not in (1.0,1.5,2.0):
        raise engine.Halt('评分仓位倍率必须是1 / 1.5 / 2')
    buy=side=='做多'; direction=1 if buy else -1
    ask,bid=float(ticker['askPx']),float(ticker['bidPx'])
    if not 0<bid<=ask or (ask-bid)/bid>float(s.slippage_bps)/10000:
        raise engine.Halt('买卖价差过大')
    limit=float(engine.rounded(bid if buy else ask,meta['tickSz'],not buy))
    dist=float(atr)*float(s.stop_atr)
    sl=engine.rounded(limit-direction*dist,meta['tickSz'],not buy)
    tp=engine.rounded(limit+direction*dist*2,meta['tickSz'],buy)
    if not (float(sl)<limit<float(tp) if buy else float(tp)<limit<float(sl)):
        raise engine.Halt('止盈止损价格非法')
    unit=float(meta['ctVal'])*float(meta.get('ctMult') or 1)
    if unit<=0 or float(meta['minSz'])<=0 or float(meta['lotSz'])<=0:
        raise engine.Halt('合约单位异常')

    maker=float(s.fee_bps)/10000
    taker=float(s.taker_fee_bps)/10000
    slip=float(s.slippage_bps)/10000
    risk_entry_fee=max(maker,taker)
    per_btc=abs(limit-float(sl))+limit*risk_entry_fee+float(sl)*(taker+slip)

    account_equity=v136_runtime._live_equity_for(available)
    risk_capital=min(float(s.capital),float(account_equity))
    base_risk=min(float(s.risk_usdt),risk_capital*float(s.risk_pct)/100)
    risk=min(base_risk*multiplier,float(daily_remaining))
    notional=min(float(s.max_notional),risk_capital*float(s.leverage),float(available)*.9*float(s.leverage))
    quantity=engine.rounded(min(risk/per_btc,notional/limit)/unit,meta['lotSz'])
    if Decimal(quantity)<Decimal(str(meta['minSz'])):
        raise engine.Halt('风险预算不足以满足最小下单量，跳过')
    btc=float(quantity)*unit
    if btc*per_btc>risk+1e-9 or btc*limit>notional+1e-9:
        raise engine.Halt('取整后风险超限')

    expected_cost_per_btc=limit*maker+float(tp)*taker
    worst_cost_per_btc=limit*risk_entry_fee+float(tp)*(taker+slip)
    reward_distance=dist*2
    expected_multiple=reward_distance/expected_cost_per_btc if expected_cost_per_btc>0 else math.inf
    worst_multiple=reward_distance/worst_cost_per_btc if worst_cost_per_btc>0 else math.inf
    mapped=v136_entry_fix._engine_gate_multiple(expected_multiple)

    entry_fee=btc*limit*maker
    exit_fee=btc*float(tp)*taker
    gross=btc*reward_distance
    expected_cost=entry_fee+exit_fee
    worst_cost=btc*worst_cost_per_btc
    expected_net=gross-expected_cost
    worst_net=gross-worst_cost

    ctx=dict(_CURRENT_CONTEXT)
    plan=dict(
        side=side,posSide='long' if buy else 'short',exchange_side='buy' if buy else 'sell',
        px=str(limit),sz=quantity,sl=sl,tp=tp,tp1=tp,tp2=tp,tp1_sz=quantity,tp2_sz='0',
        btc=btc,notional=btc*limit,estimated_loss=btc*per_btc,position_multiplier=multiplier,
        base_risk_budget=base_risk,effective_risk_budget=risk,
        account_equity=float(account_equity),available_balance=float(available),strategy_capital_cap=float(s.capital),
        risk_capital=risk_capital,atr_15m=float(atr),stop_distance=dist,
        expected_roundtrip_cost=expected_cost,worst_roundtrip_cost=worst_cost,
        expected_fee_multiple=expected_multiple,worst_cost_multiple=worst_multiple,
        cost_multiple=mapped,legacy_engine_cost_multiple=mapped,expected_cost_min_multiple=EXPECTED_COST_MIN,
        tp_full_gross=gross,tp_full_net=expected_net,expected_net_profit=expected_net,worst_net_profit=worst_net,
        reward_r=2.0,breakeven_after_tp1=False,entry_fee_budget_bps=float(s.fee_bps),
        v137=True,signal_bar=ctx.get('bar'),signal_tier=ctx.get('tier'),signal_level=ctx.get('level'),
    )
    v136_runtime._LAST_PLAN_DIAG=dict(plan)
    v136_entry_fix._LAST_PLAN=plan
    return plan


def _diag_text(plan,prefix='开仓成本诊断'):
    if not isinstance(plan,dict):
        return ''
    return (
        f"{prefix}：BTC {float(plan.get('px',0)):,.2f}；15m ATR {float(plan.get('atr_15m',0)):,.2f}，"
        f"1R {float(plan.get('stop_distance',0)):,.2f}；仓位 {float(plan.get('notional',0)):,.2f} USDT；"
        f"预计手续费 {float(plan.get('expected_roundtrip_cost',0)):.2f} USDT，"
        f"最坏执行成本(含滑点预算) {float(plan.get('worst_roundtrip_cost',0)):.2f} USDT；"
        f"整仓2R毛/净 {float(plan.get('tp_full_gross',0)):.2f}/{float(plan.get('tp_full_net',0)):.2f} USDT；"
        f"2R TP/预计手续费 {float(plan.get('expected_fee_multiple',0)):.2f}×，"
        f"2R TP/最坏成本 {float(plan.get('worst_cost_multiple',0)):.2f}×"
    )


def _transform_single_bracket(original_post):
    def post(self,path,body):
        payload=body
        if path=='/api/v5/trade/order' and isinstance(body,dict) and body.get('attachAlgoOrds'):
            items=list(body.get('attachAlgoOrds') or [])
            tps=[x for x in items if isinstance(x,dict) and x.get('tpTriggerPx') not in (None,'')]
            sls=[x for x in items if isinstance(x,dict) and x.get('slTriggerPx') not in (None,'')]
            if tps and sls:
                tp=tps[-1]; sl=sls[-1]
                bracket={
                    'attachAlgoClOrdId':str(tp.get('attachAlgoClOrdId') or sl.get('attachAlgoClOrdId') or ''),
                    'tpOrdKind':'condition','tpTriggerPx':str(tp['tpTriggerPx']),'tpOrdPx':'-1',
                    'tpTriggerPxType':str(tp.get('tpTriggerPxType') or 'last'),
                    'slTriggerPx':str(sl['slTriggerPx']),'slOrdPx':'-1',
                    'slTriggerPxType':str(sl.get('slTriggerPxType') or 'last'),
                }
                payload=copy.deepcopy(body); payload['attachAlgoOrds']=[bracket]
        return original_post(self,path,payload)
    return post


def _leg_from_active(p):
    tier=int(p.get('signal_tier') or _tier(p.get('score',0))[0] or 1)
    return {
        'client_id':p.get('client_id',''),'order_id':p.get('order_id',''),
        'bracket_id':p.get('tp2_id') or p.get('tp1_id') or p.get('sl_id') or '',
        'px':p.get('px'),'sz':p.get('sz'),'sl':p.get('sl'),'tp':p.get('tp') or p.get('tp2'),
        'estimated_loss':p.get('estimated_loss',0.0),'notional':p.get('notional',0.0),
        'position_multiplier':p.get('position_multiplier',1.0),
        'submitted':p.get('submitted',time.time()),'expires':p.get('expires',time.time()+300),
        'filled':bool(p.get('filled')),'filled_at':p.get('filled_at'),
        'state':'filled' if p.get('filled') else 'pending',
        'cancel_requested':p.get('cancel_requested',False),'cancel_on_reconcile':p.get('cancel_on_reconcile',False),
        'tier':tier,'score':p.get('score'),'signal_bar':p.get('signal_bar'),'counted':True,
    }


def _ensure_v137_state(self,p):
    if not isinstance(p,dict) or not p.get('v137'):
        return
    changed=False
    if not isinstance(p.get('legs'),list):
        p['legs']=[_leg_from_active(p)]; changed=True
    if not isinstance(p.get('tier_counts'),dict):
        counts={'1':0,'2':0,'3':0}
        for leg in p['legs']:
            if leg.get('counted',True):
                t=int(leg.get('tier') or 0)
                if t in (1,2,3): counts[str(t)]+=1
        p['tier_counts']=counts; changed=True
    if not p.get('highest_tier'):
        p['highest_tier']=max([int(k) for k,v in p['tier_counts'].items() if int(v or 0)>0] or [0]); changed=True
    if p.get('last_entry_bar') is None:
        p['last_entry_bar']=p.get('signal_bar') or (self.store.data.get('last_bar') if self.store else 0); changed=True
    p['version']='1.3.7'
    if changed and self.store:self.store.save()


def _known_parent(row,leg):
    if not isinstance(row,dict): return False
    return bool((leg.get('order_id') and str(row.get('ordId') or '')==str(leg.get('order_id'))) or
                (leg.get('client_id') and str(row.get('clOrdId') or '')==str(leg.get('client_id'))))


def _position_qty(self,p,positions):
    bad=[r for r in positions if abs(float(r.get('pos') or 0))>0 and
         (r.get('mgnMode')!='isolated' or r.get('posSide')!=p.get('posSide'))]
    if bad: raise engine.Halt('V1.3.7检测到非本策略方向/非逐仓BTC仓位，停止自动交易')
    same=[r for r in positions if r.get('mgnMode')=='isolated' and r.get('posSide')==p.get('posSide') and abs(float(r.get('pos') or 0))>0]
    if len(same)>1: raise engine.Halt('V1.3.7同方向BTC仓位记录不唯一，请立即核对OKX')
    return (abs(float(same[0].get('pos') or 0)) if same else 0.0),same


def _cancel_leg(self,leg,reason):
    if leg.get('cancel_requested'): return
    leg['cancel_requested']=time.time(); leg['cancel_reason']=reason; self.store.save()
    body={'instId':engine.INSTRUMENT}
    if leg.get('order_id'): body['ordId']=str(leg['order_id'])
    else: body['clOrdId']=str(leg.get('client_id') or '')
    self.x.post('/api/v5/trade/cancel-order',body)
    self.store.record('V1.3.7撤销限价开仓',{'client_id':leg.get('client_id'),'reason':reason})
    self.emit('log','V1.3.7已发送限价开仓撤单请求：'+reason+'；等待交易所确认')


def _emergency_flatten(self,p,qty,reason):
    if qty<=0 or p.get('emergency_close_id'): return
    cid='ec'+uuid.uuid4().hex[:28]
    p['emergency_close_id']=cid; p['emergency_close_requested']=time.time(); p['emergency_close_reason']=reason
    self.store.save()
    reply=self.x.post('/api/v5/trade/order',{'instId':engine.INSTRUMENT,'tdMode':'isolated','posSide':p['posSide'],
        'side':'sell' if p['posSide']=='long' else 'buy','ordType':'market','sz':str(qty),'clOrdId':cid})
    p['emergency_close_order_id']=str((reply[0] if reply else {}).get('ordId') or '')
    p['emergency_close_ack_at']=time.time(); self.store.save()
    self.store.record('V1.3.7保护异常安全平仓请求',{'client_id':cid,'size':qty,'reason':reason})
    self.halt(reason+'；已发送一次市价安全平仓，禁止重复提交')


def _finalize_cycle(self,p):
    equity,_=self.x.balance(); pnl=float(equity)-float(p.get('equity_before') or equity)
    self._reset_streak_day(); state=self.store.data
    state['streak']=state['streak']+1 if pnl<0 else 0
    state['active']=None; state['last_close']=time.time(); self.store.save()
    self.store.record('V1.3.7仓位归零',{'client_id':p.get('client_id'),'equity_change':pnl,'side':p.get('side'),
        'legs':len(p.get('legs') or []),'tier_counts':p.get('tier_counts',{})})
    self.emit('log',f'V1.3.7仓位已全部归零；本轮USDT净权益变化 {pnl:+.4f}；三级开仓计数已重置')


def _reconcile_v137(self,p):
    _ensure_v137_state(self,p)
    positions=self.x.positions(); pending=self.x.orders(); algos=self.x.algos(); qty,_=_position_qty(self,p,positions)
    legs=p.get('legs') or []
    for row in pending:
        if not any(_known_parent(row,leg) for leg in legs):
            raise engine.Halt('V1.3.7检测到非本策略BTC普通委托，请立即核对OKX')
    known_algo={str(leg.get('bracket_id') or '') for leg in legs if leg.get('bracket_id')}
    for row in algos:
        aid=str(row.get('algoClOrdId') or '')
        if aid and aid not in known_algo: raise engine.Halt('V1.3.7检测到非本策略BTC策略委托，请立即核对OKX')

    history=None; wrote=False
    for leg in legs:
        if leg.get('state') in ('canceled','closed'): continue
        if leg.get('state')=='pending':
            order=next((r for r in pending if _known_parent(r,leg)),None)
            if order is None:
                try: order=self.x.order(leg.get('client_id',''),leg.get('order_id',''))
                except Exception as exc:
                    if str(getattr(exc,'code',''))!='51603': raise
                    if history is None: history=self.x.recent_orders() if hasattr(self.x,'recent_orders') else []
                    order=next((r for r in history if _known_parent(r,leg)),None)
                    if order is None:
                        if time.time()-float(leg.get('submitted') or time.time())<20: continue
                        raise engine.Halt('V1.3.7开仓订单20秒后仍无法由当前委托/历史订单核实，禁止重复提交')
            status=str(order.get('state') or ''); filled=float(order.get('accFillSz') or 0)
            if status in ('live','partially_filled'):
                if leg.get('cancel_requested'):
                    if time.time()-float(leg['cancel_requested'])>15: raise engine.Halt('V1.3.7限价撤单状态长时间无法核实')
                    continue
                if filled>0:
                    _emergency_flatten(self,p,qty,'V1.3.7加仓限价单出现部分成交，为避免多腿保护失配'); return
                if leg.get('cancel_on_reconcile'):
                    _cancel_leg(self,leg,'手动停止自动交易'); wrote=True; continue
                if time.time()>=float(leg.get('expires') or leg.get('submitted',0)+300):
                    _cancel_leg(self,leg,'限价挂单已等待1根5m K线'); wrote=True; continue
                continue
            if status=='canceled' and filled<=0:
                leg['state']='canceled'; leg['counted']=False; self.store.save()
                self.emit('log','V1.3.7限价开仓未成交/已撤销；该笔不计入本轮等级次数'); continue
            if status=='canceled' and filled>0:
                _emergency_flatten(self,p,qty,'V1.3.7限价开仓取消时存在部分成交，为避免保护失配'); return
            if status!='filled' or filled<=0:
                if time.time()-float(leg.get('submitted') or time.time())>15: raise engine.Halt('V1.3.7订单状态长时间不确定，禁止重复开仓')
                continue
            leg['state']='filled'; leg['filled']=True; leg['filled_sz']=filled; leg['filled_at']=time.time()
            if order.get('ordId'): leg['order_id']=str(order.get('ordId'))
            self.store.save()
    if wrote:return

    counts={'1':0,'2':0,'3':0}
    for leg in legs:
        if leg.get('counted',True):
            t=int(leg.get('tier') or 0)
            if t in (1,2,3): counts[str(t)]+=1
    p['tier_counts']=counts; p['highest_tier']=max([int(k) for k,v in counts.items() if v>0] or [0])
    pending_legs=[leg for leg in legs if leg.get('state')=='pending']
    filled_legs=[leg for leg in legs if leg.get('state')=='filled']
    live_by_id={str(a.get('algoClOrdId') or ''):a for a in algos if isinstance(a,dict) and str(a.get('state') or '')=='live'}
    tol=1e-8; live_qty=0.0; missing=[]
    for leg in filled_legs:
        a=live_by_id.get(str(leg.get('bracket_id') or ''))
        if a:
            tp_ok=float(a.get('tpTriggerPx') or 0)==float(leg.get('tp') or 0) and str(a.get('tpOrdPx') or '')=='-1'
            sl_ok=float(a.get('slTriggerPx') or 0)==float(leg.get('sl') or 0) and str(a.get('slOrdPx') or '')=='-1'
            side_ok=a.get('posSide')==p.get('posSide') and a.get('tdMode')=='isolated'
            if not (tp_ok and sl_ok and side_ok): raise engine.Halt('V1.3.7整仓2R TP / 1R SL保护参数与本地计划不一致')
            leg['protected']=True; live_qty+=float(leg.get('sz') or 0)
        else: missing.append(leg)

    if qty<=tol:
        if pending_legs:
            p['filled']=False; self.store.save(); self.emit('position',positions); return
        _finalize_cycle(self,p); return
    if qty+tol<live_qty: raise engine.Halt('V1.3.7实际BTC仓位小于仍在生效的保护腿数量，请立即核对OKX')
    if abs(qty-live_qty)<=tol:
        for leg in missing:
            if leg.get('state')=='filled': leg['state']='closed'; leg['closed_at']=time.time()
        p['filled']=True; p['protected']=not pending_legs; self.store.save()
    else:
        newest=max([float(leg.get('filled_at') or leg.get('submitted') or 0) for leg in missing] or [0])
        if newest and time.time()-newest<=15:
            p['filled']=True; p['protected']=False; self.store.save(); self.emit('position',positions); return
        _emergency_flatten(self,p,qty,'V1.3.7发现已成交仓位超过可核实的TP/SL保护数量'); return
    p['sz']=str(sum(float(leg.get('sz') or 0) for leg in legs if leg.get('state') in ('pending','filled')))
    self.emit('position',positions)


def _cap_plan(plan,meta,remaining_notional):
    if float(plan.get('notional') or 0)<=remaining_notional+1e-9: return plan
    if remaining_notional<=0: raise engine.Halt('已达到最大名义仓位，不再加仓')
    unit=float(meta['ctVal'])*float(meta.get('ctMult') or 1); lot=Decimal(str(meta['lotSz']))
    raw=Decimal(str(remaining_notional/float(plan['px'])/unit)); units=(raw/lot).to_integral_value(rounding=ROUND_DOWN); qty=units*lot
    if qty<Decimal(str(meta['minSz'])): raise engine.Halt('剩余最大名义仓位不足最小加仓数量')
    old=float(plan['sz']); new=float(qty); ratio=new/old; plan=dict(plan); plan['sz']=format(qty,'f')
    for key in ('btc','notional','estimated_loss','expected_roundtrip_cost','worst_roundtrip_cost','tp_full_gross','tp_full_net','expected_net_profit','worst_net_profit'):
        if key in plan: plan[key]=float(plan[key])*ratio
    return plan


def _submit_addon(self,p,market,score,tier,multiplier,level,equity,available,remaining):
    state=self.store.data; s=self.settings; ticker=self.x.ticker(); self.emit('ticker',ticker)
    if abs(float(ticker['last'])-float(market['close']))>.3*float(market['h']['atr']):
        self.emit('log','价格偏离信号超过0.3 ATR，本轮不追价'); return
    current_risk=sum(float(leg.get('estimated_loss') or 0) for leg in p.get('legs',[]) if leg.get('state')=='filled')
    risk_remaining=float(remaining)-current_risk
    if risk_remaining<=0:
        self.emit('log','V1.3.7当前已持仓风险占满日内剩余风险预算，本轮不加仓'); return
    meta=self.x.instrument(); _CURRENT_CONTEXT.update(bar=market['bar'],tier=tier,level=level,score=float(score['total']))
    plan=_v137_make_plan(s,p['side'],ticker,meta,market['m']['atr'],available,risk_remaining,multiplier)
    current_notional=sum(float(leg.get('notional') or 0) for leg in p.get('legs',[]) if leg.get('state')=='filled')
    plan=_cap_plan(plan,meta,float(s.max_notional)-current_notional)
    if float(plan.get('expected_fee_multiple') or 0)<EXPECTED_COST_MIN:
        self.emit('log',f"V1.3.7加仓成本过滤：2R TP仅为预计实际手续费 {float(plan.get('expected_fee_multiple') or 0):.2f} 倍（最低{EXPECTED_COST_MIN:.2f}倍）；未提交OKX订单"); return
    self.x.post('/api/v5/account/set-leverage',{'instId':engine.INSTRUMENT,'lever':str(s.leverage),'mgnMode':'isolated','posSide':plan['posSide']})
    infos=self.x.get('/api/v5/account/leverage-info',{'instId':engine.INSTRUMENT,'mgnMode':'isolated'},True)
    if not any(i.get('posSide')==plan['posSide'] and float(i['lever'])==s.leverage and i.get('mgnMode')=='isolated' for i in infos): raise engine.Halt('逐仓杠杆回读不一致')
    if not self.enabled:return
    self._verify_latest('5m',market['bar'],engine.ENTRY_STEP)
    cid='mac'+uuid.uuid4().hex[:28]; algo='br'+uuid.uuid4().hex[:28]
    leg=dict(plan,client_id=cid,order_id='',bracket_id=algo,submitted=time.time(),expires=time.time()+300,
        filled=False,state='pending',cancel_requested=False,cancel_on_reconcile=False,tier=tier,score=float(score['total']),signal_bar=market['bar'],counted=True,protected=False)
    before_last=state.get('last_bar'); state['last_bar']=market['bar']; p.setdefault('legs',[]).append(leg)
    p.setdefault('tier_counts',{'1':0,'2':0,'3':0})[str(tier)]=int(p['tier_counts'].get(str(tier),0) or 0)+1
    p['highest_tier']=max(int(p.get('highest_tier') or 0),tier); p['last_entry_bar']=market['bar']; self.store.save()
    body={'instId':engine.INSTRUMENT,'tdMode':'isolated','side':plan['exchange_side'],'posSide':plan['posSide'],'ordType':'limit','px':plan['px'],'sz':plan['sz'],'clOrdId':cid,
        'attachAlgoOrds':[{'attachAlgoClOrdId':algo,'tpOrdKind':'condition','tpTriggerPx':plan['tp'],'tpOrdPx':'-1','tpTriggerPxType':'last','slTriggerPx':plan['sl'],'slOrdPx':'-1','slTriggerPxType':'last'}]}
    try: reply=self.x.post('/api/v5/trade/order',body)
    except Exception as exc:
        if getattr(exc,'write_rejected',False):
            p['legs'].remove(leg); p['tier_counts'][str(tier)]=max(0,int(p['tier_counts'].get(str(tier),1))-1)
            p['highest_tier']=max([int(k) for k,v in p['tier_counts'].items() if int(v or 0)>0] or [0]); p['last_entry_bar']=max([int(x.get('signal_bar') or 0) for x in p['legs']] or [0])
            state['last_bar']=before_last; self.store.save(); self.enabled=False; self.stopped=True
            self.emit('log',f'OKX明确拒绝V1.3.7加仓请求：{exc}；本次未产生新订单，已释放该笔加仓占位；自动新开仓已停止'); return
        raise
    row=reply[0] if reply else {}; order_id=str(row.get('ordId') or '') if isinstance(row,dict) else ''
    if not order_id: raise engine.Halt('V1.3.7加仓响应缺少ordId；本地占位已保留，禁止重复提交')
    leg['order_id']=order_id; leg['okx_ack_at']=time.time(); self.store.save()
    self.store.record('V1.3.7提交加仓请求',{'tier':tier,'score':score['total'],'client_id':cid,'order_id':order_id,'plan':plan})
    self.emit('log',f'V1.3.7 {level}：已提交第 {p["tier_counts"][str(tier)]}/{_tier_limit(tier)} 次该等级限价开仓；{multiplier:g}×风险仓位，整仓TP=2R，SL=1R')
    self.emit('plan',plan)


def _widgets(root):
    out=[]
    try: children=root.winfo_children()
    except Exception:return out
    for child in children:
        out.append(child); out.extend(_widgets(child))
    return out


def apply():
    if getattr(engine.Engine,'_kaytrade_v137_applied',False): return
    engine.Settings.validate=_validate_settings; v136_runtime._diag_text=_diag_text
    original_signal=strategy.signal
    def signal(*args,**kwargs):
        kwargs['threshold']=V137_THRESHOLD; result=original_signal(*args,**kwargs); result['threshold']=V137_THRESHOLD; scores=result.get('scores') or {}
        for side,score in scores.items():
            total=float(score.get('total') or 0); trigger=float((score.get('layers') or {}).get('trigger') or 0); structure=score.get('structure') or {}
            gate=trigger>0 and not bool(structure.get('blocked')); tier,multiplier,level=_tier(total); eligible=gate and tier>0
            score.update(required=V137_THRESHOLD,gate=gate,eligible=eligible,level=f'{level} · {multiplier:g}×仓位' if tier else level,position_multiplier=multiplier if eligible else 0.0,signal_tier=tier)
            if structure.get('blocked'): score['reason']=f'前方强结构距离 {float(structure.get("front_r") or 0):.2f}R < 1R，禁止开仓'
            elif trigger<=0: score['reason']='5m Trigger 0/1.0，禁止开仓/加仓'
            elif eligible: score['reason']=f'{level} · {total:g}/10 达标；5m Trigger {trigger:g}>0'
            else: score['reason']=f'{total:g}/10，未达固定开仓门槛3.5'
        qualified=[side for side,score in scores.items() if score.get('eligible')]
        if len(qualified)==1:selected=qualified[0]
        elif len(qualified)==2 and float(scores[qualified[0]].get('total') or 0)!=float(scores[qualified[1]].get('total') or 0): selected=max(qualified,key=lambda side:float(scores[side].get('total') or 0))
        else:selected='观望'
        result['side']=selected; result['why']=scores[selected]['reason'] if selected!='观望' else ' / '.join(side+': '+score.get('reason','') for side,score in scores.items()); return result
    strategy.signal=signal; engine.signal=signal; engine.make_plan=_v137_make_plan
    exchange.Exchange.post=_transform_single_bracket(exchange.Exchange.post)
    previous_reconcile=engine.Engine.reconcile; previous_cycle=engine.Engine.cycle; previous_stop=engine.Engine.stop; previous_flatten=engine.Engine.flatten
    previous_app_init=app.App.__init__; previous_app_settings=app.App.settings; previous_app_render_plan=app.App.render_plan; previous_app_arm=app.App.arm

    def reconcile(self):
        p=self.store.data.get('active') if self.store else None
        return _reconcile_v137(self,p) if isinstance(p,dict) and p.get('v137') else previous_reconcile(self)

    def cycle(self):
        global _CURRENT_CONTEXT
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict) and p.get('v137'):
            self.reconcile(); p=self.store.data.get('active') if self.store else None
            if not isinstance(p,dict) or not p.get('v137') or not self.enabled:return
            _ensure_v137_state(self,p)
            if p.get('emergency_close_id') or any(leg.get('state')=='pending' for leg in p.get('legs',[])) or not p.get('protected'):return
            expected=int(self.market_now()//300)*engine.ENTRY_STEP-engine.ENTRY_STEP
            if not self.market or self.market.get('bar')!=expected:self.refresh_market()
            market=self.market; self._verify_latest('5m',market['bar'],engine.ENTRY_STEP)
            if market.get('side')=='观望' or market.get('side')!=p.get('side'):return
            score=(market.get('scores') or {}).get(p['side']) or {}; total=float(score.get('total') or 0); trigger=float((score.get('layers') or {}).get('trigger') or 0)
            if trigger<=0 or not score.get('gate',True) or total<V137_THRESHOLD:return
            tier,multiplier,level=_tier(total); allowed,_=_allow_entry(p,tier,market.get('bar'))
            if not allowed:return
            equity,available=self.x.balance(); remaining=self.daily(equity); self._reset_streak_day()
            if self.store.data.get('streak',0)>=self.settings.consecutive_losses:return
            return _submit_addon(self,p,market,score,tier,multiplier,level,equity,available,remaining)

        if self.market and isinstance(self.market,dict):
            side=self.market.get('side'); score=(self.market.get('scores') or {}).get(side) if side in ('做多','做空') else None
            if score:
                tier,_,level=_tier(score.get('total')); _CURRENT_CONTEXT={'bar':self.market.get('bar'),'tier':tier,'level':level,'score':score.get('total')}
        original_emit=self.emit
        def emit(kind,data):
            if kind=='log' and isinstance(data,str):
                data=data.replace('TP1 1R平50% / TP2 2R平50% / SL 1×15m ATR；TP1后自动移保本','整仓TP=2R / SL=1×15m ATR；不分批止盈，不移保本')
                data=data.replace('TP1=1R平50%，TP2=2R平余下50%；首个TP成交后由OKX自动把SL移动到成交均价保本','整仓TP=2R；SL=1×15m ATR；不分批止盈')
            return original_emit(kind,data)
        self.emit=emit
        try: result=previous_cycle(self)
        finally:self.emit=original_emit
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict) and p.get('v137'):_ensure_v137_state(self,p)
        return result

    def stop(self):
        p=self.store.data.get('active') if self.store else None
        if not isinstance(p,dict) or not p.get('v137'):return previous_stop(self)
        self.enabled=False; self.stopped=True; self.startup_buffer_until=0.0; changed=False
        for leg in p.get('legs',[]):
            if leg.get('state')=='pending' and not leg.get('cancel_requested'):leg['cancel_on_reconcile']=True; changed=True
        if changed:self.store.save()
        self.emit('log','已停止V1.3.7新开仓/加仓；未成交限价单将在核对后撤销，已成交仓位继续由各自2R TP / 1R SL保护'); return True

    def flatten(self):
        p=self.store.data.get('active') if self.store else None
        if not isinstance(p,dict) or not p.get('v137'):return previous_flatten(self)
        self.stop()
        if p.get('close_id'):raise engine.Halt('已有平仓请求，禁止重复发送；请到OKX核对结果')
        positions=self.x.positions(); qty,_=_position_qty(self,p,positions)
        if qty<=0:raise engine.Halt('当前已无本程序BTC仓位')
        cid='cl'+uuid.uuid4().hex[:28]; p['close_id']=cid; p['close_requested']=time.time(); self.store.save()
        self.x.post('/api/v5/trade/order',{'instId':engine.INSTRUMENT,'tdMode':'isolated','posSide':p['posSide'],'side':'sell' if p['posSide']=='long' else 'buy','ordType':'market','sz':str(qty),'clOrdId':cid})
        self.emit('log','V1.3.7已发送整仓市价平仓请求；不会重复发送，原TP/SL由交易所后续状态处理')

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:self.root.title('KAYTRADE 1.3.7 · BTC 策略控制台')
        except Exception:pass
        if 'score_threshold' in self.fields:self.fields['score_threshold'].set('3.5')
        for w in _widgets(self.root):
            try:text=str(w.cget('text') or '')
            except Exception:text=''
            try:
                if text in ('自动开仓评分阈值 1—10（0.5步进）','自动开仓评分阈值 3.5—10（0.5步进）'):w.configure(text='自动开仓评分阈值 3.5（固定）')
                elif text=='固定执行成本':
                    card=getattr(getattr(w,'master',None),'master',None)
                    if card is not None:card.configure(bg=app.PANEL_ALT)
                elif '费率与滑点预算锁定不可编辑' in text:w.configure(text='费率与滑点预算锁定不可编辑 · 整仓TP=2R · SL=1R · 不再分批止盈')
                elif text=='按账户风险与15m ATR动态计算 · TP1后自动移保本':w.configure(text='按账户风险与15m ATR动态计算 · 整仓2R止盈 / 1R止损')
                elif text=='止盈 TP1 · 50%':w.configure(text='整仓止盈 TP · 2R')
                elif text=='止盈 TP2 · 余下50%':w.configure(text='止盈模式')
                elif 'V1.3.6 运行稳定性版' in text:w.configure(text=text.replace('V1.3.6 运行稳定性版','V1.3.7 策略更新版'))
                elif '每单TP1/TP2+SL' in text:w.configure(text=text.replace('每单TP1/TP2+SL','每单整仓TP=2R / SL=1R'))
            except Exception:pass
            if isinstance(w,app.RoundedEntry) and getattr(w,'variable',None) is self.fields.get('score_threshold'):
                try:w.variable.set('3.5'); w.entry.configure(state='disabled',disabledbackground=app.FIELD,disabledforeground=app.MUTED)
                except Exception:pass

    def app_settings(self):
        if hasattr(self,'fields') and 'score_threshold' in self.fields:self.fields['score_threshold'].set('3.5')
        return previous_app_settings(self)

    def app_render_plan(self,data):
        result=previous_app_render_plan(self,data)
        if isinstance(data,dict) and hasattr(self,'plan_vars'):
            try:self.plan_vars['tp1'].set(f"{float(data.get('tp',data.get('tp2'))):,.2f}")
            except Exception:pass
            self.plan_vars['tp2'].set('整仓一次性')
        return result

    def app_arm(self):
        original_ask=app.simpledialog.askstring
        def askstring(title,prompt,*args,**kwargs):
            text=str(prompt)
            text=text.replace('止盈TP1=1R平50%，TP2=2R平余下50%；TP1后SL自动移到成交均价','止损=1R（15m ATR），整仓止盈=2R；不分批止盈，不移动保本')
            for old in ('最高10分；最终评分 ≥ 3.5/10 才进入开仓风控。1.0–4.5=1×仓位 / 5.0–6.5=1.5× / 7.0–10=2×。','最高10分；最终评分 ≥ 3.5/10 才进入开仓风控。3.5–4.5=1×仓位 / 5.0–6.5=1.5× / 7.0–10=2×。'):
                text=text.replace(old,'最高10分；固定3.5/10起步。3.5–5.0一级最多1次；5.5–7.0二级最多1次；7.5–10三级最多2次；每根已收盘5m最多新增1笔。')
            text=text.replace('15m Setup参与评分；5m Trigger≥0.5；前方结构<1R禁止开仓。','15m Setup参与评分；做多/做空均要求5m Trigger>0；前方结构<1R禁止开仓。')
            return original_ask(title,text,*args,**kwargs)
        app.simpledialog.askstring=askstring
        try:return previous_app_arm(self)
        finally:app.simpledialog.askstring=original_ask

    engine.Engine.reconcile=reconcile; engine.Engine.cycle=cycle; engine.Engine.stop=stop; engine.Engine.flatten=flatten
    app.App.__init__=app_init; app.App.settings=app_settings; app.App.render_plan=app_render_plan; app.App.arm=app_arm
    engine.Engine._kaytrade_v137_applied=True


apply()

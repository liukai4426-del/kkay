"""Single-position, fail-closed automatic engine. Demo by default in GUI."""
import hashlib
import json
import math
import os
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from core import INSTRUMENT
from strategy import signal
from candles import check_latest, CandlePending, CandleLag

class Halt(RuntimeError):
    pass

HALT_PREFIX='已有故障锁：'
HALT_SUFFIX='。先人工核对并解除故障锁'
ENTRY_STEP=300000

def normalize_halt_reason(reason):
    text=str(reason or '').strip()
    while text.startswith(HALT_PREFIX):
        text=text[len(HALT_PREFIX):].strip()
    while text.endswith(HALT_SUFFIX):
        text=text[:-len(HALT_SUFFIX)].rstrip()
    while text.startswith(HALT_PREFIX):
        text=text[len(HALT_PREFIX):].strip()
    return text

@dataclass(frozen=True)
class Settings:
    capital:float=100
    max_notional:float=100
    leverage:int=5
    risk_usdt:float=1
    risk_pct:float=1
    daily_loss:float=3
    consecutive_losses:int=3
    cooldown_minutes:int=30
    stop_atr:float=1.0
    reward_r:float=2.0
    score_threshold:float=3.5
    # V1.3.4 fixed OKX cost assumptions. fee_bps is maker entry fee.
    fee_bps:float=2.0
    taker_fee_bps:float=5.0
    slippage_bps:float=5.0

    def validate(self):
        if not 3.5<=self.score_threshold<=10.0 or abs(self.score_threshold*2-round(self.score_threshold*2))>1e-9:
            raise Halt('评分阈值必须是3.5—10之间、以0.5为步进')
        for name,value in asdict(self).items():
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise Halt(name+' 必须是有限正数')
        if int(self.leverage)!=self.leverage or not 1<=self.leverage<=10:
            raise Halt('杠杆范围1—10倍（整数）')
        if int(self.consecutive_losses)!=self.consecutive_losses or self.consecutive_losses>20:
            raise Halt('连续亏损上限必须是1—20的整数')
        if self.risk_pct>5 or self.risk_usdt>self.capital*.05:
            raise Halt('单笔风险不得超过配置资金的5%')
        if self.daily_loss>self.capital or self.max_notional>self.capital*self.leverage:
            raise Halt('日亏损/名义仓位超出资金与杠杆范围')
        if not .6<=self.stop_atr<=3:
            raise Halt('ATR止损倍数范围0.6—3')
        if abs(self.reward_r-2.0)>1e-9:
            raise Halt('V1.3.4最终止盈固定为2R')
        if abs(self.fee_bps-2.0)>1e-9 or abs(self.taker_fee_bps-5.0)>1e-9 or abs(self.slippage_bps-5.0)>1e-9:
            raise Halt('V1.3.4成本参数固定：Maker 2bps / Taker 5bps / 滑点预算5bps')
        return self

def rounded(value,tick,up=False):
    v,t=Decimal(str(value)),Decimal(str(tick))
    return format((v/t).to_integral_value(rounding=ROUND_UP if up else ROUND_DOWN)*t,'f')

def _split_tp_sizes(quantity,lot_size,min_size):
    q=Decimal(str(quantity)); lot=Decimal(str(lot_size)); minimum=Decimal(str(min_size))
    units=(q/lot).to_integral_value(rounding=ROUND_DOWN)
    first_units=(units/Decimal('2')).to_integral_value(rounding=ROUND_DOWN)
    second_units=units-first_units
    first=first_units*lot; second=second_units*lot
    if first<minimum or second<minimum or first+second!=q:
        raise Halt('下单数量不足以安全拆分为两笔止盈，跳过')
    return format(first,'f'),format(second,'f')

def make_plan(s,side,ticker,meta,atr,available,daily_remaining,position_multiplier=1.0):
    s.validate()
    if side not in ('做多','做空') or not math.isfinite(atr) or atr<=0:
        raise Halt('无效信号或ATR')
    if float(position_multiplier) not in (1.0,1.5,2.0):
        raise Halt('评分仓位倍率必须是1 / 1.5 / 2')
    buy=side=='做多'; d=1 if buy else -1
    ask,bid=float(ticker['askPx']),float(ticker['bidPx'])
    if not 0<bid<=ask or (ask-bid)/bid>s.slippage_bps/10000:
        raise Halt('买卖价差过大')
    limit=float(rounded(bid if buy else ask,meta['tickSz'],not buy))
    dist=atr*s.stop_atr
    sl=rounded(limit-d*dist,meta['tickSz'],not buy)
    tp1=rounded(limit+d*dist,meta['tickSz'],buy)
    tp2=rounded(limit+d*dist*2,meta['tickSz'],buy)
    if not (float(sl)<limit<float(tp1)<float(tp2) if buy else float(tp2)<float(tp1)<limit<float(sl)):
        raise Halt('止盈止损价格非法')
    unit=float(meta['ctVal'])*float(meta.get('ctMult') or 1)
    if unit<=0 or float(meta['minSz'])<=0 or float(meta['lotSz'])<=0:
        raise Halt('合约单位异常')
    maker=s.fee_bps/10000; taker=s.taker_fee_bps/10000; slip=s.slippage_bps/10000
    # Maximum-loss sizing assumes maker entry + marketable/market stop exit + slippage budget.
    per_btc=abs(limit-float(sl))+limit*maker+float(sl)*(taker+slip)
    base_risk=min(s.risk_usdt,s.capital*s.risk_pct/100)
    # Score tiers scale the base risk unit, but never bypass the daily remaining loss budget or the 5% absolute per-trade cap.
    risk=min(base_risk*float(position_multiplier),daily_remaining,s.capital*.05)
    notional=min(s.max_notional,s.capital*s.leverage,available*.9*s.leverage)
    quantity=rounded(min(risk/per_btc,notional/limit)/unit,meta['lotSz'])
    if Decimal(quantity)<Decimal(meta['minSz']):
        raise Halt('风险预算不足以满足最小下单量，跳过')
    tp1_sz,tp2_sz=_split_tp_sizes(quantity,meta['lotSz'],meta['minSz'])
    btc=float(quantity)*unit
    if btc*per_btc>risk+1e-9 or btc*limit>notional+1e-9:
        raise Halt('取整后风险超限')
    # Planned gross reward distance is 50% at 1R + 50% at 2R = 1.5R average.
    weighted_exit=(float(tp1)+float(tp2))/2
    expected_roundtrip_cost=limit*maker+weighted_exit*(taker+slip)
    planned_reward_distance=dist*1.5
    cost_multiple=planned_reward_distance/expected_roundtrip_cost if expected_roundtrip_cost>0 else math.inf
    return dict(side=side,posSide='long' if buy else 'short',exchange_side='buy' if buy else 'sell',
        px=str(limit),sz=quantity,sl=sl,tp=tp2,tp1=tp1,tp2=tp2,tp1_sz=tp1_sz,tp2_sz=tp2_sz,
        btc=btc,notional=btc*limit,estimated_loss=btc*per_btc,position_multiplier=float(position_multiplier),
        base_risk_budget=base_risk,effective_risk_budget=risk,
        expected_roundtrip_cost=btc*expected_roundtrip_cost,cost_multiple=cost_multiple,
        reward_r=2.0,breakeven_after_tp1=True)


class Store:
    def __init__(self,path):
        self.path=Path(path)
        self.data={'active':None,'last_bar':0,'last_close':0,'streak':0,'streak_day':'','streak_notice_day':'','day':'','peak':0,'halt':''}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except Exception:
                raise Halt('本地状态损坏；禁止自动交易。保留文件并人工核对OKX') from None
            old=self.data.get('halt','')
            clean=normalize_halt_reason(old)
            if clean!=old:
                self.data['halt']=clean
                self.save()

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        temp=self.path.with_suffix('.tmp')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:
            json.dump(self.data,f,ensure_ascii=False,indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp,self.path)

    def record(self,event,data):
        path=self.path.with_suffix('.history.jsonl')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        with os.fdopen(fd,'a') as f:
            f.write(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(),event=event,data=data),ensure_ascii=False)+'\n')

class Engine:
    def __init__(self,exchange,folder,emit=lambda kind,data:None):
        self.x=exchange; self.folder=Path(folder); self.emit=emit
        self.enabled=False; self.stopped=False; self.settings=None; self.store=None
        self.market=None; self.market_at=0; self.poll_at=0
        self.connection_id=None
        self.candle_lag_count=0; self.candle_paused=False

    def connect(self):
        self.x.sync_time()
        a=self.x.account()
        uid=a.get('uid')
        if not uid:
            raise Halt('账户标识缺失')
        name=hashlib.sha256((self.x.host+str(self.x.demo)+uid).encode()).hexdigest()[:24]
        self.store=Store(self.folder/(name+'.json'))
        self.connection_id=uid
        self.emit('account',{'environment':'OKX模拟盘' if self.x.demo else '真实账户','mode':a.get('posMode'),
                             'equity':self.x.balance()[0],'positions':self.x.positions(),'orders':self.x.orders()})
        self.emit('log','只读连接检查成功；未开仓')

    def halt(self,reason):
        self.enabled=False
        raw=str(reason)
        if self.store:
            current=normalize_halt_reason(self.store.data.get('halt',''))
            if raw.startswith(HALT_PREFIX) and current:
                self.store.data['halt']=current; self.store.save()
                self.emit('alarm',HALT_PREFIX+current+HALT_SUFFIX)
                return
            reason=normalize_halt_reason(raw)
            self.store.data['halt']=reason; self.store.save()
        self.emit('alarm',reason)

    def arm(self,settings):
        settings.validate()
        if not self.store:
            raise Halt('先测试连接')
        if self.store.data['halt']:
            raise Halt(HALT_PREFIX+normalize_halt_reason(self.store.data['halt'])+HALT_SUFFIX)
        a=self.x.account()
        if a.get('uid')!=self.connection_id or a.get('posMode')!='long_short_mode':
            raise Halt('首版要求专用子账户、双向持仓模式；请在OKX手动设置')
        perms=set(a.get('perm','').split(','))
        if 'trade' not in perms or 'withdraw' in perms:
            raise Halt('API需要交易权限，且不得有提币权限')
        if not self.store.data['active'] and (self.x.positions() or self.x.orders() or self.x.algos()):
            raise Halt('BTC存在非本程序管理的仓位或挂单，请先在OKX处理')
        self.settings=settings
        equity,_=self.x.balance(); self.daily(equity)
        self._reset_streak_day()
        self.enabled=True; self.stopped=False; self.poll_at=time.monotonic()
        if self.store.data['streak']>=settings.consecutive_losses:
            self.emit('log',f'中国时间本日已连续亏损 {self.store.data["streak"]} 次：仅停止新开仓，次日自动恢复')
        self.candle_lag_count=0; self.candle_paused=False
        self.emit('log','自动交易启动；V1.3.4评分沿用10分制；15m ATR止损；TP1=1R平50%，TP2=2R平余下50%；首个TP成交后由OKX自动把SL移动到成交均价保本；每根5m信号最多一次')

    def stop(self):
        self.enabled=False; self.stopped=True
        p=self.store.data.get('active') if self.store else None
        # Do not issue a cancel blindly here: the limit may have filled between UI clicks and REST reads.
        # Reconcile order + position state first, then cancel only a genuinely unfilled/partial parent order.
        if p and not p.get('filled') and not p.get('cancel_requested'):
            p['cancel_on_reconcile']=True; self.store.save()
        self.emit('log','已停止新开仓；未成交限价开仓会在状态核对后撤销，已有仓位继续监控且交易所TP/SL不撤销')

    def _reset_streak_day(self):
        if not self.store:
            return ''
        day=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
        state=self.store.data
        if state.get('streak_day')!=day:
            state.update(streak_day=day,streak=0,streak_notice_day='')
            self.store.save()
        return day

    def daily(self,equity):
        if not math.isfinite(equity) or equity<=0:
            raise Halt('账户权益无效')
        state=self.store.data
        day=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
        if state['day']!=day:
            state.update(day=day,peak=equity)
        state['peak']=max(equity,state['peak']); self.store.save()
        remaining=self.settings.daily_loss-max(0,state['peak']-equity)
        if remaining<=0:
            raise Halt('达到中国时间日内权益回撤上限；暂停开仓，原有TP/SL继续生效')
        return remaining

    def _handle_candle_lag(self,exc):
        self.candle_lag_count=min(3,self.candle_lag_count+1)
        count=self.candle_lag_count
        if count>=3 and not self.candle_paused:
            self.enabled=False
            self.candle_paused=True
            self.emit('log','K线连续3次未更新：已暂停自动新开仓，但未写入永久故障锁；行情恢复后继续观察，需重新授权才会再次自动交易')
        raise CandlePending(f'{exc}（连续异常 {count}/3）') from None

    def _candle_recovered(self):
        if not self.candle_lag_count:
            return
        was_paused=self.candle_paused
        self.candle_lag_count=0; self.candle_paused=False
        if was_paused:
            self.emit('log','K线已恢复：行情观察恢复；自动交易保持停止，请重新授权启动')
        else:
            self.emit('log','K线已恢复：继续使用最新已收盘K线')

    def _verify_latest(self,bar,timestamp,step):
        try:
            check_latest(bar,timestamp,self.market_now(),step)
        except CandleLag as exc:
            self._handle_candle_lag(exc)

    def refresh_market(self):
        try:
            d,q,h,m,f=self.x.candles('1Dutc'),self.x.candles('4H'),self.x.candles('1H'),self.x.candles('15m'),self.x.candles('5m')
            check_latest('1D',d[-1]['t'],self.market_now(),86400000)
            check_latest('4H',q[-1]['t'],self.market_now(),14400000)
            check_latest('1H',h[-1]['t'],self.market_now(),3600000)
            check_latest('15m',m[-1]['t'],self.market_now(),900000)
            check_latest('5m',f[-1]['t'],self.market_now(),ENTRY_STEP)
        except CandleLag as exc:
            self._handle_candle_lag(exc)
        value=signal(h,m,f,self.settings.score_threshold if self.settings else 3.5,self.settings.stop_atr if self.settings else 1.0,four=q,day=d)
        self.market=dict(value,bar=f[-1]['t'],bar15=m[-1]['t'],bar1h=h[-1]['t'],bar4h=q[-1]['t'],bar1d=d[-1]['t'],close=f[-1]['c'])
        self.market_at=time.time()
        self.market_monotonic=time.monotonic()
        self._candle_recovered()
        self.emit('market',self.market)

    def market_now(self):
        return getattr(self.x,'server_now',time.time)()

    def cycle(self):
        now=time.monotonic()
        if self.poll_at and now-self.poll_at>60 and self.enabled:
            self.halt('检测到睡眠或长时间停顿，必须人工重新检查后启动')
        self.poll_at=now
        if not self.store:
            return
        # Reconciliation continues when the auto-entry switch is off.
        if self.store.data['active']:
            self.reconcile()
            if self.store.data['active']:
                if self.enabled:
                    equity,_=self.x.balance()
                    self.daily(equity)
                expected=int(self.market_now()//300)*ENTRY_STEP-ENTRY_STEP
                if not self.market or self.market['bar']!=expected:
                    self.refresh_market()
                return
        expected=int(self.market_now()//300)*ENTRY_STEP-ENTRY_STEP
        if not self.market or self.market['bar']!=expected:
            self.refresh_market()
        if not self.enabled:
            return
        equity,available=self.x.balance()
        remaining=self.daily(equity)
        self._reset_streak_day()
        state=self.store.data; s=self.settings
        if state['streak']>=s.consecutive_losses:
            if state.get('streak_notice_day')!=state.get('streak_day'):
                state['streak_notice_day']=state.get('streak_day',''); self.store.save()
                self.emit('log',f'中国时间本日连续净亏损已达 {s.consecutive_losses} 次：停止新开仓；已有仓位/TP/SL继续管理，次日自动恢复')
            return
        if time.time()-state['last_close']<s.cooldown_minutes*60:
            return
        if self.x.positions() or self.x.orders() or self.x.algos():
            raise Halt('出现非本程序仓位或挂单，停止自动开仓')
        expected=int(self.market_now()//300)*ENTRY_STEP-ENTRY_STEP
        if not self.market or self.market['bar']!=expected:
            self.refresh_market()
        market=self.market
        self._verify_latest('5m',market['bar'],ENTRY_STEP)
        age=time.monotonic()-self.market_monotonic if hasattr(self,'market_monotonic') else time.time()-self.market_at
        if age>300:
            self._handle_candle_lag(CandleLag('5m 策略缓存超过5分钟未刷新；本轮不使用旧信号'))
        if market['side']=='观望' or state['last_bar']==market['bar']:
            return
        # Score and threshold are independently rechecked at the execution boundary.
        score=market.get('scores',{}).get(market['side'])
        if not score or not score.get('gate',True) or score['total']<s.score_threshold:
            return
        ticker=self.x.ticker(); self.emit('ticker',ticker)
        if abs(float(ticker['last'])-market['close'])>.3*market['h']['atr']:
            self.emit('log','价格偏离信号超过0.3 ATR，本轮不追价'); return
        multiplier=float(score.get('position_multiplier') or 1.0)
        plan=make_plan(s,market['side'],ticker,self.x.instrument(),market['m']['atr'],available,remaining,multiplier)
        # Expected TP space must cover at least 2x estimated round-trip execution cost.
        if plan['cost_multiple'] < 2:
            state['last_bar']=market['bar']; self.store.save()
            self.emit('log',f"预计TP空间仅为往返成本 {plan['cost_multiple']:.2f} 倍（最低2倍），本轮跳过")
            return
        # Check settings explicitly; set only isolated leverage for this side, never account mode.
        self.x.post('/api/v5/account/set-leverage',{'instId':INSTRUMENT,'lever':str(s.leverage),
                    'mgnMode':'isolated','posSide':plan['posSide']})
        infos=self.x.get('/api/v5/account/leverage-info',{'instId':INSTRUMENT,'mgnMode':'isolated'},True)
        if not any(i.get('posSide')==plan['posSide'] and float(i['lever'])==s.leverage and i.get('mgnMode')=='isolated' for i in infos):
            raise Halt('逐仓杠杆回读不一致')
        if not self.enabled:
            return
        self._verify_latest('5m',market['bar'],ENTRY_STEP)
        cid='mac'+uuid.uuid4().hex[:28]
        tp1_id='t1'+uuid.uuid4().hex[:28]; tp2_id='t2'+uuid.uuid4().hex[:28]; sl_id='sl'+uuid.uuid4().hex[:28]
        # Persist BEFORE POST so ambiguous writes can never create a duplicate parent order.
        state['last_bar']=market['bar']
        state['active']=dict(plan,client_id=cid,algo_id=sl_id,tp1_id=tp1_id,tp2_id=tp2_id,sl_id=sl_id,
                             submitted=time.time(),expires=time.time()+300,equity_before=equity,filled=False,
                             cancel_requested=False,cancel_on_reconcile=False,partial_close_id='',
                             tp1_done=False,breakeven_verified=False,score=score['total'],score_items=score.get('items',[]))
        self.store.save()
        # OKX split-TP rules require TP and SL as one-way attached algos. Two TP sizes sum exactly to parent size.
        # Cost-price SL is exchange-native: amendPxOnTriggerType=1 moves SL to avgPx after the first split TP triggers.
        # TP orders are conditional, marketable limits bounded by the fixed 5bps execution budget; SL remains market.
        tick=float(self.x.instrument()['tickSz']); slip=s.slippage_bps/10000
        tp1_exec=rounded(float(plan['tp1'])*(1-slip if plan['posSide']=='long' else 1+slip),tick,plan['posSide']=='short')
        tp2_exec=rounded(float(plan['tp2'])*(1-slip if plan['posSide']=='long' else 1+slip),tick,plan['posSide']=='short')
        body={'instId':INSTRUMENT,'tdMode':'isolated','side':plan['exchange_side'],'posSide':plan['posSide'],
            'ordType':'limit','px':plan['px'],'sz':plan['sz'],'clOrdId':cid,
            'attachAlgoOrds':[
                {'attachAlgoClOrdId':tp1_id,'tpOrdKind':'condition','tpTriggerPx':plan['tp1'],'tpOrdPx':tp1_exec,'tpTriggerPxType':'last','sz':plan['tp1_sz']},
                {'attachAlgoClOrdId':tp2_id,'tpOrdKind':'condition','tpTriggerPx':plan['tp2'],'tpOrdPx':tp2_exec,'tpTriggerPxType':'last','sz':plan['tp2_sz']},
                {'attachAlgoClOrdId':sl_id,'slTriggerPx':plan['sl'],'slOrdPx':'-1','slTriggerPxType':'last','amendPxOnTriggerType':'1'}
            ]}
        self.x.post('/api/v5/trade/order',body)
        self.store.record('提交开仓请求',state['active'])
        self.emit('log',f"已提交逐仓限价开仓（{score.get('level','信号')} · {score['total']:g}/10 · {multiplier:g}×仓位）：TP1 1R平50% / TP2 2R平50% / SL 1×15m ATR；TP1后自动移保本")
        self.emit('plan',state['active'])

    def _cancel_pending_entry(self,p,reason):
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
            if p.get('cancel_requested'):
                if time.time()-float(p['cancel_requested'])>15:
                    raise Halt('限价撤单状态长时间无法核实；请到OKX核对，禁止重复开仓')
                return
            if filled>0 or positions:
                self._cancel_pending_entry(p,'限价单出现部分成交，先撤销剩余数量')
                return
            if p.get('cancel_on_reconcile'):
                self._cancel_pending_entry(p,'手动停止自动交易')
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
        # OKX attached TP/SL is created only after the parent order is fully filled.
        # A canceled parent with partial fills is therefore flattened at market instead of left unprotected.
        if status=='canceled' and filled>0 and positions:
            if p.get('partial_close_id'):
                if time.time()-float(p.get('partial_close_requested',time.time()))>15:
                    raise Halt('部分成交仓位的市价安全平仓结果长时间无法核实；请立即到OKX核对')
                return
            size=sum(abs(float(r['pos'])) for r in positions)
            if size<=0 or size>float(p['sz'])+1e-10:
                raise Halt('部分成交仓位数量异常，请立即到OKX核对')
            close_id='pc'+uuid.uuid4().hex[:28]
            p['partial_close_id']=close_id; p['partial_close_requested']=time.time(); self.store.save()
            self.x.post('/api/v5/trade/order',{'instId':INSTRUMENT,'tdMode':'isolated','posSide':p['posSide'],
                'side':'sell' if p['posSide']=='long' else 'buy','ordType':'market','sz':str(size),'clOrdId':close_id})
            self.store.record('部分成交安全平仓请求',{'client_id':p['client_id'],'close_id':close_id,'size':size})
            self.emit('log','限价开仓仅部分成交：剩余挂单已撤销，已对成交部分发送市价安全平仓请求；禁止重复发送')
            return
        if status not in ('filled','canceled'):
            if time.time()-p['submitted']>15:
                raise Halt('订单状态长时间不确定，禁止重复开仓；请到OKX核对')
            return
        if filled<=0:
            raise Halt('成交状态异常')
        if not p.get('filled'):
            p['filled']=True; p['filled_sz']=filled; p['filled_at']=time.time(); self.store.save()
        if not positions:
            if time.time()-float(p.get('filled_at',p['submitted']))<10:
                return
            equity,_=self.x.balance()
            pnl=equity-p['equity_before']
            self._reset_streak_day()
            state['streak']=state['streak']+1 if pnl<0 else 0
            state['active']=None; state['last_close']=time.time(); self.store.save()
            self.store.record('仓位归零',dict(client_id=p['client_id'],equity_change=pnl,side=p['side'],px=p['px'],sz=p['sz'],score=p.get('score')))
            self.emit('log',f'仓位已归零；本轮USDT净权益变化 {pnl:+.4f}；中国时间连续亏损 {state["streak"]}/{self.settings.consecutive_losses}')
            return
        if not all(k in p for k in ('tp1','tp2','tp1_sz','tp2_sz','tp1_id','tp2_id','sl_id')):
            raise Halt('检测到旧版活动仓位；V1.3.4不能接管旧TP/SL结构，请先在OKX完成或人工处理该仓位')
        qty=sum(abs(float(r['pos'])) for r in positions)
        full=float(p['sz']); remaining=float(p['tp2_sz']); tol=1e-10
        if abs(qty-full)>tol and abs(qty-remaining)>tol:
            raise Halt('分批止盈后的仓位数量与计划不一致，请立即到OKX核对')
        algos=self.x.algos()
        def live(cid):
            return next((a for a in algos if a.get('algoClOrdId')==cid and a.get('state')=='live'
                         and a.get('posSide')==p['posSide'] and a.get('side')!=p['exchange_side']
                         and a.get('tdMode')=='isolated'),None)
        t1=live(p['tp1_id']); t2=live(p['tp2_id']); sl=live(p['sl_id'])
        tp2_ok=bool(t2 and float(t2.get('tpTriggerPx') or 0)==float(p['tp2'])
                    and float(t2.get('sz') or 0)+tol>=float(p['tp2_sz']))
        sl_ok=bool(sl and sl.get('slOrdPx')=='-1' and sl.get('amendPxOnTriggerType')=='1')
        if abs(qty-full)<=tol:
            tp1_ok=bool(t1 and float(t1.get('tpTriggerPx') or 0)==float(p['tp1'])
                        and float(t1.get('sz') or 0)+tol>=float(p['tp1_sz']))
            sl_price_ok=sl_ok and float(sl.get('slTriggerPx') or 0)==float(p['sl'])
            protection_ok=tp1_ok and tp2_ok and sl_price_ok
        else:
            if not p.get('tp1_done'):
                p['tp1_done']=True; p['tp1_seen_at']=time.time(); self.store.save()
                self.emit('log','TP1已完成：50%仓位止盈；等待核对剩余仓位保本SL已移动到成交均价')
            entry_avg=float(positions[0].get('avgPx') or p['px'])
            sl_px=float(sl.get('slTriggerPx') or 0) if sl else 0
            be_ok=sl_ok and abs(sl_px-entry_avg)<=max(.11,entry_avg*1e-8)
            protection_ok=tp2_ok and be_ok
            if protection_ok and not p.get('breakeven_verified'):
                p['breakeven_verified']=True; self.store.save()
                self.emit('log',f'已核对TP1后保本止损：剩余50% SL={sl_px:g}（成交均价 {entry_avg:g}）')
        if protection_ok:
            if not p.get('protected'):
                p['protected']=True; self.store.save(); self.emit('log','已核对V1.3.4分批TP与SL保护结构')
        else:
            grace=float(p.get('tp1_seen_at',p.get('filled_at',p['submitted'])))
            if time.time()-grace>15:
                self.halt('V1.3.4分批止盈/保本止损保护状态无法核实，已锁住新开仓；请立即到OKX核对')
        self.emit('position',positions)

    def flatten(self):
        self.stop()
        p=self.store.data['active'] if self.store else None
        if not p:
            raise Halt('没有可识别的本程序仓位')
        if p.get('close_id'):
            raise Halt('已有平仓请求，禁止重复发送；请到OKX核对结果')
        pos=self.x.positions()
        if len(pos)!=1 or pos[0].get('posSide')!=p['posSide'] or pos[0].get('mgnMode')!='isolated':
            raise Halt('仓位不唯一或不匹配，请在OKX平仓')
        size=abs(float(pos[0]['pos']))
        if size>float(p['sz'])+1e-10:
            raise Halt('仓位数量超出本程序记录')
        p['close_id']='cl'+uuid.uuid4().hex[:28]; self.store.save()
        # Hedge-mode opposite side + SAME posSide means close, not a reverse opening.
        self.x.post('/api/v5/trade/order',{'instId':INSTRUMENT,'tdMode':'isolated','posSide':p['posSide'],
            'side':'sell' if p['posSide']=='long' else 'buy','ordType':'market','sz':str(size),'clOrdId':p['close_id']})
        self.emit('log','仅平本程序逐仓仓位的请求已发送；请等待仓位归零，不撤销原保护单')

    def acknowledge(self):
        if self.enabled:
            raise Halt('先停止自动开仓')
        if self.x.positions() or self.x.orders() or self.x.algos():
            raise Halt('账户仍有BTC仓位/挂单；请先在OKX人工处理')
        p=self.store.data['active']
        if p:
            order=self.x.order(p['client_id'])
            if order.get('state') not in ('filled','canceled'):
                raise Halt('原订单仍无法核实，不可解除故障锁')
            if float(order.get('accFillSz') or 0)>0:
                equity,_=self.x.balance()
                pnl=equity-p['equity_before']
                self._reset_streak_day()
                self.store.data['streak']=self.store.data['streak']+1 if pnl<0 else 0
                self.store.record('人工核对平仓',dict(client_id=p['client_id'],equity_change=pnl,side=p['side'],px=p['px'],sz=p['sz'],score=p.get('score')))
        self.store.data['active']=None; self.store.data['halt']=''; self.store.save()
        self.emit('log','故障锁已解除；每日权益基准、连续亏损计数与信号去重仍保留')

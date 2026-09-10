"""KAYTRADE V1.3.5 runtime refinements and safety hotfixes."""
import copy
import json
import math
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

STARTUP_BUFFER_SECONDS = 5.0
CANDLE_SETTLEMENT_GRACE_SECONDS = 60.0
OLD_THRESHOLD_LABEL = '自动开仓评分阈值 3.5—10（0.5步进）'
NEW_THRESHOLD_LABEL = '自动开仓评分阈值 1—10（0.5步进）'
DAILY_STOP_TEXT = '达到中国时间日内权益回撤上限'


class DailyRiskStop(RuntimeError):
    """Daily drawdown is a trading stop, not a system/integrity fault."""


def _validate_settings(self):
    from engine import Halt
    if not 1.0 <= self.score_threshold <= 10.0 or abs(self.score_threshold * 2 - round(self.score_threshold * 2)) > 1e-9:
        raise Halt('评分阈值必须是1—10之间、以0.5为步进')
    for name, value in asdict(self).items():
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise Halt(name + ' 必须是有限正数')
    if int(self.leverage) != self.leverage or not 1 <= self.leverage <= 10:
        raise Halt('杠杆范围1—10倍（整数）')
    if int(self.consecutive_losses) != self.consecutive_losses or self.consecutive_losses > 20:
        raise Halt('连续亏损上限必须是1—20的整数')
    if self.daily_loss > self.capital or self.max_notional > self.capital * self.leverage:
        raise Halt('日亏损/名义仓位超出资金与杠杆范围')
    if not .6 <= self.stop_atr <= 3:
        raise Halt('ATR止损倍数范围0.6—3')
    if abs(self.reward_r - 2.0) > 1e-9:
        raise Halt('V1.3.5最终止盈固定为2R')
    if abs(self.fee_bps - 2.0) > 1e-9 or abs(self.taker_fee_bps - 5.0) > 1e-9 or abs(self.slippage_bps - 5.0) > 1e-9:
        raise Halt('V1.3.5成本参数固定：Maker 2bps / Taker 5bps / 滑点预算5bps')
    return self


def _widgets(root):
    out=[]
    try: children=root.winfo_children()
    except Exception: return out
    for child in children:
        out.append(child); out.extend(_widgets(child))
    return out


def _is_explicit_rejection(exc):
    return bool(getattr(exc,'write_rejected',False))


def _score_tier(total,eligible=False):
    total=float(total)
    if total>=7.0:return 2.0,'三级信号 · 2.0×仓位'
    if total>=5.0:return 1.5,'二级信号 · 1.5×仓位'
    if total>=1.0:return 1.0,'一级信号 · 1.0×仓位'
    return (1.0 if eligible else 0.0),'未达最低可选评分'


def _safe_make_plan(s,side,ticker,meta,atr,available,daily_remaining,position_multiplier=1.0):
    """Keep limit entry, but size risk using worst-case taker entry fee."""
    import engine
    s.validate()
    if side not in ('做多','做空') or not math.isfinite(atr) or atr<=0:
        raise engine.Halt('无效信号或ATR')
    if float(position_multiplier) not in (1.0,1.5,2.0):
        raise engine.Halt('评分仓位倍率必须是1 / 1.5 / 2')
    buy=side=='做多'; d=1 if buy else -1
    ask,bid=float(ticker['askPx']),float(ticker['bidPx'])
    if not 0<bid<=ask or (ask-bid)/bid>s.slippage_bps/10000:
        raise engine.Halt('买卖价差过大')
    limit=float(engine.rounded(bid if buy else ask,meta['tickSz'],not buy))
    dist=atr*s.stop_atr
    sl=engine.rounded(limit-d*dist,meta['tickSz'],not buy)
    tp1=engine.rounded(limit+d*dist,meta['tickSz'],buy)
    tp2=engine.rounded(limit+d*dist*2,meta['tickSz'],buy)
    if not (float(sl)<limit<float(tp1)<float(tp2) if buy else float(tp2)<float(tp1)<limit<float(sl)):
        raise engine.Halt('止盈止损价格非法')
    unit=float(meta['ctVal'])*float(meta.get('ctMult') or 1)
    if unit<=0 or float(meta['minSz'])<=0 or float(meta['lotSz'])<=0:
        raise engine.Halt('合约单位异常')
    maker=s.fee_bps/10000; taker=s.taker_fee_bps/10000; slip=s.slippage_bps/10000
    entry_fee=max(maker,taker)
    per_btc=abs(limit-float(sl))+limit*entry_fee+float(sl)*(taker+slip)
    base_risk=min(s.risk_usdt,s.capital*s.risk_pct/100)
    risk=min(base_risk*float(position_multiplier),daily_remaining)
    notional=min(s.max_notional,s.capital*s.leverage,available*.9*s.leverage)
    quantity=engine.rounded(min(risk/per_btc,notional/limit)/unit,meta['lotSz'])
    if Decimal(quantity)<Decimal(meta['minSz']):
        raise engine.Halt('风险预算不足以满足最小下单量，跳过')
    tp1_sz,tp2_sz=engine._split_tp_sizes(quantity,meta['lotSz'],meta['minSz'])
    btc=float(quantity)*unit
    if btc*per_btc>risk+1e-9 or btc*limit>notional+1e-9:
        raise engine.Halt('取整后风险超限')
    weighted_exit=(float(tp1)+float(tp2))/2
    expected_roundtrip_cost=limit*entry_fee+weighted_exit*(taker+slip)
    planned_reward_distance=dist*1.5
    cost_multiple=planned_reward_distance/expected_roundtrip_cost if expected_roundtrip_cost>0 else math.inf
    return dict(side=side,posSide='long' if buy else 'short',exchange_side='buy' if buy else 'sell',
        px=str(limit),sz=quantity,sl=sl,tp=tp2,tp1=tp1,tp2=tp2,tp1_sz=tp1_sz,tp2_sz=tp2_sz,
        btc=btc,notional=btc*limit,estimated_loss=btc*per_btc,position_multiplier=float(position_multiplier),
        base_risk_budget=base_risk,effective_risk_budget=risk,
        expected_roundtrip_cost=btc*expected_roundtrip_cost,cost_multiple=cost_multiple,
        reward_r=2.0,breakeven_after_tp1=True,entry_fee_budget_bps=max(s.fee_bps,s.taker_fee_bps))


def _remove_15m_setup_gate(original_signal,strategy_module):
    def signal(*args,**kwargs):
        result=original_signal(*args,**kwargs); scores=result.get('scores',{})
        for side,score in scores.items():
            structure=score.get('structure',{}) or {}
            trigger=float((score.get('layers',{}) or {}).get('trigger',0.0) or 0.0)
            hard_trigger=trigger>=.5; blocked=bool(structure.get('blocked'))
            gate=hard_trigger and not blocked
            total=float(score.get('total',0.0) or 0.0); required=float(score.get('required',result.get('threshold',3.5)) or 0.0)
            eligible=gate and total>=required; multiplier,level=_score_tier(total,eligible)
            score.update(gate=gate,eligible=eligible,level=level,position_multiplier=multiplier if eligible else 0.0)
            if blocked:score['reason']=f'前方强结构距离 {structure.get("front_r",0):.2f}R < 1R，禁止开仓'
            elif not hard_trigger:score['reason']=f'5m Trigger {trigger:g}/1.0 < 0.5，等待入场触发'
            elif eligible:score['reason']=f'{level} · {total:g}/{result.get("score_max",10):g} 达标；按 {multiplier:g}× 仓位进入执行与风险检查'
            else:score['reason']=f'{level} · {total:g}/{result.get("score_max",10):g}，未达开仓阈值 {required:g}'
        qualified=[side for side,score in scores.items() if score.get('eligible')]
        if len(qualified)==1:selected=qualified[0]
        elif len(qualified)==2 and scores[qualified[0]].get('total')!=scores[qualified[1]].get('total'):selected=max(qualified,key=lambda side:scores[side].get('total',0))
        else:selected='观望'
        result['side']=selected
        result['why']=scores[selected]['reason'] if selected!='观望' else ' / '.join(side+': '+score.get('reason','') for side,score in scores.items())
        return result
    return signal


def _china_day():
    return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')


def _daily(self,equity):
    import engine
    if not math.isfinite(equity) or equity<=0:
        raise engine.Halt('账户权益无效')
    state=self.store.data; day=_china_day()
    if state.get('day')!=day:
        state.update(day=day,peak=equity,daily_stop_day='',daily_notice_day='')
    peak=float(state.get('peak') or equity)
    state['peak']=max(equity,peak)
    drawdown=max(0.0,state['peak']-equity)
    remaining=self.settings.daily_loss-drawdown
    detail=(f'峰值权益 {state["peak"]:.4f} USDT，当前权益 {equity:.4f} USDT，'
            f'已回撤 {drawdown:.4f} / 上限 {self.settings.daily_loss:.4f} USDT')
    self.store.save()
    # Once hit, the daily stop remains latched for the current China day even if
    # equity later rebounds. It is not a permanent integrity fault and must not
    # be cleared by the generic fault-lock acknowledgement button.
    if state.get('daily_stop_day')==day:
        raise DailyRiskStop('中国时间本日已达到权益回撤上限；'+detail+'；停止新开仓，原有TP/SL继续生效，次日自动恢复')
    if remaining<=0:
        state['daily_stop_day']=day; self.store.save()
        raise DailyRiskStop('中国时间本日已达到权益回撤上限；'+detail+'；停止新开仓，原有TP/SL继续生效，次日自动恢复')
    return remaining


def _check_latest(bar,timestamp,now,step):
    import candles
    expected=candles.expected_bar(now,step)
    if timestamp==expected:return
    latest_close=datetime.fromtimestamp((timestamp+step)/1000,timezone.utc).strftime('%H:%M:%S UTC')
    expected_close=datetime.fromtimestamp((expected+step)/1000,timezone.utc).strftime('%H:%M:%S UTC')
    current=datetime.fromtimestamp(now,timezone.utc).strftime('%H:%M:%S UTC'); boundary=expected+step
    if timestamp==expected-step and 0<=now*1000-boundary<=CANDLE_SETTLEMENT_GRACE_SECONDS*1000:
        raise candles.CandlePending(f'{bar} 刚收盘，等待OKX确认最新K线：上一根已确认收盘 {latest_close}，本应确认收盘 {expected_close}，交易所时间 {current}；正常结算窗口最多60秒，不使用未确认K线')
    lag=max(0.0,(expected-timestamp)/1000)
    raise candles.CandleLag(f'{bar} K线持续过期/时间异常：上一根已确认收盘 {latest_close}，本应确认收盘 {expected_close}，交易所时间 {current}，落后 {lag:.0f}秒；不使用旧信号')


def _market_split_tp_post(original_post):
    def post(self,path,body):
        payload=body
        if path=='/api/v5/trade/order' and isinstance(body,dict) and body.get('attachAlgoOrds'):
            payload=copy.deepcopy(body); tps=[item for item in payload.get('attachAlgoOrds',[]) if item.get('tpTriggerPx') is not None]
            if len(tps)>=2:
                for item in tps:item['tpOrdKind']='condition'; item['tpOrdPx']='-1'
        reply=original_post(self,path,payload)
        if path!='/api/v5/trade/order':return reply
        import exchange
        row=reply[0] if isinstance(reply,list) and reply else None
        if not isinstance(row,dict):raise exchange.APIError('OKX交易请求响应为空/结构异常；写入结果需核对，禁止重复提交',method='POST',path=path)
        if not str(row.get('ordId') or ''):raise exchange.APIError('OKX交易请求响应缺少ordId；写入结果需核对，禁止重复提交',method='POST',path=path)
        sent_client=str(payload.get('clOrdId') or '') if isinstance(payload,dict) else ''; returned_client=str(row.get('clOrdId') or '')
        if sent_client and returned_client and sent_client!=returned_client:raise exchange.APIError('OKX交易请求返回的clOrdId与本地请求不一致；写入结果需核对，禁止重复提交',method='POST',path=path)
        return reply
    return post


def apply():
    import app, candles, engine, exchange, strategy
    if getattr(engine.Engine,'_kaytrade_v135_patch_applied',False):return
    engine.Settings.validate=_validate_settings
    engine.make_plan=_safe_make_plan
    original_strategy_signal=strategy.signal; relaxed_signal=_remove_15m_setup_gate(original_strategy_signal,strategy)
    strategy.signal=relaxed_signal; engine.signal=relaxed_signal
    candles.check_latest=_check_latest; engine.check_latest=_check_latest
    original_exchange_post=exchange.Exchange.post; exchange.Exchange.post=_market_split_tp_post(original_exchange_post)

    original_app_init=app.App.__init__; original_app_stop=app.App.stop
    def app_init(self,*args,**kwargs):
        original_app_init(self,*args,**kwargs)
        try:
            path=Path(self.settings_path)
            if path.exists():
                saved=json.loads(path.read_text())
                if 'score_threshold' in saved:
                    value=float(saved['score_threshold']); value=max(1.0,min(10.0,round(value*2)/2)); self.fields['score_threshold'].set(f'{value:g}')
        except Exception:pass
        for widget in _widgets(self.root):
            try:
                if widget.cget('text')==OLD_THRESHOLD_LABEL:widget.configure(text=NEW_THRESHOLD_LABEL)
            except Exception:pass
    def app_stop(self):
        if getattr(self,'engine',None):
            self.engine.stopped=True; self.engine.enabled=False; self.engine.startup_buffer_until=0.0
        return original_app_stop(self)
    app.App.__init__=app_init; app.App.stop=app_stop

    engine.Engine.daily=_daily
    original_engine_init=engine.Engine.__init__; original_connect=engine.Engine.connect; original_arm=engine.Engine.arm
    original_cycle=engine.Engine.cycle; original_stop=engine.Engine.stop; original_reconcile=engine.Engine.reconcile; original_flatten=engine.Engine.flatten
    def engine_init(self,*args,**kwargs):
        original_engine_init(self,*args,**kwargs); self.startup_buffer_until=0.0
    def connect(self):
        result=original_connect(self)
        if self.store:
            reason=str(self.store.data.get('halt') or '')
            if DAILY_STOP_TEXT in reason:
                self.store.data['halt']=''
                if self.store.data.get('day')==_china_day():self.store.data['daily_stop_day']=_china_day()
                self.store.save(); self.emit('log','旧版日内回撤故障锁已迁移为“当日停止新开仓”；权益峰值基准保留，次日自动恢复')
        return result
    def arm(self,settings):
        try:result=original_arm(self,settings)
        except DailyRiskStop as exc:
            self.enabled=False; self.startup_buffer_until=0.0; day=_china_day()
            if self.store and self.store.data.get('daily_notice_day')!=day:
                self.store.data['daily_notice_day']=day; self.store.save(); self.emit('log',str(exc))
            return None
        self.startup_buffer_until=time.monotonic()+STARTUP_BUFFER_SECONDS
        self.emit('log','自动交易已开启；启动缓冲5秒，期间只更新行情/核对仓位，不允许自动开仓'); return result
    def _handle_daily_stop(self,exc):
        day=_china_day()
        if self.store and self.store.data.get('daily_notice_day')!=day:
            self.store.data['daily_notice_day']=day; self.store.save(); self.emit('log',str(exc))
        return None
    def _handle_explicit_rejection(self,exc):
        p=self.store.data.get('active') if self.store else None; cleared=False
        if isinstance(p,dict) and not p.get('order_id') and not p.get('filled') and getattr(exc,'path','')=='/api/v5/trade/order':
            snapshot={'client_id':p.get('client_id',''),'side':p.get('side',''),'score':p.get('score'),'submitted':p.get('submitted'),'code':str(getattr(exc,'code','') or ''),'reason':str(exc)}
            self.store.data['active']=None; self.store.save(); self.store.record('OKX明确拒绝开仓',snapshot); cleared=True
        self.enabled=False; self.startup_buffer_until=0.0; code=str(getattr(exc,'code','') or '')
        path=str(getattr(exc,'path','') or '未知接口'); method=str(getattr(exc,'method','') or 'POST')
        detail='；已清理本地未成交占位，不进入51603订单核对' if cleared else '；本地活动状态保留，禁止重复提交'
        if code=='50123':
            message=f'OKX 50123明确拒绝 {method} {path}；本次请求没有产生新开仓订单'
        else:
            message=f'OKX明确拒绝 {method} {path}'+(f'（错误码 {code}）' if code else '')+'：'+str(exc)
        raise engine.Halt(message+detail+'；已停止新开仓，请核对API权限、账户环境及OKX返回信息后重新测试连接/启动') from None
    def run_original_cycle(self):
        try:return original_cycle(self)
        except DailyRiskStop as exc:return _handle_daily_stop(self,exc)
        except Exception as exc:
            if _is_explicit_rejection(exc):return _handle_explicit_rejection(self,exc)
            raise
    def cycle(self):
        until=float(getattr(self,'startup_buffer_until',0.0) or 0.0)
        if self.enabled and until:
            now=time.monotonic()
            if now<until:
                self.enabled=False
                try:return run_original_cycle(self)
                finally:
                    if not self.stopped and self.store and not self.store.data.get('halt'):self.enabled=True
            self.startup_buffer_until=0.0; self.emit('log','启动缓冲5秒结束；自动开仓现已启用')
        return run_original_cycle(self)
    def reconcile(self):
        result=original_reconcile(self); p=self.store.data.get('active') if self.store else None
        if not p:return result
        if p.get('protected') and p.get('filled') and not p.get('market_tp_verified') and not p.get('tp1_done'):
            algos=self.x.algos(); by_id={a.get('algoClOrdId'):a for a in algos if isinstance(a,dict) and a.get('state')=='live'}
            t1=by_id.get(p.get('tp1_id')); t2=by_id.get(p.get('tp2_id'))
            if t1 and t2:
                if str(t1.get('tpOrdPx') or '')!='-1' or str(t2.get('tpOrdPx') or '')!='-1':self.halt('V1.3.5检测到TP不是市价执行；已锁住新开仓，请立即核对OKX保护单')
                else:p['market_tp_verified']=True; self.store.save(); self.emit('log','已核对TP1/TP2均为触发后市价止盈（tpOrdPx=-1），SL为市价止损；分批保护结构有效')
        halt_reason=str(self.store.data.get('halt') or '')
        if p.get('filled') and '保护状态无法核实' in halt_reason and not p.get('close_id') and not p.get('partial_close_id') and not p.get('emergency_close_id'):
            positions=self.x.positions(); same=[r for r in positions if r.get('mgnMode')=='isolated' and r.get('posSide')==p.get('posSide') and Decimal(str(r.get('pos') or '0'))!=0]
            if len(same)==1:
                size=abs(Decimal(str(same[0].get('pos') or '0')))
                if size>0 and size<=Decimal(str(p.get('sz') or '0')):
                    close_id='ec'+uuid.uuid4().hex[:28]; p['emergency_close_id']=close_id; p['emergency_close_requested']=time.time(); p['emergency_close_reason']=halt_reason; self.store.save()
                    body={'instId':engine.INSTRUMENT,'tdMode':'isolated','posSide':p['posSide'],'side':'sell' if p['posSide']=='long' else 'buy','ordType':'market','sz':format(size,'f'),'clOrdId':close_id}
                    reply=self.x.post('/api/v5/trade/order',body); p['emergency_close_order_id']=str(reply[0].get('ordId') or ''); p['emergency_close_ack_at']=time.time(); self.store.save()
                    self.store.record('保护异常自动安全平仓请求',{'client_id':p.get('client_id'),'close_id':close_id,'ordId':p['emergency_close_order_id'],'size':format(size,'f')})
                    self.emit('log','成交仓位的TP/SL保护超过15秒仍无法核实：已发送一次市价安全平仓；不会自动重复发送，请继续核对OKX')
        return result
    def flatten(self):
        if self.store:
            p=self.store.data.get('active')
            if isinstance(p,dict) and (p.get('partial_close_id') or p.get('emergency_close_id')):raise engine.Halt('已有自动安全平仓请求，禁止再发送第二笔手动平仓；请先在OKX核对结果')
        try:return original_flatten(self)
        except Exception as exc:
            if _is_explicit_rejection(exc) and self.store:
                p=self.store.data.get('active')
                if isinstance(p,dict) and p.get('close_id'):
                    rejected_id=p.get('close_id'); p.pop('close_id',None); p.pop('close_requested',None)
                    p['last_close_rejection']={'time':time.time(),'code':str(getattr(exc,'code','') or ''),'message':str(exc),'client_id':rejected_id}
                    self.store.save(); self.store.record('手动平仓被OKX明确拒绝',p['last_close_rejection']); self.emit('log','手动市价平仓被OKX明确拒绝；未创建平仓单，已释放本地close_id，可在修正原因后再次手动提交')
            raise
    def stop(self):
        self.startup_buffer_until=0.0; return original_stop(self)
    engine.Engine.__init__=engine_init; engine.Engine.connect=connect; engine.Engine.arm=arm; engine.Engine.cycle=cycle
    engine.Engine.reconcile=reconcile; engine.Engine.flatten=flatten; engine.Engine.stop=stop; engine.Engine._kaytrade_v135_patch_applied=True
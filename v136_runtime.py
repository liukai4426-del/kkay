"""V1.3.6 runtime observability, account-state and manual recovery patch.

Strategy scoring, 15m ATR, limit-entry order type and TP1/TP2 structure stay unchanged.
V1.3.6 refines execution-cost diagnostics and uses live OKX equity/available balance
for displayed account state and sizing caps. Manual fault unlock may reset recoverable
day-risk stops only after the existing account cross-check proves BTC exposure is flat.
"""
import math
import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from v135_auto_entry_guard import apply as apply_v135_guard
apply_v135_guard()

import app
import engine
import exchange


_ACCOUNT_SNAPSHOT = {}
_LAST_PLAN_DIAG = None


def _china_day():
    return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')


def _once(self, key, message, interval=60.0):
    now=time.monotonic()
    seen=getattr(self,'_v136_decisions',None)
    if not isinstance(seen,dict):
        seen={}; self._v136_decisions=seen
    if key not in seen or now-float(seen.get(key,0) or 0)>=interval:
        seen[key]=now; self.emit('log',message)


def _preserved_risk_block(self, settings=None):
    """Return a human-readable recoverable day-risk blocker."""
    if not self.store:
        return ''
    state=self.store.data; day=_china_day(); settings=settings or getattr(self,'settings',None)
    if state.get('daily_stop_day')==day:
        limit=getattr(settings,'daily_loss',None)
        suffix=f'（日内回撤上限 {float(limit):g} USDT）' if isinstance(limit,(int,float)) else ''
        return '中国时间本日已触发日内权益回撤停止'+suffix+'；可在账户空仓且无挂单时使用“解除故障锁”人工重置'
    streak_day=state.get('streak_day')
    streak=int(state.get('streak') or 0)
    limit=int(getattr(settings,'consecutive_losses',0) or 0) if settings else 0
    if streak_day==day and limit>0 and streak>=limit:
        return f'中国时间本日连续净亏损已达 {streak} 次（上限 {limit} 次）；可在账户空仓且无挂单时使用“解除故障锁”人工重置'
    return ''


def _decision(self):
    """Explain a no-order cycle using state already read by the engine."""
    if not self.store:return
    state=self.store.data
    if state.get('halt'):
        return
    if state.get('active'):
        p=state['active']; phase=p.get('phase') or ('已成交' if p.get('filled') else '等待OKX订单状态')
        _once(self,'active:'+str(p.get('client_id')),f'自动下单状态：已有本程序活动记录（{phase}），先核对/管理该订单，不重复开仓')
        return
    risk_block=_preserved_risk_block(self)
    if risk_block:
        _once(self,'risk-block:'+_china_day()+':'+risk_block,'自动下单检查：'+risk_block,60)
        return
    if not self.enabled:
        return
    until=float(getattr(self,'startup_buffer_until',0) or 0)
    if until>time.monotonic():
        _once(self,'startup-buffer','自动下单检查：启动安全缓冲中，缓冲结束前只更新行情和核对账户',5)
        return
    market=getattr(self,'market',None)
    if not isinstance(market,dict):
        _once(self,'market-none','自动下单检查：指标K线尚未完成加载，暂不生成订单',15)
        return
    bar=market.get('bar'); side=market.get('side','观望')
    if side=='观望':
        _once(self,'watch:'+str(bar),'自动下单检查：当前5m收盘信号为观望，多空条件尚未同时满足',300)
        return
    score=(market.get('scores') or {}).get(side) or {}
    threshold=float(getattr(self.settings,'score_threshold',3.5))
    total=float(score.get('total') or 0)
    if not score.get('gate',True):
        _once(self,'gate:'+str(bar)+side,f"自动下单检查：{side} {total:g}/10，但执行门槛未通过：{score.get('reason','触发条件不足')}",300)
        return
    if total<threshold:
        _once(self,'score:'+str(bar)+side,f'自动下单检查：{side}评分 {total:g}/10，低于设置阈值 {threshold:g}/10',300)
        return
    if state.get('last_bar')==bar:
        _once(self,'dedup:'+str(bar),f'自动下单检查：{side} {total:g}/10 已达标，但本根5m信号已处理；为防重复下单等待下一根K线',300)
        return
    cooldown=float(getattr(self.settings,'cooldown_minutes',0) or 0)*60
    remain=cooldown-(time.time()-float(state.get('last_close',0) or 0))
    if remain>0:
        _once(self,'cooldown:'+str(bar),f'自动下单检查：{side} {total:g}/10 已达标，平仓冷却仍剩约 {int(remain+59)//60} 分钟',60)
        return
    _once(self,'eligible:'+str(bar)+side,f'自动下单检查：{side} {total:g}/10 已通过评分与触发门槛，正在执行账户、价格偏离、成本、数量及限价委托检查',300)


def _live_equity_for(available):
    """Return the equity paired with the balance read that produced `available`."""
    try:
        equity=float(_ACCOUNT_SNAPSHOT.get('equity'))
        cached_available=float(_ACCOUNT_SNAPSHOT.get('available'))
        age=time.monotonic()-float(_ACCOUNT_SNAPSHOT.get('at') or 0)
        tolerance=max(.01,abs(float(available))*.001)
        if math.isfinite(equity) and equity>0 and age<=10 and abs(cached_available-float(available))<=tolerance:
            return equity
    except Exception:
        pass
    return float(available)


def _v136_make_plan(s,side,ticker,meta,atr,available,daily_remaining,position_multiplier=1.0):
    """Size safely from live account state while separating expected fees from worst-case buffers.

    Risk sizing remains fail-safe: it still budgets taker entry plus taker+slippage at SL.
    The TP-cost quality filter now treats the passive limit entry as Maker and TP exits as
    Taker; the 5bps slippage value remains visible as a worst-case execution buffer instead
    of being counted as if it were a guaranteed fee on every profitable trade.
    """
    global _LAST_PLAN_DIAG
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
    dist=float(atr)*float(s.stop_atr)
    sl=engine.rounded(limit-d*dist,meta['tickSz'],not buy)
    tp1=engine.rounded(limit+d*dist,meta['tickSz'],buy)
    tp2=engine.rounded(limit+d*dist*2,meta['tickSz'],buy)
    if not (float(sl)<limit<float(tp1)<float(tp2) if buy else float(tp2)<float(tp1)<limit<float(sl)):
        raise engine.Halt('止盈止损价格非法')
    unit=float(meta['ctVal'])*float(meta.get('ctMult') or 1)
    if unit<=0 or float(meta['minSz'])<=0 or float(meta['lotSz'])<=0:
        raise engine.Halt('合约单位异常')

    maker=float(s.fee_bps)/10000
    taker=float(s.taker_fee_bps)/10000
    slip=float(s.slippage_bps)/10000

    # Keep risk sizing conservative even though the intended entry is passive LIMIT.
    risk_entry_fee=max(maker,taker)
    per_btc=abs(limit-float(sl))+limit*risk_entry_fee+float(sl)*(taker+slip)

    account_equity=_live_equity_for(available)
    risk_capital=min(float(s.capital),account_equity)
    base_risk=min(float(s.risk_usdt),risk_capital*float(s.risk_pct)/100)
    risk=min(base_risk*float(position_multiplier),float(daily_remaining))
    notional=min(float(s.max_notional),risk_capital*float(s.leverage),float(available)*.9*float(s.leverage))
    quantity=engine.rounded(min(risk/per_btc,notional/limit)/unit,meta['lotSz'])
    if Decimal(quantity)<Decimal(str(meta['minSz'])):
        raise engine.Halt('风险预算不足以满足最小下单量，跳过')
    tp1_sz,tp2_sz=engine._split_tp_sizes(quantity,meta['lotSz'],meta['minSz'])
    btc=float(quantity)*unit
    if btc*per_btc>risk+1e-9 or btc*limit>notional+1e-9:
        raise engine.Halt('取整后风险超限')

    weighted_exit=(float(tp1)+float(tp2))/2
    planned_reward_distance=dist*1.5

    # Expected profitable-trade cost: passive Maker entry + Taker TP exits.
    expected_cost_per_btc=limit*maker+weighted_exit*taker
    # Safety reference only: taker-like entry + Taker TP exit + full slippage budget.
    worst_cost_per_btc=limit*risk_entry_fee+weighted_exit*(taker+slip)
    cost_multiple=planned_reward_distance/expected_cost_per_btc if expected_cost_per_btc>0 else math.inf
    worst_cost_multiple=planned_reward_distance/worst_cost_per_btc if worst_cost_per_btc>0 else math.inf

    entry_fee=btc*limit*maker
    tp1_exit_fee=btc*.5*float(tp1)*taker
    tp2_exit_fee=btc*.5*float(tp2)*taker
    tp1_gross=btc*.5*dist
    tp2_gross=btc*dist
    average_gross=tp1_gross+tp2_gross
    expected_roundtrip_cost=entry_fee+tp1_exit_fee+tp2_exit_fee
    worst_roundtrip_cost=btc*worst_cost_per_btc
    tp1_net=tp1_gross-(entry_fee*.5+tp1_exit_fee)
    tp2_net=tp2_gross-(entry_fee*.5+tp2_exit_fee)
    expected_net=average_gross-expected_roundtrip_cost
    worst_net=average_gross-worst_roundtrip_cost

    plan=dict(
        side=side,posSide='long' if buy else 'short',exchange_side='buy' if buy else 'sell',
        px=str(limit),sz=quantity,sl=sl,tp=tp2,tp1=tp1,tp2=tp2,tp1_sz=tp1_sz,tp2_sz=tp2_sz,
        btc=btc,notional=btc*limit,estimated_loss=btc*per_btc,position_multiplier=float(position_multiplier),
        base_risk_budget=base_risk,effective_risk_budget=risk,
        account_equity=account_equity,available_balance=float(available),strategy_capital_cap=float(s.capital),
        risk_capital=risk_capital,atr_15m=float(atr),stop_distance=dist,
        expected_roundtrip_cost=expected_roundtrip_cost,worst_roundtrip_cost=worst_roundtrip_cost,
        expected_fee_multiple=cost_multiple,worst_cost_multiple=worst_cost_multiple,cost_multiple=cost_multiple,
        tp1_leg_gross=tp1_gross,tp2_leg_gross=tp2_gross,tp1_leg_net=tp1_net,tp2_leg_net=tp2_net,
        average_tp_gross=average_gross,expected_net_profit=expected_net,worst_net_profit=worst_net,
        reward_r=2.0,breakeven_after_tp1=True,entry_fee_budget_bps=float(s.fee_bps),
    )
    _LAST_PLAN_DIAG=dict(plan)
    return plan


def _diag_text(plan, prefix='开仓成本诊断'):
    if not isinstance(plan,dict):
        return ''
    return (
        f"{prefix}：BTC {float(plan.get('px',0)):,.2f}；15m ATR {float(plan.get('atr_15m',0)):,.2f}，"
        f"1R {float(plan.get('stop_distance',0)):,.2f}；仓位 {float(plan.get('notional',0)):,.2f} USDT；"
        f"预计手续费 {float(plan.get('expected_roundtrip_cost',0)):.2f} USDT，"
        f"最坏执行成本(含滑点预算) {float(plan.get('worst_roundtrip_cost',0)):.2f} USDT；"
        f"TP1半仓毛/净 {float(plan.get('tp1_leg_gross',0)):.2f}/{float(plan.get('tp1_leg_net',0)):.2f} USDT，"
        f"TP2半仓毛/净 {float(plan.get('tp2_leg_gross',0)):.2f}/{float(plan.get('tp2_leg_net',0)):.2f} USDT；"
        f"两段合计预计净收益 {float(plan.get('expected_net_profit',0)):.2f} USDT；"
        f"平均TP/预计手续费 {float(plan.get('expected_fee_multiple',0)):.2f}×，"
        f"平均TP/最坏成本 {float(plan.get('worst_cost_multiple',0)):.2f}×"
    )


def apply():
    if getattr(engine.Engine,'_kaytrade_v136_runtime_applied',False):return
    original_arm=engine.Engine.arm
    original_cycle=engine.Engine.cycle
    original_stop=engine.Engine.stop
    original_acknowledge=engine.Engine.acknowledge
    original_balance=exchange.Exchange.balance
    original_refresh_plan_settings=app.App.refresh_plan_settings
    original_render_plan=app.App.render_plan
    original_drain=app.App.drain

    # Cache the exact balance already read by the engine. No extra OKX request is added.
    def balance(self):
        equity,available=original_balance(self)
        _ACCOUNT_SNAPSHOT.update(equity=float(equity),available=float(available),at=time.monotonic())
        self._v136_last_balance=(float(equity),float(available),time.monotonic())
        return equity,available
    exchange.Exchange.balance=balance

    # Replace V1.3.5 economics only; scoring/ATR/TP geometry stay untouched.
    engine.make_plan=_v136_make_plan

    def refresh_plan_settings(self):
        original_refresh_plan_settings(self)
        e=getattr(self,'engine',None)
        snap=getattr(getattr(e,'x',None),'_v136_last_balance',None) if e else None
        if snap and hasattr(self,'plan_vars'):
            try:
                equity,available,stamp=snap
                if time.monotonic()-float(stamp)<=15:
                    self.plan_vars['capital'].set(f'{float(equity):,.2f} USDT · 可用 {float(available):,.2f}')
            except Exception:
                pass
    app.App.refresh_plan_settings=refresh_plan_settings

    def render_plan(self,data):
        result=original_render_plan(self,data)
        if not hasattr(self,'plan_vars') or not isinstance(data,dict):
            return result
        try:
            self.plan_vars['capital'].set(
                f"{float(data.get('account_equity')):,.2f} USDT · 可用 {float(data.get('available_balance')):,.2f}"
            )
        except Exception:
            refresh_plan_settings(self)
        try:
            self.plan_vars['risk'].set(
                f"{float(data.get('effective_risk_budget')):.2f} USDT · {float(self.fields['risk_pct'].get()):g}%上限"
            )
        except Exception:
            pass
        return result
    app.App.render_plan=render_plan

    def drain(self):
        result=original_drain(self)
        refresh_plan_settings(self)
        e=getattr(self,'engine',None)
        snap=getattr(getattr(e,'x',None),'_v136_last_balance',None) if e else None
        if snap:
            try:
                equity,available,stamp=snap
                if time.monotonic()-float(stamp)<=15:
                    self.equity.set(f'实时USDT权益：{float(equity):,.2f} · 可用 {float(available):,.2f}')
            except Exception:
                pass
        return result
    app.App.drain=drain

    def arm(self,settings):
        result=original_arm(self,settings)
        risk_block=_preserved_risk_block(self,settings)
        if not self.enabled:
            self.startup_buffer_until=0.0
            self.stopped=True
            if risk_block:
                self.emit('log','自动交易启动未授权：'+risk_block+'；未产生开仓请求')
            else:
                self.emit('log','自动交易启动未授权：启动检查未通过；未产生开仓请求。请查看同一时间的“自动交易启动检查未通过”日志获取具体原因')
            return False
        self.stopped=False
        if risk_block:
            self.emit('log','V1.3.6运行状态确认：自动交易已授权，但'+risk_block)
        else:
            self.emit('log','V1.3.6运行状态确认：自动开仓已授权；5秒缓冲后进入信号执行')
        return result

    def acknowledge(self):
        equity,_=self.x.balance()
        equity=float(equity)
        if not math.isfinite(equity) or equity<=0:
            raise engine.Halt('当前账户权益无效，不能重置日内风控')
        original_emit=self.emit
        def filtered_emit(kind,data):
            if kind=='log' and isinstance(data,str) and '每日权益基准、连续亏损计数与信号去重仍保留' in data:
                return
            return original_emit(kind,data)
        self.emit=filtered_emit
        try:
            result=original_acknowledge(self)
        finally:
            self.emit=original_emit
        if not self.store:
            return result
        day=_china_day(); state=self.store.data
        preserved_last_bar=state.get('last_bar',0)
        state.update(
            day=day,
            peak=equity,
            daily_stop_day='',
            daily_notice_day='',
            streak=0,
            streak_day=day,
            streak_notice_day='',
            last_bar=preserved_last_bar,
        )
        self.store.save()
        self.store.record('V1.3.6人工解除可恢复风险停止',{
            'day':day,
            'equity_baseline':equity,
            'last_bar_preserved':preserved_last_bar,
        })
        self.emit('log',f'V1.3.6人工恢复完成：故障锁、当日日内回撤停止与连续亏损停止均已解除；日内权益基准重置为 {equity:g} USDT；信号去重仍保留。重新启动自动交易后会再次执行完整启动检查')
        return result

    def cycle(self):
        global _LAST_PLAN_DIAG
        _LAST_PLAN_DIAG=None
        before_active=(self.store.data.get('active') if self.store else None)
        original_emit=self.emit
        def runtime_emit(kind,data):
            if kind=='log' and isinstance(data,str) and data.startswith('预计TP空间仅为往返成本') and isinstance(_LAST_PLAN_DIAG,dict):
                data=(
                    f"开仓成本过滤：平均TP毛收益为预计实际手续费 "
                    f"{float(_LAST_PLAN_DIAG.get('expected_fee_multiple',0)):.2f} 倍（最低2.00倍）；"
                    f"这不是亏损判定。"+_diag_text(_LAST_PLAN_DIAG,'详细')
                )
            return original_emit(kind,data)
        self.emit=runtime_emit
        try:
            result=original_cycle(self)
        finally:
            self.emit=original_emit
        after_active=(self.store.data.get('active') if self.store else None)
        if not before_active and after_active:
            p=after_active
            self.emit('log',f"自动下单链路完成：已建立本地防重复记录，方向 {p.get('side')}，限价 {p.get('px')}，数量 {p.get('sz')}，状态 {p.get('phase','已提交')}")
            self.emit('log',_diag_text(p))
        else:
            _decision(self)
        return result

    def stop(self):
        if not self.enabled and self.stopped:
            _once(self,'already-stopped','自动开仓原本已停止；无需重复停止',5)
            return False
        result=original_stop(self)
        self.startup_buffer_until=0.0
        return result

    engine.Engine.arm=arm
    engine.Engine.cycle=cycle
    engine.Engine.stop=stop
    engine.Engine.acknowledge=acknowledge
    engine.Engine._kaytrade_v136_runtime_applied=True


apply()

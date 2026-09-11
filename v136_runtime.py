"""V1.3.6 runtime state/decision observability and manual recovery patch.

No strategy weights, sizing, entry order type, or TP/SL rules are changed here.
Manual fault unlock may reset recoverable day-risk stops after the existing account
cross-check proves BTC positions/orders/algos are empty. Ambiguous live exposure or
unverified order state still fails closed through the original acknowledge checks.
"""
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from v135_auto_entry_guard import apply as apply_v135_guard
apply_v135_guard()

import engine


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


def apply():
    if getattr(engine.Engine,'_kaytrade_v136_runtime_applied',False):return
    original_arm=engine.Engine.arm
    original_cycle=engine.Engine.cycle
    original_stop=engine.Engine.stop
    original_acknowledge=engine.Engine.acknowledge

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
        before_active=(self.store.data.get('active') if self.store else None)
        result=original_cycle(self)
        after_active=(self.store.data.get('active') if self.store else None)
        if not before_active and after_active:
            p=after_active
            self.emit('log',f"自动下单链路完成：已建立本地防重复记录，方向 {p.get('side')}，限价 {p.get('px')}，数量 {p.get('sz')}，状态 {p.get('phase','已提交')}")
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

    engine.Engine.arm=arm; engine.Engine.cycle=cycle; engine.Engine.stop=stop; engine.Engine.acknowledge=acknowledge
    engine.Engine._kaytrade_v136_runtime_applied=True


apply()

"""KAYTRADE V1.3.6 runtime wiring: versioned UI, 13-point threshold, 5s buffer and 50123 fail-safe."""
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

STARTUP_BUFFER_SECONDS = 5.0
DEFAULT_THRESHOLD = 6.0
SCORE_MAX = 13.0


def _validate_settings(self):
    from engine import Halt
    if not 1.0 <= self.score_threshold <= SCORE_MAX or abs(self.score_threshold*2-round(self.score_threshold*2))>1e-9:
        raise Halt('评分阈值必须是1—13之间、以0.5为步进')
    for name,value in asdict(self).items():
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise Halt(name+' 必须是有限正数')
    if int(self.leverage)!=self.leverage or not 1<=self.leverage<=10:
        raise Halt('杠杆范围1—10倍（整数）')
    if int(self.consecutive_losses)!=self.consecutive_losses or self.consecutive_losses>20:
        raise Halt('连续亏损上限必须是1—20的整数')
    if self.daily_loss>self.capital or self.max_notional>self.capital*self.leverage:
        raise Halt('日亏损/名义仓位超出资金与杠杆范围')
    if not .6<=self.stop_atr<=3:
        raise Halt('ATR止损倍数范围0.6—3')
    if abs(self.reward_r-2.0)>1e-9:
        raise Halt('V1.3.6最终止盈固定为2R')
    if abs(self.fee_bps-2.0)>1e-9 or abs(self.taker_fee_bps-5.0)>1e-9 or abs(self.slippage_bps-5.0)>1e-9:
        raise Halt('V1.3.6成本参数固定：Maker 2bps / Taker 5bps / 滑点预算5bps')
    return self


def _widgets(root):
    out=[]
    try: children=root.winfo_children()
    except Exception: return out
    for child in children:
        out.append(child); out.extend(_widgets(child))
    return out


def _is_okx_50123(exc):
    code=str(getattr(exc,'code','') or '')
    text=str(exc or '')
    return code=='50123' or 'OKX 50123' in text or '错误码 50123' in text


def apply():
    import app
    import engine
    if getattr(engine.Engine,'_kaytrade_v136_patch_applied',False):
        return

    engine.Settings.validate=_validate_settings

    original_app_init=app.App.__init__
    original_emit=app.App.emit
    original_drain=app.App.drain

    def app_init(self,*args,**kwargs):
        original_app_init(self,*args,**kwargs)
        self.root.title('KAYTRADE 1.3.6 · BTC 短线趋势策略控制台')
        v136=Path(self.folder)/'settings-v1.3.6.json'
        self.settings_path=v136
        if v136.exists():
            try:
                saved=json.loads(v136.read_text())
                for key,var in self.fields.items():
                    if key in saved:
                        var.set(str(saved[key]))
            except Exception:
                pass
        else:
            # Migrate all V1.3.5 risk/execution values already loaded by App,
            # but start the new scoring model at its agreed 6/13 threshold.
            self.fields['score_threshold'].set(f'{DEFAULT_THRESHOLD:g}')
        for widget in _widgets(self.root):
            try:
                text=widget.cget('text')
            except Exception:
                continue
            replacements={
                'BTC / USDT   ·   V1.3.5 10分细分结构策略':'BTC / USDT   ·   V1.3.6 5m主导 · 13分趋势修正策略',
                '自动开仓评分阈值 3.5—10（0.5步进）':'自动开仓最终评分阈值 1—13（默认6，0.5步进）',
                '基础单笔风险 %（评分倍率前）':'基础单笔风险 %（V1.3.6固定1×仓位）',
                '除连续亏损停开次数外，其余风险数值均可修改并保存；单笔风险不再设资金5%硬上限，仍保留日回撤、名义仓位/杠杆与基础合法性校验。':
                    '除连续亏损停开次数外，其余风险数值均可修改并保存；V1.3.6评分只决定是否开仓，不放大仓位，仍保留日回撤、名义仓位/杠杆与基础合法性校验。',
            }
            if text in replacements:
                try: widget.configure(text=replacements[text])
                except Exception: pass
        self.signal.set('V1.3.6 · 5m最高6 + 15m最高4 + 1H±1 + 4H±2 · 最终13分 · 默认≥6；5m回调收回+突破触发为硬条件')
        for side in ('做多','做空'):
            self.score_vars[side].set('入场 —/10 · 趋势 — · 最终 —/13')
            self.score_bars[side].maximum=SCORE_MAX
            self.score_bars[side].set_value(0)
        self._v136_last_market=None

    def emit(self,kind,data):
        if kind=='market' and isinstance(data,dict):
            self._v136_last_market=data
        return original_emit(self,kind,data)

    def drain(self):
        result=original_drain(self)
        market=getattr(self,'_v136_last_market',None)
        if market and hasattr(self,'score_vars'):
            for side,score in market.get('scores',{}).items():
                trend=float(score.get('trend_adjust',0))
                self.score_vars[side].set(
                    f"入场 {float(score.get('entry_score',0)):g}/10 · 趋势 {trend:+g} · 最终 {float(score.get('total',0)):g}/13"
                )
                self.score_bars[side].maximum=SCORE_MAX
                self.score_bars[side].set_value(score.get('total',0))
        return result

    def arm_ui(self):
        if not self.engine:
            app.messagebox.showerror('未连接','先测试连接'); return
        if self.network_paused:
            app.messagebox.showerror('网络暂停','等待网络恢复并核对账户后再启动'); return
        if self.host.get()!=self.engine.x.host or (self.mode.get()=='OKX模拟盘')!=self.engine.x.demo:
            app.messagebox.showerror('连接不一致','账户环境已修改，请重新测试连接'); return
        try:
            s=self.settings()
        except Exception as exc:
            app.messagebox.showerror('设置错误',str(exc)); return
        env='OKX模拟盘' if self.engine.x.demo else '真实账户'
        summary=(f'{env} / BTC-USDT-SWAP / 逐仓{s.leverage}倍\n'
                 f'资金预算{s.capital} USDT，最大名义仓位{s.max_notional} USDT\n'
                 f'基础单笔风险≤{min(s.risk_usdt,s.capital*s.risk_pct/100)} USDT；V1.3.6合格信号统一1×基础风险，不按趋势分放大仓位\n'
                 f'中国时间日回撤{s.daily_loss} USDT，连亏{s.consecutive_losses}次停止新开仓\n'
                 f'止损{s.stop_atr}×15m ATR；TP1=1R平50%，TP2=2R平余下50%，TP1后SL移到成交均价\n'
                 '评分：5m最高6，15m最高4，1H顺/逆势±1，4H顺/逆势±2，最终最高13分。\n'
                 f'开仓：最终≥{s.score_threshold:g}/13，同时5m≥4/6，5m回调收回与突破触发必须同时成立，前方强结构空间≥1R。\n'
                 'BOLL用于趋势回调/扩张确认，不以单纯触碰上下轨作为反转信号。\n'
                 '每根5m信号最多一次；通过成本、数据、ATR、余额和风险检查后才允许下单。\n'
                 '本版本仍属测试版，不保证盈利或止损成交价。')
        token='LIVE' if not self.engine.x.demo else 'DEMO'
        typed=app.simpledialog.askstring('启动V1.3.6全自动授权',summary+'\n\n同意上述参数请输入 '+token,parent=self.root)
        if typed==token:
            self.submit('arm',s)

    app.App.__init__=app_init
    app.App.emit=emit
    app.App.drain=drain
    app.App.arm=arm_ui

    original_engine_init=engine.Engine.__init__
    original_arm=engine.Engine.arm
    original_cycle=engine.Engine.cycle
    original_stop=engine.Engine.stop
    original_refresh=engine.Engine.refresh_market

    def engine_init(self,*args,**kwargs):
        original_engine_init(self,*args,**kwargs)
        self.startup_buffer_until=0.0

    def refresh_market(self):
        # Before the user has armed the strategy, preview using the V1.3.6 default
        # threshold rather than the legacy engine.py fallback of 3.5.
        if self.settings is None:
            self.settings=engine.Settings(score_threshold=DEFAULT_THRESHOLD)
            try: return original_refresh(self)
            finally: self.settings=None
        return original_refresh(self)

    def arm(self,settings):
        original_emit_engine=self.emit
        def filtered(kind,data):
            if kind=='log' and 'V1.3.5评分沿用10分制' in str(data):
                return
            return original_emit_engine(kind,data)
        self.emit=filtered
        try:
            result=original_arm(self,settings)
        finally:
            self.emit=original_emit_engine
        self.startup_buffer_until=time.monotonic()+STARTUP_BUFFER_SECONDS
        self.emit('log',f'自动交易启动；V1.3.6最终13分制，阈值≥{settings.score_threshold:g}；5m≥4且回调收回+突破触发同时成立；仓位固定1×基础风险；启动缓冲5秒')
        return result

    def run_original_cycle(self):
        try:
            return original_cycle(self)
        except Exception as exc:
            if not _is_okx_50123(exc):
                raise
            p=self.store.data.get('active') if self.store else None
            cleared=False
            if isinstance(p,dict) and not p.get('order_id') and not p.get('filled'):
                snapshot={'client_id':p.get('client_id',''),'side':p.get('side',''),'score':p.get('score'),
                          'submitted':p.get('submitted'),'reason':'OKX 50123 explicit API permission rejection'}
                self.store.data['active']=None; self.store.save(); self.store.record('OKX明确拒绝开仓',snapshot)
                cleared=True
            self.enabled=False; self.startup_buffer_until=0.0
            detail='；已清理本地未成交记录，不进入51603订单核对' if cleared else ''
            raise engine.Halt('OKX 50123：API Key没有BTC/对应交易市场的下单权限；本次开仓被OKX明确拒绝，订单未创建'+detail+'；已停止新开仓') from None

    def cycle(self):
        until=float(getattr(self,'startup_buffer_until',0.0) or 0.0)
        if self.enabled and until:
            now=time.monotonic()
            if now<until:
                self.enabled=False
                try:
                    return run_original_cycle(self)
                finally:
                    if not self.stopped and self.store and not self.store.data.get('halt'):
                        self.enabled=True
            self.startup_buffer_until=0.0
            self.emit('log','启动缓冲5秒结束；V1.3.6自动开仓现已启用')
        return run_original_cycle(self)

    def stop(self):
        self.startup_buffer_until=0.0
        return original_stop(self)

    engine.Engine.__init__=engine_init
    engine.Engine.refresh_market=refresh_market
    engine.Engine.arm=arm
    engine.Engine.cycle=cycle
    engine.Engine.stop=stop
    engine.Engine._kaytrade_v136_patch_applied=True

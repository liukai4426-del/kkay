"""KAYTRADE V1.3.9 position-cap and entry-threshold update.

Changes on top of V1.3.8:
- split the position ceiling into max initial-entry notional and max total-cycle notional
- initial entry is capped independently so later Tier 2 / Tier 3 add-ons keep room
- fixed auto-entry floor becomes 4.5/10
- Tier 1 becomes 4.5-5.0; Tier 2 stays 5.5-7.0; Tier 3 stays 7.5-10.0
- connection security note uses the card background and tighter small-text layout
"""
import json
import math
import tkinter as tk
from dataclasses import asdict, dataclass, replace

from v138_strategy_patch import apply as apply_v138
apply_v138()

import app
import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138

V139_THRESHOLD=4.5


@dataclass(frozen=True)
class SettingsV139(engine.Settings):
    max_initial_notional:float=50.0

    def validate(self):
        if abs(float(self.score_threshold)-V139_THRESHOLD)>1e-9:
            raise engine.Halt('V1.3.9自动开仓评分门槛固定为4.5，不可修改')
        for name,value in asdict(self).items():
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise engine.Halt(name+' 必须是有限正数')
        if int(self.leverage)!=self.leverage or not 1<=self.leverage<=50:
            raise engine.Halt('杠杆范围1—50倍（整数）')
        if int(self.consecutive_losses)!=self.consecutive_losses or self.consecutive_losses>20:
            raise engine.Halt('连续亏损上限必须是1—20的整数')
        if self.daily_loss>self.capital or self.max_notional>self.capital*self.leverage:
            raise engine.Halt('日亏损/最大名义总仓位超出资金与杠杆范围')
        if self.max_initial_notional>self.max_notional:
            raise engine.Halt('最大名义首仓不能大于最大名义总仓位')
        if not .6<=self.stop_atr<=3:
            raise engine.Halt('ATR止损倍数范围0.6—3')
        if abs(self.reward_r-2.0)>1e-9:
            raise engine.Halt('V1.3.9止盈固定为2R')
        if abs(self.fee_bps-2.0)>1e-9 or abs(self.taker_fee_bps-5.0)>1e-9 or abs(self.slippage_bps-5.0)>1e-9:
            raise engine.Halt('V1.3.9成本参数固定：Maker 2bps / Taker 5bps / 滑点预算5bps')
        return self


def _tier(total):
    value=float(total or 0.0)
    if value>=7.5:
        return 3,2.0,'三级超强信号'
    if value>=5.5:
        return 2,1.5,'二级强信号'
    if value>=4.5:
        return 1,1.0,'一级开仓信号'
    return 0,0.0,'未达开仓线'


def _initial_cap(settings):
    return min(float(settings.max_initial_notional),float(settings.max_notional))


def _translate_v139(text):
    if not isinstance(text,str):
        return text
    return (text.replace('V1.3.8','V1.3.9')
                .replace('固定开仓门槛3.5','固定开仓门槛4.5')
                .replace('固定3.5/10起步','固定4.5/10起步')
                .replace('3.5–5.0一级最多1次','4.5–5.0一级最多1次'))


def apply():
    if getattr(engine.Engine,'_kaytrade_v139_applied',False):
        return

    engine.Settings=SettingsV139
    app.Settings=SettingsV139

    v137._tier=_tier
    v138.V138_THRESHOLD=V139_THRESHOLD
    original_signal=v138.v138_signal
    def v139_signal(*args,**kwargs):
        result=original_signal(*args,**kwargs)
        result['threshold']=V139_THRESHOLD
        scores=result.get('scores') or {}
        for row in scores.values():
            total=float(row.get('total') or 0.0)
            tier,multiplier,level=_tier(total)
            gate=bool(row.get('gate'))
            eligible=gate and tier>0
            row['required']=V139_THRESHOLD
            row['eligible']=eligible
            row['signal_tier']=tier
            row['position_multiplier']=multiplier if eligible else 0.0
            row['level']=f'{level} · {multiplier:g}×仓位' if tier else level
            row['reason']=_translate_v139(str(row.get('reason') or ''))
            if gate and not tier:
                row['reason']=f'{total:g}/10，未达固定开仓门槛4.5'
        qualified=[side for side,row in scores.items() if row.get('eligible')]
        if len(qualified)==1:
            selected=qualified[0]
        elif len(qualified)==2 and float(scores[qualified[0]].get('total') or 0)!=float(scores[qualified[1]].get('total') or 0):
            selected=max(qualified,key=lambda side:float(scores[side].get('total') or 0))
        else:
            selected='观望'
        result['side']=selected
        result['why']=scores[selected]['reason'] if selected!='观望' else ' / '.join(side+': '+str(row.get('reason') or '') for side,row in scores.items())
        return result
    v138.v138_signal=v139_signal

    original_submit_initial=v138._submit_initial
    def submit_initial(self,market,score,equity,available,remaining):
        original_settings=self.settings
        cap=_initial_cap(original_settings)
        capped=replace(original_settings,max_notional=cap,max_initial_notional=cap)
        self.settings=capped
        try:
            return original_submit_initial(self,market,score,equity,available,remaining)
        finally:
            self.settings=original_settings
    v138._submit_initial=submit_initial

    previous_cycle=engine.Engine.cycle
    previous_stop=engine.Engine.stop
    previous_flatten=engine.Engine.flatten
    previous_app_init=app.App.__init__
    previous_app_arm=app.App.arm

    def with_v139_emit(self,func,*args,**kwargs):
        original_emit=self.emit
        def emit(kind,data):
            return original_emit(kind,_translate_v139(data))
        self.emit=emit
        try:
            return func(*args,**kwargs)
        finally:
            self.emit=original_emit

    def cycle(self):
        result=with_v139_emit(self,previous_cycle,self)
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict) and p.get('v137'):
            changed=p.get('version')!='1.3.9' or not p.get('v139')
            p['v139']=True; p['version']='1.3.9'
            if changed:self.store.save()
        return result

    def stop(self):
        return with_v139_emit(self,previous_stop,self)

    def flatten(self):
        return with_v139_emit(self,previous_flatten,self)

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:self.root.title('KAYTRADE 1.3.9 · BTC 策略控制台')
        except Exception:pass
        if 'score_threshold' in self.fields:self.fields['score_threshold'].set('4.5')

        total_widget=None
        for w in v138._widgets(self.root):
            if isinstance(w,app.RoundedEntry) and getattr(w,'variable',None) is self.fields.get('max_notional'):
                total_widget=w; break
        if total_widget is not None and 'max_initial_notional' not in self.fields:
            parent=total_widget.master
            saved=None
            try:
                if self.settings_path.exists():
                    saved=json.loads(self.settings_path.read_text()).get('max_initial_notional')
            except Exception:
                saved=None
            try:total=float(self.fields['max_notional'].get())
            except Exception:total=100.0
            initial=float(saved) if isinstance(saved,(int,float)) and float(saved)>0 else max(1.0,total*.5)
            initial=min(initial,total)
            for child in parent.winfo_children():
                info=child.grid_info()
                if info and int(info.get('row',-1))>=2:
                    child.grid_configure(row=int(info['row'])+1)
            var=tk.StringVar(value=str(initial)); self.fields['max_initial_notional']=var
            tk.Label(parent,text='最大名义首仓 USDT（仅限制第一笔）',wraplength=340,bg=app.PANEL,fg='#dbe6eb',font=('Helvetica',12),anchor='w',bd=0).grid(row=2,column=0,sticky='w',padx=6,pady=11)
            app.RoundedEntry(parent,textvariable=var,width=170,height=40,font=('Helvetica',14)).grid(row=2,column=1,sticky='e',padx=8,pady=11)

        note_text=('密钥仅保存在本次运行内存，退出后需重新填写；不会发送给GPT/Gemini。\n'
                   '使用专用交易子账户，不与手动交易或其他机器人共用BTC仓位。\n'
                   '测试连接仅只读；自动交易需要读取+交易权限，禁止提币权限。\n'
                   '模拟盘与真实账户密钥不可混用；地区或产品不支持时停止。\n'
                   '程序仅连接OKX官方接口，不接入原Sites网页。')
        try:
            style=app.ttk.Style(self.root)
            style.configure('V139ConnectionNote.TLabel',background=app.PANEL,foreground=app.MUTED,font=('Helvetica',9),padding=0)
        except Exception:
            style=None

        for w in v138._widgets(self.root):
            try:text=str(w.cget('text') or '')
            except Exception:text=''
            try:
                if text.startswith('最大名义仓位 USDT'):
                    w.configure(text='最大名义总仓位 USDT（整轮上限）')
                elif text=='自动开仓评分阈值 3.5（固定）':
                    w.configure(text='自动开仓评分阈值 4.5（固定）')
                elif 'V1.3.8 1m触发版' in text:
                    w.configure(text=text.replace('V1.3.8 1m触发版','V1.3.9 仓位上限版'))
                elif text.startswith('密钥仅保存在此次运行内存中') or text.startswith('密钥仅保存在本次运行内存'):
                    w.configure(text=note_text,style='V139ConnectionNote.TLabel',wraplength=720,justify='left')
                    w.grid_configure(sticky='w',pady=(10,12))
            except Exception:pass
            if isinstance(w,app.RoundedEntry) and getattr(w,'variable',None) is self.fields.get('score_threshold'):
                try:w.variable.set('4.5'); w.entry.configure(state='disabled',disabledbackground=app.FIELD,disabledforeground=app.MUTED)
                except Exception:pass

    def app_settings(self):
        if 'score_threshold' in self.fields:self.fields['score_threshold'].set('4.5')
        values={k:float(v.get()) for k,v in self.fields.items()}
        for k in ('leverage','consecutive_losses','cooldown_minutes'):
            if int(values[k])!=values[k]:
                raise engine.Halt(k+'必须是整数')
            values[k]=int(values[k])
        return SettingsV139(**values).validate()

    def app_arm(self):
        original=app.simpledialog.askstring
        def askstring(title,prompt,*args,**kwargs):
            text=_translate_v139(str(prompt))
            text=text.replace('最高10分；固定3.5/10起步。','最高10分；固定4.5/10起步。')
            text += '\nV1.3.9：最大名义首仓仅限制第一笔；最大名义总仓位限制整轮累计仓位。一级4.5–5.0；二级/三级规则不变。'
            return original(title,text,*args,**kwargs)
        app.simpledialog.askstring=askstring
        try:return previous_app_arm(self)
        finally:app.simpledialog.askstring=original

    engine.Engine.cycle=cycle
    engine.Engine.stop=stop
    engine.Engine.flatten=flatten
    app.App.__init__=app_init
    app.App.settings=app_settings
    app.App.arm=app_arm
    engine.Engine._kaytrade_v139_applied=True


apply()

"""KAYTRADE V1.4 score tiers and unified result dialogs.

Changes on top of V1.3.9:
- fixed entry floor 4.0/10
- Tier 1: 4.0-6.0, Tier 2: 6.5-7.5, Tier 3: 8.0-10.0 (8.0 belongs to Tier 3)
- unified rounded KAYTRADE success/failure dialogs for connect, settings save,
  flatten completion, fault-lock acknowledgement and auto-trading startup
"""
import json
import math
import os
import tkinter as tk
from dataclasses import asdict

from v139_position_patch import apply as apply_v139
apply_v139()

import app
import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v139_position_patch as v139

V140_THRESHOLD=4.0
_TRACKED={'connect','arm','flatten','ack'}
_ACTION_TEXT={
    'connect':('连接成功','账户连接与只读状态核对已完成。','连接失败'),
    'arm':('自动交易已启动','自动交易已成功启动，当前按 V1.4 规则等待有效 1m 触发。','启动自动交易失败'),
    'flatten':('平仓成功','本程序管理的 BTC 逐仓仓位已确认归零。','平仓失败'),
    'ack':('故障锁已解除','账户核对通过，故障锁已成功解除。','解除故障锁失败'),
}


class SettingsV140(v139.SettingsV139):
    def validate(self):
        if abs(float(self.score_threshold)-V140_THRESHOLD)>1e-9:
            raise engine.Halt('V1.4自动开仓评分门槛固定为4.0，不可修改')
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
            raise engine.Halt('V1.4止盈固定为2R')
        if abs(self.fee_bps-2.0)>1e-9 or abs(self.taker_fee_bps-5.0)>1e-9 or abs(self.slippage_bps-5.0)>1e-9:
            raise engine.Halt('V1.4成本参数固定：Maker 2bps / Taker 5bps / 滑点预算5bps')
        return self


def _tier(total):
    value=float(total or 0.0)
    if value>=8.0:
        return 3,2.0,'三级超强信号'
    if value>=6.5:
        return 2,1.5,'二级强信号'
    if value>=4.0:
        return 1,1.0,'一级开仓信号'
    return 0,0.0,'未达开仓线'


def _allow_entry(active,tier,bar):
    tier=int(tier or 0)
    if tier<=0:
        return False,'评分未达4.0'
    if not isinstance(active,dict):
        return True,''
    counts=active.get('tier_counts') or {}
    highest=int(active.get('highest_tier') or 0)
    if active.get('last_entry_bar')==bar:
        return False,'本根1m已提交过一次开仓/加仓'
    if tier<highest:
        return False,'评分已从更高级别回落，不补低级别仓位'
    used=int(counts.get(str(tier),0) or 0)
    if used>=v137._tier_limit(tier):
        return False,'当前等级允许次数已用完'
    if tier==1 and active.get('legs'):
        return False,'一级信号只允许空仓首笔'
    return True,''


def _translate(text):
    if not isinstance(text,str):
        return text
    return (text.replace('V1.3.9','V1.4')
                .replace('固定开仓门槛4.5','固定开仓门槛4.0')
                .replace('固定4.5/10起步','固定4.0/10起步')
                .replace('一级4.5–5.0','一级4.0–6.0')
                .replace('5.5–7.0','6.5–7.5')
                .replace('7.5–10','8.0–10'))


def _rounded_modal(owner,title,message,success=True):
    """Borderless dark modal with rounded card and rounded green/red confirm button."""
    win=tk.Toplevel(owner)
    win.withdraw()
    win.overrideredirect(True)
    win.transient(owner)
    win.configure(bg=app.BG)
    width,height=440,220
    canvas=tk.Canvas(win,width=width,height=height,bg=app.BG,highlightthickness=0,borderwidth=0)
    canvas.pack(fill='both',expand=True)
    radius=24
    points=[radius,1,width-radius,1,width-1,1,width-1,radius,width-1,height-radius,width-1,height-1,
            width-radius,height-1,radius,height-1,1,height-1,1,height-radius,1,radius,1,1]
    canvas.create_polygon(points,smooth=True,splinesteps=28,fill=app.PANEL,outline='')
    body=tk.Frame(canvas,bg=app.PANEL,bd=0,highlightthickness=0)
    canvas.create_window(30,26,anchor='nw',window=body,width=width-60,height=height-52)
    accent=app.GREEN if success else app.RED
    symbol='✓' if success else '!'
    tk.Label(body,text=symbol,bg=app.PANEL,fg=accent,font=('Helvetica',26,'bold'),bd=0).pack(anchor='w')
    tk.Label(body,text=title,bg=app.PANEL,fg=accent,font=('Helvetica',18,'bold'),bd=0).pack(anchor='w',pady=(2,5))
    tk.Label(body,text=str(message),wraplength=370,justify='left',bg=app.PANEL,fg='#c9d5da',
             font=('Helvetica',11),bd=0).pack(anchor='w')
    def close():
        try:win.grab_release()
        except Exception:pass
        win.destroy()
    app.RoundedButton(body,text='确认',command=close,variant='accent' if success else 'danger',
                      width=116,height=38,radius=13,font=('Helvetica',11,'bold')).pack(anchor='e',side='bottom')
    owner.update_idletasks()
    x=owner.winfo_rootx()+max(0,(owner.winfo_width()-width)//2)
    y=owner.winfo_rooty()+max(0,(owner.winfo_height()-height)//2)
    win.geometry(f'{width}x{height}+{x}+{y}')
    win.deiconify(); win.lift(); win.grab_set(); win.focus_force()
    win.bind('<Escape>',lambda event:close())
    return win


def apply():
    if getattr(engine.Engine,'_kaytrade_v140_applied',False):
        return

    engine.Settings=SettingsV140
    app.Settings=SettingsV140
    v139.V139_THRESHOLD=V140_THRESHOLD
    v138.V138_THRESHOLD=V140_THRESHOLD
    v137.V137_THRESHOLD=V140_THRESHOLD
    v139._tier=_tier
    v137._tier=_tier
    v137._allow_entry=_allow_entry

    current_signal=v138.v138_signal
    def v140_signal(*args,**kwargs):
        result=current_signal(*args,**kwargs)
        result['threshold']=V140_THRESHOLD
        scores=result.get('scores') or {}
        for row in scores.values():
            total=float(row.get('total') or 0.0)
            tier,multiplier,level=_tier(total)
            gate=bool(row.get('gate'))
            eligible=gate and tier>0
            row['required']=V140_THRESHOLD
            row['eligible']=eligible
            row['signal_tier']=tier
            row['position_multiplier']=multiplier if eligible else 0.0
            row['level']=f'{level} · {multiplier:g}×仓位' if tier else level
            reason=_translate(str(row.get('reason') or ''))
            if gate and tier<=0:
                reason=f'{total:g}/10，未达固定开仓门槛4.0'
            elif gate and tier>0:
                reason=f'{level} · {total:g}/10 达标；1m Trigger {float((row.get("layers") or {}).get("trigger") or 0):g}>0'
            row['reason']=reason
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
    v138.v138_signal=v140_signal

    previous_cycle=engine.Engine.cycle
    previous_reconcile=engine.Engine.reconcile
    previous_app_init=app.App.__init__
    previous_app_settings=app.App.settings
    previous_app_arm=app.App.arm
    previous_app_connect=app.App.connect
    previous_submit=app.App.submit
    previous_emit=app.App.emit
    previous_drain=app.App.drain

    def cycle(self):
        result=previous_cycle(self)
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict) and p.get('v137'):
            changed=p.get('version')!='1.4' or not p.get('v140')
            p['v140']=True; p['version']='1.4'
            if changed:self.store.save()
        return result

    def reconcile(self):
        result=previous_reconcile(self)
        if getattr(self,'_v140_flatten_waiting',False) and self.store and not self.store.data.get('active'):
            self._v140_flatten_waiting=False
            self.emit('v140_notice',{'success':True,'action':'flatten','title':_ACTION_TEXT['flatten'][0],'message':_ACTION_TEXT['flatten'][1]})
        return result

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        self._v140_pending_action=None
        self._v140_pending_error=''
        self._v140_popup_queue=[]
        self._v140_popup_open=False
        try:self.root.title('KAYTRADE 1.4 · BTC 策略控制台')
        except Exception:pass
        if 'score_threshold' in self.fields:self.fields['score_threshold'].set('4.0')
        for w in v138._widgets(self.root):
            try:text=str(w.cget('text') or '')
            except Exception:text=''
            try:
                if text=='自动开仓评分阈值 4.5（固定）':w.configure(text='自动开仓评分阈值 4.0（固定）')
                elif 'V1.3.9 仓位上限版' in text:w.configure(text=text.replace('V1.3.9 仓位上限版','V1.4 评分弹窗版'))
            except Exception:pass
            if isinstance(w,app.RoundedEntry) and getattr(w,'variable',None) is self.fields.get('score_threshold'):
                try:w.variable.set('4.0'); w.entry.configure(state='disabled',disabledbackground=app.FIELD,disabledforeground=app.MUTED)
                except Exception:pass

    def app_settings(self):
        if 'score_threshold' in self.fields:self.fields['score_threshold'].set('4.0')
        values={k:float(v.get()) for k,v in self.fields.items()}
        for k in ('leverage','consecutive_losses','cooldown_minutes'):
            if int(values[k])!=values[k]:raise engine.Halt(k+'必须是整数')
            values[k]=int(values[k])
        return SettingsV140(**values).validate()

    def save_settings(self):
        try:
            if self.engine and self.engine.enabled:
                raise engine.Halt('先停止自动开仓，再修改设置')
            settings=self.settings()
            fd=os.open(self.settings_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as f:json.dump(asdict(settings),f,ensure_ascii=False,indent=2)
            self.refresh_plan_settings()
            self.emit('log','设置校验并保存成功；固定成本参数未开放编辑')
            _rounded_modal(self.root,'设置保存成功','参数校验通过并已保存。',True)
        except Exception as exc:
            _rounded_modal(self.root,'设置保存失败',str(exc),False)

    def submit(self,kind,data=None):
        if kind in _TRACKED:
            if self.busy:
                _rounded_modal(self.root,'操作未执行','请等待当前操作完成后再试。',False); return
            self._v140_pending_action=kind
            self._v140_pending_error=''
        return previous_submit(self,kind,data)

    def emit(self,kind,data):
        if isinstance(data,str):data=_translate(data)
        if kind=='v140_notice':
            self._v140_popup_queue.append(dict(data))
            return
        action=getattr(self,'_v140_pending_action',None)
        if kind=='alarm':
            if action in _TRACKED:self._v140_pending_error=str(data)
            waiting=bool(self.engine and getattr(self.engine,'_v140_flatten_waiting',False))
            if waiting:
                self.engine._v140_flatten_waiting=False
                self._v140_popup_queue.append({'success':False,'action':'flatten','title':_ACTION_TEXT['flatten'][2],'message':str(data)})
        if kind=='done' and action in _TRACKED:
            error=str(getattr(self,'_v140_pending_error','') or '')
            success=False
            if not error:
                if action=='connect':success=bool(self.engine and self.engine.store)
                elif action=='arm':success=bool(self.engine and self.engine.enabled)
                elif action=='ack':success=bool(self.engine and self.engine.store and not self.engine.store.data.get('halt'))
                elif action=='flatten':
                    if self.engine and self.engine.store and not self.engine.store.data.get('active'):
                        success=True
                    elif self.engine:
                        self.engine._v140_flatten_waiting=True
                        self._v140_pending_action=None; self._v140_pending_error=''
                        return previous_emit(self,kind,data)
            title,message,fail_title=_ACTION_TEXT[action]
            self._v140_popup_queue.append({'success':success,'action':action,'title':title if success else fail_title,
                                           'message':message if success else (error or '操作未能完成，请查看运行日志。')})
            self._v140_pending_action=None; self._v140_pending_error=''
        return previous_emit(self,kind,data)

    def drain(self):
        result=previous_drain(self)
        queue=getattr(self,'_v140_popup_queue',None) or []
        if queue and not getattr(self,'_v140_popup_open',False):
            item=queue.pop(0); self._v140_popup_open=True
            win=_rounded_modal(self.root,item.get('title','提示'),item.get('message',''),bool(item.get('success')))
            def released(event=None):self._v140_popup_open=False
            win.bind('<Destroy>',released,add='+')
        return result

    def arm(self):
        original=app.simpledialog.askstring
        original_error=app.messagebox.showerror
        def askstring(title,prompt,*args,**kwargs):
            text=_translate(str(prompt))
            text=text.replace('4.5–5.0一级最多1次','4.0–6.0一级最多1次')
            text=text.replace('5.5–7.0二级最多1次','6.5–7.5二级最多1次')
            text=text.replace('7.5–10三级最多2次','8.0–10三级最多2次')
            text=text.replace('一级4.5–5.0；二级/三级规则不变。','一级4.0–6.0；二级6.5–7.5；三级8.0–10（8.0优先三级）。')
            text+='\nV1.4评分：4.0–6.0一级 / 6.5–7.5二级 / 8.0–10三级；8.0优先三级。'
            return original(title,text,*args,**kwargs)
        def showerror(title,message,*args,**kwargs):
            _rounded_modal(self.root,str(title),str(message),False)
        app.simpledialog.askstring=askstring; app.messagebox.showerror=showerror
        try:return previous_app_arm(self)
        finally:
            app.simpledialog.askstring=original; app.messagebox.showerror=original_error

    def connect(self):
        original_error=app.messagebox.showerror
        app.messagebox.showerror=lambda title,message,*args,**kwargs:_rounded_modal(self.root,str(title),str(message),False)
        try:return previous_app_connect(self)
        finally:app.messagebox.showerror=original_error

    engine.Engine.cycle=cycle
    engine.Engine.reconcile=reconcile
    app.App.__init__=app_init
    app.App.settings=app_settings
    app.App.save_settings=save_settings
    app.App.submit=submit
    app.App.emit=emit
    app.App.drain=drain
    app.App.arm=arm
    app.App.connect=connect
    engine.Engine._kaytrade_v140_applied=True


apply()

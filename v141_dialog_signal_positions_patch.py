"""KAYTRADE V1.4.1 dialog alignment and per-signal position caps.

Changes on top of V1.4:
- all KAYTRADE result dialogs center icon/title/message/button and remove the dark outer gutter
- replace initial/total notional controls with independent Tier 1 / Tier 2 / Tier 3 signal notional caps
- Tier 1 uses first_signal_notional, Tier 2 uses second_signal_notional, Tier 3 uses third_signal_notional
- each tier may enter at most once, so a complete cycle has at most three entry events
"""
import json
import math
import tkinter as tk
from dataclasses import asdict, dataclass, replace

from v140_score_dialog_patch import apply as apply_v140
apply_v140()

import app
import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v140_score_dialog_patch as v140

V141_VERSION='1.4.1'


@dataclass(frozen=True)
class SettingsV141(v140.SettingsV140):
    first_signal_notional:float=50.0
    second_signal_notional:float=75.0
    third_signal_notional:float=100.0

    def validate(self):
        if abs(float(self.score_threshold)-4.0)>1e-9:
            raise engine.Halt('V1.4.1自动开仓评分门槛固定为4.0，不可修改')
        for name,value in asdict(self).items():
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise engine.Halt(name+' 必须是有限正数')
        if int(self.leverage)!=self.leverage or not 1<=self.leverage<=50:
            raise engine.Halt('杠杆范围1—50倍（整数）')
        if int(self.consecutive_losses)!=self.consecutive_losses or self.consecutive_losses>20:
            raise engine.Halt('连续亏损上限必须是1—20的整数')
        cycle_cap=float(self.first_signal_notional)+float(self.second_signal_notional)+float(self.third_signal_notional)
        if self.daily_loss>self.capital or cycle_cap>self.capital*self.leverage:
            raise engine.Halt('日亏损/三档信号仓位合计超出资金与杠杆范围')
        if not .6<=self.stop_atr<=3:
            raise engine.Halt('ATR止损倍数范围0.6—3')
        if abs(self.reward_r-2.0)>1e-9:
            raise engine.Halt('V1.4.1止盈固定为2R')
        if abs(self.fee_bps-2.0)>1e-9 or abs(self.taker_fee_bps-5.0)>1e-9 or abs(self.slippage_bps-5.0)>1e-9:
            raise engine.Halt('V1.4.1成本参数固定：Maker 2bps / Taker 5bps / 滑点预算5bps')
        return self


def _tier_limit(tier):
    return {1:1,2:1,3:1}.get(int(tier or 0),0)


def _signal_cap(settings,tier):
    tier=int(tier or 0)
    if tier==1:return float(settings.first_signal_notional)
    if tier==2:return float(settings.second_signal_notional)
    if tier==3:return float(settings.third_signal_notional)
    raise engine.Halt('无效信号等级仓位')


def _centered_modal(owner,title,message,success=True):
    """Borderless modal: no dark outer gutter, all content centered to the parent UI."""
    win=tk.Toplevel(owner)
    win.withdraw()
    win.overrideredirect(True)
    win.transient(owner)
    win.configure(bg=app.PANEL,bd=0,highlightthickness=0)
    width,height=440,220
    body=tk.Frame(win,bg=app.PANEL,bd=0,highlightthickness=0)
    body.pack(fill='both',expand=True,padx=28,pady=24)
    accent=app.GREEN if success else app.RED
    symbol='✓' if success else '!'
    tk.Label(body,text=symbol,bg=app.PANEL,fg=accent,font=('Helvetica',26,'bold'),bd=0,
             anchor='center',justify='center').pack(anchor='center')
    tk.Label(body,text=title,bg=app.PANEL,fg=accent,font=('Helvetica',18,'bold'),bd=0,
             anchor='center',justify='center').pack(anchor='center',pady=(2,6))
    tk.Label(body,text=str(message),wraplength=370,justify='center',anchor='center',bg=app.PANEL,fg='#c9d5da',
             font=('Helvetica',11),bd=0).pack(anchor='center',fill='x')
    def close():
        try:win.grab_release()
        except Exception:pass
        win.destroy()
    app.RoundedButton(body,text='确认',command=close,variant='accent' if success else 'danger',
                      width=116,height=38,radius=13,font=('Helvetica',11,'bold')).pack(anchor='center',side='bottom')
    owner.update_idletasks()
    x=owner.winfo_rootx()+max(0,(owner.winfo_width()-width)//2)
    y=owner.winfo_rooty()+max(0,(owner.winfo_height()-height)//2)
    win.geometry(f'{width}x{height}+{x}+{y}')
    win.deiconify(); win.lift(); win.grab_set(); win.focus_force()
    win.bind('<Escape>',lambda event:close())
    return win


def _saved_signal_values(path,first_old,total_old):
    saved={}
    try:
        if path.exists():saved=json.loads(path.read_text())
    except Exception:
        saved={}
    first=float(saved.get('first_signal_notional') or first_old)
    if saved.get('second_signal_notional') is not None:
        second=float(saved['second_signal_notional'])
    else:
        second=min(float(total_old),first*1.5) if float(total_old)>0 else first*1.5
    if saved.get('third_signal_notional') is not None:
        third=float(saved['third_signal_notional'])
    else:
        third=min(float(total_old),first*2.0) if float(total_old)>0 else first*2.0
    return max(first,1e-9),max(second,1e-9),max(third,1e-9)


def apply():
    if getattr(engine.Engine,'_kaytrade_v141_applied',False):
        return

    engine.Settings=SettingsV141
    app.Settings=SettingsV141
    v137._tier_limit=_tier_limit
    v140._rounded_modal=_centered_modal

    previous_app_init=app.App.__init__
    previous_app_arm=app.App.arm
    previous_cycle=engine.Engine.cycle
    previous_submit_initial=v138._submit_initial
    previous_submit_addon=v138._submit_addon

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:self.root.title('KAYTRADE 1.4.1 · BTC 策略控制台')
        except Exception:pass

        initial_var=self.fields.get('max_initial_notional')
        total_var=self.fields.get('max_notional')
        if initial_var is None or total_var is None:
            return
        try:first_old=float(initial_var.get())
        except Exception:first_old=50.0
        try:total_old=float(total_var.get())
        except Exception:total_old=max(first_old,100.0)
        first,second,third=_saved_signal_values(self.settings_path,first_old,total_old)
        initial_var.set(str(first)); total_var.set(str(second))

        initial_entry=None; total_entry=None; initial_label=None; total_label=None
        for w in v138._widgets(self.root):
            if isinstance(w,app.RoundedEntry):
                if getattr(w,'variable',None) is initial_var:initial_entry=w
                elif getattr(w,'variable',None) is total_var:total_entry=w
            try:text=str(w.cget('text') or '')
            except Exception:text=''
            if text.startswith('最大名义首仓'):initial_label=w
            elif text.startswith('最大名义总仓位') or text.startswith('最大名义仓位 USDT'):total_label=w
            try:
                if 'V1.4 评分弹窗版' in text:w.configure(text=text.replace('V1.4 评分弹窗版','V1.4.1 三档信号仓位版'))
            except Exception:pass

        if initial_label is not None:
            try:initial_label.configure(text='第一信号仓位 USDT（一级 4.0–6.0）')
            except Exception:pass
        if total_label is not None:
            try:total_label.configure(text='第二信号仓位 USDT（二级 6.5–7.5）')
            except Exception:pass

        parent=(total_entry.master if total_entry is not None else initial_entry.master if initial_entry is not None else None)
        if parent is not None:
            rows=[]
            for widget in (initial_entry,total_entry,initial_label,total_label):
                if widget is None:continue
                try:
                    info=widget.grid_info()
                    if info:rows.append(int(info.get('row',0)))
                except Exception:pass
            third_row=(max(rows)+1) if rows else 4
            for child in parent.winfo_children():
                try:
                    info=child.grid_info()
                    if info and int(info.get('row',-1))>=third_row:
                        child.grid_configure(row=int(info['row'])+1)
                except Exception:pass
            third_var=tk.StringVar(value=str(third))
            tk.Label(parent,text='第三信号仓位 USDT（三级 8.0–10，仅1次）',wraplength=340,bg=app.PANEL,fg='#dbe6eb',
                     font=('Helvetica',12),anchor='w',bd=0).grid(row=third_row,column=0,sticky='w',padx=6,pady=11)
            app.RoundedEntry(parent,textvariable=third_var,width=170,height=40,font=('Helvetica',14)).grid(
                row=third_row,column=1,sticky='e',padx=8,pady=11)
            self.fields.pop('max_initial_notional',None); self.fields.pop('max_notional',None)
            self.fields['first_signal_notional']=initial_var
            self.fields['second_signal_notional']=total_var
            self.fields['third_signal_notional']=third_var

    def app_settings(self):
        if 'score_threshold' in self.fields:self.fields['score_threshold'].set('4.0')
        values={k:float(v.get()) for k,v in self.fields.items()}
        for k in ('leverage','consecutive_losses','cooldown_minutes'):
            if int(values[k])!=values[k]:raise engine.Halt(k+'必须是整数')
            values[k]=int(values[k])
        first=float(values['first_signal_notional']); second=float(values['second_signal_notional']); third=float(values['third_signal_notional'])
        values['max_initial_notional']=first
        values['max_notional']=first+second+third
        return SettingsV141(**values).validate()

    def submit_initial(self,market,score,equity,available,remaining):
        tier=int((score or {}).get('signal_tier') or v137._tier(float((score or {}).get('total') or 0))[0])
        cap=_signal_cap(self.settings,tier)
        original_settings=self.settings
        self.settings=replace(original_settings,max_notional=cap,max_initial_notional=cap)
        try:
            return previous_submit_initial(self,market,score,equity,available,remaining)
        finally:
            self.settings=original_settings

    def submit_addon(self,p,market,score,equity,available,remaining):
        tier=int((score or {}).get('signal_tier') or v137._tier(float((score or {}).get('total') or 0))[0])
        cap=_signal_cap(self.settings,tier)
        current=sum(float(leg.get('notional') or 0) for leg in (p.get('legs') or []) if leg.get('state')=='filled')
        original_settings=self.settings
        temp_total=current+cap
        self.settings=replace(original_settings,max_notional=temp_total,max_initial_notional=min(float(original_settings.first_signal_notional),temp_total))
        try:
            return previous_submit_addon(self,p,market,score,equity,available,remaining)
        finally:
            self.settings=original_settings

    def cycle(self):
        result=previous_cycle(self)
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict) and p.get('v137'):
            changed=p.get('version')!=V141_VERSION or not p.get('v141')
            p['v141']=True; p['version']=V141_VERSION
            if changed:self.store.save()
        return result

    def arm(self):
        original=app.simpledialog.askstring
        def askstring(title,prompt,*args,**kwargs):
            text=str(prompt)
            text=text.replace('三级最多2次','三级最多1次')
            text=text.replace('三级8.0–10（8.0优先三级）。','三级8.0–10（8.0优先三级，最多1次）。')
            text+='\nV1.4.1仓位：一级=第一信号仓位；二级=第二信号仓位；三级=第三信号仓位；每级最多1次。'
            return original(title,text,*args,**kwargs)
        app.simpledialog.askstring=askstring
        try:return previous_app_arm(self)
        finally:app.simpledialog.askstring=original

    app.App.__init__=app_init
    app.App.settings=app_settings
    app.App.arm=arm
    v138._submit_initial=submit_initial
    v138._submit_addon=submit_addon
    engine.Engine.cycle=cycle
    engine.Engine._kaytrade_v141_applied=True


apply()

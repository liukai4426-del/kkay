"""KAYTRADE V1.4.2 visual refresh only.

Preserves V1.4.1 trading/execution behavior and changes presentation:
- high-purity success/danger button colors match the long/short score colors
- all RoundedButton controls use visibly larger rounded corners
- top-right connection state: unconnected yellow, connected green, fault red
- selected circular gradient logo replaces the legacy K mark in the header
- active-cycle version is labelled 1.4.2 without changing strategy rules
"""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk

from v141_exit_limit_patch import apply as apply_v141
apply_v141()

import app
import engine
import visual
import v138_strategy_patch as v138

V142_VERSION='1.4.2'
YELLOW='#ffd84d'
ACCENT_HOVER='#1aefb1'
ACCENT_PRESSED='#09b987'
DANGER_HOVER='#ff7892'
DANGER_PRESSED='#df4f6b'


def _asset_root():
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))


def _logo_path():
    return _asset_root()/'assets'/'kaytrade-v142-logo.png'


def _button_radius(height,current=None):
    h=max(2,int(height or 42))
    target=max(16,h//2-2)
    return min(h//2-1,max(int(current or 0),target))


def _status_kind(text,connected=False,halted=False):
    text=str(text or '')
    if halted or any(word in text for word in ('故障','锁定','失败')):
        return 'red'
    if connected:
        return 'green'
    return 'yellow'


def _walk(root):
    try:children=root.winfo_children()
    except Exception:return
    for child in children:
        yield child
        yield from _walk(child)


def apply():
    if getattr(engine.Engine,'_kaytrade_v142_visual_applied',False):
        return

    # Use exactly the score-board long/short colors for primary green/red buttons.
    app.RoundedButton.PALETTES['accent']=(app.GREEN,ACCENT_HOVER,ACCENT_PRESSED,'#04120d')
    app.RoundedButton.PALETTES['danger']=(app.RED,DANGER_HOVER,DANGER_PRESSED,'#19070b')

    previous_button_init=app.RoundedButton.__init__
    def rounded_button_init(self,*args,**kwargs):
        height=int(kwargs.get('height',42) or 42)
        kwargs['radius']=_button_radius(height,kwargs.get('radius'))
        return previous_button_init(self,*args,**kwargs)
    app.RoundedButton.__init__=rounded_button_init
    visual.RoundedButton.__init__=rounded_button_init

    previous_mark=app.mark
    def logo_mark(parent):
        path=_logo_path()
        if not path.exists():
            return previous_mark(parent)
        c=tk.Canvas(parent,width=50,height=50,bg=app.BG,highlightthickness=0,borderwidth=0)
        try:
            source=tk.PhotoImage(file=str(path))
            factor=max(1,int(round(max(source.width(),source.height())/44.0)))
            image=source.subsample(factor,factor) if factor>1 else source
            c.create_image(25,25,image=image,anchor='center')
            c._kaytrade_v142_logo=True
            c._kaytrade_v142_logo_image=image
            c._kaytrade_v142_logo_source=source
        except Exception:
            return previous_mark(parent)
        return c
    app.mark=logo_mark
    visual.mark=logo_mark

    previous_app_init=app.App.__init__
    previous_emit=app.App.emit
    previous_cycle=engine.Engine.cycle

    def refresh_status(self):
        label=getattr(self,'_v142_status_label',None)
        if label is None:
            return
        connected=bool(getattr(self,'engine',None) and getattr(self.engine,'store',None))
        halted=False
        try:halted=bool(self.engine.store.data.get('halt')) if connected else False
        except Exception:halted=False
        kind=_status_kind(self.status.get(),connected,halted)
        style={'yellow':'V142StatusYellow.TLabel','green':'V142StatusGreen.TLabel','red':'V142StatusRed.TLabel'}[kind]
        try:label.configure(style=style)
        except Exception:pass
        self._v142_status_kind=kind

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:self.root.title('KAYTRADE 1.4.2 · BTC 策略控制台')
        except Exception:pass

        style=ttk.Style(self.root)
        style.configure('V142StatusYellow.TLabel',background=app.BG,foreground=YELLOW,font=('Helvetica',11,'bold'))
        style.configure('V142StatusGreen.TLabel',background=app.BG,foreground=app.GREEN,font=('Helvetica',11,'bold'))
        style.configure('V142StatusRed.TLabel',background=app.BG,foreground=app.RED,font=('Helvetica',11,'bold'))

        self._v142_status_label=None
        for w in _walk(self.root):
            try:
                if isinstance(w,ttk.Label) and str(w.cget('textvariable'))==str(self.status):
                    self._v142_status_label=w
                text=str(w.cget('text') or '')
                if 'V1.4.1 三档信号仓位版' in text:
                    w.configure(text=text.replace('V1.4.1 三档信号仓位版','V1.4.2 视觉优化版'))
            except Exception:
                pass
        try:
            if not (getattr(self,'engine',None) and getattr(self.engine,'store',None)):
                self.status.set('默认停止 · 未连接')
        except Exception:pass
        refresh_status(self)
        self._v142_refresh_status=lambda:refresh_status(self)

    def emit(self,kind,data):
        result=previous_emit(self,kind,data)
        if kind in ('status','done','alarm','v140_notice'):
            try:self.root.after_idle(lambda:refresh_status(self))
            except Exception:pass
        return result

    def cycle(self):
        result=previous_cycle(self)
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict) and p.get('v141'):
            changed=p.get('version')!=V142_VERSION or not p.get('v142')
            p['v142']=True; p['version']=V142_VERSION
            if changed:self.store.save()
        return result

    app.App.__init__=app_init
    app.App.emit=emit
    engine.Engine.cycle=cycle
    engine.Engine._kaytrade_v142_visual_applied=True


apply()

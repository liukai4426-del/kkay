"""KAYTRADE V1.4.4 UI polish only.

Trading logic is inherited unchanged from V1.4.3. This patch:
- pushes visible action buttons to a true capsule radius
- renders button/scrollbar surfaces with supersampled anti-aliasing when Pillow is available
- makes all vertical scrollbars the same width and maximally rounded
- removes black label blocks in Account Connection
- shortens Network Self-check / Test Connection labels
- aligns the first Trade Plan row to the same 3-column width used below
- removes the legacy V1.3.6 startup hint while preserving live signal updates
"""
from v143_visual_polish_patch import apply as apply_v143_visual
apply_v143_visual()

import tkinter as tk
from tkinter import ttk

import app
import engine
import visual

V144_VERSION='1.4.4'
SCROLL_WIDTH=20
AA_SCALE=4
ACCOUNT_FIELDS={'账户官方域名','环境','API Key','Secret Key','Passphrase'}


def _walk(root):
    try: children=root.winfo_children()
    except Exception: return
    for child in children:
        yield child
        yield from _walk(child)


def _capsule_radius(height):
    return max(1.0,(max(2,int(height))-2)/2.0)


def _aa_rounded_photo(widget,width,height,fill,radius,inset=1):
    """Return a supersampled rounded RGBA PhotoImage; lazy Pillow import keeps headless tests light."""
    from PIL import Image,ImageDraw,ImageTk
    w=max(2,int(round(width))); h=max(2,int(round(height))); s=AA_SCALE
    image=Image.new('RGBA',(w*s,h*s),(0,0,0,0))
    draw=ImageDraw.Draw(image)
    box=(inset*s,inset*s,max(inset*s,(w-inset)*s-1),max(inset*s,(h-inset)*s-1))
    draw.rounded_rectangle(box,radius=max(1,int(round(radius*s))),fill=fill)
    image=image.resize((w,h),Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(image,master=widget)


def _button_draw(self):
    self.delete('all')
    base,hover,pressed,fg=self.PALETTES.get(self.variant,self.PALETTES['neutral'])
    fill=pressed if self.pressed else hover if self.hover else base
    if self.state=='disabled': fill=app.PANEL_ALT; fg=app.MUTED
    w=max(2,self.winfo_width() or self.button_width); h=max(2,self.winfo_height() or self.button_height)
    radius=_capsule_radius(h)
    try:
        image=_aa_rounded_photo(self,w,h,fill,radius,1)
        self.create_image(0,0,image=image,anchor='nw',tags='surface')
        self._v144_button_image=image
        self._v144_aa=True
    except Exception:
        # Fallback still uses maximum-radius geometry if Pillow is unavailable.
        r=min(radius,(h-2)/2,(w-2)/2)
        self.create_oval(1,1,1+2*r,1+2*r,fill=fill,outline='',tags='surface')
        self.create_oval(w-1-2*r,1,w-1,h-1,fill=fill,outline='',tags='surface')
        self.create_rectangle(1+r,1,w-1-r,h-1,fill=fill,outline='',tags='surface')
        self._v144_aa=False
    self.create_text(w/2,h/2,text=self.text,fill=fg,font=self.font,anchor='center',tags='label')


def _scrollbar_draw(self):
    if not self.winfo_exists(): return
    self.delete('all')
    w=max(SCROLL_WIDTH,self.winfo_width()); h=max(1,self.winfo_height())
    y1,y2,_,_,_=self._bounds()
    ty=max(0,int(round(y1))); th=max(2,int(round(y2-y1)))
    try:
        track=_aa_rounded_photo(self,w,h,app.BG,min((w-4)/2,(h-2)/2),2)
        thumb_fill=app.FIELD if getattr(self,'drag_y',None) is not None else app.PANEL_ALT
        thumb=_aa_rounded_photo(self,w,th,thumb_fill,min((w-6)/2,(th-2)/2),3)
        self.create_image(0,0,image=track,anchor='nw',tags='track')
        self.create_image(0,ty,image=thumb,anchor='nw',tags='thumb')
        self._v144_scroll_images=(track,thumb)
        self._v144_aa=True
    except Exception:
        self.create_rectangle(2,0,w-2,h,fill=app.BG,outline='',tags='track')
        self.create_rectangle(3,ty,w-3,ty+th,fill=app.PANEL_ALT,outline='',tags='thumb')
        self._v144_aa=False


def _child_texts(widget):
    out=[]
    for item in _walk(widget):
        try:
            text=str(item.cget('text') or '')
            if text: out.append(text)
        except Exception: pass
    return out


def _uniform_trade_plan(self):
    """Give Account Funds / Single-trade Risk the same one-third width as rows below."""
    for frame in _walk(self.root):
        if not isinstance(frame,tk.Frame): continue
        children=list(frame.winfo_children())
        tile_children=[]
        for child in children:
            if isinstance(child,tk.Frame):
                texts=set(_child_texts(child))
                if '账户资金' in texts or '单笔风险' in texts:
                    tile_children.append(child)
        titles=set()
        for child in tile_children: titles.update(_child_texts(child))
        if '账户资金' not in titles or '单笔风险' not in titles: continue
        for child in tile_children:
            try: child.pack_configure(side='left',fill='x',expand=True,padx=4)
            except Exception: pass
        if not any(getattr(child,'_v144_plan_spacer',False) for child in children):
            spacer=tk.Frame(frame,bg=app.PANEL,bd=0,highlightthickness=0,height=78)
            spacer._v144_plan_spacer=True
            spacer.pack_propagate(False)
            spacer.pack(side='left',fill='x',expand=True,padx=4)
            self._v144_plan_spacer=spacer
        self._v144_overview_uniform=True
        return True
    self._v144_overview_uniform=False
    return False


def apply():
    if getattr(engine.Engine,'_kaytrade_v144_ui_polish_applied',False): return

    previous_button_init=app.RoundedButton.__init__
    previous_scroll_init=visual.WideScrollbar.__init__
    previous_app_init=app.App.__init__
    previous_cycle=engine.Engine.cycle

    # Use the AA renderers for both app and visual module references.
    app.RoundedButton._draw=_button_draw
    visual.RoundedButton._draw=_button_draw
    visual.WideScrollbar._draw=_scrollbar_draw
    app.WideScrollbar._draw=_scrollbar_draw

    def button_init(self,*args,**kwargs):
        result=previous_button_init(self,*args,**kwargs)
        self.radius=int(round(_capsule_radius(getattr(self,'button_height',kwargs.get('height',42)))) )
        self._draw()
        return result

    def scrollbar_init(self,*args,**kwargs):
        kwargs['width']=SCROLL_WIDTH
        result=previous_scroll_init(self,*args,**kwargs)
        self.bar_width=SCROLL_WIDTH
        self.configure(width=SCROLL_WIDTH,bg=app.BG)
        self._draw()
        return result

    app.RoundedButton.__init__=button_init
    visual.RoundedButton.__init__=button_init
    visual.WideScrollbar.__init__=scrollbar_init
    app.WideScrollbar.__init__=scrollbar_init

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try: self.root.title('KAYTRADE 1.4.4 · BTC 策略控制台')
        except Exception: pass
        style=ttk.Style(self.root)
        style.configure('V144ConnectionLabel.TLabel',background=app.PANEL,foreground=visual.TEXT,font=('Helvetica',13),borderwidth=0)
        for widget in _walk(self.root):
            try:
                text=str(widget.cget('text') or '')
            except Exception:
                text=''
            if isinstance(widget,ttk.Label) and text in ACCOUNT_FIELDS:
                try: widget.configure(style='V144ConnectionLabel.TLabel')
                except Exception: pass
            if isinstance(widget,app.RoundedButton):
                if widget.text.startswith('网络自检（'): widget.configure(text='网络自检')
                elif widget.text.startswith('测试连接（'): widget.configure(text='测试连接')
            if text and 'V1.4.3 评分择优版' in text:
                try: widget.configure(text=text.replace('V1.4.3 评分择优版','V1.4.4 视觉精修版'))
                except Exception: pass
        try:
            if str(self.signal.get()).startswith('V1.3.6 最高10分'):
                self.signal.set('')
        except Exception: pass
        _uniform_trade_plan(self)
        self.root.update_idletasks()

    def cycle(self):
        result=previous_cycle(self)
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict) and p.get('v143'):
            changed=p.get('version')!=V144_VERSION or not p.get('v144')
            p['v144']=True; p['version']=V144_VERSION
            if changed: self.store.save()
        return result

    app.App.__init__=app_init
    engine.Engine.cycle=cycle
    engine.Engine._kaytrade_v144_ui_polish_applied=True


apply()

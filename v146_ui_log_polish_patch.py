"""KAYTRADE V1.4.6 UI/log presentation polish only.

Trading and execution behavior are inherited unchanged from V1.4.5/V1.4.3.
This patch implements the confirmed V1.4.6 visual requirements:
- LONG/SHORT score energy bars are 1.6x thicker with maximum capsule rounding
- account/network status stay on one horizontal row aligned to the main 15 px content edge
- header logo, KAYTRADE title and BTC/USDT version line are scaled to 1.2x V1.4.5
- the legacy bottom log/filter strip is replaced by a rounded "运行记录" card
- the old "全部" log filter is hidden; log rows use semantic status dots:
  waiting yellow, alarm red, normal green
"""
from v145_layout_fix_patch import apply as apply_v145
apply_v145()

import tkinter as tk
from tkinter import ttk

import app
import engine
import visual
import v142_visual_patch as v142
import v145_layout_fix_patch as v145
import v144_ui_polish_patch as v144

V146_VERSION='1.4.6'
MAIN_PAD=15
ENERGY_BAR_SCALE=1.6
ENERGY_BAR_BASE_HEIGHT=9
ENERGY_BAR_HEIGHT=int(round(ENERGY_BAR_BASE_HEIGHT*ENERGY_BAR_SCALE))
BRAND_SCALE=1.2
BRAND_TITLE_FONT=('Helvetica',34,'bold')
BRAND_META_FONT=('Helvetica',17)
HEADER_LOGO_CANVAS=70
HEADER_LOGO_IMAGE=62
WAIT_COLOR=v142.YELLOW
ALARM_COLOR=app.RED
NORMAL_COLOR=app.GREEN
LOG_CARD_HEIGHT=250
LOG_VISIBLE_ROWS=3


def _walk(root):
    try:children=root.winfo_children()
    except Exception:return
    for child in children:
        yield child
        yield from _walk(child)


def _capsule(canvas,x1,y1,x2,y2,fill):
    """Maximum-radius capsule fallback used when Pillow is unavailable."""
    if x2<=x1 or y2<=y1:return
    h=y2-y1; w=x2-x1; r=max(0.0,min(h/2.0,w/2.0))
    if w<=h:
        canvas.create_oval(x1,y1,x2,y2,fill=fill,outline='')
        return
    canvas.create_rectangle(x1+r,y1,x2-r,y2,fill=fill,outline='')
    canvas.create_oval(x1,y1,x1+2*r,y2,fill=fill,outline='')
    canvas.create_oval(x2-2*r,y1,x2,y2,fill=fill,outline='')


def _energy_draw(self):
    """Anti-aliased maximum-radius score bar, preserving the existing animation."""
    self.delete('all')
    w=max(2,int(self.winfo_width() or 2)); h=max(4,int(self.winfo_height() or self.bar_height))
    radius=max(1.0,(h-2)/2.0)
    images=[]
    try:
        track=v144._aa_rounded_photo(self,w,h,self.track,radius,1)
        self.create_image(0,0,image=track,anchor='nw',tags='track')
        images.append(track)
    except Exception:
        _capsule(self,0,0,w,h,self.track)
    ratio=max(0.0,min(1.0,self.current/self.maximum))
    end=w*ratio
    if end>2:
        fw=max(2,int(round(end)))
        fill_radius=max(1.0,(min(fw,h)-2)/2.0)
        try:
            fill=v144._aa_rounded_photo(self,fw,h,self.color,fill_radius,1)
            self.create_image(0,0,image=fill,anchor='nw',tags='fill')
            images.append(fill)
        except Exception:
            _capsule(self,0,0,end,h,self.color)
        sheen='#54efc0' if self.color==app.GREEN else '#ff9aae'
        sx=2+(max(end-6,1)*self.phase)
        self.create_line(sx,2,sx,max(2,h-2),fill=sheen,width=max(1,int(h/3)),capstyle='round')
    self._v146_energy_images=tuple(images)
    self._v146_max_round=True


def _header_logo_mark(parent):
    """1.2x V1.4.5 header logo, retaining the subtle 2% spherical crop."""
    path=v142._logo_path()
    if not path.exists():return v145._header_logo_mark(parent)
    canvas=tk.Canvas(parent,width=HEADER_LOGO_CANVAS,height=HEADER_LOGO_CANVAS,
                     bg=app.BG,highlightthickness=0,borderwidth=0)
    try:
        from PIL import Image,ImageTk
        pil=v145._crop_logo(Image.open(path).convert('RGBA'))
        pil=pil.resize((HEADER_LOGO_IMAGE,HEADER_LOGO_IMAGE),Image.Resampling.LANCZOS)
        image=ImageTk.PhotoImage(pil,master=parent)
        center=HEADER_LOGO_CANVAS/2
        canvas.create_image(center,center,image=image,anchor='center')
        canvas._kaytrade_v146_logo_pil=pil
        canvas._kaytrade_v146_logo_image=image
    except Exception:
        canvas.destroy(); return v145._header_logo_mark(parent)
    canvas._kaytrade_v142_logo=True
    canvas._kaytrade_v145_logo=True
    canvas._kaytrade_v146_logo=True
    canvas._kaytrade_v142_logo_image=image
    return canvas


def _style_brand(owner):
    style=ttk.Style(owner.root)
    style.configure('V146BrandTitle.TLabel',background=app.BG,foreground=visual.TEXT,
                    font=BRAND_TITLE_FONT,borderwidth=0)
    style.configure('V146BrandMeta.TLabel',background=app.BG,foreground=app.MUTED,
                    font=BRAND_META_FONT,borderwidth=0)
    owner._v146_brand_title=None; owner._v146_brand_meta=None
    for widget in _walk(owner.root):
        if not isinstance(widget,ttk.Label):continue
        try:text=str(widget.cget('text') or '')
        except Exception:continue
        if text=='KAYTRADE':
            widget.configure(style='V146BrandTitle.TLabel'); owner._v146_brand_title=widget
        elif text.startswith('BTC / USDT'):
            if 'V1.4.5 布局修复版' in text:
                widget.configure(text=text.replace('V1.4.5 布局修复版','V1.4.6 UI精修版'))
            widget.configure(style='V146BrandMeta.TLabel'); owner._v146_brand_meta=widget
    return bool(owner._v146_brand_title and owner._v146_brand_meta)


def _status_color(text):
    text=str(text or '')
    if text.startswith('已连接：'):return app.GREEN
    if '未连接' in text:return WAIT_COLOR
    return app.MUTED


def _align_account_network(owner):
    """Create a deterministic inline status row while preserving legacy bound widgets hidden."""
    env=getattr(owner,'env_label',None)
    if env is None:return False
    badges=env.master
    try:badges.configure(padding=(MAIN_PAD,0)); badges.pack_configure(fill='x')
    except Exception:pass

    network_old=None
    for widget in badges.winfo_children():
        try:
            if isinstance(widget,ttk.Label) and str(widget.cget('textvariable'))==str(owner.network):
                network_old=widget; break
        except Exception:pass
    try:env.pack_forget()
    except Exception:pass
    if network_old is not None:
        try:network_old.pack_forget()
        except Exception:pass

    inline=tk.Frame(badges,bg=app.BG,bd=0,highlightthickness=0)
    inline.pack(side='left',anchor='w',fill='y')
    account=tk.Label(inline,textvariable=owner.environment,bg=app.BG,
                     fg=_status_color(owner.environment.get()),font=('Helvetica',13,'bold'),
                     anchor='w',justify='left',bd=0,highlightthickness=0,padx=0,pady=6)
    account.pack(side='left',anchor='w')
    network=tk.Label(inline,textvariable=owner.network,bg=app.BG,fg=visual.TEXT,
                     font=('Helvetica',13,'bold'),anchor='w',justify='left',
                     bd=0,highlightthickness=0,padx=0,pady=6)
    network.pack(side='left',anchor='w',padx=(16,0))

    def refresh_account(*_):
        try:account.configure(fg=_status_color(owner.environment.get()))
        except Exception:pass
    try:owner.environment.trace_add('write',refresh_account)
    except Exception:pass

    owner._v146_legacy_env_label=env
    owner._v146_legacy_network_label=network_old
    owner._v146_account_label=account
    owner._v146_network_label=network
    owner._v146_status_inline=inline
    owner._v146_status_row=badges
    owner._v146_status_row_aligned=True
    return True


def _log_state(kind,line):
    text=str(line or '')
    if kind=='alarm' or '警报：' in text:
        return '警报',ALARM_COLOR
    positive=('成功','通过','已恢复','已启动','已连接','已解除','完成','正常')
    waiting=('等待','暂停','未连接','尚未','核对','冷却','停止新开仓','锁定')
    if any(word in text for word in positive):
        return '正常运行',NORMAL_COLOR
    if any(word in text for word in waiting):
        return '等待中',WAIT_COLOR
    return '正常运行',NORMAL_COLOR


def _strip_timestamp(line):
    text=str(line or '')
    if len(text)>=20 and text[4]=='-' and text[7]=='-' and text[10]==' ' and text[13]==':' and text[16]==':':
        return text[20:].lstrip()
    return text


def _find_legacy_log_filter(owner):
    """Find the old footer frame using either its title or the bound filter control."""
    for widget in _walk(owner.root):
        try:
            if isinstance(widget,ttk.Label) and str(widget.cget('text') or '')=='运行日志':
                return widget.master
        except Exception:pass
        try:
            if isinstance(widget,app.RoundedCombobox) and getattr(widget,'variable',None) is owner.log_filter:
                return widget.master
        except Exception:pass
    return None


def _build_log_card(owner):
    """Replace the old Text + 全部 filter footer with the requested rounded record card."""
    old_filter=_find_legacy_log_filter(owner)
    if old_filter is not None:
        try:old_filter.pack_forget()
        except Exception:pass
        owner._v146_hidden_log_filter=old_filter
    old_log=getattr(owner,'log',None)
    if old_log is not None:
        try:old_log.pack_forget()
        except Exception:pass
        old_log._v146_hidden=True

    # Safety sweep: no visible control bound to the legacy 全部 selector may survive.
    for widget in _walk(owner.root):
        try:
            if isinstance(widget,app.RoundedCombobox) and getattr(widget,'variable',None) is owner.log_filter:
                widget.pack_forget()
        except Exception:pass

    surface=app.Card(owner.root,height=LOG_CARD_HEIGHT)
    try:surface.pack(side='bottom',fill='x',padx=MAIN_PAD,pady=(0,12),before=owner.book)
    except Exception:surface.pack(side='bottom',fill='x',padx=MAIN_PAD,pady=(0,12))
    body=surface.body
    app.card_label(body,text='运行记录',size=17,bold=True).pack(anchor='w')
    app.card_label(body,text='所有策略循环与安全拦截',color=app.MUTED,size=10).pack(anchor='w',pady=(5,10))
    tk.Frame(body,bg='#243139',height=1,bd=0,highlightthickness=0).pack(fill='x',pady=(0,8))
    rows=tk.Frame(body,bg=app.PANEL,bd=0,highlightthickness=0)
    rows.pack(fill='both',expand=True)
    owner._v146_log_surface=surface
    owner._v146_log_rows=rows
    owner._v146_log_redesign=True
    owner._v146_log_filter_removed=(old_filter is not None)
    return True


def _render_log_row(parent,status,color,title,message,separator=False):
    if separator:
        tk.Frame(parent,bg='#1d2a31',height=1,bd=0,highlightthickness=0).pack(fill='x',pady=5)
    row=tk.Frame(parent,bg=app.PANEL,bd=0,highlightthickness=0)
    row.pack(fill='x',pady=2)
    left=tk.Frame(row,bg=app.PANEL,width=150,bd=0,highlightthickness=0)
    left.pack(side='left',fill='y'); left.pack_propagate(False)
    tk.Label(left,text='●',bg=app.PANEL,fg=color,font=('Helvetica',11,'bold'),bd=0).pack(side='left',anchor='n',pady=(3,0))
    tk.Label(left,text=status,bg=app.PANEL,fg=app.MUTED,font=('Helvetica',10),bd=0).pack(side='left',anchor='n',padx=(8,0),pady=(3,0))
    right=tk.Frame(row,bg=app.PANEL,bd=0,highlightthickness=0)
    right.pack(side='left',fill='x',expand=True)
    tk.Label(right,text=title,bg=app.PANEL,fg=visual.TEXT,font=('Helvetica',11,'bold'),anchor='w',bd=0).pack(fill='x')
    tk.Label(right,text=message,bg=app.PANEL,fg=app.MUTED,font=('Helvetica',9),anchor='w',justify='left',bd=0,
             wraplength=1200).pack(fill='x',pady=(3,0))


def _render_logs(self):
    rows=getattr(self,'_v146_log_rows',None)
    if rows is None:
        return _PREVIOUS_RENDER_LOGS(self)
    for child in rows.winfo_children():
        try:child.destroy()
        except Exception:pass
    visible=list(getattr(self,'log_lines',[]) or [])[-LOG_VISIBLE_ROWS:]
    if not visible:
        _render_log_row(rows,'等待中',WAIT_COLOR,'等待运行记录','连接账户并开始运行后，这里会显示策略循环与安全拦截。')
        return
    for index,(kind,line) in enumerate(reversed(visible)):
        status,color=_log_state(kind,line)
        clean=_strip_timestamp(line)
        if clean.startswith('警报：'):clean=clean[3:].lstrip()
        title='安全警报' if status=='警报' else '等待下一步' if status=='等待中' else '系统运行'
        _render_log_row(rows,status,color,title,clean,separator=index>0)


_PREVIOUS_RENDER_LOGS=app.App.render_logs


def apply():
    if getattr(engine.Engine,'_kaytrade_v146_ui_log_polish_applied',False):return

    previous_bar_init=visual.AnimatedScoreBar.__init__
    previous_app_init=app.App.__init__
    previous_cycle=engine.Engine.cycle

    def energy_init(self,*args,**kwargs):
        requested=int(kwargs.get('height',ENERGY_BAR_BASE_HEIGHT) or ENERGY_BAR_BASE_HEIGHT)
        kwargs['height']=max(1,int(round(requested*ENERGY_BAR_SCALE)))
        result=previous_bar_init(self,*args,**kwargs)
        self.bar_height=kwargs['height']
        self.configure(height=kwargs['height'])
        self._v146_thickness_scale=ENERGY_BAR_SCALE
        self._draw()
        return result
    visual.AnimatedScoreBar.__init__=energy_init
    app.AnimatedScoreBar.__init__=energy_init
    visual.AnimatedScoreBar._draw=_energy_draw
    app.AnimatedScoreBar._draw=_energy_draw

    app.mark=_header_logo_mark
    visual.mark=_header_logo_mark
    app.App.render_logs=_render_logs

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:self.root.title('KAYTRADE 1.4.6 · BTC 策略控制台')
        except Exception:pass
        _style_brand(self)
        _align_account_network(self)
        _build_log_card(self)
        self.render_logs()
        # Explicitly resolve the V1.4.6 logo after all inherited wrappers finish.
        self._v146_logo_widget=None
        for widget in _walk(self.root):
            if getattr(widget,'_kaytrade_v146_logo',False):
                self._v146_logo_widget=widget
                self._v142_logo_widget=widget
                break
        self._v146_brand_scale=BRAND_SCALE
        self.root.update_idletasks()

    def cycle(self):
        result=previous_cycle(self)
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict) and p.get('v145'):
            changed=p.get('version')!=V146_VERSION or not p.get('v146')
            p['v146']=True; p['version']=V146_VERSION
            if changed:self.store.save()
        return result

    app.App.__init__=app_init
    engine.Engine.cycle=cycle
    engine.Engine._kaytrade_v146_ui_log_polish_applied=True


apply()

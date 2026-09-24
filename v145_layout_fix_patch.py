"""KAYTRADE V1.4.5 layout and visual feedback repair.

Preserves V1.4.4 visual styling and V1.4.3 trading logic while fixing the
V1.4.5 UI regressions reported from the packaged Intel Mac build:
- main pages expand across the application instead of collapsing right
- all custom result dialogs are centered and have no black outer frame
- header brand/logo are slightly larger; the spherical logo body is subtly enlarged
- connected account state uses green text on the normal background, not a green badge
- the three right-side header information lines align to the main panel edge
- the trade signal summary is smaller and left-aligned to its panel
- historical chart keeps date/percent ticks but removes standalone axis-title words
"""
from v144_ui_polish_patch import apply as apply_v144
apply_v144()

import tkinter as tk
from tkinter import ttk

import app
import engine
import visual
import v140_score_dialog_patch as v140
import v141_dialog_signal_positions_patch as v141
import v142_visual_patch as v142

V145_VERSION='1.4.5'
MAIN_PAD=15
RUNTIME_NOTE_PREFIX='KAYTRADE · 本机执行'
RUNTIME_NOTE_COLOR=app.MUTED
RUNTIME_NOTE_FONT=('Helvetica',9)
SIGNAL_FONT=('Helvetica',11)
BRAND_TITLE_FONT=('Helvetica',28,'bold')
BRAND_META_FONT=('Helvetica',14)
HEADER_LOGO_CANVAS=58
HEADER_LOGO_IMAGE=52
LOGO_CROP_RATIO=0.02
HISTORY_AXIS_TITLES={'收益率','日期'}


def _walk(root):
    try:children=root.winfo_children()
    except Exception:return
    for child in children:
        yield child
        yield from _walk(child)


def _main_page_top(book):
    """Top offset below the main navigation strip."""
    try:
        book.update_idletasks()
        return max(44,int(book.nav.winfo_reqheight())+10)
    except Exception:
        return 54


def _crop_logo(pil):
    """Trim only a tiny outer margin so the spherical body reads slightly larger."""
    w,h=pil.size
    dx=max(0,int(round(w*LOGO_CROP_RATIO)))
    dy=max(0,int(round(h*LOGO_CROP_RATIO)))
    if dx*2>=w or dy*2>=h:return pil
    return pil.crop((dx,dy,w-dx,h-dy))


def _header_logo_mark(parent):
    """Slightly larger header logo without changing its gradient artwork."""
    path=v142._logo_path()
    if not path.exists():
        return v142.logo_mark(parent)
    canvas=tk.Canvas(parent,width=HEADER_LOGO_CANVAS,height=HEADER_LOGO_CANVAS,
                     bg=app.BG,highlightthickness=0,borderwidth=0)
    try:
        from PIL import Image,ImageTk
        pil=_crop_logo(Image.open(path).convert('RGBA'))
        pil=pil.resize((HEADER_LOGO_IMAGE,HEADER_LOGO_IMAGE),Image.Resampling.LANCZOS)
        image=ImageTk.PhotoImage(pil,master=parent)
        center=HEADER_LOGO_CANVAS/2
        canvas.create_image(center,center,image=image,anchor='center')
        canvas._kaytrade_v145_logo_pil=pil
        canvas._kaytrade_v145_logo_image=image
    except Exception:
        canvas.destroy()
        return v142.logo_mark(parent)
    canvas._kaytrade_v142_logo=True
    canvas._kaytrade_v145_logo=True
    canvas._kaytrade_v142_logo_image=image
    return canvas


def _centered_modal(owner,title,message,success=True):
    """Borderless panel-colored modal with every visible element centered."""
    win=tk.Toplevel(owner)
    win.withdraw()
    win.overrideredirect(True)
    win.transient(owner)
    win.configure(bg=app.PANEL,bd=0,highlightthickness=0,relief='flat')
    width,height=440,226
    body=tk.Frame(win,bg=app.PANEL,bd=0,highlightthickness=0)
    body.pack(fill='both',expand=True,padx=28,pady=22)
    accent=app.GREEN if success else app.RED
    symbol='✓' if success else '!'
    tk.Label(body,text=symbol,bg=app.PANEL,fg=accent,font=('Helvetica',26,'bold'),bd=0,
             anchor='center',justify='center').pack(anchor='center')
    tk.Label(body,text=title,bg=app.PANEL,fg=accent,font=('Helvetica',18,'bold'),bd=0,
             anchor='center',justify='center').pack(anchor='center',pady=(2,6))
    tk.Label(body,text=str(message),wraplength=370,justify='center',anchor='center',bg=app.PANEL,
             fg='#c9d5da',font=('Helvetica',11),bd=0).pack(anchor='center',fill='x')
    def close():
        try:win.grab_release()
        except Exception:pass
        win.destroy()
    app.RoundedButton(body,text='确认',command=close,variant='accent' if success else 'danger',
                      width=116,height=38,font=('Helvetica',11,'bold')).pack(anchor='center',side='bottom')
    owner.update_idletasks()
    x=owner.winfo_rootx()+max(0,(owner.winfo_width()-width)//2)
    y=owner.winfo_rooty()+max(0,(owner.winfo_height()-height)//2)
    win.geometry(f'{width}x{height}+{x}+{y}')
    win.deiconify(); win.lift(); win.grab_set(); win.focus_force()
    win.bind('<Escape>',lambda event:close())
    win._v145_no_outer_black_frame=True
    win._v145_all_centered=True
    return win


def _style_runtime_note(owner):
    """Make the execution-description line small, muted gray and panel-right aligned."""
    style=ttk.Style(owner.root)
    style.configure('V145RuntimeNote.TLabel',background=app.BG,foreground=RUNTIME_NOTE_COLOR,
                    font=RUNTIME_NOTE_FONT,borderwidth=0)
    for widget in _walk(owner.root):
        if not isinstance(widget,ttk.Label):continue
        try:text=str(widget.cget('text') or '')
        except Exception:continue
        if not text.startswith(RUNTIME_NOTE_PREFIX):continue
        try:
            widget.configure(style='V145RuntimeNote.TLabel',anchor='e',justify='right',padding=(0,2))
            widget.pack_configure(fill='x',anchor='e',padx=MAIN_PAD)
        except Exception:pass
        owner._v145_runtime_note=widget
        owner._v145_runtime_note_styled=True
        return True
    owner._v145_runtime_note=None
    owner._v145_runtime_note_styled=False
    return False


def _style_brand(owner):
    """Increase logo/name/version presentation slightly without changing layout."""
    style=ttk.Style(owner.root)
    style.configure('V145BrandTitle.TLabel',background=app.BG,foreground=visual.TEXT,
                    font=BRAND_TITLE_FONT,borderwidth=0)
    style.configure('V145BrandMeta.TLabel',background=app.BG,foreground=app.MUTED,
                    font=BRAND_META_FONT,borderwidth=0)
    owner._v145_brand_title=None; owner._v145_brand_meta=None
    for widget in _walk(owner.root):
        if not isinstance(widget,ttk.Label):continue
        try:text=str(widget.cget('text') or '')
        except Exception:continue
        if text=='KAYTRADE':
            widget.configure(style='V145BrandTitle.TLabel')
            owner._v145_brand_title=widget
        elif text.startswith('BTC / USDT'):
            widget.configure(style='V145BrandMeta.TLabel')
            owner._v145_brand_meta=widget
    return bool(owner._v145_brand_title and owner._v145_brand_meta)


def _flatten_connection_badge(owner):
    """Keep the connection state on the normal background; connected text is green."""
    label=getattr(owner,'env_label',None)
    if label is None:return False
    original=label.configure
    def configure(cnf=None,**kwargs):
        if cnf is None and not kwargs:return original()
        if isinstance(cnf,dict):
            options=dict(cnf); options.update(kwargs)
        elif cnf is not None:
            return original(cnf,**kwargs)
        else:
            options=dict(kwargs)
        options.pop('bg',None); options.pop('background',None)
        options.pop('fg',None); options.pop('foreground',None)
        text=str(owner.environment.get() or '')
        options['bg']=app.BG
        options['fg']=app.GREEN if text.startswith('已连接：') else v142.YELLOW if '未连接' in text else app.MUTED
        return original(**options)
    label.configure=configure
    label.config=configure
    label.configure(padx=0,pady=6)
    owner._v145_env_label_original_configure=original
    owner._v145_connection_badge_flat=True
    return True


def _align_right_header_lines(owner):
    """Use the same right edge as the 15 px-inset main content panel."""
    status=getattr(owner,'_v142_status_label',None)
    if status is not None:
        try:status.configure(anchor='e',justify='right',padding=(0,0))
        except Exception:pass
    equity_label=None
    for widget in _walk(owner.root):
        if not isinstance(widget,ttk.Label):continue
        try:
            if str(widget.cget('textvariable'))==str(owner.equity):
                equity_label=widget
                widget.configure(anchor='e',justify='right',padding=(0,0))
                break
        except Exception:pass
    owner._v145_status_right_label=status
    owner._v145_equity_right_label=equity_label
    owner._v145_right_header_aligned=bool(status is not None and equity_label is not None and getattr(owner,'_v145_runtime_note',None) is not None)
    return owner._v145_right_header_aligned


def _style_signal_summary(owner):
    """Make the one-line market decision summary smaller and align it to score cards."""
    style=ttk.Style(owner.root)
    style.configure('V145SignalSummary.TLabel',background=app.BG,foreground=app.MUTED,
                    font=SIGNAL_FONT,borderwidth=0)
    for widget in _walk(owner.root):
        if not isinstance(widget,ttk.Label):continue
        try:
            if str(widget.cget('textvariable'))!=str(owner.signal):continue
            widget.configure(style='V145SignalSummary.TLabel',anchor='w',justify='left',padding=(0,0))
            widget.pack_configure(anchor='w',padx=(4,0),pady=(0,10))
            owner._v145_signal_label=widget
            owner._v145_signal_styled=True
            return True
        except Exception:pass
    owner._v145_signal_label=None
    owner._v145_signal_styled=False
    return False


def _rebuild_connection_page(owner):
    """Rebuild Account Connection as one full-width responsive panel."""
    book=getattr(owner,'book',None)
    if book is None or not getattr(book,'pages',None):return False
    page=book.pages[0]
    for child in list(page.winfo_children()):
        try:child.destroy()
        except Exception:pass

    surface=app.Card(page,height=650)
    surface.pack(fill='both',expand=True,pady=(0,4))
    body=surface.body
    body.grid_columnconfigure(0,weight=0,minsize=190)
    body.grid_columnconfigure(1,weight=1)
    body.grid_rowconfigure(8,weight=1)

    tk.Label(body,text='账户连接',bg=app.PANEL,fg='#eef5f7',font=('Helvetica',20,'bold'),
             anchor='w',bd=0).grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,14))

    rows=[('账户官方域名',owner.host,app.HOSTS),
          ('环境',owner.mode,('OKX模拟盘','真实账户')),
          ('API Key',owner.key,None),('Secret Key',owner.secret,None),('Passphrase',owner.phrase,None)]
    owner.connection_widgets=[]
    for i,(label,var,values) in enumerate(rows,1):
        tk.Label(body,text=label,bg=app.PANEL,fg=visual.TEXT,font=('Helvetica',13),anchor='w',bd=0,
                 highlightthickness=0).grid(row=i,column=0,sticky='w',padx=(0,18),pady=9)
        if values:widget=app.RoundedCombobox(body,textvariable=var,values=values,width=620,height=40)
        else:widget=app.RoundedEntry(body,textvariable=var,show='•',width=620,height=40)
        widget.grid(row=i,column=1,sticky='ew',pady=9)
        owner.connection_widgets.append(widget)

    note=('密钥仅保存在本次运行内存中，退出后需重新填写；不会发送给GPT/Gemini。\n'
          '使用专用交易子账户，不要与手动交易或其他机器人共用BTC仓位。\n'
          '测试连接只读取账户与持仓；自动交易需读取+交易权限，禁止提币权限。\n'
          '模拟与真实账户密钥不可混用；地区或产品不支持时停止，不绕过限制。\n'
          '程序仅连接OKX官方接口，不接入原Sites网页。')
    tk.Label(body,text=note,wraplength=1050,justify='left',anchor='w',bg=app.PANEL,fg=app.MUTED,
             font=('Helvetica',10),bd=0,highlightthickness=0).grid(
                 row=6,column=0,columnspan=2,sticky='ew',pady=(18,12))

    actions=tk.Frame(body,bg=app.PANEL,bd=0,highlightthickness=0)
    actions.grid(row=7,column=0,columnspan=2,sticky='w',pady=(0,14))
    app.RoundedButton(actions,text='网络自检',command=owner.diagnose,variant='neutral',width=190).pack(side='left',padx=(0,10))
    app.RoundedButton(actions,text='测试连接',command=owner.connect,variant='accent',width=190).pack(side='left')

    owner.account_view=tk.Text(body,height=10,wrap='word',bg=app.PANEL_ALT,fg='#c8e5f5',font=('Menlo',12),
                               bd=0,highlightthickness=0,padx=14,pady=12)
    owner.account_view.grid(row=8,column=0,columnspan=2,sticky='nsew',pady=(0,2))

    owner._v145_connection_page=page
    owner._v145_connection_surface=surface
    owner._v145_connection_body=body
    owner._v145_connection_rebuilt=True
    return True


def apply():
    if getattr(engine.Engine,'_kaytrade_v145_layout_fix_applied',False):return

    # Reassert the final modal implementation after all older patch layers.
    v140._rounded_modal=_centered_modal
    v141._centered_modal=_centered_modal

    previous_tabs_select=app.Tabs.select
    previous_app_init=app.App.__init__
    previous_cycle=engine.Engine.cycle
    previous_draw_curve=app.App.draw_curve

    # App.__init__ resolves app.mark dynamically, so install the slightly enlarged
    # header logo before the wrapped constructor builds the header.
    app.mark=_header_logo_mark
    visual.mark=_header_logo_mark

    def tabs_select(self,page=None):
        # Nested score/indicator tabs keep the previous compact layout. Only the
        # top-level application pages use the full-width placement geometry.
        if not getattr(self,'_v145_main_layout',False):return previous_tabs_select(self,page)
        if page is None:
            if self.active is None:return ''
            return str(self.pages[self.active])
        index=page if isinstance(page,int) else self.pages.index(page)
        for p in self.pages:
            try:p.pack_forget()
            except Exception:pass
            try:p.place_forget()
            except Exception:pass
        top=_main_page_top(self)
        selected=self.pages[index]
        selected.place(x=0,y=top,relwidth=1.0,relheight=1.0,height=-top)
        selected.lift(); self.active=index
        for i,b in enumerate(self.buttons):b.configure(variant='tab_active' if i==index else 'tab')
        self.event_generate('<<NotebookTabChanged>>')
        self.after_idle(lambda:self.repaint(selected))
        return str(selected)

    def draw_curve(self):
        result=previous_draw_curve(self)
        # Keep all date and percentage tick values, remove only the two standalone axis words.
        try:
            for item in self.chart.find_all():
                if self.chart.type(item)=='text' and self.chart.itemcget(item,'text') in HISTORY_AXIS_TITLES:
                    self.chart.delete(item)
        except Exception:pass
        return result

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:self.root.title('KAYTRADE 1.4.5 · BTC 策略控制台')
        except Exception:pass

        self.book._v145_main_layout=True
        _rebuild_connection_page(self)
        _style_runtime_note(self)
        _style_brand(self)
        _flatten_connection_badge(self)
        _align_right_header_lines(self)
        _style_signal_summary(self)

        current=self.book.active if self.book.active is not None else 0
        self.book.select(current)
        self.root.update_idletasks()
        self._v145_layout_full_width=True

        for widget in _walk(self.root):
            try:
                text=str(widget.cget('text') or '')
                if 'V1.4.4 视觉精修版' in text:
                    widget.configure(text=text.replace('V1.4.4 视觉精修版','V1.4.5 布局修复版'))
            except Exception:pass

    def cycle(self):
        result=previous_cycle(self)
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict) and p.get('v144'):
            changed=p.get('version')!=V145_VERSION or not p.get('v145')
            p['v145']=True; p['version']=V145_VERSION
            if changed:self.store.save()
        return result

    app.Tabs.select=tabs_select
    visual.Tabs.select=tabs_select
    app.App.__init__=app_init
    app.App.draw_curve=draw_curve
    engine.Engine.cycle=cycle
    engine.Engine._kaytrade_v145_layout_fix_applied=True


apply()

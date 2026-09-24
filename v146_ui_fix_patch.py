"""KAYTRADE V1.4.6 interface repair.

UI-only follow-up on V1.4.6. Trading/scoring/execution behavior is unchanged.
Confirmed repairs:
- halve the run-record card height and add the same rounded dark vertical scrollbar
- hide the unused account-result rectangle below Network Self-check / Test Connection
- give Risk Settings and Execution Parameters their own matching vertical page scrollbars
- align the account/network status row to the visible left edge of the circular logo artwork
"""
from v146_ui_log_polish_patch import apply as apply_v146
apply_v146()

import tkinter as tk

import app
import engine
import visual
import v146_ui_log_polish_patch as v146

V146_FIX_VERSION='1.4.6'
LOG_CARD_HEIGHT=130
LOG_HISTORY_LIMIT=100
# Header container starts at 15 px; the 62 px logo image is centered in a 70 px canvas,
# so its visible image starts 4 px inside the canvas. Align status text to that edge.
STATUS_LEFT_PAD=v146.MAIN_PAD + max(0,(v146.HEADER_LOGO_CANVAS-v146.HEADER_LOGO_IMAGE)//2)
RISK_SCROLL_HEIGHT=760
EXEC_SCROLL_HEIGHT=610


def _walk(root):
    try:children=root.winfo_children()
    except Exception:return
    for child in children:
        yield child
        yield from _walk(child)


def _hide_connection_result_box(owner):
    """Keep the Text object alive for connection callbacks, but remove it from layout."""
    box=getattr(owner,'account_view',None)
    if box is None:return False
    try:box.grid_remove()
    except Exception:
        try:box.pack_forget()
        except Exception:return False
    box._v146_fix_hidden=True
    owner._v146_fix_connection_box_removed=True
    return True


def _realign_status_to_logo(owner):
    row=getattr(owner,'_v146_status_row',None)
    inline=getattr(owner,'_v146_status_inline',None)
    if row is None or inline is None:return False
    try:row.configure(padding=(STATUS_LEFT_PAD,0))
    except Exception:return False
    try:inline.pack_configure(side='left',anchor='w',fill='y')
    except Exception:pass
    owner._v146_fix_status_left_pad=STATUS_LEFT_PAD
    owner._v146_fix_status_logo_aligned=True
    return True


def _field_row(parent,row,label,var,disabled=False):
    tk.Label(parent,text=label,wraplength=430,bg=app.PANEL,fg='#dbe6eb',
             font=('Helvetica',12),anchor='w',justify='left',bd=0).grid(
                 row=row,column=0,sticky='w',padx=(6,24),pady=11)
    entry=app.RoundedEntry(parent,textvariable=var,width=190,height=40,font=('Helvetica',14))
    entry.grid(row=row,column=1,sticky='e',padx=8,pady=11)
    if disabled:
        try:entry.entry.configure(state='disabled',disabledbackground=app.FIELD,disabledforeground=app.MUTED)
        except Exception:pass
    return entry


def _scroll_surface(page,height):
    for child in list(page.winfo_children()):
        try:child.destroy()
        except Exception:pass
    scroll=app.ScrollablePage(page)
    scroll.pack(fill='both',expand=True)
    surface=app.Card(scroll.body,height=height)
    surface.pack(fill='x',expand=False,pady=(0,4))
    return scroll,surface,surface.body


def _rebuild_risk_page(owner):
    if not getattr(owner.book,'pages',None) or len(owner.book.pages)<3:return False
    page=owner.book.pages[1]
    scroll,surface,body=_scroll_surface(page,RISK_SCROLL_HEIGHT)
    body.grid_columnconfigure(0,weight=1)
    body.grid_columnconfigure(1,weight=0)
    tk.Label(body,text='风险设置',bg=app.PANEL,fg='#eef5f7',font=('Helvetica',20,'bold'),
             anchor='w',bd=0).grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,14))

    rows=[
        ('capital','策略资金预算 USDT'),
        ('risk_usdt','基础单笔预估亏损 USDT'),
        ('risk_pct','基础单笔风险 %（评分倍率前）'),
        ('daily_loss','中国时间日内权益回撤上限 USDT'),
        ('first_signal_notional','第一信号仓位 USDT（一级 4.0–6.0）'),
        ('second_signal_notional','第二信号仓位 USDT（二级 6.5–7.5）'),
        ('third_signal_notional','第三信号仓位 USDT（三级 8.0–10，仅1次）'),
    ]
    row=1
    owner._v146_fix_risk_entries=[]
    for key,label in rows:
        var=owner.fields.get(key)
        if var is None:continue
        owner._v146_fix_risk_entries.append(_field_row(body,row,label,var)); row+=1

    if owner.fields.get('consecutive_losses') is not None:
        tk.Label(body,text='中国时间连续亏损停开次数',wraplength=430,bg=app.PANEL,fg='#dbe6eb',
                 font=('Helvetica',12),anchor='w',bd=0).grid(row=row,column=0,sticky='w',padx=6,pady=11)
        tk.Label(body,text='3（固定）',bg=app.PANEL,fg=app.MUTED,font=('Helvetica',13,'bold'),anchor='e',
                 bd=0,padx=8,pady=8).grid(row=row,column=1,sticky='e',padx=8,pady=11)
        row+=1

    tk.Label(body,text='除连续亏损停开次数外，其余风险数值均可修改并保存；三档信号仓位分别独立作为一级、二级、三级单次名义仓位上限。',
             wraplength=900,bg=app.PANEL,fg=app.MUTED,font=('Helvetica',10),anchor='w',justify='left').grid(
                 row=row,column=0,columnspan=2,sticky='w',pady=(16,8)); row+=1
    app.RoundedButton(body,text='校验并保存风险设置',command=owner.save_settings,variant='accent',width=220).grid(
        row=row,column=0,columnspan=2,sticky='w',pady=(4,12))

    owner._v146_fix_risk_scroll=scroll
    owner._v146_fix_risk_surface=surface
    owner._v146_fix_risk_scrollbar=scroll.scroll
    return True


def _rebuild_execution_page(owner):
    if not getattr(owner.book,'pages',None) or len(owner.book.pages)<3:return False
    page=owner.book.pages[2]
    scroll,surface,body=_scroll_surface(page,EXEC_SCROLL_HEIGHT)
    body.grid_columnconfigure(0,weight=1)
    body.grid_columnconfigure(1,weight=0)
    tk.Label(body,text='交易执行参数',bg=app.PANEL,fg='#eef5f7',font=('Helvetica',20,'bold'),
             anchor='w',bd=0).grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,14))

    rows=[
        ('leverage','逐仓杠杆 1—50倍'),
        ('cooldown_minutes','平仓后冷却时间 分钟'),
        ('stop_atr','15分钟 ATR 止损倍数 0.6—3'),
        ('score_threshold','自动开仓评分阈值 4.0（固定）'),
    ]
    row=1
    owner._v146_fix_execution_entries=[]
    for key,label in rows:
        var=owner.fields.get(key)
        if var is None:continue
        disabled=(key=='score_threshold')
        if disabled:
            try:var.set('4.0')
            except Exception:pass
        owner._v146_fix_execution_entries.append(_field_row(body,row,label,var,disabled=disabled)); row+=1

    fixed=app.Card(body,height=150,fill=app.PANEL_ALT)
    fixed.grid(row=row,column=0,columnspan=2,sticky='ew',pady=(18,10)); row+=1
    app.card_label(fixed.body,text='固定执行成本',color=app.MUTED,size=10,bold=True).pack(anchor='w')
    app.card_label(fixed.body,text='Maker 0.02%   ·   Taker 0.05%   ·   滑点预算 0.05%',size=16,bold=True).pack(anchor='w',pady=(7,4))
    app.card_label(fixed.body,text='费率与滑点预算锁定不可编辑 · 15m ATR 止损 · 全仓单次止盈 2R',color=app.MUTED,size=9).pack(anchor='w')
    app.RoundedButton(body,text='校验并保存执行参数',command=owner.save_settings,variant='accent',width=220).grid(
        row=row,column=0,columnspan=2,sticky='w',pady=(8,14))

    owner._v146_fix_execution_scroll=scroll
    owner._v146_fix_execution_surface=surface
    owner._v146_fix_execution_scrollbar=scroll.scroll
    return True


def _bind_log_wheel(canvas):
    def wheel(event):
        delta=getattr(event,'delta',0)
        if not delta:return
        units=(-1 if delta>0 else 1) if abs(delta)<120 else int(-delta/120)
        canvas.yview_scroll(units,'units')
        return 'break'
    canvas.bind('<MouseWheel>',wheel)
    canvas.bind('<Button-4>',lambda event:(canvas.yview_scroll(-1,'units'),'break')[1])
    canvas.bind('<Button-5>',lambda event:(canvas.yview_scroll(1,'units'),'break')[1])


def _build_compact_log_card(owner):
    old=getattr(owner,'_v146_log_surface',None)
    if old is not None:
        try:old.pack_forget()
        except Exception:pass

    surface=app.Card(owner.root,height=LOG_CARD_HEIGHT)
    try:surface.pack(side='bottom',fill='x',padx=v146.MAIN_PAD,pady=(0,12),before=owner.book)
    except Exception:surface.pack(side='bottom',fill='x',padx=v146.MAIN_PAD,pady=(0,12))
    body=surface.body
    header=tk.Frame(body,bg=app.PANEL,bd=0,highlightthickness=0)
    header.pack(fill='x')
    app.card_label(header,text='运行记录',size=15,bold=True).pack(side='left',anchor='w')
    app.card_label(header,text='所有策略循环与安全拦截',color=app.MUTED,size=9).pack(side='left',anchor='w',padx=(14,0),pady=(3,0))
    tk.Frame(body,bg='#243139',height=1,bd=0,highlightthickness=0).pack(fill='x',pady=(7,6))

    viewport=tk.Frame(body,bg=app.PANEL,bd=0,highlightthickness=0)
    viewport.pack(fill='both',expand=True)
    canvas=tk.Canvas(viewport,bg=app.PANEL,highlightthickness=0,borderwidth=0,yscrollincrement=22)
    scroll=app.WideScrollbar(viewport,command=canvas.yview,width=20)
    canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right',fill='y')
    canvas.pack(side='left',fill='both',expand=True)
    rows=tk.Frame(canvas,bg=app.PANEL,bd=0,highlightthickness=0)
    window=canvas.create_window(0,0,anchor='nw',window=rows)

    def sync(event=None):
        try:canvas.configure(scrollregion=canvas.bbox('all') or (0,0,1,1))
        except Exception:pass
    def resize(event):
        try:canvas.itemconfigure(window,width=max(1,event.width)); sync()
        except Exception:pass
    rows.bind('<Configure>',sync)
    canvas.bind('<Configure>',resize)
    _bind_log_wheel(canvas)

    owner._v146_log_surface=surface
    owner._v146_log_rows=rows
    owner._v146_fix_log_surface=surface
    owner._v146_fix_log_canvas=canvas
    owner._v146_fix_log_scrollbar=scroll
    owner._v146_fix_log_window=window
    owner._v146_fix_log_compact=True
    return True


def _render_logs(self):
    rows=getattr(self,'_v146_log_rows',None)
    if rows is None:return _PREVIOUS_RENDER_LOGS(self)
    for child in list(rows.winfo_children()):
        try:child.destroy()
        except Exception:pass
    visible=list(getattr(self,'log_lines',[]) or [])[-LOG_HISTORY_LIMIT:]
    if not visible:
        v146._render_log_row(rows,'等待中',v146.WAIT_COLOR,'等待运行记录','连接账户并开始运行后，这里会显示策略循环与安全拦截。')
    else:
        for index,(kind,line) in enumerate(reversed(visible)):
            status,color=v146._log_state(kind,line)
            clean=v146._strip_timestamp(line)
            if clean.startswith('警报：'):clean=clean[3:].lstrip()
            title='安全警报' if status=='警报' else '等待下一步' if status=='等待中' else '系统运行'
            v146._render_log_row(rows,status,color,title,clean,separator=index>0)
    canvas=getattr(self,'_v146_fix_log_canvas',None)
    if canvas is not None:
        try:
            self.root.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox('all') or (0,0,1,1))
            canvas.yview_moveto(0.0)
        except Exception:pass


_PREVIOUS_RENDER_LOGS=app.App.render_logs


def apply():
    if getattr(engine.Engine,'_kaytrade_v146_ui_fix_applied',False):return
    previous_app_init=app.App.__init__

    app.App.render_logs=_render_logs

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        _hide_connection_result_box(self)
        _realign_status_to_logo(self)
        _rebuild_risk_page(self)
        _rebuild_execution_page(self)
        _build_compact_log_card(self)
        self.render_logs()
        self.root.update_idletasks()
        self._v146_ui_fix_ready=True

    app.App.__init__=app_init
    engine.Engine._kaytrade_v146_ui_fix_applied=True


apply()

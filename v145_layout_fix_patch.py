"""KAYTRADE V1.4.5 main-layout repair.

V1.4.4 visual styling is preserved, but the main page geometry is corrected so
selected pages expand across the application instead of collapsing into a
narrow right-side area.  Width normalization remains local to peer cards; it
is not applied across unrelated page regions.
"""
from v144_ui_polish_patch import apply as apply_v144
apply_v144()

import tkinter as tk
from tkinter import ttk

import app
import engine
import visual

V145_VERSION='1.4.5'


def _main_page_top(book):
    """Top offset below the main navigation strip."""
    try:
        book.update_idletasks()
        return max(44, int(book.nav.winfo_reqheight()) + 10)
    except Exception:
        return 54


def _rebuild_connection_page(owner):
    """Rebuild the Account Connection page as one full-width responsive card."""
    book=getattr(owner,'book',None)
    if book is None or not getattr(book,'pages',None):
        return False
    page=book.pages[0]
    for child in list(page.winfo_children()):
        try: child.destroy()
        except Exception: pass

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
        if values:
            widget=app.RoundedCombobox(body,textvariable=var,values=values,width=620,height=40)
        else:
            widget=app.RoundedEntry(body,textvariable=var,show='•',width=620,height=40)
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
    if getattr(engine.Engine,'_kaytrade_v145_layout_fix_applied',False):
        return

    previous_tabs_select=app.Tabs.select
    previous_app_init=app.App.__init__
    previous_cycle=engine.Engine.cycle

    def tabs_select(self,page=None):
        # Nested score/indicator tabs keep their original compact layout.
        if not getattr(self,'_v145_main_layout',False):
            return previous_tabs_select(self,page)
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
        for i,b in enumerate(self.buttons):
            b.configure(variant='tab_active' if i==index else 'tab')
        self.event_generate('<<NotebookTabChanged>>')
        self.after_idle(lambda:self.repaint(selected))
        return str(selected)

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:self.root.title('KAYTRADE 1.4.5 · BTC 策略控制台')
        except Exception:pass

        self.book._v145_main_layout=True
        _rebuild_connection_page(self)

        # Re-apply the currently selected main page using the full-width geometry.
        current=self.book.active if self.book.active is not None else 0
        self.book.select(current)
        self.root.update_idletasks()
        self._v145_layout_full_width=True

        # Update only the version subtitle; no trading wording or strategy state is changed.
        for widget in visual._walk(self.root) if hasattr(visual,'_walk') else ():
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
    engine.Engine.cycle=cycle
    engine.Engine._kaytrade_v145_layout_fix_applied=True


apply()

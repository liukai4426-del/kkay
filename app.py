"""Native Tk desktop UI; no listener, web server, web secrets or third-party packages."""
import fcntl
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path
from dataclasses import asdict
from core import HOSTS, INSTRUMENT
from exchange import Exchange, NetworkError
from engine import Engine, Settings, Halt
from history import summarize
from candles import CandlePending
from visual import theme, Card, Tabs, ScrollablePage, MetricTile, mark, RoundedButton, RoundedEntry, RoundedCombobox, AnimatedScoreBar, ScoreTable, WideScrollbar, label as card_label, BG, PANEL, PANEL_ALT, FIELD, MUTED, GREEN, RED

DATA=Path.home()/'Library'/'Application Support'/'OKXLocal'

class App:
    def __init__(self,root,folder=DATA):
        self.root=root; self.folder=folder
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=(folder/'instance.lock').open('a')
        try:
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise Halt('本地程序已运行，请使用已有窗口')
        self.tasks=queue.Queue(); self.events=queue.Queue(); self.engine=None
        self.busy=False; self.finished=threading.Event(); self.public_at=0
        self.network_paused=False; self.recovery_count=0; self.probe_at=0
        self.candle_wait_log=0
        self.log_lines=[]
        self.history_key=None; self.history_curve=[]
        self.root.title('KAYTRADE 1.3.5 · BTC 策略控制台'); self.root.geometry('1200x920'); self.root.minsize(820,620)
        theme(root)
        top=ttk.Frame(root,padding=15); top.pack(fill='x')
        mark(top).pack(side='left',padx=(0,12))
        brand=ttk.Frame(top); brand.pack(side='left')
        ttk.Label(brand,text='KAYTRADE',style='Title.TLabel').pack(anchor='w')
        ttk.Label(brand,text='BTC / USDT   ·   V1.3.5 10分细分结构策略',style='Muted.TLabel').pack(anchor='w')
        self.status=tk.StringVar(value='默认停止 · 未连接')
        ttk.Label(top,textvariable=self.status,style='Muted.TLabel').pack(side='right')
        badges=ttk.Frame(root,padding=(15,0)); badges.pack(fill='x')
        self.environment=tk.StringVar(value='账户：未连接 · 默认模拟盘')
        self.network=tk.StringVar(value='网络：未检测')
        self.equity=tk.StringVar(value='USDT 权益：—')
        self.env_label=tk.Label(badges,textvariable=self.environment,bg='#173c31',fg='#65eeb4',padx=12,pady=6)
        self.env_label.pack(side='left')
        ttk.Label(badges,textvariable=self.network,padding=8).pack(side='left')
        ttk.Label(badges,textvariable=self.equity,padding=8).pack(side='right')
        note='KAYTRADE · 本机执行 · 逐仓 / 单策略仓位 / 每单TP1/TP2+SL · 测试版，尚未完成账户端到端验收'
        ttk.Label(root,text=note,padding=(15,5)).pack(fill='x')
        book=Tabs(root); book.pack(fill='both',expand=True,padx=15,pady=10)
        self.book=book
        # Raise the selected pane explicitly: Aqua Tk can leave a newly mapped
        # notebook pane behind its siblings even while inputs report mapped.
        book.bind('<<NotebookTabChanged>>',lambda event: root.nametowidget(book.select()).lift() if book.select() else None)
        connection_page=ttk.Frame(book,padding=8); risk_page=ttk.Frame(book,padding=8); execution_page=ttk.Frame(book,padding=8); dash_page=ScrollablePage(book)
        book.add(connection_page,text='连接设置'); book.add(risk_page,text='风险设置'); book.add(execution_page,text='执行参数'); book.add(dash_page,text='交易总览')
        connection_surface=Card(connection_page,height=650); connection_surface.pack(fill='both',expand=True,pady=(0,4))
        connection=connection_surface.body
        risk_surface=Card(risk_page,height=650); risk_surface.pack(fill='both',expand=True,pady=(0,4)); risk=risk_surface.body
        execution_surface=Card(execution_page,height=650); execution_surface.pack(fill='both',expand=True,pady=(0,4)); execution=execution_surface.body
        dash=dash_page.body
        history_tab=ttk.Frame(book,padding=8); book.add(history_tab,text='历史收益')
        history_summary=Card(history_tab,height=118); history_summary.pack(fill='x',pady=(0,10))
        card_label(history_summary.body,text='历史收益 / 本程序已平仓轮次',color=MUTED,size=11,bold=True).pack(anchor='w')
        self.performance=tk.StringVar(value='等待连接账户 · 暂无记录')
        self.performance_label=card_label(history_summary.body,variable=self.performance,size=19,bold=True)
        self.performance_label.pack(anchor='w',pady=(6,2))
        card_label(history_summary.body,text='盈利绿色 · 亏损红色 · 累计收益同样按正负显示',color=MUTED,size=10).pack(anchor='w')
        history_chart=Card(history_tab,height=205); history_chart.pack(fill='x',pady=(0,10))
        self.chart=tk.Canvas(history_chart.body,height=155,bg=PANEL,highlightthickness=0,borderwidth=0)
        self.chart.pack(fill='both',expand=True); self.chart.bind('<Configure>',lambda event:self.draw_curve())
        history_table_surface=Card(history_tab,height=300); history_table_surface.pack(fill='both',expand=True)
        history_body=history_table_surface.body
        self.history_table=ttk.Treeview(history_body,columns=('side','pnl','total','id'),show='tree headings',height=9)
        for key,title,width in (('#0','平仓记录时间（UTC）',210),('side','方向',80),('pnl','权益变化 USDT',140),('total','累计 USDT',140),('id','本程序订单号',240)):
            self.history_table.heading(key,text=title,anchor='w'); self.history_table.column(key,width=width,anchor='w')
        hs=ttk.Scrollbar(history_body,orient='vertical',command=self.history_table.yview)
        self.history_table.configure(yscrollcommand=hs.set); hs.pack(side='right',fill='y')
        self.history_table.pack(fill='both',expand=True)
        self.history_table.tag_configure('win',foreground=GREEN); self.history_table.tag_configure('loss',foreground=RED); self.history_table.tag_configure('flat',foreground=MUTED)
        self.host=tk.StringVar(value=HOSTS[0]); self.mode=tk.StringVar(value='OKX模拟盘')
        self.key=tk.StringVar(); self.secret=tk.StringVar(); self.phrase=tk.StringVar()
        self.connection_widgets=[]
        tk.Label(connection,text='账户连接',bg=PANEL,fg='#eef5f7',font=('Helvetica',20,'bold'),anchor='w',bd=0).grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,12))
        rows=[('账户官方域名',self.host,HOSTS),('环境',self.mode,('OKX模拟盘','真实账户')),
              ('API Key',self.key,None),('Secret Key',self.secret,None),('Passphrase',self.phrase,None)]
        for i,(label,var,values) in enumerate(rows):
            ttk.Label(connection,text=label,style='Card.TLabel').grid(row=i+1,column=0,sticky='w',pady=8)
            widget=RoundedCombobox(connection,textvariable=var,values=values,width=48) if values else RoundedEntry(connection,textvariable=var,show='•',width=50)
            widget.grid(row=i+1,column=1,sticky='ew',padx=12,pady=8); self.connection_widgets.append(widget)
        connection.columnconfigure(1,weight=1)
        text=('密钥仅保存在此次运行内存中，退出后需重新填写；不会发送给GPT/Gemini。\n'
              '使用专用交易子账户，不要与手动交易/其他机器人共用BTC仓位。\n'
              '先用读取权限测试连接；自动交易需读取+交易权限，禁止提币权限。\n'
              '模拟与真实账户密钥不可混用。地区/产品不支持时停止，不绕过限制。\n'
              '“测试连接”只读取账户与持仓，不下单。程序不接入原Sites网页。')
        ttk.Label(connection,text=text,wraplength=800,justify='left').grid(row=6,column=0,columnspan=2,sticky='w',pady=20)
        RoundedButton(connection,text='网络自检（不下单）',command=self.diagnose,variant='neutral',width=170).grid(row=7,column=0,sticky='w',pady=4)
        RoundedButton(connection,text='测试连接（只读）',command=self.connect,variant='accent',width=170).grid(row=7,column=1,sticky='w',padx=12,pady=4)
        self.account_view=tk.Text(connection,height=9,wrap='word',bg=PANEL_ALT,fg='#c8e5f5',font=('Menlo',12),bd=0,highlightthickness=0,padx=12,pady=10)
        self.account_view.grid(row=8,column=0,columnspan=2,sticky='nsew',pady=16); connection.rowconfigure(8,weight=1)
        defaults=asdict(Settings())
        # Separate V1.1 risk preferences; preserve all account state and locks.
        settings_path=folder/'settings-v1.3.5.json'
        previous_path=folder/'settings-v1.3.4.json'
        legacy_path=folder/'settings-v1.1.json'
        self.settings_path=settings_path
        source=settings_path if settings_path.exists() else previous_path if previous_path.exists() else legacy_path
        if source.exists():
            try:
                loaded=json.loads(source.read_text())
                for k,v in loaded.items():
                    if k in defaults and (source==settings_path or k!='score_threshold'):
                        defaults[k]=v
                if source==settings_path:
                    value=float(defaults.get('score_threshold',3.5))
                    defaults['score_threshold']=max(3.5,min(10.0,round(value*2)/2))
                else:
                    # V1.3.5 intentionally starts its new score model at the new 3.5 default; preserve other user settings.
                    defaults['score_threshold']=3.5
            except Exception:
                pass
        # V1.3.5 fixed execution economics are not user-editable.
        defaults['fee_bps']=2.0; defaults['taker_fee_bps']=5.0; defaults['slippage_bps']=5.0; defaults['reward_r']=2.0
        # Consecutive-loss stop is the only fixed risk control in the Risk page.
        defaults['consecutive_losses']=3
        self.fields={}
        def add_fields(parent,title,items):
            tk.Label(parent,text=title,bg=PANEL,fg='#eef5f7',font=('Helvetica',20,'bold'),anchor='w',bd=0).grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,14))
            for i,(name,text) in enumerate(items,1):
                tk.Label(parent,text=text,wraplength=340,bg=PANEL,fg='#dbe6eb',font=('Helvetica',12),anchor='w',bd=0).grid(row=i,column=0,sticky='w',padx=6,pady=11)
                var=tk.StringVar(value=str(defaults[name])); self.fields[name]=var
                RoundedEntry(parent,textvariable=var,width=170,height=40,font=('Helvetica',14)).grid(row=i,column=1,sticky='e',padx=8,pady=11)
            parent.columnconfigure(0,weight=1)
        add_fields(risk,'风险设置',[
            ('capital','策略资金预算 USDT'),('max_notional','最大名义仓位 USDT（不是保证金）'),
            ('risk_usdt','基础单笔预估亏损 USDT'),('risk_pct','基础单笔风险 %（评分倍率前）'),
            ('daily_loss','中国时间日内权益回撤上限 USDT')])
        self.fields['consecutive_losses']=tk.StringVar(value='3')
        tk.Label(risk,text='中国时间连续亏损停开次数',wraplength=340,bg=PANEL,fg='#dbe6eb',
                 font=('Helvetica',12),anchor='w',bd=0).grid(row=6,column=0,sticky='w',padx=6,pady=11)
        tk.Label(risk,text='3（固定）',bg=PANEL,fg=MUTED,font=('Helvetica',13,'bold'),anchor='e',bd=0,
                 padx=8,pady=8).grid(row=6,column=1,sticky='e',padx=8,pady=11)
        tk.Label(risk,text='除连续亏损停开次数外，其余风险数值均可修改并保存；单笔风险不再设资金5%硬上限，仍保留日回撤、名义仓位/杠杆与基础合法性校验。',
                 wraplength=760,bg=PANEL,fg=MUTED,font=('Helvetica',10),anchor='w',justify='left').grid(row=7,column=0,columnspan=2,sticky='w',pady=(16,8))
        RoundedButton(risk,text='校验并保存风险设置',command=self.save_settings,variant='accent',width=190).grid(row=8,column=0,columnspan=2,sticky='w')
        add_fields(execution,'交易执行参数',[
            ('leverage','逐仓杠杆 1—10倍'),('cooldown_minutes','平仓后冷却时间 分钟'),
            ('stop_atr','15分钟 ATR 止损倍数 0.6—3'),('score_threshold','自动开仓评分阈值 3.5—10（0.5步进）')])
        fixed=Card(execution,height=150,fill=PANEL_ALT); fixed.grid(row=5,column=0,columnspan=2,sticky='ew',pady=(18,10))
        card_label(fixed.body,text='固定执行成本',color=MUTED,size=10,bold=True).pack(anchor='w')
        card_label(fixed.body,text='Maker 0.02%   ·   Taker 0.05%   ·   滑点预算 0.05%',size=16,bold=True).pack(anchor='w',pady=(7,4))
        card_label(fixed.body,text='费率与滑点预算锁定不可编辑 · TP1=1R平50% · TP2=2R平50% · TP1后SL自动移到成交均价',color=MUTED,size=9).pack(anchor='w')
        RoundedButton(execution,text='校验并保存执行参数',command=self.save_settings,variant='accent',width=190).grid(row=6,column=0,columnspan=2,sticky='w',pady=(8,0))
        self.price=tk.StringVar(value='等待行情')
        self.updated=tk.StringVar(value='尚未连接 · 价格以交易所返回为准')
        quote=Card(dash,height=112); quote.pack(fill='x',pady=(0,10))
        self.quote=quote
        card_label(quote.body,text='BTC-USDT-SWAP',color=MUTED,size=11).pack(anchor='w')
        card_label(quote.body,variable=self.price,size=44,bold=True).pack(anchor='w')
        card_label(quote.body,variable=self.updated,color=MUTED,size=10).pack(anchor='w')
        self.price_history=[]
        self.spark=tk.Canvas(quote.body,width=300,height=72,bg=PANEL,highlightthickness=0)
        self.spark.place(relx=1,y=4,anchor='ne')
        self.spark.create_text(150,36,text='连接后显示行情走势',fill=MUTED,font=('Helvetica',11))
        self.signal=tk.StringVar(value='V1.3.5 最高10分 · 日线EMA位置 + 结构 + 极值回归 · 1H/4H逆势扣分 · 默认≥3.5开仓')
        ttk.Label(dash,textvariable=self.signal,wraplength=1080,style='Muted.TLabel').pack(anchor='w',pady=(0,10))
        cards=ttk.Frame(dash); cards.pack(fill='x',pady=(0,12))
        self.score_vars={}; self.gate_vars={}; self.score_bars={}
        for side in ('做多','做空'):
            surface=Card(cards,height=154); surface.pack(side='left',fill='both',expand=True,padx=4)
            card=surface.body
            color=GREEN if side=='做多' else RED
            card_label(card,text=side+' / LONG' if side=='做多' else side+' / SHORT',color=color,size=14,bold=True).pack(anchor='w')
            self.score_vars[side]=tk.StringVar(value='— / 10')
            self.gate_vars[side]=tk.StringVar(value='等待评分；不是胜率')
            card_label(card,variable=self.score_vars[side],size=36,color=color,bold=True).pack(anchor='w')
            self.score_bars[side]=AnimatedScoreBar(card,maximum=10,color=color,height=9); self.score_bars[side].pack(fill='x',pady=7)
            card_label(card,variable=self.gate_vars[side],color=MUTED,size=9).pack(anchor='w')
        self.plan_vars={k:tk.StringVar(value='—') for k in ('capital','risk','entry','sl','tp1','tp2','qty','loss')}
        self.plan_vars['capital'].set(f"{float(self.fields['capital'].get()):,.2f} USDT")
        self.plan_vars['risk'].set(f"{float(self.fields['risk_pct'].get()):g}%")
        plan_surface=Card(dash,height=360); plan_surface.pack(fill='x',pady=(0,12))
        card_label(plan_surface.body,text='交易计划',size=18,bold=True).pack(anchor='w')
        card_label(plan_surface.body,text='按账户风险与15m ATR动态计算 · TP1后自动移保本',color=MUTED,size=9).pack(anchor='w',pady=(2,10))
        def plan_tile(parent,title,variable,accent='#eef5f7',height=74):
            # Plain Frame avoids the Canvas background gutter that looked like a black border.
            tile=tk.Frame(parent,bg=PANEL_ALT,bd=0,highlightthickness=0,height=height)
            tile.pack_propagate(False)
            card_label(tile,text=title,color=MUTED,size=9,bg=PANEL_ALT).pack(anchor='w',padx=14,pady=(11,0))
            card_label(tile,variable=variable,color=accent,size=16,bold=True,bg=PANEL_ALT).pack(anchor='w',padx=14,pady=(5,8))
            return tile
        top_plan=tk.Frame(plan_surface.body,bg=PANEL); top_plan.pack(fill='x',pady=(0,8))
        plan_tile(top_plan,'账户资金',self.plan_vars['capital'],height=78).pack(side='left',fill='x',expand=True,padx=(0,5))
        plan_tile(top_plan,'单笔风险',self.plan_vars['risk'],height=78).pack(side='left',fill='x',expand=True,padx=(5,0))
        grid=tk.Frame(plan_surface.body,bg=PANEL); grid.pack(fill='x')
        tiles=[('entry','计划入场'),('sl','止损 SL'),('tp1','止盈 TP1 · 50%'),('tp2','止盈 TP2 · 余下50%'),('qty','理论数量'),('loss','最大亏损')]
        for idx,(key,title) in enumerate(tiles):
            tile=plan_tile(grid,title,self.plan_vars[key],accent=GREEN if key in ('tp1','tp2') else RED if key=='sl' else '#eef5f7',height=78)
            tile.grid(row=idx//3,column=idx%3,sticky='ew',padx=4,pady=4)
        for c in range(3):grid.columnconfigure(c,weight=1,uniform='plan')
        detail=Tabs(dash); detail.pack(fill='both',expand=True)
        score_tab=ttk.Frame(detail); indicator_tab=ttk.Frame(detail)
        detail.add(score_tab,text='评分明细'); detail.add(indicator_tab,text='指标数值')
        self.score_table=ScoreTable(score_tab,columns=('long','short','max'),show='tree headings',height=8)
        for key,title in (('#0','已收盘K线 · 评分条件'),('long','做多得分'),('short','做空得分'),('max','最高分')):
            self.score_table.heading(key,text=title,anchor='w'); self.score_table.column(key,width=300 if key=='#0' else 130,anchor='w')
        self.score_scroll=WideScrollbar(score_tab,command=self.score_table.yview,width=20)
        self.score_table.configure(yscrollcommand=self.score_scroll.set); self.score_scroll.pack(side='right',fill='y',padx=(5,0))
        self.score_table.pack(fill='both',expand=True)
        self.matrix=ttk.Treeview(indicator_tab,columns=('d','h','m','f'),show='tree headings',height=7)
        self.matrix.heading('#0',text='指标',anchor='w'); self.matrix.heading('d',text='1日',anchor='w'); self.matrix.heading('h',text='1小时',anchor='w'); self.matrix.heading('m',text='15分钟',anchor='w'); self.matrix.heading('f',text='5分钟',anchor='w')
        self.matrix.column('#0',width=150,anchor='w',stretch=True); self.matrix.column('d',width=145,anchor='w',stretch=True); self.matrix.column('h',width=145,anchor='w',stretch=True); self.matrix.column('m',width=145,anchor='w',stretch=True); self.matrix.column('f',width=145,anchor='w',stretch=True)
        self.indicator_scroll=WideScrollbar(indicator_tab,command=self.matrix.yview,width=20)
        self.matrix.configure(yscrollcommand=self.indicator_scroll.set); self.indicator_scroll.pack(side='right',fill='y',padx=(5,0))
        self.matrix.pack(fill='both',expand=True)
        for field in ('ema5','ema10','ema20','ema50','ema200','rsi','atr','upper','middle','lower','k','d','j'):
            self.matrix.insert('', 'end', iid=field,text=field.upper(),values=('—','—','—','—'))
        self.position=tk.StringVar(value='本程序仓位：无 / 待核对')
        ttk.Label(dash,textvariable=self.position,wraplength=1050).pack(anchor='w',pady=10)
        actions=ttk.Frame(dash); actions.pack(fill='x',pady=8)
        self.trade_button=RoundedButton(actions,text='▶  启动自动交易',command=self.toggle_auto,variant='accent',width=190); self.trade_button.pack(side='left',padx=3)
        RoundedButton(actions,text='仅平本程序仓位',command=self.flatten,variant='danger',width=180).pack(side='left',padx=3)
        RoundedButton(actions,text='核对后解除故障锁',command=self.ack,variant='neutral',width=180).pack(side='left',padx=3)
        ttk.Label(dash,text='规则策略 · 未调用GPT/Gemini · 断网/睡眠后不开新仓，已生效的交易所保护单保留。',wraplength=900,font=('Helvetica',10)).pack(anchor='w',pady=4)
        filterbar=ttk.Frame(root,padding=(15,0)); filterbar.pack(fill='x')
        ttk.Label(filterbar,text='运行日志').pack(side='left')
        self.log_filter=tk.StringVar(value='全部')
        selector=RoundedCombobox(filterbar,textvariable=self.log_filter,values=('全部','警报'),width=10,height=34)
        selector.pack(side='right'); selector.bind('<<ComboboxSelected>>',lambda event:self.render_logs())
        self.log=tk.Text(root,height=3,bg=PANEL,fg='#a9d8bf',font=('Menlo',11),wrap='word',state='disabled',relief='flat',highlightthickness=0,bd=0,padx=12,pady=8)
        self.log.tag_configure('alarm',foreground='#ff9d96'); self.log.tag_configure('log',foreground='#9acbb9')
        self.log.pack(fill='x',padx=15,pady=(0,12))
        # Reserve the footer before allocating the flexible content area.
        book.pack_forget()
        self.log.pack_configure(side='bottom',before=filterbar)
        filterbar.pack_configure(side='bottom')
        book.pack(fill='both',expand=True,padx=15,pady=10)
        book.select(dash_page)
        self.thread=threading.Thread(target=self.worker,daemon=True); self.thread.start()
        root.after(150,self.drain); root.protocol('WM_DELETE_WINDOW',self.quit)

    def refresh_plan_settings(self):
        if not hasattr(self,'plan_vars'):return
        try:self.plan_vars['capital'].set(f"{float(self.fields['capital'].get()):,.2f} USDT")
        except Exception:self.plan_vars['capital'].set('—')
        try:self.plan_vars['risk'].set(f"{float(self.fields['risk_pct'].get()):g}%")
        except Exception:self.plan_vars['risk'].set('—')

    def render_plan(self,data):
        self.refresh_plan_settings()
        if not hasattr(self,'plan_vars') or not data:return
        def px(key):
            try:return f"{float(data[key]):,.2f}"
            except Exception:return '—'
        self.plan_vars['entry'].set(px('px'))
        self.plan_vars['sl'].set(px('sl'))
        self.plan_vars['tp1'].set(px('tp1'))
        self.plan_vars['tp2'].set(px('tp2'))
        self.plan_vars['qty'].set(f"{data.get('btc',0):.6f} BTC · {float(data.get('position_multiplier',1.0)):g}×")
        self.plan_vars['loss'].set(f"{data.get('estimated_loss',0):.2f} USDT")

    def emit(self,kind,data):
        self.events.put((kind,data))

    def submit(self,kind,data=None):
        if self.busy:
            messagebox.showinfo('处理中','请等待当前操作完成'); return
        self.busy=True; self.tasks.put((kind,data))

    def settings(self):
        values={k:float(v.get()) for k,v in self.fields.items()}
        for k in ('leverage','consecutive_losses','cooldown_minutes'):
            if int(values[k])!=values[k]:
                raise Halt(k+'必须是整数')
            values[k]=int(values[k])
        return Settings(**values).validate()

    def save_settings(self):
        try:
            if self.engine and self.engine.enabled:
                raise Halt('先停止自动开仓，再修改设置')
            s=self.settings()
            path=self.settings_path
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as f:
                json.dump(asdict(s),f,indent=2)
            self.refresh_plan_settings()
            self.emit('log','设置校验并保存成功；固定成本参数未开放编辑')
        except Exception as exc:
            messagebox.showerror('设置错误',str(exc))

    def connect(self):
        if self.engine and (self.engine.enabled or (self.engine.store and self.engine.store.data['active'])):
            messagebox.showerror('不可切换连接','先停止并处理现有本程序订单/仓位'); return
        config=(self.host.get(),self.key.get().strip(),self.secret.get().strip(),self.phrase.get(),self.mode.get()=='OKX模拟盘')
        self.submit('connect',config)

    def diagnose(self):
        if self.engine and self.engine.enabled:
            messagebox.showinfo('先停止','请先停止新开仓，再进行网络自检'); return
        config=(self.host.get(),self.key.get().strip(),self.secret.get().strip(),self.phrase.get(),self.mode.get()=='OKX模拟盘')
        self.submit('diagnose',config)

    def render_logs(self):
        self.log.configure(state='normal'); self.log.delete('1.0','end')
        for kind,line in self.log_lines:
            if self.log_filter.get()=='全部' or kind=='alarm': self.log.insert('end',line+'\n',kind)
        self.log.see('end'); self.log.configure(state='disabled')

    def draw_curve(self):
        self.chart.delete('all')
        values=self.history_curve
        if len(values)<2:
            self.chart.create_text(24,65,anchor='w',text='完成首轮模拟交易后显示累计收益曲线',fill='#8fa4b3'); return
        w=max(200,self.chart.winfo_width())-60; h=110
        low=min(values); high=max(values); span=max(high-low,1e-8)
        coords=[]
        for i,value in enumerate(values): coords.extend((30+i*w/(len(values)-1),130-(value-low)/span*h))
        final=values[-1]; color=GREEN if final>0 else RED if final<0 else MUTED
        self.chart.create_line(*coords,fill=color,width=2)
        self.chart.create_text(30,10,anchor='nw',text=f'累计权益变化估算：{final:+.4f} USDT',fill=color)

    def refresh_history(self):
        if not self.engine or not self.engine.store: return
        path=self.engine.store.path.with_suffix('.history.jsonl')
        key=(str(path),path.stat().st_mtime_ns if path.exists() else 0)
        if key!=self.history_key:
            self.emit('history',summarize(path)); self.history_key=key

    def toggle_auto(self):
        if self.engine and self.engine.enabled:
            self.stop()
        else:
            self.arm()

    def update_trade_button(self):
        button=getattr(self,'trade_button',None)
        if not button:
            return
        running=bool(self.engine and self.engine.enabled)
        button.configure(text='■  停止新开仓' if running else '▶  启动自动交易',variant='danger' if running else 'accent')

    def arm(self):
        if not self.engine:
            messagebox.showerror('未连接','先测试连接'); return
        if self.network_paused:
            messagebox.showerror('网络暂停','等待网络恢复并核对账户后再启动'); return
        if self.host.get()!=self.engine.x.host or (self.mode.get()=='OKX模拟盘')!=self.engine.x.demo:
            messagebox.showerror('连接不一致','账户环境已修改，请重新测试连接'); return
        try:
            s=self.settings()
        except Exception as exc:
            messagebox.showerror('设置错误',str(exc)); return
        env='OKX模拟盘' if self.engine.x.demo else '真实账户'
        summary=f'{env} / BTC-USDT-SWAP / 逐仓{s.leverage}倍\n资金预算{s.capital} USDT，最大名义仓位{s.max_notional} USDT\n基础单笔风险≤{min(s.risk_usdt,s.capital*s.risk_pct/100)} USDT；评分仓位倍率1×/1.5×/2×，不再设资金5%硬上限，最终仍受日回撤、最大名义仓位与杠杆约束\n中国时间日回撤{s.daily_loss} USDT，连亏{s.consecutive_losses}次停止新开仓\n止损{s.stop_atr}×15m ATR，止盈TP1=1R平50%，TP2=2R平余下50%；TP1后SL自动移到成交均价\n每个信号可自动下单，无需逐笔确认。\n使用专用子账户；必须确认当地账户有合约/API资格。\n本版本未经过真实资金/真实Mac验收，不保证盈利或止损成交价。'
        token='LIVE' if not self.engine.x.demo else 'DEMO'
        typed=simpledialog.askstring('启动全自动授权',summary+f'\n最高10分；最终评分 ≥ {s.score_threshold:g}/10 才进入开仓风控。3.5–4.5=1×仓位 / 5.0–6.5=1.5× / 7.0–10=2×。1D EMA5/10/20支撑或压力最高分别+1/+2/+3且只取最高；1H与4H明显反向趋势各-1。15m Setup≥0.5、5m Trigger≥0.5；前方结构<1R禁止开仓。\n\n同意上述参数请输入 '+token,parent=self.root)
        if typed==token:
            self.submit('arm',s)

    def stop(self):
        if self.engine:
            self.engine.enabled=False  # immediate flag, even while a read request is pending
        self.update_trade_button()
        self.tasks.put(('stop',None))

    def flatten(self):
        if messagebox.askyesno('真实平仓确认','立即停止新开仓，并以市价平掉本程序管理的BTC逐仓仓位？\n网络错误时不自动重复提交；可能产生滑点。'):
            self.stop(); self.submit('flatten')

    def ack(self):
        if messagebox.askyesno('核对确认','你已在OKX核对所有BTC仓位和普通/策略挂单？\n程序会再次读取；无法核实则拒绝解除。亏损计数不会重置。'):
            self.submit('ack')

    def worker(self):
        while not self.finished.is_set():
            try:
                kind,data=self.tasks.get(timeout=5)
            except queue.Empty:
                kind,data='tick',None
            try:
                if self.network_paused and kind=='tick' and self.engine:
                    if time.monotonic()-self.probe_at<30: continue
                    self.probe_at=time.monotonic()
                    self.engine.x.sync_time()
                    if self.engine.x.account().get('uid')!=self.engine.connection_id:
                        raise Halt('恢复后的账户标识不匹配')
                    self.engine.x.balance(); self.engine.x.positions(); self.engine.x.orders(); self.engine.x.algos()
                    self.recovery_count+=1
                    self.emit('network',f'恢复核对 {self.recovery_count}/2 · 不开仓')
                    if self.recovery_count<2: continue
                    if self.engine.store.data['active']: self.engine.reconcile()
                    self.engine.store.data['last_bar']=int(self.engine.market_now()//300)*300000-300000
                    self.engine.store.save()
                    self.network_paused=False
                    self.emit('log','网络已恢复并核对账户，仅恢复观察；请核对解除故障锁后重新授权，不补发错过的订单')
                if kind=='diagnose':
                    x=Exchange(*data[:4],demo=data[4]); x.network_event=lambda message:self.emit('network',message)
                    x.sync_time(); self.emit('log','网络自检：公共时间接口通过')
                    if all(data[1:4]):
                        x.account(); self.emit('log','网络自检：账户只读认证通过')
                    else: self.emit('log','未填写完整密钥，跳过账户自检')
                    continue
                if kind=='connect':
                    x=Exchange(*data[:4],demo=data[4]); e=Engine(x,self.folder,self.emit)
                    x.network_event=lambda message:self.emit('network',message)
                    e.connect(); self.engine=e
                    self.network_paused=False; self.recovery_count=0
                    self.history_key=None; self.refresh_history()
                    self.emit('log','开始加载1D / 4H / 1H / 15m / 5m指标历史K线，首次可能需数十秒')
                    e.refresh_market()
                elif kind=='arm':
                    if not self.engine: raise Halt('先连接')
                    self.engine.market=None
                    self.engine.arm(data)
                elif kind=='stop' and self.engine:
                    self.engine.stop()
                elif kind=='flatten' and self.engine:
                    self.engine.flatten()
                elif kind=='ack' and self.engine:
                    self.engine.acknowledge()
                if self.engine:
                    self.engine.cycle()
                    self.refresh_history()
                    if time.monotonic()-self.public_at>5:
                        self.emit('ticker',self.engine.x.ticker()); self.public_at=time.monotonic()
                    self.emit('status','全自动运行 / '+('模拟盘' if self.engine.x.demo else '实盘') if self.engine.enabled else '故障暂停 · 需核对' if self.engine.store.data['halt'] else '已停止新开仓 / 继续核对持仓')
            except CandlePending as exc:
                message=str(exc)
                self.emit('status','等待最新收盘K线 · 暂不新开仓')
                self.emit('candle_wait',message)
                # A just-closed OKX candle normally needs a few seconds to receive confirm=1.
                # Keep that visible in the status area, but do not write a scary log every
                # quarter-hour. Only persistent/confirmed lag belongs in the event log.
                persistent=('持续过期' in message or '连续异常' in message)
                if persistent and time.monotonic()-self.candle_wait_log>=15:
                    self.emit('log',message); self.candle_wait_log=time.monotonic()
            except NetworkError as exc:
                self.network_paused=True; self.recovery_count=0; self.probe_at=time.monotonic()
                if self.engine: self.engine.halt(str(exc))
                else: self.emit('alarm',str(exc))
            except Exception as exc:
                if self.engine:
                    self.engine.halt(str(exc))
                else:
                    self.emit('alarm',str(exc))
            finally:
                if kind!='tick': self.emit('done',None)

    def drain(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='done': self.busy=False; self.update_trade_button()
                elif kind=='history':
                    rate='—' if data['win_rate'] is None else f"{data['win_rate']:.1f}%"
                    self.performance.set(f"累计 {data['total']:+.4f} USDT   |   胜率 {rate}   |   已平 {data['count']}轮   盈 {data['wins']} / 亏 {data['losses']} / 平 {data['breakeven']}")
                    self.performance_label.configure(fg=GREEN if data['total']>0 else RED if data['total']<0 else MUTED)
                    self.history_curve=data['curve']; self.draw_curve()
                    for item in self.history_table.get_children(): self.history_table.delete(item)
                    for row in reversed(data['rows']):
                        tag='win' if row['pnl']>0 else 'loss' if row['pnl']<0 else 'flat'
                        self.history_table.insert('','end',text=row['time'][:19].replace('T',' '),values=(row['side'],f"{row['pnl']:+.4f}",f"{row['cumulative']:+.4f}",row['client_id']),tags=(tag,))
                    if data['skipped']: self.emit('log',f"历史记录有 {data['skipped']} 行损坏，已跳过；统计可能不完整")
                elif kind=='network': self.network.set('网络：'+data)
                elif kind=='status': self.status.set(data); self.update_trade_button()
                elif kind=='candle_wait': self.signal.set(str(data))
                elif kind=='ticker':
                    self.price.set(f"{float(data['last']):,.2f} USDT")
                    self.updated.set('最近更新 '+time.strftime('%Y-%m-%d %H:%M:%S'))
                    self.price_history=(self.price_history+[float(data['last'])])[-120:]
                    self.spark.delete('all')
                    if len(self.price_history)>1:
                        low=min(self.price_history); span=max(max(self.price_history)-low,1e-8)
                        points=[]
                        for i,v in enumerate(self.price_history):
                            points.extend((6+288*i/(len(self.price_history)-1),58-44*(v-low)/span))
                        self.spark.create_line(*points,fill=GREEN,width=2)
                    self.spark.create_text(294,70,anchor='se',text='本次连接 · 最近行情采样',fill=MUTED,font=('Helvetica',9))
                elif kind=='market':
                    self.signal.set(time.strftime('%m-%d %H:%M',time.localtime((data['bar']+300000)/1000))+' 5m已收盘｜'+data['side']+'｜'+data['why'])
                    for side,score in data.get('scores',{}).items():
                        self.score_vars[side].set(f"{score['total']:g} / {data.get('score_max',10):g}")
                        self.score_bars[side]['value']=score['total']
                        self.gate_vars[side].set(score['reason'])
                    for item in self.score_table.get_children(): self.score_table.delete(item)
                    scores=data.get('scores',{})
                    if scores:
                        for a,b in zip(scores['做多']['items'],scores['做空']['items']):
                            self.score_table.insert('','end',text=a[0],values=(a[1],b[1],a[2]))
                    for k in self.matrix.get_children():
                        self.matrix.item(k,values=(f"{data['d'][k]:,.2f}",f"{data['h'][k]:,.2f}",f"{data['m'][k]:,.2f}",f"{data['f'][k]:,.2f}"))
                elif kind=='plan':
                    self.position.set(f"当前计划：{data['side']} · {float(data.get('position_multiplier',1.0)):g}×仓位 · 入场 {data['px']} · SL {data['sl']} · TP1 {data['tp1']} / TP2 {data['tp2']}")
                    self.render_plan(data)
                elif kind=='position':
                    self.position.set('交易所持仓：'+' / '.join(f"{p['posSide']} {p['pos']}张 · 浮盈亏 {p.get('upl','—')} USDT" for p in data))
                elif kind=='account':
                    self.price_history=[]
                    self.price.set('等待行情'); self.updated.set('已连接 · 等待最新成交价')
                    self.spark.delete('all')
                    self.environment.set('已连接：'+data['environment'])
                    self.env_label.configure(bg='#173c31' if data['environment']=='OKX模拟盘' else '#622f37',fg='#65eeb4' if data['environment']=='OKX模拟盘' else '#ffb4b4')
                    self.equity.set(f"连接时USDT权益：{data['equity']:,.2f}")
                    self.account_view.delete('1.0','end'); self.account_view.insert('end',json.dumps(data,ensure_ascii=False,indent=2))
                elif kind in ('log','alarm'):
                    line=time.strftime('%Y-%m-%d %H:%M:%S')+' '+('警报：' if kind=='alarm' else '')+str(data)
                    self.log_lines.append((kind,line)); self.log_lines=self.log_lines[-500:]
                    self.render_logs()
                    with (self.folder/'events.log').open('a') as f:
                        f.write(line+'\n')
                    os.chmod(self.folder/'events.log',0o600)
                    if kind=='alarm':
                        self.status.set('故障锁定：停止新开仓'); self.update_trade_button(); self.root.bell()
        except queue.Empty:
            pass
        self.root.after(150,self.drain)

    def quit(self):
        if not messagebox.askyesno('退出','退出后不再监控或开仓。已生效的交易所TP/SL继续保留。\n如有未确认请求或保护单异常，请先到OKX核对。确定退出？'):
            return
        if self.engine: self.engine.enabled=False
        self.finished.set(); self.root.destroy()

def main():
    root=tk.Tk()
    try:
        app=App(root)
    except Exception as exc:
        messagebox.showerror('启动失败',str(exc)); root.destroy(); return
    root.mainloop()

if __name__=='__main__':
    main()

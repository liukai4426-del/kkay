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
from visual import theme, Card, Tabs, mark, BG, PANEL, MUTED, GREEN, RED

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
        self.log_lines=[]
        self.history_key=None; self.history_curve=[]
        self.root.title('OKX Local 1.2 · BTC 策略控制台'); self.root.geometry('1200x920'); self.root.minsize(1040,840)
        style=ttk.Style(); style.theme_use('clam')
        style.configure('.',font=('Helvetica',13),background='#101820',foreground='#e5eef5')
        style.configure('TEntry',fieldbackground='#192832',foreground='#e5eef5',padding=7,insertcolor='white')
        style.configure('TCombobox',fieldbackground='#192832',foreground='#e5eef5',padding=5)
        style.map('TCombobox',fieldbackground=[('readonly','#192832')],foreground=[('readonly','#e5eef5')])
        style.configure('Treeview',background='#15212b',fieldbackground='#15212b',foreground='#dce7ee',rowheight=23,borderwidth=0)
        style.configure('Horizontal.TProgressbar',troughcolor='#0d1921',background='#18e7a4',bordercolor='#0d1921',lightcolor='#18e7a4',darkcolor='#18e7a4',thickness=7)
        style.configure('Treeview.Heading',background='#20303c',foreground='#95aab7',font=('Helvetica',11,'bold'))
        style.map('Treeview',background=[('selected','#254b53')],foreground=[('selected','#ffffff')])
        style.configure('TNotebook',background='#101820',borderwidth=0)
        style.map('TNotebook.Tab',background=[('selected','#203a40'),('!selected','#17232e')],foreground=[('selected','#41e5af'),('!selected','#9caebb')])
        style.configure('Card.TFrame',background='#172630')
        style.configure('Card.TLabel',background='#172630',foreground='#a5b7c3')
        style.configure('Score.TLabel',background='#172630',foreground='#41e5af',font=('Helvetica',25,'bold'))
        style.configure('Accent.TButton',background='#205844',foreground='#7bffd0')
        style.configure('Danger.TButton',background='#532e39',foreground='#ffb4b4')
        style.configure('TButton',padding=8,background='#224255',foreground='white')
        style.configure('Title.TLabel',font=('Helvetica',23,'bold'),foreground='#41e5af')
        style.configure('TNotebook.Tab',padding=(18,10))
        theme(root)
        top=ttk.Frame(root,padding=15); top.pack(fill='x')
        mark(top).pack(side='left',padx=(0,12))
        brand=ttk.Frame(top); brand.pack(side='left')
        ttk.Label(brand,text='OKX Local',style='Title.TLabel').pack(anchor='w')
        ttk.Label(brand,text='BTC / USDT   ·   V1.2 视觉版',style='Muted.TLabel').pack(anchor='w')
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
        note='本机执行 · 逐仓 / 单策略仓位 / 每单TP+SL · 测试版，尚未完成账户端到端验收'
        ttk.Label(root,text=note,padding=(15,5)).pack(fill='x')
        book=Tabs(root); book.pack(fill='both',expand=True,padx=15,pady=10)
        self.book=book
        # Raise the selected pane explicitly: Aqua Tk can leave a newly mapped
        # notebook pane behind its siblings even while inputs report mapped.
        book.bind('<<NotebookTabChanged>>',lambda event: root.nametowidget(book.select()).lift() if book.select() else None)
        connection=ttk.Frame(book,padding=16); risk=ttk.Frame(book,padding=16); dash=ttk.Frame(book,padding=16)
        book.add(connection,text='连接设置'); book.add(risk,text='风险参数'); book.add(dash,text='交易总览')
        risk_body=tk.Frame(risk,bg=BG)
        risk_body.pack(fill='both',expand=True)
        risk=risk_body
        history_tab=ttk.Frame(book,padding=16); book.add(history_tab,text='历史收益')
        ttk.Label(history_tab,text='收益与胜率 / 本程序已平仓轮次',style='Title.TLabel').pack(anchor='w')
        self.performance=tk.StringVar(value='等待连接账户 · 暂无记录')
        ttk.Label(history_tab,textvariable=self.performance,font=('Helvetica',17),padding=(0,15)).pack(anchor='w')
        ttk.Label(history_tab,text='收益口径：账户USDT权益变化估算，含费用/资金费；入出金和其他交易也会影响结果。\n胜率 = 盈利轮次 ÷ 全部已平仓轮次（含持平）；未成交订单与未平仓交易不计入。',wraplength=950).pack(anchor='w')
        self.chart=tk.Canvas(history_tab,height=160,bg='#101d26',highlightthickness=0)
        self.chart.pack(fill='x',pady=14); self.chart.bind('<Configure>',lambda event:self.draw_curve())
        self.history_table=ttk.Treeview(history_tab,columns=('side','pnl','total','id'),show='tree headings',height=9)
        for key,title,width in (('#0','平仓记录时间（UTC）',210),('side','方向',80),('pnl','权益变化 USDT',140),('total','累计 USDT',140),('id','本程序订单号',240)):
            self.history_table.heading(key,text=title); self.history_table.column(key,width=width)
        hs=ttk.Scrollbar(history_tab,orient='vertical',command=self.history_table.yview)
        self.history_table.configure(yscrollcommand=hs.set); hs.pack(side='right',fill='y')
        self.history_table.pack(fill='both',expand=True)
        self.history_table.tag_configure('win',foreground='#41e5af'); self.history_table.tag_configure('loss',foreground='#ff8b9d')
        self.host=tk.StringVar(value=HOSTS[0]); self.mode=tk.StringVar(value='OKX模拟盘')
        self.key=tk.StringVar(); self.secret=tk.StringVar(); self.phrase=tk.StringVar()
        self.connection_widgets=[]
        rows=[('账户官方域名',self.host,HOSTS),('环境',self.mode,('OKX模拟盘','真实账户')),
              ('API Key',self.key,None),('Secret Key',self.secret,None),('Passphrase',self.phrase,None)]
        for i,(label,var,values) in enumerate(rows):
            ttk.Label(connection,text=label).grid(row=i,column=0,sticky='w',pady=8)
            widget=ttk.Combobox(connection,textvariable=var,values=values,state='readonly',width=48) if values else ttk.Entry(connection,textvariable=var,show='•',width=50)
            widget.grid(row=i,column=1,sticky='ew',padx=12,pady=8); self.connection_widgets.append(widget)
        connection.columnconfigure(1,weight=1)
        text=('密钥仅保存在此次运行内存中，退出后需重新填写；不会发送给GPT/Gemini。\n'
              '使用专用交易子账户，不要与手动交易/其他机器人共用BTC仓位。\n'
              '先用读取权限测试连接；自动交易需读取+交易权限，禁止提币权限。\n'
              '模拟与真实账户密钥不可混用。地区/产品不支持时停止，不绕过限制。\n'
              '“测试连接”只读取账户与持仓，不下单。程序不接入原Sites网页。')
        ttk.Label(connection,text=text,wraplength=800,justify='left').grid(row=6,column=0,columnspan=2,sticky='w',pady=20)
        ttk.Button(connection,text='测试连接（只读）',command=self.connect).grid(row=7,column=1,sticky='w')
        ttk.Button(connection,text='网络自检（不下单）',command=self.diagnose).grid(row=7,column=0,sticky='w')
        self.account_view=tk.Text(connection,height=9,wrap='word',bg='#0b1118',fg='#c8e5f5',font=('Menlo',12))
        self.account_view.grid(row=8,column=0,columnspan=2,sticky='nsew',pady=16); connection.rowconfigure(8,weight=1)
        defaults=asdict(Settings())
        # Separate V1.1 risk preferences; preserve all account state and locks.
        settings_path=folder/'settings-v1.1.json'
        if settings_path.exists():
            try:
                loaded=json.loads(settings_path.read_text())
                defaults.update({k:v for k,v in loaded.items() if k in defaults})
            except Exception:
                pass
        self.fields={}
        labels={'capital':'策略资金预算 USDT','max_notional':'最大名义仓位 USDT（不是保证金）',
                'leverage':'逐仓杠杆 1—10倍','risk_usdt':'单笔预估亏损上限 USDT',
                'risk_pct':'单笔预估亏损上限 %（取较小值）','daily_loss':'UTC日内权益回撤上限 USDT',
                'consecutive_losses':'连续亏损停机次数','cooldown_minutes':'平仓后冷却时间 分钟',
                'stop_atr':'1小时ATR止损倍数 0.6—3','reward_r':'止盈距离 / 止损距离 1—5',
                'fee_bps':'单边手续费预算 bps（10=0.1%）','slippage_bps':'FOK限价偏移 / SL滑点预算 bps',
                'score_threshold':'自动开仓评分阈值 7—10（整数）'}
        for i,(name,label) in enumerate(labels.items()):
            col=0 if i<7 else 2; row=i%7
            tk.Label(risk,text=label,wraplength=260,bg=BG,fg='#e5eef5',font=('Helvetica',13)).grid(row=row,column=col,sticky='w',padx=6,pady=12)
            v=tk.StringVar(value=str(defaults[name])); self.fields[name]=v
            tk.Entry(risk,textvariable=v,width=12,bg='#192832',fg='#e5eef5',insertbackground='white',font=('Helvetica',14),highlightthickness=1,highlightbackground='#49606c',relief='flat').grid(row=row,column=col+1,padx=8,pady=12,ipady=7)
        ttk.Label(risk,text='停止后修改，下次启动生效。运行时不更改已有止盈止损。\n日亏损包含浮动盈亏/资金费及资金出入影响；达到上限暂停新开仓，不保证按上限成交。\n同一时间仅一个BTC仓位；单个TP目标全平，无分批止盈。',wraplength=850).grid(row=7,column=0,columnspan=4,sticky='w',pady=18)
        ttk.Button(risk,text='校验并保存设置（不含密钥）',command=self.save_settings).grid(row=8,column=0,columnspan=4,sticky='w')
        self.price=tk.StringVar(value='等待行情')
        self.updated=tk.StringVar(value='尚未连接 · 价格以交易所返回为准')
        quote=Card(dash,height=112); quote.pack(fill='x',pady=(0,10))
        self.quote=quote
        from visual import label as card_label
        card_label(quote.body,text='BTC-USDT-SWAP',color=MUTED,size=11).pack(anchor='w')
        card_label(quote.body,variable=self.price,size=32,bold=True).pack(anchor='w')
        card_label(quote.body,variable=self.updated,color=MUTED,size=10).pack(anchor='w')
        self.price_history=[]
        self.spark=tk.Canvas(quote.body,width=300,height=72,bg=PANEL,highlightthickness=0)
        self.spark.place(relx=1,y=4,anchor='ne')
        self.spark.create_text(150,36,text='连接后显示行情走势',fill=MUTED,font=('Helvetica',11))
        self.signal=tk.StringVar(value='极值反转评分 · 等待已收盘1小时/15分钟K线')
        ttk.Label(dash,textvariable=self.signal,wraplength=1080,style='Muted.TLabel').pack(anchor='w',pady=(0,10))
        cards=ttk.Frame(dash); cards.pack(fill='x',pady=(0,12))
        self.score_vars={}; self.gate_vars={}; self.score_bars={}
        for side in ('做多','做空'):
            surface=Card(cards,height=132); surface.pack(side='left',fill='both',expand=True,padx=4)
            card=surface.body
            color=GREEN if side=='做多' else RED
            card_label(card,text=side+' / LONG' if side=='做多' else side+' / SHORT',color=color,size=11,bold=True).pack(anchor='w')
            self.score_vars[side]=tk.StringVar(value='— / 10')
            self.gate_vars[side]=tk.StringVar(value='等待评分；不是胜率')
            card_label(card,variable=self.score_vars[side],size=28,color=color,bold=True).pack(anchor='w')
            self.score_bars[side]=ttk.Progressbar(card,maximum=10,style=('Long' if side=='做多' else 'Short')+'.Horizontal.TProgressbar'); self.score_bars[side].pack(fill='x',pady=5)
            card_label(card,variable=self.gate_vars[side],color=MUTED,size=10).pack(anchor='w')
        detail=Tabs(dash); detail.pack(fill='both',expand=True)
        score_tab=ttk.Frame(detail); indicator_tab=ttk.Frame(detail)
        detail.add(score_tab,text='评分明细'); detail.add(indicator_tab,text='指标数值')
        self.score_table=ttk.Treeview(score_tab,columns=('long','short','max'),show='tree headings',height=7)
        for key,title in (('#0','已收盘K线 · 评分条件'),('long','做多得分'),('short','做空得分'),('max','最高分')):
            self.score_table.heading(key,text=title); self.score_table.column(key,width=300 if key=='#0' else 130)
        score_scroll=ttk.Scrollbar(score_tab,orient='vertical',command=self.score_table.yview)
        self.score_table.configure(yscrollcommand=score_scroll.set); score_scroll.pack(side='right',fill='y')
        self.score_table.pack(fill='both',expand=True)
        self.matrix=ttk.Treeview(indicator_tab,columns=('h','m'),show='tree headings',height=7)
        self.matrix.heading('#0',text='指标'); self.matrix.heading('h',text='1小时'); self.matrix.heading('m',text='15分钟')
        self.matrix.column('#0',width=180); self.matrix.column('h',width=200); self.matrix.column('m',width=200)
        scroll=ttk.Scrollbar(indicator_tab,orient='vertical',command=self.matrix.yview)
        self.matrix.configure(yscrollcommand=scroll.set); scroll.pack(side='right',fill='y')
        self.matrix.pack(fill='both',expand=True)
        for field in ('ema20','ema50','ema200','rsi','atr','upper','middle','lower','k','d','j'):
            self.matrix.insert('', 'end', iid=field,text=field.upper(),values=('—','—'))
        self.position=tk.StringVar(value='本程序仓位：无 / 待核对')
        ttk.Label(dash,textvariable=self.position,wraplength=1050).pack(anchor='w',pady=10)
        actions=ttk.Frame(dash); actions.pack(fill='x',pady=8)
        ttk.Button(actions,text='▶  启动自动交易',command=self.arm,style='Accent.TButton').pack(side='left',padx=3)
        ttk.Button(actions,text='停止新开仓',command=self.stop).pack(side='left',padx=3)
        ttk.Button(actions,text='仅平本程序仓位',command=self.flatten,style='Danger.TButton').pack(side='left',padx=3)
        ttk.Button(actions,text='核对后解除故障锁',command=self.ack).pack(side='left',padx=3)
        ttk.Label(dash,text='规则策略 · 未调用GPT/Gemini · 断网/睡眠后不开新仓，已生效的交易所保护单保留。',wraplength=900,font=('Helvetica',10)).pack(anchor='w',pady=4)
        filterbar=ttk.Frame(root,padding=(15,0)); filterbar.pack(fill='x')
        ttk.Label(filterbar,text='运行日志').pack(side='left')
        self.log_filter=tk.StringVar(value='全部')
        selector=ttk.Combobox(filterbar,textvariable=self.log_filter,values=('全部','警报'),state='readonly',width=10)
        selector.pack(side='right'); selector.bind('<<ComboboxSelected>>',lambda event:self.render_logs())
        self.log=tk.Text(root,height=3,bg=PANEL,fg='#a9d8bf',font=('Menlo',11),wrap='word',state='disabled',relief='flat',highlightthickness=1,highlightbackground='#223038',padx=12,pady=8)
        self.log.tag_configure('alarm',foreground='#ff9d96'); self.log.tag_configure('log',foreground='#9acbb9')
        self.log.pack(fill='x',padx=15,pady=(0,12))
        # Reserve the footer before allocating the flexible content area.
        book.pack_forget()
        self.log.pack_configure(side='bottom',before=filterbar)
        filterbar.pack_configure(side='bottom')
        book.pack(fill='both',expand=True,padx=15,pady=10)
        book.select(dash)
        self.thread=threading.Thread(target=self.worker,daemon=True); self.thread.start()
        root.after(150,self.drain); root.protocol('WM_DELETE_WINDOW',self.quit)

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
            path=self.folder/'settings-v1.1.json'
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as f:
                json.dump(asdict(s),f,indent=2)
            self.emit('log','设置校验并保存成功；不包含密钥')
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
        self.chart.create_line(*coords,fill='#41e5af',width=2)
        self.chart.create_text(30,10,anchor='nw',text=f'累计权益变化估算：{values[-1]:+.4f} USDT',fill='#cfe9e0')

    def refresh_history(self):
        if not self.engine or not self.engine.store: return
        path=self.engine.store.path.with_suffix('.history.jsonl')
        key=(str(path),path.stat().st_mtime_ns if path.exists() else 0)
        if key!=self.history_key:
            self.emit('history',summarize(path)); self.history_key=key

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
        summary=f'{env} / BTC-USDT-SWAP / 逐仓{s.leverage}倍\n资金预算{s.capital} USDT，最大名义仓位{s.max_notional} USDT\n单笔风险≤{min(s.risk_usdt,s.capital*s.risk_pct/100)} USDT（估计）\nUTC日回撤{s.daily_loss} USDT，连亏{s.consecutive_losses}次停止新开仓\n止损{s.stop_atr}×ATR，止盈{s.reward_r}R\n每个信号可自动下单，无需逐笔确认。\n使用专用子账户；必须确认当地账户有合约/API资格。\n本版本未经过真实资金/真实Mac验收，不保证盈利或止损成交价。'
        token='LIVE' if not self.engine.x.demo else 'DEMO'
        typed=simpledialog.askstring('启动全自动授权',summary+f'\nRSI双周期硬条件 + 评分 ≥ {s.score_threshold:g}/10\n\n同意上述参数请输入 '+token,parent=self.root)
        if typed==token:
            self.submit('arm',s)

    def stop(self):
        if self.engine:
            self.engine.enabled=False  # immediate flag, even while a read request is pending
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
                    self.engine.store.data['last_bar']=int(time.time()//900)*900000-900000
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
                    self.emit('log','开始加载指标历史K线，首次可能需数十秒')
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
                if kind=='done': self.busy=False
                elif kind=='history':
                    rate='—' if data['win_rate'] is None else f"{data['win_rate']:.1f}%"
                    self.performance.set(f"累计 {data['total']:+.4f} USDT   |   胜率 {rate}   |   已平 {data['count']}轮   盈 {data['wins']} / 亏 {data['losses']} / 平 {data['breakeven']}")
                    self.history_curve=data['curve']; self.draw_curve()
                    for item in self.history_table.get_children(): self.history_table.delete(item)
                    for row in reversed(data['rows']):
                        self.history_table.insert('','end',text=row['time'][:19].replace('T',' '),values=(row['side'],f"{row['pnl']:+.4f}",f"{row['cumulative']:+.4f}",row['client_id']),tags=('win' if row['pnl']>=0 else 'loss',))
                    if data['skipped']: self.emit('log',f"历史记录有 {data['skipped']} 行损坏，已跳过；统计可能不完整")
                elif kind=='network': self.network.set('网络：'+data)
                elif kind=='status': self.status.set(data)
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
                    self.signal.set(time.strftime('%m-%d %H:%M',time.localtime((data['bar']+900000)/1000))+' 已收盘｜'+data['side']+'｜'+data['why'])
                    for side,score in data.get('scores',{}).items():
                        self.score_vars[side].set(f"{score['total']} / 10")
                        self.score_bars[side]['value']=score['total']
                        self.gate_vars[side].set(score['reason'])
                    for item in self.score_table.get_children(): self.score_table.delete(item)
                    scores=data.get('scores',{})
                    if scores:
                        for a,b in zip(scores['做多']['items'],scores['做空']['items']):
                            self.score_table.insert('','end',text=a[0],values=(a[1],b[1],a[2]))
                    for k in self.matrix.get_children():
                        self.matrix.item(k,values=(f"{data['h'][k]:,.2f}",f"{data['m'][k]:,.2f}"))
                elif kind=='plan': self.position.set('本次计划：'+json.dumps(data,ensure_ascii=False))
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
                        self.status.set('故障锁定：停止新开仓'); self.root.bell()
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

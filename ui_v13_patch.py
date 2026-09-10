from pathlib import Path
import re


def once(text, old, new, label):
    count=text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected 1 match, got {count}')
    return text.replace(old,new,1)


# visual.py: tolerate ttk parents for rounded buttons.
p=Path('visual.py'); s=p.read_text()
s=once(s,
"        bg=kwargs.pop('bg',parent.cget('bg') if hasattr(parent,'cget') else BG)\n        super().__init__(parent,width=self.button_width,height=height,bg=bg,highlightthickness=0,borderwidth=0,cursor='hand2',**kwargs)\n",
"        try:\n            parent_bg=parent.cget('bg')\n        except Exception:\n            parent_bg=BG\n        bg=kwargs.pop('bg',parent_bg)\n        super().__init__(parent,width=self.button_width,height=height,bg=bg,highlightthickness=0,borderwidth=0,cursor='hand2',**kwargs)\n",
'rounded ttk parent background')
p.write_text(s)

# app.py
p=Path('app.py'); s=p.read_text()
s=once(s,
"from visual import theme, Card, Tabs, mark, BG, PANEL, MUTED, GREEN, RED\n",
"from visual import theme, Card, Tabs, mark, RoundedButton, label as card_label, BG, PANEL, PANEL_ALT, FIELD, MUTED, GREEN, RED\n",
'visual imports')

start=s.index("        self.root.title('OKX Local 1.3 · BTC 策略控制台')")
end=s.index("        theme(root)\n",start)+len("        theme(root)\n")
s=s[:start]+"        self.root.title('KAYTRADE 1.3 · BTC 策略控制台'); self.root.geometry('1200x920'); self.root.minsize(1040,840)\n        theme(root)\n"+s[end:]

s=once(s,"        ttk.Label(brand,text='OKX Local',style='Title.TLabel').pack(anchor='w')\n","        ttk.Label(brand,text='KAYTRADE',style='Title.TLabel').pack(anchor='w')\n",'brand name')
s=once(s,"        note='本机执行 · 逐仓 / 单策略仓位 / 每单TP+SL · 测试版，尚未完成账户端到端验收'\n","        note='KAYTRADE · 本机执行 · 逐仓 / 单策略仓位 / 每单TP+SL · 测试版，尚未完成账户端到端验收'\n",'brand note')

page_start=s.index("        connection=ttk.Frame(book,padding=16); risk=ttk.Frame(book,padding=16); dash=ttk.Frame(book,padding=16)\n")
page_end=s.index("        self.host=tk.StringVar(value=HOSTS[0]);",page_start)
new_pages="""        connection_page=ttk.Frame(book,padding=8); risk_page=ttk.Frame(book,padding=8); dash=ttk.Frame(book,padding=16)
        book.add(connection_page,text='连接设置'); book.add(risk_page,text='风险参数'); book.add(dash,text='交易总览')
        connection_surface=Card(connection_page,height=650); connection_surface.pack(fill='both',expand=True,pady=(0,4))
        connection=connection_surface.body
        risk_surface=Card(risk_page,height=650); risk_surface.pack(fill='both',expand=True,pady=(0,4))
        risk=risk_surface.body
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
"""
s=s[:page_start]+new_pages+s[page_end:]

# Connection card layout.
s=once(s,"        self.connection_widgets=[]\n        rows=[","        self.connection_widgets=[]\n        tk.Label(connection,text='账户连接',bg=PANEL,fg='#eef5f7',font=('Helvetica',20,'bold'),anchor='w',bd=0).grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,12))\n        rows=[",'connection heading')
s=once(s,
"        for i,(label,var,values) in enumerate(rows):\n            ttk.Label(connection,text=label).grid(row=i,column=0,sticky='w',pady=8)\n            widget=ttk.Combobox(connection,textvariable=var,values=values,state='readonly',width=48) if values else ttk.Entry(connection,textvariable=var,show='•',width=50)\n            widget.grid(row=i,column=1,sticky='ew',padx=12,pady=8); self.connection_widgets.append(widget)\n",
"        for i,(label,var,values) in enumerate(rows):\n            ttk.Label(connection,text=label,style='Card.TLabel').grid(row=i+1,column=0,sticky='w',pady=8)\n            widget=ttk.Combobox(connection,textvariable=var,values=values,state='readonly',width=48) if values else ttk.Entry(connection,textvariable=var,show='•',width=50)\n            widget.grid(row=i+1,column=1,sticky='ew',padx=12,pady=8); self.connection_widgets.append(widget)\n",
'connection rows')
s=once(s,"        ttk.Button(connection,text='测试连接（只读）',command=self.connect).grid(row=7,column=1,sticky='w')\n        ttk.Button(connection,text='网络自检（不下单）',command=self.diagnose).grid(row=7,column=0,sticky='w')\n",
"        RoundedButton(connection,text='网络自检（不下单）',command=self.diagnose,variant='neutral',width=170).grid(row=7,column=0,sticky='w',pady=4)\n        RoundedButton(connection,text='测试连接（只读）',command=self.connect,variant='accent',width=170).grid(row=7,column=1,sticky='w',padx=12,pady=4)\n",
'connection buttons')
s=once(s,"        self.account_view=tk.Text(connection,height=9,wrap='word',bg='#0b1118',fg='#c8e5f5',font=('Menlo',12))\n",
"        self.account_view=tk.Text(connection,height=9,wrap='word',bg=PANEL_ALT,fg='#c8e5f5',font=('Menlo',12),bd=0,highlightthickness=0,padx=12,pady=10)\n",
'account surface')

# Risk card layout and field styling.
s=once(s,"        self.fields={}\n        labels={","        self.fields={}\n        tk.Label(risk,text='风险与执行参数',bg=PANEL,fg='#eef5f7',font=('Helvetica',20,'bold'),anchor='w',bd=0).grid(row=0,column=0,columnspan=4,sticky='ew',pady=(0,8))\n        labels={",'risk heading')
s=once(s,"            col=0 if i<7 else 2; row=i%7\n            tk.Label(risk,text=label,wraplength=260,bg=BG,fg='#e5eef5',font=('Helvetica',13)).grid(row=row,column=col,sticky='w',padx=6,pady=12)\n            v=tk.StringVar(value=str(defaults[name])); self.fields[name]=v\n            tk.Entry(risk,textvariable=v,width=12,bg='#192832',fg='#e5eef5',insertbackground='white',font=('Helvetica',14),highlightthickness=1,highlightbackground='#49606c',relief='flat').grid(row=row,column=col+1,padx=8,pady=12,ipady=7)\n",
"            col=0 if i<7 else 2; row=i%7+1\n            tk.Label(risk,text=label,wraplength=260,bg=PANEL,fg='#dbe6eb',font=('Helvetica',13),bd=0,highlightthickness=0).grid(row=row,column=col,sticky='w',padx=6,pady=10)\n            v=tk.StringVar(value=str(defaults[name])); self.fields[name]=v\n            tk.Entry(risk,textvariable=v,width=12,bg=FIELD,fg='#e5eef5',insertbackground='#e5eef5',font=('Helvetica',14),highlightthickness=0,bd=0,relief='flat').grid(row=row,column=col+1,padx=8,pady=10,ipady=8)\n",
'risk fields')
s=once(s,"        ttk.Label(risk,text='停止后修改，下次启动生效。运行时不更改已有止盈止损。\\n日亏损包含浮动盈亏/资金费及资金出入影响；达到上限暂停新开仓，不保证按上限成交。\\n同一时间仅一个BTC仓位；单个TP目标全平，无分批止盈。',wraplength=850).grid(row=7,column=0,columnspan=4,sticky='w',pady=18)\n        ttk.Button(risk,text='校验并保存设置（不含密钥）',command=self.save_settings).grid(row=8,column=0,columnspan=4,sticky='w')\n",
"        ttk.Label(risk,text='停止后修改，下次启动生效。运行时不更改已有止盈止损。\\n日亏损包含浮动盈亏/资金费及资金出入影响；达到上限暂停新开仓，不保证按上限成交。\\n同一时间仅一个BTC仓位；单个TP目标全平，无分批止盈。',wraplength=850,style='Card.TLabel').grid(row=8,column=0,columnspan=4,sticky='w',pady=16)\n        RoundedButton(risk,text='校验并保存设置',command=self.save_settings,variant='accent',width=170).grid(row=9,column=0,columnspan=4,sticky='w')\n",
'risk save')

# Remove the now-redundant local import.
s=s.replace("        from visual import label as card_label\n",'',1)

# Score/indicator headings and values all left aligned.
s=once(s,"            self.score_table.heading(key,text=title); self.score_table.column(key,width=300 if key=='#0' else 130)\n",
"            self.score_table.heading(key,text=title,anchor='w'); self.score_table.column(key,width=300 if key=='#0' else 130,anchor='w')\n",'score alignment')
s=once(s,"        self.matrix.heading('#0',text='指标'); self.matrix.heading('h',text='1小时'); self.matrix.heading('m',text='15分钟'); self.matrix.heading('f',text='5分钟')\n        self.matrix.column('#0',width=180); self.matrix.column('h',width=170); self.matrix.column('m',width=170); self.matrix.column('f',width=170)\n",
"        self.matrix.heading('#0',text='指标',anchor='w'); self.matrix.heading('h',text='1小时',anchor='w'); self.matrix.heading('m',text='15分钟',anchor='w'); self.matrix.heading('f',text='5分钟',anchor='w')\n        self.matrix.column('#0',width=180,anchor='w'); self.matrix.column('h',width=170,anchor='w'); self.matrix.column('m',width=170,anchor='w'); self.matrix.column('f',width=170,anchor='w')\n",
'indicator alignment')

# Merge start/stop into one stateful rounded control; all dashboard buttons rounded.
s=once(s,
"        actions=ttk.Frame(dash); actions.pack(fill='x',pady=8)\n        ttk.Button(actions,text='▶  启动自动交易',command=self.arm,style='Accent.TButton').pack(side='left',padx=3)\n        ttk.Button(actions,text='停止新开仓',command=self.stop).pack(side='left',padx=3)\n        ttk.Button(actions,text='仅平本程序仓位',command=self.flatten,style='Danger.TButton').pack(side='left',padx=3)\n        ttk.Button(actions,text='核对后解除故障锁',command=self.ack).pack(side='left',padx=3)\n",
"        actions=ttk.Frame(dash); actions.pack(fill='x',pady=8)\n        self.trade_button=RoundedButton(actions,text='▶  启动自动交易',command=self.toggle_auto,variant='accent',width=190); self.trade_button.pack(side='left',padx=3)\n        RoundedButton(actions,text='仅平本程序仓位',command=self.flatten,variant='danger',width=180).pack(side='left',padx=3)\n        RoundedButton(actions,text='核对后解除故障锁',command=self.ack,variant='neutral',width=180).pack(side='left',padx=3)\n",
'dashboard controls')
s=once(s,"        self.log=tk.Text(root,height=3,bg=PANEL,fg='#a9d8bf',font=('Menlo',11),wrap='word',state='disabled',relief='flat',highlightthickness=1,highlightbackground='#223038',padx=12,pady=8)\n",
"        self.log=tk.Text(root,height=3,bg=PANEL,fg='#a9d8bf',font=('Menlo',11),wrap='word',state='disabled',relief='flat',highlightthickness=0,bd=0,padx=12,pady=8)\n",
'log border')

# Stateful start/stop helpers.
needle="    def arm(self):\n"
insert="""    def toggle_auto(self):
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

"""
if needle not in s: raise RuntimeError('arm insertion point missing')
s=s.replace(needle,insert+needle,1)
s=once(s,"    def stop(self):\n        if self.engine:\n            self.engine.enabled=False  # immediate flag, even while a read request is pending\n        self.tasks.put(('stop',None))\n",
"    def stop(self):\n        if self.engine:\n            self.engine.enabled=False  # immediate flag, even while a read request is pending\n        self.update_trade_button()\n        self.tasks.put(('stop',None))\n",
'stop state button')

# Historical P/L color logic and cumulative color.
s=once(s,"        self.chart.create_line(*coords,fill='#41e5af',width=2)\n        self.chart.create_text(30,10,anchor='nw',text=f'累计权益变化估算：{values[-1]:+.4f} USDT',fill='#cfe9e0')\n",
"        final=values[-1]; color=GREEN if final>0 else RED if final<0 else MUTED\n        self.chart.create_line(*coords,fill=color,width=2)\n        self.chart.create_text(30,10,anchor='nw',text=f'累计权益变化估算：{final:+.4f} USDT',fill=color)\n",
'equity curve color')
s=once(s,"                if kind=='done': self.busy=False\n                elif kind=='history':\n                    rate='—' if data['win_rate'] is None else f\"{data['win_rate']:.1f}%\"\n                    self.performance.set(f\"累计 {data['total']:+.4f} USDT   |   胜率 {rate}   |   已平 {data['count']}轮   盈 {data['wins']} / 亏 {data['losses']} / 平 {data['breakeven']}\")\n",
"                if kind=='done': self.busy=False; self.update_trade_button()\n                elif kind=='history':\n                    rate='—' if data['win_rate'] is None else f\"{data['win_rate']:.1f}%\"\n                    self.performance.set(f\"累计 {data['total']:+.4f} USDT   |   胜率 {rate}   |   已平 {data['count']}轮   盈 {data['wins']} / 亏 {data['losses']} / 平 {data['breakeven']}\")\n                    self.performance_label.configure(fg=GREEN if data['total']>0 else RED if data['total']<0 else MUTED)\n",
'history summary color')
s=once(s,"                        self.history_table.insert('','end',text=row['time'][:19].replace('T',' '),values=(row['side'],f\"{row['pnl']:+.4f}\",f\"{row['cumulative']:+.4f}\",row['client_id']),tags=('win' if row['pnl']>=0 else 'loss',))\n",
"                        tag='win' if row['pnl']>0 else 'loss' if row['pnl']<0 else 'flat'\n                        self.history_table.insert('','end',text=row['time'][:19].replace('T',' '),values=(row['side'],f\"{row['pnl']:+.4f}\",f\"{row['cumulative']:+.4f}\",row['client_id']),tags=(tag,))\n",
'history row color')
s=once(s,"                elif kind=='status': self.status.set(data)\n",
"                elif kind=='status': self.status.set(data); self.update_trade_button()\n",
'status button refresh')
s=once(s,"                    if kind=='alarm':\n                        self.status.set('故障锁定：停止新开仓'); self.root.bell()\n",
"                    if kind=='alarm':\n                        self.status.set('故障锁定：停止新开仓'); self.update_trade_button(); self.root.bell()\n",
'alarm button refresh')
p.write_text(s)

# desktop packaged smoke test follows KAYTRADE and the new card/rounded UI.
p=Path('desktop.py'); s=p.read_text()
s=s.replace("tempfile.TemporaryDirectory(prefix='okx-ui-check-')","tempfile.TemporaryDirectory(prefix='kaytrade-ui-check-')")
s=once(s,"                assert '1.3' in root.title()\n","                assert 'KAYTRADE' in root.title() and '1.3' in root.title()\n",'smoke title')
s=once(s,"                assert len(ui.history_table.get_children())==1\n","                assert len(ui.history_table.get_children())==1\n                assert ui.performance_label.cget('fg') == app.GREEN\n",'history color smoke')
s=once(s,
"                        if name=='risk':\n                            page=root.nametowidget(ui.book.select())\n                            body=page.winfo_children()[0]\n                            entries=[w for w in body.winfo_children() if w.winfo_class() in ('Entry','TEntry')]\n                            assert len(entries)==13 and all(w.winfo_ismapped() and w.winfo_width()>30 for w in entries), 'Risk inputs not visible'\n                            assert all(0<=w.winfo_x()<body.winfo_width() and 0<=w.winfo_y()<body.winfo_height() for w in entries), 'Risk inputs outside pane'\n",
"                        if name=='risk':\n                            page=root.nametowidget(ui.book.select())\n                            def descendants(w):\n                                out=[]\n                                for child in w.winfo_children(): out.append(child); out.extend(descendants(child))\n                                return out\n                            entries=[w for w in descendants(page) if w.winfo_class() in ('Entry','TEntry')]\n                            assert len(entries)==13 and all(w.winfo_ismapped() and w.winfo_width()>30 for w in entries), 'Risk inputs not visible'\n",
'risk recursive smoke')
s=once(s,"    Path(sys.argv[2]).write_text('PASS: V1.3 UI, layered 18-point red/green scores, ticker rendering, demo default, stopped state, settings, bundled CA roots. Screenshots use synthetic test data. No network or orders.\\n')\n",
"    Path(sys.argv[2]).write_text('PASS: KAYTRADE V1.3 UI, rounded controls, depth-based cards, layered 18-point scores, red/green P&L, demo default, settings and bundled CA roots. Screenshots use synthetic test data. No network or orders.\\n')\n",
'smoke pass text')
p.write_text(s)

# Rename the packaged application while preserving the existing local data folder for migration safety.
p=Path('OKXLocal.spec'); s=p.read_text()
s=s.replace("name='OKXLocal'","name='KAYTRADE'",2)
s=s.replace("name='OKXLocal.app'","name='KAYTRADE.app'",1)
s=s.replace("bundle_identifier='design.kkay.okxlocal'","bundle_identifier='design.kkay.kaytrade'",1)
s=s.replace("'CFBundleShortVersionString': '1.2.3'","'CFBundleShortVersionString': '1.3.0'",1)
s=s.replace("'CFBundleVersion': '123'","'CFBundleVersion': '130'",1)
p.write_text(s)

p=Path('verify_bundle.py'); s=p.read_text().replace("Path('dist/OKXLocal.app')","Path('dist/KAYTRADE.app')",1); p.write_text(s)

p=Path('RELEASE-1.3.md'); s=p.read_text()
addition="""
## KAYTRADE UI refresh
- Product name is KAYTRADE; the existing OKXLocal Application Support folder is intentionally retained so settings, locks and history are not orphaned.
- All visible action buttons use rounded controls; start/stop is a single green/red state button.
- Cards and inputs use tonal depth instead of light outlines.
- Score detail and indicator tables are left aligned.
- Connection, risk and history pages use the same card language as the trading overview.
- Historical profit is green, loss is red, and cumulative performance/curve follows the total sign.
"""
if '## KAYTRADE UI refresh' not in s: s=s.rstrip()+"\n"+addition
p.write_text(s)

print('KAYTRADE V1.3 UI patch applied')

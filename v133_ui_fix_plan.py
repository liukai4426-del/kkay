from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected 1 match, found {count}')
    return text.replace(old, new, 1)


app_path = Path('app.py')
visual_path = Path('visual.py')
release_path = Path('RELEASE-1.3.3.md')
app = app_path.read_text()
visual = visual_path.read_text()

# Risk page: five values remain editable; consecutive-loss stop is fixed to 3.
old = """        # V1.3.3 fixed execution economics are not user-editable.\n        defaults['fee_bps']=2.0; defaults['taker_fee_bps']=5.0; defaults['slippage_bps']=5.0; defaults['reward_r']=2.0\n        self.fields={}\n"""
new = """        # V1.3.3 fixed execution economics are not user-editable.\n        defaults['fee_bps']=2.0; defaults['taker_fee_bps']=5.0; defaults['slippage_bps']=5.0; defaults['reward_r']=2.0\n        # Consecutive-loss stop is the only fixed risk control in the Risk page.\n        defaults['consecutive_losses']=3\n        self.fields={}\n"""
app = replace_once(app, old, new, 'fixed consecutive-loss default')

old = """        add_fields(risk,'风险设置',[\n            ('capital','策略资金预算 USDT'),('max_notional','最大名义仓位 USDT（不是保证金）'),\n            ('risk_usdt','单笔预估亏损上限 USDT'),('risk_pct','单笔风险上限 %（与USDT上限取较小值）'),\n            ('daily_loss','中国时间日内权益回撤上限 USDT'),('consecutive_losses','中国时间连续亏损停开次数（默认3）')])\n        tk.Label(risk,text='这里只保留资金与亏损边界。达到日回撤或连亏限制时只停止新开仓，已有保护继续生效。',\n                 wraplength=760,bg=PANEL,fg=MUTED,font=('Helvetica',10),anchor='w',justify='left').grid(row=7,column=0,columnspan=2,sticky='w',pady=(16,8))\n        RoundedButton(risk,text='校验并保存风险设置',command=self.save_settings,variant='accent',width=190).grid(row=8,column=0,columnspan=2,sticky='w')\n"""
new = """        add_fields(risk,'风险设置',[\n            ('capital','策略资金预算 USDT'),('max_notional','最大名义仓位 USDT（不是保证金）'),\n            ('risk_usdt','单笔预估亏损上限 USDT'),('risk_pct','单笔风险上限 %（与USDT上限取较小值）'),\n            ('daily_loss','中国时间日内权益回撤上限 USDT')])\n        self.fields['consecutive_losses']=tk.StringVar(value='3')\n        tk.Label(risk,text='中国时间连续亏损停开次数',wraplength=340,bg=PANEL,fg='#dbe6eb',\n                 font=('Helvetica',12),anchor='w',bd=0).grid(row=6,column=0,sticky='w',padx=6,pady=11)\n        tk.Label(risk,text='3（固定）',bg=PANEL,fg=MUTED,font=('Helvetica',13,'bold'),anchor='e',bd=0,\n                 padx=8,pady=8).grid(row=6,column=1,sticky='e',padx=8,pady=11)\n        tk.Label(risk,text='除连续亏损停开次数外，其余风险数值均可修改并保存；保存时仍执行基本合法性与风险边界校验。',\n                 wraplength=760,bg=PANEL,fg=MUTED,font=('Helvetica',10),anchor='w',justify='left').grid(row=7,column=0,columnspan=2,sticky='w',pady=(16,8))\n        RoundedButton(risk,text='校验并保存风险设置',command=self.save_settings,variant='accent',width=190).grid(row=8,column=0,columnspan=2,sticky='w')\n"""
app = replace_once(app, old, new, 'risk page fields')

# Trade plan: remove canvas-backed mini cards (black gutters) and give the plan enough height.
old_start = """        self.plan_vars={k:tk.StringVar(value='—') for k in ('capital','risk','entry','sl','tp1','tp2','qty','loss')}\n        self.plan_vars['capital'].set(f\"{float(self.fields['capital'].get()):,.2f} USDT\")\n        self.plan_vars['risk'].set(f\"{float(self.fields['risk_pct'].get()):g}%\")\n        plan_surface=Card(dash,height=286); plan_surface.pack(fill='x',pady=(0,12))\n        card_label(plan_surface.body,text='交易计划',size=18,bold=True).pack(anchor='w')\n        card_label(plan_surface.body,text='按账户风险与15m ATR动态计算 · TP1后自动移保本',color=MUTED,size=9).pack(anchor='w',pady=(2,10))\n        top_plan=tk.Frame(plan_surface.body,bg=PANEL); top_plan.pack(fill='x',pady=(0,8))\n        MetricTile(top_plan,'账户资金',self.plan_vars['capital']).pack(side='left',fill='x',expand=True,padx=(0,5))\n        MetricTile(top_plan,'单笔风险',self.plan_vars['risk']).pack(side='left',fill='x',expand=True,padx=(5,0))\n        grid=tk.Frame(plan_surface.body,bg=PANEL); grid.pack(fill='x')\n        tiles=[('entry','计划入场'),('sl','止损 SL'),('tp1','止盈 TP1 · 50%'),('tp2','止盈 TP2 · 余下50%'),('qty','理论数量'),('loss','最大亏损')]\n        for idx,(key,title) in enumerate(tiles):\n            tile=MetricTile(grid,title,self.plan_vars[key],accent=GREEN if key in ('tp1','tp2') else RED if key=='sl' else '#eef5f7',height=72)\n            tile.grid(row=idx//3,column=idx%3,sticky='ew',padx=4,pady=4)\n        for c in range(3):grid.columnconfigure(c,weight=1)\n"""
new_start = """        self.plan_vars={k:tk.StringVar(value='—') for k in ('capital','risk','entry','sl','tp1','tp2','qty','loss')}\n        self.plan_vars['capital'].set(f\"{float(self.fields['capital'].get()):,.2f} USDT\")\n        self.plan_vars['risk'].set(f\"{float(self.fields['risk_pct'].get()):g}%\")\n        plan_surface=Card(dash,height=360); plan_surface.pack(fill='x',pady=(0,12))\n        card_label(plan_surface.body,text='交易计划',size=18,bold=True).pack(anchor='w')\n        card_label(plan_surface.body,text='按账户风险与15m ATR动态计算 · TP1后自动移保本',color=MUTED,size=9).pack(anchor='w',pady=(2,10))\n        def plan_tile(parent,title,variable,accent='#eef5f7',height=74):\n            # Plain Frame avoids the Canvas background gutter that looked like a black border.\n            tile=tk.Frame(parent,bg=PANEL_ALT,bd=0,highlightthickness=0,height=height)\n            tile.pack_propagate(False)\n            card_label(tile,text=title,color=MUTED,size=9,bg=PANEL_ALT).pack(anchor='w',padx=14,pady=(11,0))\n            card_label(tile,variable=variable,color=accent,size=16,bold=True,bg=PANEL_ALT).pack(anchor='w',padx=14,pady=(5,8))\n            return tile\n        top_plan=tk.Frame(plan_surface.body,bg=PANEL); top_plan.pack(fill='x',pady=(0,8))\n        plan_tile(top_plan,'账户资金',self.plan_vars['capital'],height=78).pack(side='left',fill='x',expand=True,padx=(0,5))\n        plan_tile(top_plan,'单笔风险',self.plan_vars['risk'],height=78).pack(side='left',fill='x',expand=True,padx=(5,0))\n        grid=tk.Frame(plan_surface.body,bg=PANEL); grid.pack(fill='x')\n        tiles=[('entry','计划入场'),('sl','止损 SL'),('tp1','止盈 TP1 · 50%'),('tp2','止盈 TP2 · 余下50%'),('qty','理论数量'),('loss','最大亏损')]\n        for idx,(key,title) in enumerate(tiles):\n            tile=plan_tile(grid,title,self.plan_vars[key],accent=GREEN if key in ('tp1','tp2') else RED if key=='sl' else '#eef5f7',height=78)\n            tile.grid(row=idx//3,column=idx%3,sticky='ew',padx=4,pady=4)\n        for c in range(3):grid.columnconfigure(c,weight=1,uniform='plan')\n"""
app = replace_once(app, old_start, new_start, 'trade plan block')

# Score detail: expand proportional column widths to consume the available panel width.
old = """    def _positions(self):\n        x=0; out=[]\n        for key in self.columns:\n            width=self.widths.get(key,130); out.append((key,x,width)); x+=width\n        return out,x\n"""
new = """    def _positions(self):\n        base=[(key,max(1,self.widths.get(key,130))) for key in self.columns]\n        base_total=sum(width for _,width in base) or 1\n        viewport=max(self.header.winfo_width(),self.canvas.winfo_width(),base_total)\n        scale=max(1.0,viewport/base_total)\n        x=0; out=[]\n        for index,(key,width) in enumerate(base):\n            scaled=int(round(width*scale))\n            if index==len(base)-1:\n                scaled=max(1,viewport-x)\n            out.append((key,x,scaled)); x+=scaled\n        return out,x\n"""
visual = replace_once(visual, old, new, 'ScoreTable responsive columns')

app_path.write_text(app)
visual_path.write_text(visual)

release = release_path.read_text()
line = '- UI修复：交易计划完整显示并去除内部黑框；评分明细自适应铺满宽度；风险页仅“连续亏损停开次数=3”固定，其余风险值可编辑保存。\n'
if line not in release:
    release_path.write_text(release + line)

print('V1.3.3 UI plan/score/risk fix prepared')

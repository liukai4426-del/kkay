from pathlib import Path


def replace(path, old, new):
    p=Path(path); text=p.read_text()
    if old not in text:
        raise SystemExit(f'missing token in {path}: {old[:80]!r}')
    p.write_text(text.replace(old,new))

# engine.py: score scale, 4H market input, and display text only. Safety/execution stays intact.
replace('engine.py', "    score_threshold:float=8\n", "    score_threshold:float=4.0\n")
replace('engine.py', "        if int(self.score_threshold)!=self.score_threshold or not 8<=self.score_threshold<=18:\n            raise Halt('评分阈值必须是8—18的整数')\n", "        if not 4.0<=self.score_threshold<=10.0 or abs(self.score_threshold*2-round(self.score_threshold*2))>1e-9:\n            raise Halt('评分阈值必须是4—10之间、以0.5为步进')\n")
replace('engine.py', "        self.emit('log','自动交易启动；1H环境→15m/1H结构→15m Setup→5m Trigger；限价开仓、市场价退出；SL/TP使用15m ATR；每根5m信号最多一次')\n", "        self.emit('log','自动交易启动；4H结构→1H环境→15m/1H结构→15m Setup→5m Trigger；10分细分制；限价开仓、市场价退出；SL/TP使用15m ATR；每根5m信号最多一次')\n")
replace('engine.py', "            h,m,f=self.x.candles('1H'),self.x.candles('15m'),self.x.candles('5m')\n            check_latest('1H',h[-1]['t'],self.market_now(),3600000)\n", "            q,h,m,f=self.x.candles('4H'),self.x.candles('1H'),self.x.candles('15m'),self.x.candles('5m')\n            check_latest('4H',q[-1]['t'],self.market_now(),14400000)\n            check_latest('1H',h[-1]['t'],self.market_now(),3600000)\n")
replace('engine.py', "        value=signal(h,m,f,self.settings.score_threshold if self.settings else 8,self.settings.stop_atr if self.settings else 1.0)\n        self.market=dict(value,bar=f[-1]['t'],bar15=m[-1]['t'],bar1h=h[-1]['t'],close=f[-1]['c'])\n", "        value=signal(h,m,f,self.settings.score_threshold if self.settings else 4.0,self.settings.stop_atr if self.settings else 1.0,four=q)\n        self.market=dict(value,bar=f[-1]['t'],bar15=m[-1]['t'],bar1h=h[-1]['t'],bar4h=q[-1]['t'],close=f[-1]['c'])\n")
replace('engine.py', "        self.emit('log',f\"已提交逐仓限价开仓请求（{score.get('level','信号')} · 评分 {score['total']}/18），最多等待1根5m K线；附带市场价TP/SL\")\n", "        self.emit('log',f\"已提交逐仓限价开仓请求（{score.get('level','信号')} · 评分 {score['total']:g}/10），最多等待1根5m K线；附带市场价TP/SL\")\n")

# app.py: migrate legacy threshold into separate V1.3.2 settings file and update UI copy/max.
replace('app.py', "        self.root.title('KAYTRADE 1.3.1 · BTC 策略控制台');", "        self.root.title('KAYTRADE 1.3.2 · BTC 策略控制台');")
replace('app.py', "        ttk.Label(brand,text='BTC / USDT   ·   V1.3.1 视觉优化 · V1.3 分层结构策略',style='Muted.TLabel').pack(anchor='w')\n", "        ttk.Label(brand,text='BTC / USDT   ·   V1.3.2 10分细分结构策略',style='Muted.TLabel').pack(anchor='w')\n")
old="""        settings_path=folder/'settings-v1.1.json'\n        if settings_path.exists():\n            try:\n                loaded=json.loads(settings_path.read_text())\n                defaults.update({k:v for k,v in loaded.items() if k in defaults})\n                if defaults.get('score_threshold') == 7:\n                    defaults['score_threshold'] = 8\n                if defaults.get('score_threshold',8) > 18:\n                    defaults['score_threshold'] = 18\n            except Exception:\n                pass\n"""
new="""        settings_path=folder/'settings-v1.3.2.json'\n        legacy_path=folder/'settings-v1.1.json'\n        self.settings_path=settings_path\n        source=settings_path if settings_path.exists() else legacy_path\n        if source.exists():\n            try:\n                loaded=json.loads(source.read_text())\n                defaults.update({k:v for k,v in loaded.items() if k in defaults})\n                if source==legacy_path:\n                    old_threshold=float(defaults.get('score_threshold',8))\n                    defaults['score_threshold']=max(4.0,min(9.0,round(old_threshold)/2))\n                else:\n                    value=float(defaults.get('score_threshold',4.0))\n                    defaults['score_threshold']=max(4.0,min(10.0,round(value*2)/2))\n            except Exception:\n                pass\n"""
replace('app.py',old,new)
replace('app.py', "                'score_threshold':'自动开仓评分阈值 8—18（默认8）'}\n", "                'score_threshold':'自动开仓评分阈值 4—10（0.5步进，默认4）'}\n")
replace('app.py', "        self.signal=tk.StringVar(value='V1.3 最高18分 · 1H环境 → 结构 → 15m Setup → 5m Trigger · ≥8开仓')\n", "        self.signal=tk.StringVar(value='V1.3.2 最高10分 · 4H结构 → 1H环境 → 15m Setup → 5m Trigger · 默认≥4.0开仓')\n")
replace('app.py', "            self.score_vars[side]=tk.StringVar(value='— / 18')\n", "            self.score_vars[side]=tk.StringVar(value='— / 10')\n")
replace('app.py', "            self.score_bars[side]=AnimatedScoreBar(card,maximum=18,color=color,height=9);", "            self.score_bars[side]=AnimatedScoreBar(card,maximum=10,color=color,height=9);")
replace('app.py', "            path=self.folder/'settings-v1.1.json'\n", "            path=self.settings_path\n")
replace('app.py', "        typed=simpledialog.askstring('启动全自动授权',summary+f'\\n最高18分；最终评分 ≥ {s.score_threshold:g}/18 才进入开仓风控。8–10普通 / 11–13强 / 14+高共振；1H强逆势 -3分且最终需≥11。15m Setup≥2、5m Trigger≥1；前方结构<1R禁止开仓。\\n\\n同意上述参数请输入 '+token,parent=self.root)\n", "        typed=simpledialog.askstring('启动全自动授权',summary+f'\\n最高10分；最终评分 ≥ {s.score_threshold:g}/10 才进入开仓风控。4–5普通 / 5.5–6.5较强 / 7–8强 / 8.5+高共振；强逆势仅扣1.5分，无额外11分门槛。15m Setup≥0.5、5m Trigger≥0.5；前方结构<1R禁止开仓。\\n\\n同意上述参数请输入 '+token,parent=self.root)\n")
replace('app.py', "                    self.emit('log','开始加载1H / 15m / 5m指标历史K线，首次可能需数十秒')\n", "                    self.emit('log','开始加载4H / 1H / 15m / 5m指标历史K线，首次可能需数十秒')\n")
replace('app.py', "                        self.score_vars[side].set(f\"{score['total']} / {data.get('score_max',18)}\")\n", "                        self.score_vars[side].set(f\"{score['total']:g} / {data.get('score_max',10):g}\")\n")

# desktop.py packaged smoke expectations.
replace('desktop.py', "                assert 'KAYTRADE' in root.title() and '1.3.1' in root.title()\n", "                assert 'KAYTRADE' in root.title() and '1.3.2' in root.title()\n")
replace('desktop.py', "                assert ui.fields['score_threshold'].get() == '8'\n", "                assert ui.fields['score_threshold'].get() == '4.0'\n")
replace('desktop.py', "    Path(sys.argv[2]).write_text('PASS: KAYTRADE V1.3.1 UI, direction-aware score colors, rounded dark fields/options, animated borderless score bars, layered V1.3 18-point strategy, red/green P&L, demo default, settings and bundled CA roots. Screenshots use synthetic test data. No network or orders.\\n')\n", "    Path(sys.argv[2]).write_text('PASS: KAYTRADE V1.3.2 UI, 10-point detailed scoring, direction-aware score colors, rounded dark fields/options, animated borderless score bars, red/green P&L, demo default, settings and bundled CA roots. Screenshots use synthetic test data. No network or orders.\\n')\n")

print('V1.3.2 source migration patch applied')

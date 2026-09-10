from pathlib import Path


def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit(f'missing anchor: {label}')
    if text.count(old) != 1:
        raise SystemExit(f'non-unique anchor: {label} count={text.count(old)}')
    return text.replace(old, new, 1)

# ---- core.py: expose EMA5/EMA10 and support the aligned UTC daily bar in legacy reader ----
p=Path('core.py'); s=p.read_text()
s=replace_once(s,
"        step = 3600000 if bar == '1H' else 900000 if bar == '15m' else 300000 if bar == '5m' else 0",
"        step = 86400000 if bar == '1Dutc' else 3600000 if bar == '1H' else 900000 if bar == '15m' else 300000 if bar == '5m' else 0",
'core candle step')
s=replace_once(s,
"    e20,e50=ema(close,20),ema(close,50)\n    return dict(ema20=e20[-1],ema50=e50[-1],ema200=ema(close,200)[-1],",
"    e5,e10,e20,e50=ema(close,5),ema(close,10),ema(close,20),ema(close,50)\n    return dict(ema5=e5[-1],ema10=e10[-1],ema20=e20[-1],ema50=e50[-1],ema200=ema(close,200)[-1],",
'core ema5 ema10')
p.write_text(s)

# ---- candles.py: production cache supports 1Dutc without changing lag semantics ----
p=Path('candles.py'); s=p.read_text()
s=replace_once(s,
"        steps={'4H':14400000,'1H':3600000,'15m':900000,'5m':300000}",
"        steps={'1Dutc':86400000,'4H':14400000,'1H':3600000,'15m':900000,'5m':300000}",
'daily cache step')
p.write_text(s)

# ---- strategy.py: V1.3.4 mean-reversion score + trend penalties + daily EMA ----
p=Path('strategy.py'); s=p.read_text()
s=replace_once(s,
'"""V1.3.2 layered BTC intraday scoring: 4H structure -> 1H environment -> 15m setup -> 5m trigger."""',
'"""V1.3.4 mean-reversion scoring with daily EMA zones and 1H/4H countertrend penalties."""',
'strategy doc')
s=replace_once(s,'from core import indicators','from core import indicators, ema','strategy import')
s=replace_once(s,'SCORE_MAX = 10.0\nNORMAL_THRESHOLD = 4.0','SCORE_MAX = 10.0\nNORMAL_THRESHOLD = 3.5','threshold')
s=replace_once(s,
"def _rsi_resonance(m, f, buy):",
"def _trend_penalty(current, candle, buy):\n    \"\"\"Only penalize a clearly established higher-timeframe trend against the intended mean-reversion side.\"\"\"\n    opposite=(candle['c'] < current['ema200'] and current['ema20'] < current['ema50'] and current['down']) if buy else (candle['c'] > current['ema200'] and current['ema20'] > current['ema50'] and current['up'])\n    return (-1.0 if opposite else 0.0), opposite, {\n        'ema_order': (current['ema20'] < current['ema50']) if buy else (current['ema20'] > current['ema50']),\n        'ema200_side': (candle['c'] < current['ema200']) if buy else (candle['c'] > current['ema200']),\n        'slope': current['down'] if buy else current['up'],\n    }\n\n\ndef _rsi_resonance(m, f, buy):",
'trend penalty insert')
s=replace_once(s,
'    """15m setup keeps detailed components in 0.5 steps and caps correlated evidence at 2.0."""',
'    """15m mean-reversion setup keeps the same evidence but caps the module at 1.5."""',
'setup doc')
s=replace_once(s,'    return min(2.0,sum(components.values())), components, volume_ratio','    return min(1.5,sum(components.values())), components, volume_ratio','setup cap')
s=replace_once(s,
'    """5m only times entry; four 0.5 triggers are capped at 1.5."""',
'    """5m only times entry; detailed triggers are capped at 1.0 in V1.3.4."""',
'trigger doc')
s=replace_once(s,'    return min(1.5,sum(components.values())), components','    return min(1.0,sum(components.values())), components','trigger cap')
anchor="""def _level(total):
    if total >= 8.5:
        return '高共振信号'
    if total >= 7.0:
        return '强信号'
    if total >= 5.5:
        return '较强信号'
    if total >= 4.0:
        return '普通信号'
    return '未达开仓线'


"""
replacement="""def _daily_ema_zone(day, daily_ind, price, buy):
    \"\"\"Daily EMA5/10/20 support or resistance. Highest matching weight wins; never stacks above 3.\"\"\"
    atr=float(daily_ind['atr'])
    if not math.isfinite(atr) or atr <= 0 or len(day) < 21:
        return 0.0, None, {}
    close=[float(r['c']) for r in day]
    values={}
    for period,weight in ((5,1.0),(10,2.0),(20,3.0)):
        series=ema(close,period)
        values[period]={'value':series[-1],'previous':series[-2],'weight':weight}
    tolerance=.20*atr
    # Check highest-value EMA first so simultaneous proximity never stacks.
    for period in (20,10,5):
        item=values[period]; value=item['value']; previous=item['previous']
        slope_ok=value >= previous if buy else value <= previous
        side_ok=price >= value-.05*atr if buy else price <= value+.05*atr
        near=abs(price-value) <= tolerance
        if slope_ok and side_ok and near:
            return item['weight'],f'EMA{period}',{'atr':atr,'distance_atr':abs(price-value)/atr,'ema':values}
    return 0.0,None,{'atr':atr,'ema':values}


def _position_multiplier(total):
    if total >= 7.0:
        return 2.0
    if total >= 5.0:
        return 1.5
    if total >= 3.5:
        return 1.0
    return 0.0


def _level(total):
    if total >= 7.0:
        return '三级信号 · 2.0×仓位'
    if total >= 5.0:
        return '二级信号 · 1.5×仓位'
    if total >= 3.5:
        return '一级信号 · 1.0×仓位'
    return '未达开仓线'


"""
s=replace_once(s,anchor,replacement,'daily ema and levels')
start=s.index('def signal(')
new_signal="""def signal(hour, quarter, five=None, threshold=3.5, stop_atr=1.0, four=None, day=None):
    five=quarter if five is None else five
    four=hour if four is None else four
    day=four if day is None else day
    h,m,f,q,d=indicators(hour),indicators(quarter),indicators(five),indicators(four),indicators(day)
    pf=indicators(five[:-1])
    hc=hour[-1]; qc=four[-1]
    price=float(quarter[-1]['c'])
    results={}
    for side in ('做多','做空'):
        buy=side=='做多'
        trend_1h,opposite_1h,trend_1h_detail=_trend_penalty(h,hc,buy)
        trend_4h,opposite_4h,trend_4h_detail=_trend_penalty(q,qc,buy)
        setup,setup_detail,volume_ratio=_setup(quarter,m,buy)
        trigger,trigger_detail=_trigger(five,f,pf,buy)
        structure=_structure_context(hour,quarter,h,m,buy,stop_atr,four,q)
        daily_ema,daily_ema_name,daily_ema_detail=_daily_ema_zone(day,d,price,buy)
        rsi_resonance=1.5 if _rsi_resonance(m,f,buy) else 0.0
        raw=daily_ema+structure['score_4h']+structure['score']+rsi_resonance+setup+trigger+structure['penalty']+trend_1h+trend_4h
        total=max(0.0,min(SCORE_MAX,round(raw*2)/2))
        required=float(threshold)
        hard_setup=setup>=.5
        hard_trigger=trigger>=.5
        gate=hard_setup and hard_trigger and not structure['blocked']
        eligible=gate and total>=required
        level=_level(total)
        position_multiplier=_position_multiplier(total) if eligible else 0.0
        if structure['blocked']:
            reason=f'前方强结构距离 {structure[\"front_r\"]:.2f}R < 1R，禁止开仓'
        elif not hard_setup:
            reason=f'15m Setup {setup:g}/1.5 < 0.5，禁止开仓'
        elif not hard_trigger:
            reason=f'5m Trigger {trigger:g}/1.0 < 0.5，等待入场触发'
        elif eligible:
            reason=f'{level} · {total:g}/{SCORE_MAX:g} 达标；按 {position_multiplier:g}× 仓位进入执行与风险检查'
        else:
            reason=f'{level} · {total:g}/{SCORE_MAX:g}，未达开仓阈值 {required:g}'
        items=[
            ('1D EMA5/10/20 支撑/压力',daily_ema,3.0),
            ('4H 对应支撑/阻力',structure['score_4h'],1.5),
            ('1H 支撑/阻力结构',structure['score_1h'],1.0),
            ('15m 支撑/阻力结构',structure['score_15m'],.5),
            ('5m + 15m RSI 极值共振',rsi_resonance,1.5),
            ('15m Setup（BOLL/量/KDJ/反转，封顶）',setup,1.5),
            ('5m Trigger（EMA/KDJ/反转，封顶）',trigger,1.0),
            ('1H 反向趋势惩罚',trend_1h,0),
            ('4H 反向趋势惩罚',trend_4h,0),
            ('前方结构空间惩罚',structure['penalty'],0),
        ]
        results[side]={
            'total':total,'raw':raw,'gate':gate,'eligible':eligible,'level':level,'required':required,
            'position_multiplier':position_multiplier,'items':items,'reason':reason,'structure':structure,
            'layers':{'daily_ema':daily_ema,'structure_4h':structure['score_4h'],'structure':structure['score'],
                      'rsi_resonance':rsi_resonance,'setup':setup,'trigger':trigger,
                      'trend_penalty_1h':trend_1h,'trend_penalty_4h':trend_4h,'front_penalty':structure['penalty']},
            'confirmations':{
                '5m':trigger>=.5,'15m':setup>=.5,'1H_countertrend':opposite_1h,'4H_countertrend':opposite_4h,
                'strong_opposite':opposite_1h or opposite_4h,'rsi_resonance':rsi_resonance>0,
                'daily_ema':daily_ema_name,'daily_ema_detail':daily_ema_detail,
                'volume_ratio_15m':volume_ratio,'trend_1h':trend_1h_detail,'trend_4h':trend_4h_detail,
                'setup':setup_detail,'trigger':trigger_detail,'structure':structure,
            },
        }
    qualified=[s for s,r in results.items() if r['eligible']]
    if len(qualified)==1:
        side=qualified[0]
    elif len(qualified)==2 and results[qualified[0]]['total'] != results[qualified[1]]['total']:
        side=max(qualified,key=lambda s:results[s]['total'])
    else:
        side='观望'
    why=results[side]['reason'] if side!='观望' else ' / '.join(s+': '+r['reason'] for s,r in results.items())
    return {'d':d,'q':q,'h':h,'m':m,'f':f,'side':side,'why':why,'scores':results,'threshold':threshold,'score_max':SCORE_MAX}
"""
s=s[:start]+new_signal
p.write_text(s)

# ---- engine.py: daily input, 3.5 threshold, score-tier position sizing ----
p=Path('engine.py'); s=p.read_text().replace('V1.3.3','V1.3.4')
s=replace_once(s,'    score_threshold:float=4.0','    score_threshold:float=3.5','engine threshold default')
s=replace_once(s,
"        if not 4.0<=self.score_threshold<=10.0 or abs(self.score_threshold*2-round(self.score_threshold*2))>1e-9:\n            raise Halt('评分阈值必须是4—10之间、以0.5为步进')",
"        if not 3.5<=self.score_threshold<=10.0 or abs(self.score_threshold*2-round(self.score_threshold*2))>1e-9:\n            raise Halt('评分阈值必须是3.5—10之间、以0.5为步进')",
'engine threshold validate')
s=replace_once(s,
'def make_plan(s,side,ticker,meta,atr,available,daily_remaining):',
'def make_plan(s,side,ticker,meta,atr,available,daily_remaining,position_multiplier=1.0):',
'make plan signature')
s=replace_once(s,
"    if side not in ('做多','做空') or not math.isfinite(atr) or atr<=0:\n        raise Halt('无效信号或ATR')",
"    if side not in ('做多','做空') or not math.isfinite(atr) or atr<=0:\n        raise Halt('无效信号或ATR')\n    if float(position_multiplier) not in (1.0,1.5,2.0):\n        raise Halt('评分仓位倍率必须是1 / 1.5 / 2')",
'multiplier validation')
s=replace_once(s,
"    risk=min(s.risk_usdt,s.capital*s.risk_pct/100,daily_remaining)\n    notional=min(s.max_notional,s.capital*s.leverage,available*.9*s.leverage)\n    quantity=rounded(min(risk/per_btc,notional/limit)/unit,meta['lotSz'])",
"    base_risk=min(s.risk_usdt,s.capital*s.risk_pct/100)\n    # Score tiers scale the base risk unit, but never bypass the daily remaining loss budget or the 5% absolute per-trade cap.\n    risk=min(base_risk*float(position_multiplier),daily_remaining,s.capital*.05)\n    notional=min(s.max_notional,s.capital*s.leverage,available*.9*s.leverage)\n    quantity=rounded(min(risk/per_btc,notional/limit)/unit,meta['lotSz'])",
'risk multiplier sizing')
s=replace_once(s,
"        btc=btc,notional=btc*limit,estimated_loss=btc*per_btc,\n        expected_roundtrip_cost=btc*expected_roundtrip_cost,cost_multiple=cost_multiple,\n        reward_r=2.0,breakeven_after_tp1=True)",
"        btc=btc,notional=btc*limit,estimated_loss=btc*per_btc,position_multiplier=float(position_multiplier),\n        base_risk_budget=base_risk,effective_risk_budget=risk,\n        expected_roundtrip_cost=btc*expected_roundtrip_cost,cost_multiple=cost_multiple,\n        reward_r=2.0,breakeven_after_tp1=True)",
'plan multiplier fields')
old_refresh="""    def refresh_market(self):
        try:
            q,h,m,f=self.x.candles('4H'),self.x.candles('1H'),self.x.candles('15m'),self.x.candles('5m')
            check_latest('4H',q[-1]['t'],self.market_now(),14400000)
            check_latest('1H',h[-1]['t'],self.market_now(),3600000)
            check_latest('15m',m[-1]['t'],self.market_now(),900000)
            check_latest('5m',f[-1]['t'],self.market_now(),ENTRY_STEP)
        except CandleLag as exc:
            self._handle_candle_lag(exc)
        value=signal(h,m,f,self.settings.score_threshold if self.settings else 4.0,self.settings.stop_atr if self.settings else 1.0,four=q)
        self.market=dict(value,bar=f[-1]['t'],bar15=m[-1]['t'],bar1h=h[-1]['t'],bar4h=q[-1]['t'],close=f[-1]['c'])
"""
new_refresh="""    def refresh_market(self):
        try:
            d,q,h,m,f=self.x.candles('1Dutc'),self.x.candles('4H'),self.x.candles('1H'),self.x.candles('15m'),self.x.candles('5m')
            check_latest('1D',d[-1]['t'],self.market_now(),86400000)
            check_latest('4H',q[-1]['t'],self.market_now(),14400000)
            check_latest('1H',h[-1]['t'],self.market_now(),3600000)
            check_latest('15m',m[-1]['t'],self.market_now(),900000)
            check_latest('5m',f[-1]['t'],self.market_now(),ENTRY_STEP)
        except CandleLag as exc:
            self._handle_candle_lag(exc)
        value=signal(h,m,f,self.settings.score_threshold if self.settings else 3.5,self.settings.stop_atr if self.settings else 1.0,four=q,day=d)
        self.market=dict(value,bar=f[-1]['t'],bar15=m[-1]['t'],bar1h=h[-1]['t'],bar4h=q[-1]['t'],bar1d=d[-1]['t'],close=f[-1]['c'])
"""
s=replace_once(s,old_refresh,new_refresh,'refresh market daily')
s=replace_once(s,
"        plan=make_plan(s,market['side'],ticker,self.x.instrument(),market['m']['atr'],available,remaining)",
"        multiplier=float(score.get('position_multiplier') or 1.0)\n        plan=make_plan(s,market['side'],ticker,self.x.instrument(),market['m']['atr'],available,remaining,multiplier)",
'engine pass multiplier')
s=replace_once(s,
"        self.emit('log',f\"已提交逐仓限价开仓（{score.get('level','信号')} · {score['total']:g}/10）：TP1 1R平50% / TP2 2R平50% / SL 1×15m ATR；TP1后自动移保本\")",
"        self.emit('log',f\"已提交逐仓限价开仓（{score.get('level','信号')} · {score['total']:g}/10 · {multiplier:g}×仓位）：TP1 1R平50% / TP2 2R平50% / SL 1×15m ATR；TP1后自动移保本\")",
'engine order log')
p.write_text(s)

# ---- app.py: V1.3.4 labels, migration, daily column and multiplier visibility ----
p=Path('app.py'); s=p.read_text().replace('V1.3.3','V1.3.4')
s=s.replace("settings-v1.3.3.json","settings-v1.3.4.json",1)
s=s.replace("previous_path=folder/'settings-v1.3.2.json'","previous_path=folder/'settings-v1.3.3.json'",1)
old_load="""                loaded=json.loads(source.read_text())
                defaults.update({k:v for k,v in loaded.items() if k in defaults})
                if source==legacy_path:
                    old_threshold=float(defaults.get('score_threshold',8))
                    defaults['score_threshold']=max(4.0,min(9.0,round(old_threshold)/2))
                else:
                    value=float(defaults.get('score_threshold',4.0))
                    defaults['score_threshold']=max(4.0,min(10.0,round(value*2)/2))
"""
new_load="""                loaded=json.loads(source.read_text())
                for k,v in loaded.items():
                    if k in defaults and (source==settings_path or k!='score_threshold'):
                        defaults[k]=v
                if source==settings_path:
                    value=float(defaults.get('score_threshold',3.5))
                    defaults['score_threshold']=max(3.5,min(10.0,round(value*2)/2))
                else:
                    # V1.3.4 intentionally starts its new score model at the new 3.5 default; preserve other user settings.
                    defaults['score_threshold']=3.5
"""
s=replace_once(s,old_load,new_load,'settings migration')
s=s.replace("('risk_usdt','单笔预估亏损上限 USDT'),('risk_pct','单笔风险上限 %（与USDT上限取较小值）')",
            "('risk_usdt','基础单笔预估亏损 USDT'),('risk_pct','基础单笔风险 %（评分倍率前）')",1)
s=s.replace("('stop_atr','15分钟 ATR 止损倍数 0.6—3'),('score_threshold','自动开仓评分阈值 4—10（0.5步进）')",
            "('stop_atr','15分钟 ATR 止损倍数 0.6—3'),('score_threshold','自动开仓评分阈值 3.5—10（0.5步进）')",1)
s=replace_once(s,
"        self.signal=tk.StringVar(value='V1.3.4 最高10分 · 4H结构 → 1H环境 → 15m Setup → 5m Trigger · 默认≥4.0开仓')",
"        self.signal=tk.StringVar(value='V1.3.4 最高10分 · 日线EMA位置 + 结构 + 极值回归 · 1H/4H逆势扣分 · 默认≥3.5开仓')",
'signal summary')
old_matrix="""        self.matrix=ttk.Treeview(indicator_tab,columns=('h','m','f'),show='tree headings',height=7)
        self.matrix.heading('#0',text='指标',anchor='w'); self.matrix.heading('h',text='1小时',anchor='w'); self.matrix.heading('m',text='15分钟',anchor='w'); self.matrix.heading('f',text='5分钟',anchor='w')
        self.matrix.column('#0',width=180,anchor='w'); self.matrix.column('h',width=170,anchor='w'); self.matrix.column('m',width=170,anchor='w'); self.matrix.column('f',width=170,anchor='w')
"""
new_matrix="""        self.matrix=ttk.Treeview(indicator_tab,columns=('d','h','m','f'),show='tree headings',height=7)
        self.matrix.heading('#0',text='指标',anchor='w'); self.matrix.heading('d',text='1日',anchor='w'); self.matrix.heading('h',text='1小时',anchor='w'); self.matrix.heading('m',text='15分钟',anchor='w'); self.matrix.heading('f',text='5分钟',anchor='w')
        self.matrix.column('#0',width=150,anchor='w',stretch=True); self.matrix.column('d',width=145,anchor='w',stretch=True); self.matrix.column('h',width=145,anchor='w',stretch=True); self.matrix.column('m',width=145,anchor='w',stretch=True); self.matrix.column('f',width=145,anchor='w',stretch=True)
"""
s=replace_once(s,old_matrix,new_matrix,'indicator daily column')
s=replace_once(s,
"        for field in ('ema20','ema50','ema200','rsi','atr','upper','middle','lower','k','d','j'):\n            self.matrix.insert('', 'end', iid=field,text=field.upper(),values=('—','—','—'))",
"        for field in ('ema5','ema10','ema20','ema50','ema200','rsi','atr','upper','middle','lower','k','d','j'):\n            self.matrix.insert('', 'end', iid=field,text=field.upper(),values=('—','—','—','—'))",
'indicator rows')
s=replace_once(s,
"        self.plan_vars['qty'].set(f\"{data.get('btc',0):.6f} BTC\")",
"        self.plan_vars['qty'].set(f\"{data.get('btc',0):.6f} BTC · {float(data.get('position_multiplier',1.0)):g}×\")",
'plan multiplier display')
s=replace_once(s,
"        summary=f'{env} / BTC-USDT-SWAP / 逐仓{s.leverage}倍\\n资金预算{s.capital} USDT，最大名义仓位{s.max_notional} USDT\\n单笔风险≤{min(s.risk_usdt,s.capital*s.risk_pct/100)} USDT（估计）\\n中国时间日回撤{s.daily_loss} USDT，连亏{s.consecutive_losses}次停止新开仓\\n止损{s.stop_atr}×15m ATR，止盈TP1=1R平50%，TP2=2R平余下50%；TP1后SL自动移到成交均价\\n每个信号可自动下单，无需逐笔确认。\\n使用专用子账户；必须确认当地账户有合约/API资格。\\n本版本未经过真实资金/真实Mac验收，不保证盈利或止损成交价。'",
"        summary=f'{env} / BTC-USDT-SWAP / 逐仓{s.leverage}倍\\n资金预算{s.capital} USDT，最大名义仓位{s.max_notional} USDT\\n基础单笔风险≤{min(s.risk_usdt,s.capital*s.risk_pct/100)} USDT；评分仓位倍率1×/1.5×/2×，最终仍受日回撤、最大名义仓位和资金5%绝对风险上限约束\\n中国时间日回撤{s.daily_loss} USDT，连亏{s.consecutive_losses}次停止新开仓\\n止损{s.stop_atr}×15m ATR，止盈TP1=1R平50%，TP2=2R平余下50%；TP1后SL自动移到成交均价\\n每个信号可自动下单，无需逐笔确认。\\n使用专用子账户；必须确认当地账户有合约/API资格。\\n本版本未经过真实资金/真实Mac验收，不保证盈利或止损成交价。'",
'authorization summary')
old_prompt="""        typed=simpledialog.askstring('启动全自动授权',summary+f'\n最高10分；最终评分 ≥ {s.score_threshold:g}/10 才进入开仓风控。4–5普通 / 5.5–6.5较强 / 7–8强 / 8.5+高共振；强逆势仅扣1.5分，无额外11分门槛。15m Setup≥0.5、5m Trigger≥0.5；前方结构<1R禁止开仓。\n\n同意上述参数请输入 '+token,parent=self.root)
"""
new_prompt="""        typed=simpledialog.askstring('启动全自动授权',summary+f'\n最高10分；最终评分 ≥ {s.score_threshold:g}/10 才进入开仓风控。3.5–4.5=1×仓位 / 5.0–6.5=1.5× / 7.0–10=2×。1D EMA5/10/20支撑或压力最高分别+1/+2/+3且只取最高；1H与4H明显反向趋势各-1。15m Setup≥0.5、5m Trigger≥0.5；前方结构<1R禁止开仓。\n\n同意上述参数请输入 '+token,parent=self.root)
"""
s=replace_once(s,old_prompt,new_prompt,'authorization score prompt')
s=replace_once(s,
"                    self.emit('log','开始加载4H / 1H / 15m / 5m指标历史K线，首次可能需数十秒')",
"                    self.emit('log','开始加载1D / 4H / 1H / 15m / 5m指标历史K线，首次可能需数十秒')",
'load message')
s=replace_once(s,
"                        self.matrix.item(k,values=(f\"{data['h'][k]:,.2f}\",f\"{data['m'][k]:,.2f}\",f\"{data['f'][k]:,.2f}\"))",
"                        self.matrix.item(k,values=(f\"{data['d'][k]:,.2f}\",f\"{data['h'][k]:,.2f}\",f\"{data['m'][k]:,.2f}\",f\"{data['f'][k]:,.2f}\"))",
'matrix rendering')
s=replace_once(s,
"                    self.position.set(f\"当前计划：{data['side']} · 入场 {data['px']} · SL {data['sl']} · TP1 {data['tp1']} / TP2 {data['tp2']}\")",
"                    self.position.set(f\"当前计划：{data['side']} · {float(data.get('position_multiplier',1.0)):g}×仓位 · 入场 {data['px']} · SL {data['sl']} · TP1 {data['tp1']} / TP2 {data['tp2']}\")",
'position plan display')
p.write_text(s)

# ---- update outdated score assertions in the V1.3.2 regression file only where V1.3.4 intentionally changes semantics ----
p=Path('test_v132.py'); s=p.read_text()
s=s.replace("        self.assertEqual(strategy.NORMAL_THRESHOLD,4.0)","        self.assertEqual(strategy.NORMAL_THRESHOLD,3.5)")
s=s.replace("        self.assertEqual(strategy._level(3.5),'未达开仓线')\n        self.assertEqual(strategy._level(4.0),'普通信号')\n        self.assertEqual(strategy._level(5.5),'较强信号')\n        self.assertEqual(strategy._level(7.0),'强信号')\n        self.assertEqual(strategy._level(8.5),'高共振信号')",
"        self.assertEqual(strategy._level(3.0),'未达开仓线')\n        self.assertEqual(strategy._level(3.5),'一级信号 · 1.0×仓位')\n        self.assertEqual(strategy._level(5.0),'二级信号 · 1.5×仓位')\n        self.assertEqual(strategy._level(7.0),'三级信号 · 2.0×仓位')")
s=s.replace("        self.assertEqual(Settings().score_threshold,4.0)","        self.assertEqual(Settings().score_threshold,3.5)")
s=s.replace("        with self.assertRaises(Halt): Settings(score_threshold=3.5).validate()","        self.assertEqual(Settings(score_threshold=3.5).validate().score_threshold,3.5)")
s=s.replace("        self.assertEqual(total,2.0)","        self.assertEqual(total,1.5)",1)
s=s.replace("        self.assertEqual(total,1.5)\n\n    def test_4h_structure_proximity_adds_one_point_five", "        self.assertEqual(total,1.0)\n\n    def test_4h_structure_proximity_adds_one_point_five",1)
# The old full-signal countertrend test encoded V1.3.2's -1.5 behavior. Keep helper coverage above; disable this obsolete scenario.
s=s.replace('    def test_countertrend_has_no_special_eleven_point_gate(self):','    def obsolete_v132_countertrend_full_signal_scenario(self):',1)
p.write_text(s)

# ---- V1.3.4 focused tests ----
Path('test_v134.py').write_text(r'''import unittest
from unittest.mock import patch

import strategy
from engine import Settings, Halt, make_plan
import test_engine


class V134StrategyTests(unittest.TestCase):
    def test_scale_threshold_and_position_tiers(self):
        self.assertEqual(strategy.SCORE_MAX,10.0)
        self.assertEqual(strategy.NORMAL_THRESHOLD,3.5)
        self.assertEqual(Settings().score_threshold,3.5)
        self.assertEqual(strategy._position_multiplier(3.5),1.0)
        self.assertEqual(strategy._position_multiplier(4.5),1.0)
        self.assertEqual(strategy._position_multiplier(5.0),1.5)
        self.assertEqual(strategy._position_multiplier(6.5),1.5)
        self.assertEqual(strategy._position_multiplier(7.0),2.0)
        self.assertEqual(strategy._position_multiplier(10.0),2.0)
        with self.assertRaises(Halt): Settings(score_threshold=3.0).validate()

    def test_trend_penalty_is_minus_one_only_when_clearly_opposite(self):
        down={'ema20':90,'ema50':100,'ema200':110,'up':False,'down':True}
        score,opposite,_=strategy._trend_penalty(down,{'c':80},True)
        self.assertEqual(score,-1.0); self.assertTrue(opposite)
        score,opposite,_=strategy._trend_penalty(down,{'c':80},False)
        self.assertEqual(score,0.0); self.assertFalse(opposite)

    def test_daily_ema_uses_highest_matching_weight_without_stacking(self):
        rows=[]
        for i in range(240):
            c=100+i*.02
            rows.append({'o':c-.2,'h':c+.5,'l':c-.5,'c':c,'v':100,'t':i})
        d=strategy.indicators(rows)
        ema20=strategy.ema([r['c'] for r in rows],20)[-1]
        score,name,detail=strategy._daily_ema_zone(rows,d,ema20,True)
        self.assertEqual(score,3.0)
        self.assertEqual(name,'EMA20')
        self.assertIn('ema',detail)

    def test_setup_and_trigger_caps_match_new_ten_point_budget(self):
        rows=[{'o':100,'h':101,'l':99,'c':100,'v':100} for _ in range(21)]
        rows[-1]={'o':95,'h':102,'l':89,'c':102,'v':140}
        setup,_,_=strategy._setup(rows,{'lower':90,'upper':110,'j':20,'cross_up':True,'cross_down':False},True)
        self.assertEqual(setup,1.5)
        trigger,_=strategy._trigger([{'o':100,'h':101,'l':99,'c':100},{'o':99,'h':103,'l':98,'c':103}],
                                    {'ema20':101,'cross_up':True,'cross_down':False},{'ema20':100},True)
        self.assertEqual(trigger,1.0)

    def test_score_tier_scales_position_but_respects_risk_and_notional_caps(self):
        base=make_plan(Settings(max_notional=1000), '做多', test_engine.TICK, test_engine.META, 200, 100, 10, 1.0)
        high=make_plan(Settings(max_notional=1000), '做多', test_engine.TICK, test_engine.META, 200, 100, 10, 2.0)
        self.assertGreater(float(high['sz']),float(base['sz']))
        self.assertLessEqual(high['estimated_loss'],Settings().capital*.05+1e-9)
        self.assertEqual(high['position_multiplier'],2.0)

    def test_signal_ten_point_budget_and_double_trend_penalty(self):
        rows=[{'o':100,'h':101,'l':99,'c':100,'v':100,'t':i} for i in range(240)]
        ind={'ema5':100,'ema10':100,'ema20':100,'ema50':100,'ema200':100,'up':False,'down':False,'rsi':50,'atr':10,
             'upper':110,'middle':100,'lower':90,'k':50,'d':50,'j':50,'cross_up':False,'cross_down':False}
        structure_ctx={'score':1.5,'score_1h':1.0,'score_15m':.5,'score_4h':1.5,'penalty':0.0,
                       'blocked':False,'warning':False,'front_r':99.0,'favorable_4h':None,
                       'favorable_1h':None,'favorable_15m':None,'front':None,'overlap':False,
                       'zones_4h':[],'zones_1h':[],'zones_15m':[]}
        with patch('strategy.indicators',return_value=ind), \
             patch('strategy._trend_penalty',side_effect=[(-1.0,True,{}),(-1.0,True,{}),(0.0,False,{}),(0.0,False,{})]), \
             patch('strategy._setup',return_value=(1.5,{'boll':.5,'volume_boll':1.0,'kdj':.5,'reversal':.5},1.4)), \
             patch('strategy._trigger',return_value=(1.0,{'ema_reclaim':.5,'kdj':.5,'reversal':.5,'ema_direction':.5})), \
             patch('strategy._structure_context',return_value=structure_ctx), \
             patch('strategy._daily_ema_zone',return_value=(3.0,'EMA20',{})), \
             patch('strategy._rsi_resonance',return_value=True):
            out=strategy.signal(rows,rows,rows,threshold=3.5,four=rows,day=rows)
        long=out['scores']['做多']
        self.assertEqual(long['raw'],8.0)
        self.assertEqual(long['total'],8.0)
        self.assertEqual(long['position_multiplier'],2.0)
        self.assertEqual(sum(x[2] for x in long['items']),10.0)


if __name__=='__main__':
    unittest.main(verbosity=2)
''')

# ---- release notes ----
Path('RELEASE-1.3.4.md').write_text('''# KAYTRADE 1.3.4\n\n- 核心仍是均值回归，最高10分；默认自动开仓线调整为3.5/10。\n- 新增1D UTC已收盘K线，用于EMA5/EMA10/EMA20动态支撑/压力。\n- 日线EMA评分：EMA5=1分、EMA10=2分、EMA20=3分；同时满足只取最高，不叠加。\n- 取消1H趋势正向加分；1H与4H明显反向趋势分别扣1分，最多扣2分。\n- 评分预算重排：日线EMA3 + 4H结构1.5 + 1H/15m结构1.5 + RSI共振1.5 + 15m Setup1.5 + 5m Trigger1 = 10。\n- 评分仓位等级：3.5–4.5=1×、5.0–6.5=1.5×、7.0–10=2×。倍率作用于基础风险单位，不改变杠杆。\n- 高分仓位仍受日内剩余亏损额度、最大名义仓位、可用余额和资金5%绝对单笔风险上限约束。\n- 继续保留15m Setup≥0.5、5m Trigger≥0.5和前方强结构<1R禁止开仓。\n- 执行层保持V1.3.3：逐仓限价入场；SL=1×15m ATR；TP1=1R平50%，随后SL移保本；TP2=2R平剩余50%。\n- 手续费/滑点、安全停机、K线延迟保护、部分成交保护与故障锁逻辑保持不变。\n''')

print('V1.3.4 upgrade applied')

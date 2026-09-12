"""KAYTRADE V1.4.7 strategy update.

Rules on top of V1.4.6:
- remove every 1D indicator from scoring and market refresh
- forward structure space <1.3R is a hard block; >=1.3R has no penalty/warning
- 1H aligned trend +2; clear 1H countertrend is a hard block
- 4H aligned trend +1; clear 4H countertrend keeps the inherited -1 penalty
- 1m EMA direction no longer scores and cannot activate Trigger
- valid 1m Trigger = EMA20 reclaim / KDJ cross / reversal candle, capped at +1
- 5m MACD alignment is mandatory and scores +1; 5m BOLL remains +1
- V1.4 score tiers, 1:2 TP/SL, arbitration, sizing, order execution and safety stay unchanged
"""
import math
import time

from v146_ui_fix_patch import apply as apply_v146
apply_v146()

import app
import engine
import v138_strategy_patch as v138
import v140_score_dialog_patch as v140
import v143_score_arbitration_patch as v143

V147_VERSION='1.4.7'
V147_THRESHOLD=4.0
FRONT_MIN_R=1.3


def _trend_state(current,candle,buy):
    """Return aligned / opposite / neutral using the existing strict HTF trend definition."""
    close=float(candle['c'])
    if buy:
        aligned=close>float(current['ema200']) and float(current['ema20'])>float(current['ema50']) and bool(current['up'])
        opposite=close<float(current['ema200']) and float(current['ema20'])<float(current['ema50']) and bool(current['down'])
    else:
        aligned=close<float(current['ema200']) and float(current['ema20'])<float(current['ema50']) and bool(current['down'])
        opposite=close>float(current['ema200']) and float(current['ema20'])>float(current['ema50']) and bool(current['up'])
    if aligned:
        return 'aligned'
    if opposite:
        return 'opposite'
    return 'neutral'


def _valid_trigger(row):
    """EMA direction is deliberately excluded in V1.4.7."""
    detail=dict(((row.get('confirmations') or {}).get('trigger') or {}))
    value=sum(float(detail.get(name) or 0.0) for name in ('ema_reclaim','kdj','reversal'))
    return min(1.0,value),{
        'ema_reclaim':float(detail.get('ema_reclaim') or 0.0),
        'kdj':float(detail.get('kdj') or 0.0),
        'reversal':float(detail.get('reversal') or 0.0),
    }


def _five_trend(row):
    detail=dict(((row.get('confirmations') or {}).get('trend_5m') or {}))
    macd=bool(detail.get('macd'))
    boll=bool(detail.get('boll'))
    return float(int(macd)+int(boll)),macd,boll,detail


def _rewrite_row(row,side,hour,four,result):
    buy=side=='做多'
    layers=dict(row.get('layers') or {})
    confirmations=dict(row.get('confirmations') or {})
    structure=dict(row.get('structure') or {})

    trigger,trigger_detail=_valid_trigger(row)
    trend5,macd5,boll5,trend5_detail=_five_trend(row)
    trend15=float(layers.get('trend_15m') or 0.0)
    favorable15=float(layers.get('structure_15m') or 0.0)
    setup=float(layers.get('setup') or 0.0)
    rsi=float(layers.get('rsi_penalty') or 0.0)
    adverse1=float(layers.get('structure_penalty_1h') or 0.0)
    adverse4=float(layers.get('structure_penalty_4h') or 0.0)

    hstate=_trend_state(result['h'],hour[-1],buy)
    qstate=_trend_state(result['q'],four[-1],buy)
    hscore=2.0 if hstate=='aligned' else 0.0
    qscore=1.0 if qstate=='aligned' else (-1.0 if qstate=='opposite' else 0.0)

    front_r=float(structure.get('front_r',math.inf))
    front_block=math.isfinite(front_r) and front_r<FRONT_MIN_R
    structure['blocked']=front_block
    structure['penalty']=0.0
    structure['warning']=False
    structure['front_r']=front_r

    raw=favorable15+setup+trigger+trend5+trend15+rsi+adverse1+adverse4+hscore+qscore
    total=max(0.0,min(10.0,round(raw*2.0)/2.0))
    tier,multiplier,level=v140._tier(total)

    trigger_ok=trigger>0.0
    h_ok=hstate!='opposite'
    macd_ok=macd5
    gate=trigger_ok and macd_ok and h_ok and not front_block
    eligible=gate and tier>0

    if hstate=='opposite':
        reason='1H逆趋势，V1.4.7禁止该方向开仓/加仓'
    elif front_block:
        reason=f'前方强结构有效空间 {front_r:.2f}R < 1.3R，禁止开仓/加仓'
    elif not trigger_ok:
        reason='1m有效Trigger缺失：EMA20回收 / KDJ交叉 / 反转K线均未触发，禁止开仓/加仓'
    elif not macd_ok:
        reason='5m MACD未与开仓方向确认，V1.4.7禁止开仓/加仓'
    elif eligible:
        reason=f'{level} · {total:g}/10 达标；有效1m Trigger + 5m MACD确认通过'
    else:
        reason=f'{total:g}/10，未达固定开仓门槛4.0'

    row['raw']=raw
    row['total']=total
    row['gate']=gate
    row['eligible']=eligible
    row['required']=V147_THRESHOLD
    row['signal_tier']=tier
    row['position_multiplier']=multiplier if eligible else 0.0
    row['level']=f'{level} · {multiplier:g}×仓位' if tier else level
    row['reason']=reason
    row['structure']=structure
    row['items']=[
        ('15m 有利支撑/阻力结构',favorable15,.5),
        ('15m Setup（BOLL/量/KDJ/反转）',setup,1.5),
        ('1m有效Trigger（EMA20回收/KDJ/反转）',trigger,1.0),
        ('5m MACD确认',1.0 if macd5 else 0.0,1.0),
        ('5m BOLL方向',1.0 if boll5 else 0.0,1.0),
        ('15m 趋势（MACD+BOLL）',trend15,2.0),
        ('1H 顺趋势 / 逆趋势硬过滤',hscore,2.0),
        ('4H 顺趋势 / 逆趋势惩罚',qscore,1.0),
        ('5m+15m RSI过热/超卖惩罚',rsi,0),
        ('1H 不利支撑/阻力结构',adverse1,0),
        ('4H 不利支撑/阻力结构',adverse4,0),
        ('前方结构空间：<1.3R硬过滤',0.0,0),
    ]

    layers.pop('daily_ema',None)
    layers.update({
        'structure_15m':favorable15,
        'setup':setup,
        'trigger':trigger,
        'trend_5m':trend5,
        'trend_15m':trend15,
        'trend_1h':hscore,
        'trend_4h':qscore,
        'rsi_penalty':rsi,
        'structure_penalty_1h':adverse1,
        'structure_penalty_4h':adverse4,
        'front_penalty':0.0,
    })
    row['layers']=layers

    confirmations.pop('daily_ema',None)
    confirmations.pop('daily_ema_detail',None)
    confirmations['trigger']=trigger_detail
    confirmations['valid_1m_trigger']=trigger_ok
    confirmations['trend_5m']=trend5_detail
    confirmations['5m_macd_required']=macd_ok
    confirmations['5m_boll']=boll5
    confirmations['1H_trend_state']=hstate
    confirmations['4H_trend_state']=qstate
    confirmations['1H_countertrend']=hstate=='opposite'
    confirmations['4H_countertrend']=qstate=='opposite'
    confirmations['structure']=structure
    row['confirmations']=confirmations
    return row


def _rewrite_result(result,hour,four):
    scores=result.get('scores') or {}
    for side in ('做多','做空'):
        if side in scores:
            _rewrite_row(scores[side],side,hour,four,result)
    result['scores']=scores
    result['threshold']=V147_THRESHOLD
    result['score_max']=10.0
    result['strategy_version']=V147_VERSION
    result.pop('d',None)
    return v143._apply_arbitration(result)


def apply():
    if getattr(engine.Engine,'_kaytrade_v147_applied',False):
        return

    previous_signal=v138.v138_signal
    previous_cycle=engine.Engine.cycle
    previous_app_init=app.App.__init__
    previous_app_arm=app.App.arm

    def v147_signal(hour,quarter,five,one,stop_atr=1.0,four=None,day=None):
        four=hour if four is None else four
        # V1.4.7 intentionally never consumes 1D data. Pass 4H only as a compatibility
        # placeholder to the inherited scorer, then remove its daily contribution.
        result=previous_signal(hour,quarter,five,one,stop_atr,four=four,day=four)
        return _rewrite_result(result,hour,four)

    def refresh_market(self):
        # 1Dutc is intentionally absent from V1.4.7.
        q=self.x.candles('4H')
        h=self.x.candles('1H')
        m=self.x.candles('15m')
        f=self.x.candles('5m')
        o=self.x.candles('1m')
        self._verify_latest('4H',q[-1]['t'],14400000)
        self._verify_latest('1H',h[-1]['t'],3600000)
        self._verify_latest('15m',m[-1]['t'],900000)
        self._verify_latest('5m',f[-1]['t'],300000)
        self._verify_latest('1m',o[-1]['t'],v138.ONE_MINUTE_STEP)
        value=v147_signal(h,m,f,o,self.settings.stop_atr if self.settings else 1.0,four=q)
        self.market=dict(value,bar=o[-1]['t'],bar1m=o[-1]['t'],bar5m=f[-1]['t'],bar15=m[-1]['t'],
                         bar1h=h[-1]['t'],bar4h=q[-1]['t'],close=o[-1]['c'])
        self.market_at=time.time()
        self.market_monotonic=time.monotonic()
        self._candle_recovered()
        self.emit('market',self.market)

    def cycle(self):
        result=previous_cycle(self)
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict):
            changed=p.get('version')!=V147_VERSION or not p.get('v147')
            p['v147']=True
            p['version']=V147_VERSION
            if changed:
                self.store.save()
        return result

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:
            self.root.title('KAYTRADE 1.4.7 · BTC 策略控制台')
        except Exception:
            pass
        for widget in v138._widgets(self.root):
            try:
                text=str(widget.cget('text') or '')
                if '1.4.6' in text:
                    widget.configure(text=text.replace('1.4.6','1.4.7'))
            except Exception:
                pass

    def app_arm(self):
        original=app.simpledialog.askstring
        def askstring(title,prompt,*args,**kwargs):
            text=str(prompt)+(\
                '\n\nV1.4.7策略：删除全部1D指标；前方空间<1.3R禁止开仓；1H逆势禁止、顺势+2；4H顺势+1；'
                '1m EMA方向不计分，有效Trigger仅EMA20回收/KDJ/反转；5m MACD必须确认且+1分。')
            return original(title,text,*args,**kwargs)
        app.simpledialog.askstring=askstring
        try:
            return previous_app_arm(self)
        finally:
            app.simpledialog.askstring=original

    v138.v138_signal=v147_signal
    engine.Engine.refresh_market=refresh_market
    engine.Engine.cycle=cycle
    app.App.__init__=app_init
    app.App.arm=app_arm
    engine.Engine._kaytrade_v147_applied=True


apply()

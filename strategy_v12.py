"""V1.2 BTC intraday multi-strategy signal engine.

Pure decision module: no network and no exchange writes. It classifies market
regime, scores several independent setups and returns one candidate signal.
Only closed candles should be passed in by the caller.
"""
from dataclasses import dataclass, asdict
import math


@dataclass(frozen=True)
class StrategyConfig:
    trade_score: float = 8.0
    watch_score: float = 6.0
    strong_adx: float = 28.0
    trend_adx: float = 20.0
    range_adx: float = 18.0
    chop_range: float = 60.0
    volume_confirm: float = 1.20
    breakout_volume: float = 1.50
    max_entry_atr_chase: float = 0.30

    def validate(self):
        if not 6 <= self.trade_score <= 10:
            raise ValueError('trade_score must be 6..10')
        if not 5 <= self.watch_score <= self.trade_score:
            raise ValueError('watch_score must be 5..trade_score')
        return self


def _ema(values, period):
    out=[]; value=values[0]; a=2/(period+1)
    for x in values:
        value += a*(x-value); out.append(value)
    return out


def _wilder(values, period=14):
    if len(values) < period:
        raise ValueError('insufficient data')
    value=sum(values[:period])/period
    for x in values[period:]:
        value=(value*(period-1)+x)/period
    return value


def _rsi(close, period=14):
    gains=[max(b-a,0) for a,b in zip(close,close[1:])]
    losses=[max(a-b,0) for a,b in zip(close,close[1:])]
    g,l=_wilder(gains,period),_wilder(losses,period)
    if g==l==0: return 50.0
    if l==0: return 100.0
    return 100-100/(1+g/l)


def _atr(rows, period=14):
    tr=[max(b['h']-b['l'],abs(b['h']-a['c']),abs(b['l']-a['c'])) for a,b in zip(rows,rows[1:])]
    return _wilder(tr,period)


def _adx(rows, period=14):
    if len(rows) < period*3:
        raise ValueError('insufficient ADX data')
    trs=[]; plus=[]; minus=[]
    for a,b in zip(rows,rows[1:]):
        up=b['h']-a['h']; down=a['l']-b['l']
        plus.append(up if up>down and up>0 else 0.0)
        minus.append(down if down>up and down>0 else 0.0)
        trs.append(max(b['h']-b['l'],abs(b['h']-a['c']),abs(b['l']-a['c'])))
    atr=_wilder(trs[:period],period) if len(trs[:period])>=period else sum(trs[:period])/period
    p=sum(plus[:period])/period; m=sum(minus[:period])/period
    dx=[]
    for i in range(period,len(trs)):
        atr=(atr*(period-1)+trs[i])/period
        p=(p*(period-1)+plus[i])/period; m=(m*(period-1)+minus[i])/period
        pdi=100*p/atr if atr else 0; mdi=100*m/atr if atr else 0
        dx.append(100*abs(pdi-mdi)/(pdi+mdi) if pdi+mdi else 0)
    return _wilder(dx,period) if len(dx)>=period else sum(dx)/len(dx)


def _boll(rows, period=20):
    close=[r['c'] for r in rows]
    mean=sum(close[-period:])/period
    sd=math.sqrt(sum((x-mean)**2 for x in close[-period:])/period)
    upper=mean+2*sd; lower=mean-2*sd
    width=(upper-lower)/mean if mean else 0
    return mean,upper,lower,width


def _chop(rows, period=14):
    recent=rows[-period:]
    atr_sum=0.0
    prev=rows[-period-1]
    for r in recent:
        atr_sum += max(r['h']-r['l'],abs(r['h']-prev['c']),abs(r['l']-prev['c']))
        prev=r
    span=max(r['h'] for r in recent)-min(r['l'] for r in recent)
    if span<=0 or atr_sum<=0: return 100.0
    return 100*math.log10(atr_sum/span)/math.log10(period)


def _volume_ratio(rows, period=20):
    vals=[float(r.get('vol',0) or 0) for r in rows]
    if not vals or vals[-1]<=0 or sum(vals[-period-1:-1])<=0:
        return 1.0
    return vals[-1]/(sum(vals[-period-1:-1])/period)


def _features(rows):
    if len(rows)<220:
        raise ValueError('need at least 220 closed candles')
    close=[r['c'] for r in rows]
    e9=_ema(close,9); e20=_ema(close,20); e60=_ema(close,60); e200=_ema(close,200)
    middle,upper,lower,width=_boll(rows)
    prev_middle,prev_upper,prev_lower,prev_width=_boll(rows[:-1])
    atr=_atr(rows); adx=_adx(rows); rsi=_rsi(close); chop=_chop(rows)
    return dict(price=close[-1],ema9=e9[-1],ema20=e20[-1],ema60=e60[-1],ema200=e200[-1],
                ema9_prev=e9[-2],ema20_prev=e20[-2],rsi=rsi,atr=atr,adx=adx,chop=chop,
                middle=middle,upper=upper,lower=lower,boll_width=width,boll_width_prev=prev_width,
                vol_ratio=_volume_ratio(rows),high20=max(r['h'] for r in rows[-21:-1]),
                low20=min(r['l'] for r in rows[-21:-1]),body=abs(rows[-1]['c']-rows[-1]['o']),
                candle_range=max(rows[-1]['h']-rows[-1]['l'],1e-12),
                bull=rows[-1]['c']>rows[-1]['o'],bear=rows[-1]['c']<rows[-1]['o'])


def classify_regime(h,m5,cfg=StrategyConfig()):
    cfg.validate()
    trend_up=h['price']>h['ema200'] and h['ema20']>h['ema60']
    trend_down=h['price']<h['ema200'] and h['ema20']<h['ema60']
    if h['adx']>=cfg.strong_adx and (trend_up or trend_down):
        return 'STRONG_TREND_UP' if trend_up else 'STRONG_TREND_DOWN'
    if h['adx']>=cfg.trend_adx and (trend_up or trend_down):
        return 'TREND_UP' if trend_up else 'TREND_DOWN'
    if m5['adx']<=cfg.range_adx or m5['chop']>=cfg.chop_range:
        return 'RANGE'
    if m5['boll_width'] < m5['boll_width_prev'] and m5['adx']<cfg.trend_adx:
        return 'SQUEEZE'
    return 'MIXED'


def _result(name,side,score,parts,reason):
    return dict(strategy=name,side=side,score=round(min(score,10),2),parts=parts,reason=reason)


def score_trend_pullback(h,m15,m5,regime,cfg):
    out=[]
    for side,direction in [('做多',1),('做空',-1)]:
        parts={}; score=0.0
        aligned=(direction==1 and regime in ('TREND_UP','STRONG_TREND_UP')) or (direction==-1 and regime in ('TREND_DOWN','STRONG_TREND_DOWN'))
        if aligned: parts['1H趋势环境']=2; score+=2
        if m15['adx']>=cfg.trend_adx: parts['15M ADX']=1; score+=1
        near=min(abs(m5['price']-m5['ema20']),abs(m5['price']-m15['ema20'])) <= .35*m15['atr']
        if near: parts['回踩EMA']=2; score+=2
        rsi_ok=40<=m5['rsi']<=58 if direction==1 else 42<=m5['rsi']<=60
        if rsi_ok: parts['RSI位置']=1; score+=1
        cross=(m5['ema9_prev']<=m5['ema20_prev'] and m5['ema9']>m5['ema20']) if direction==1 else (m5['ema9_prev']>=m5['ema20_prev'] and m5['ema9']<m5['ema20'])
        if cross: parts['EMA9/20重新交叉']=1; score+=1
        structure=(m5['bull'] and m5['price']>m5['ema20']) if direction==1 else (m5['bear'] and m5['price']<m5['ema20'])
        if structure: parts['价格确认']=1; score+=1
        if m5['vol_ratio']>=cfg.volume_confirm: parts['成交量确认']=1; score+=1
        if .35*m15['atr']<=m5['atr']<=1.8*m15['atr']: parts['ATR正常']=1; score+=1
        out.append(_result('Trend Pullback',side,score,parts,'顺势回调后寻找5M重新启动'))
    return out


def score_breakout(h,m15,m5,regime,cfg):
    out=[]
    for side,direction in [('做多',1),('做空',-1)]:
        parts={}; score=0.0
        breakout=m5['price']>m5['high20'] if direction==1 else m5['price']<m5['low20']
        if breakout: parts['Donchian20突破']=2; score+=2
        if m5['boll_width']>m5['boll_width_prev']*1.05: parts['BOLL Width扩张']=2; score+=2
        if m5['vol_ratio']>=cfg.breakout_volume: parts['突破成交量']=2; score+=2
        aligned=(direction==1 and h['ema20']>h['ema60']) or (direction==-1 and h['ema20']<h['ema60'])
        if aligned: parts['1H方向一致']=1; score+=1
        if m5['adx']>=cfg.trend_adx: parts['ADX扩张']=1; score+=1
        if m5['body']/m5['candle_range']>=.6: parts['实体有效']=1; score+=1
        direction_candle=m5['bull'] if direction==1 else m5['bear']
        if direction_candle: parts['K线方向确认']=1; score+=1
        out.append(_result('Momentum Breakout',side,score,parts,'20周期突破 + 波动/成交量扩张'))
    return out


def score_mean_reversion(h,m15,m5,regime,cfg):
    out=[]
    allowed=regime=='RANGE'
    for side,direction in [('做多',1),('做空',-1)]:
        parts={}; score=0.0
        if allowed: parts['震荡环境']=2; score+=2
        extreme=m5['price']<=m5['lower'] if direction==1 else m5['price']>=m5['upper']
        if extreme: parts['BOLL极值']=2; score+=2
        rsi_ext=m5['rsi']<=30 if direction==1 else m5['rsi']>=70
        if rsi_ext: parts['5M RSI极值']=2; score+=2
        dev=abs(m5['price']-m5['middle'])/m5['atr'] if m5['atr'] else 0
        if dev>=.8: parts['均值偏离']=1; score+=1
        reversal=(m5['bull'] if direction==1 else m5['bear'])
        if reversal: parts['反转K线']=1; score+=1
        if m5['vol_ratio']>=cfg.volume_confirm: parts['成交量确认']=1; score+=1
        not_strong=not regime.startswith('STRONG_TREND')
        if not_strong: parts['无强趋势冲突']=1; score+=1
        out.append(_result('Mean Reversion',side,score,parts,'仅震荡环境启用的均值回归'))
    return out


def score_extreme_reversal(h,m15,m5,regime,cfg):
    out=[]
    for side,direction in [('做多',1),('做空',-1)]:
        parts={}; score=0.0
        h_ext=h['rsi']<=25 if direction==1 else h['rsi']>=70
        m_ext=m15['rsi']<=20 if direction==1 else m15['rsi']>=75
        if h_ext: parts['1H RSI极端']=2; score+=2
        if m_ext: parts['15M RSI极端']=2; score+=2
        boll=m15['price']<=m15['lower'] if direction==1 else m15['price']>=m15['upper']
        if boll: parts['15M BOLL极值']=1; score+=1
        dev=abs(m15['price']-m15['ema20'])/m15['atr'] if m15['atr'] else 0
        if dev>=1.0: parts['EMA过度偏离']=1; score+=1
        reversal=(m5['bull'] and m5['price']>m5['ema9']) if direction==1 else (m5['bear'] and m5['price']<m5['ema9'])
        if reversal: parts['5M反转确认']=2; score+=2
        if m5['vol_ratio']>=cfg.volume_confirm: parts['成交量确认']=1; score+=1
        strong_against=(direction==1 and regime=='STRONG_TREND_DOWN') or (direction==-1 and regime=='STRONG_TREND_UP')
        if not strong_against: parts['无强趋势逆风']=1; score+=1
        out.append(_result('Extreme Reversal',side,score,parts,'保留V1.1极端反转，但加入5M确认与强趋势过滤'))
    return out


def signal_v12(hour, quarter, five, cfg=StrategyConfig()):
    """Return all scores plus the highest eligible candidate.

    No order is produced here. Caller must still apply account/risk/network
    guards before any exchange write.
    """
    cfg.validate()
    h,m15,m5=_features(hour),_features(quarter),_features(five)
    regime=classify_regime(h,m5,cfg)
    candidates=[]
    candidates += score_trend_pullback(h,m15,m5,regime,cfg)
    candidates += score_breakout(h,m15,m5,regime,cfg)
    candidates += score_mean_reversion(h,m15,m5,regime,cfg)
    candidates += score_extreme_reversal(h,m15,m5,regime,cfg)
    candidates.sort(key=lambda x:x['score'],reverse=True)
    best=candidates[0]
    # Hard routing: avoid counter-trend mean-reversion in strong trends.
    eligible=[]
    for c in candidates:
        if regime=='STRONG_TREND_UP' and c['side']=='做空' and c['strategy'] in ('Mean Reversion','Extreme Reversal'):
            continue
        if regime=='STRONG_TREND_DOWN' and c['side']=='做多' and c['strategy'] in ('Mean Reversion','Extreme Reversal'):
            continue
        eligible.append(c)
    best=eligible[0] if eligible else best
    side=best['side'] if best['score']>=cfg.trade_score else '观望'
    state='TRADE' if best['score']>=cfg.trade_score else 'WATCH' if best['score']>=cfg.watch_score else 'NO_TRADE'
    return dict(version='1.2',regime=regime,side=side,state=state,best=best,candidates=candidates,
                h=h,m15=m15,m5=m5,config=asdict(cfg))

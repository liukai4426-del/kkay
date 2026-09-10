"""Closed-candle cache and bounded settlement wait; never returns stale signals."""
import math
from datetime import datetime, timezone
from core import INSTRUMENT

class CandlePending(RuntimeError):
    """Latest closed candle is still settling within the 45-second window."""

class CandleStale(RuntimeError):
    """Candle data is structurally unsafe and requires fail-closed handling."""

class CandleLag(CandleStale):
    """Expected closed candle is late; callers may retry before requiring manual action."""

def expected_bar(now, step):
    return int(now*1000)//step*step-step

def check_latest(bar, timestamp, now, step):
    expected=expected_bar(now,step)
    if timestamp==expected:return
    close=datetime.fromtimestamp((timestamp+step)/1000,timezone.utc).strftime('%H:%M:%S UTC')
    current=datetime.fromtimestamp(now,timezone.utc).strftime('%H:%M:%S UTC')
    lag=max(0,(expected-timestamp)/1000)
    message=f'{bar} K线等待更新：最新收盘 {close}，交易所时间 {current}，落后应有K线 {lag:.0f}秒；不使用旧信号'
    boundary=expected+step
    if timestamp==expected-step and 0<=now*1000-boundary<=45000:
        raise CandlePending(message)
    raise CandleLag(message.replace('等待更新','持续过期/时间异常，已暂停本轮判断'))

class CandleCache:
    def __init__(self,api):
        self.api=api; self.rows={}

    def read(self,bar):
        steps={'1Dutc':86400000,'4H':14400000,'1H':3600000,'15m':900000,'5m':300000}
        if bar not in steps:raise ValueError('不支持的K线周期')
        step=steps[bar]
        rows=dict(self.rows.get(bar,{}))
        now=self.api.server_now()
        if rows and max(rows)==expected_bar(now,step):
            return [dict(t=t,**v) for t,v in sorted(rows.items())]

        def merge(batch):
            for row in batch:
                if len(row)<9:raise CandleStale('K线响应字段缺失，暂停判断')
                if str(row[8])!='1':continue
                t=int(row[0]); values=[float(v) for v in row[1:5]]; volume=float(row[5])
                if t%step or not all(math.isfinite(v) and v>0 for v in values) or not math.isfinite(volume) or volume<0:
                    raise CandleStale('K线时间、价格或成交量异常，暂停判断')
                rows[t]=dict(zip(('o','h','l','c'),values)); rows[t]['v']=volume

        newest=self.api.get('/api/v5/market/candles',{'instId':INSTRUMENT,'bar':bar,'limit':'300'})
        merge(newest)
        # Warm up once. Subsequent updates merge the newest page into the cache.
        if not self.rows.get(bar):
            after=min((int(r[0]) for r in newest),default=None)
            for _ in range(4):
                if after is None or len(rows)>=1200:break
                batch=self.api.get('/api/v5/market/history-candles',{'instId':INSTRUMENT,'bar':bar,'limit':'300','after':str(after)})
                if not batch:break
                merge(batch)
                cursor=min(int(r[0]) for r in batch)
                if cursor>=after:break
                after=cursor
        keys=sorted(rows)[-1500:]
        if len(keys)<1000 or any(b-a!=step for a,b in zip(keys,keys[1:])):
            self.rows.pop(bar,None)
            raise CandleStale(f'{bar} K线不足1000根或存在缺口，暂停判断；下次重新加载')
        rows={t:rows[t] for t in keys}
        self.rows[bar]=rows
        check_latest(bar,keys[-1],self.api.server_now(),step)
        return [dict(t=t,**v) for t,v in sorted(rows.items())]

"""Standard-library-only OKX reader and local paper ledger. No exchange writes."""
import base64
import hashlib
import hmac
import json
import math
import os
import time
import urllib.parse
import urllib.error
import urllib.request
import ssl
from datetime import datetime, timezone
from pathlib import Path

HOSTS = ('www.okx.com', 'eea.okx.com', 'us.okx.com')
INSTRUMENT = 'BTC-USDT-SWAP'

class Client:
    def __init__(self, host=HOSTS[0], key='', secret='', phrase='', demo=False):
        if host not in HOSTS:
            raise ValueError('仅允许官方 OKX 域名')
        self.host, self.key, self.secret, self.phrase, self.demo = host, key, secret, phrase, demo
        # The frozen macOS app bundles certifi's roots.  Use them explicitly
        # instead of relying on a process environment variable being inherited.
        try:
            import certifi
            self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            self.ssl_context = ssl.create_default_context()

    def get(self, path, params=None, private=False):
        if not path.startswith('/api/v5/'):
            raise ValueError('非法API路径')
        query = '?' + urllib.parse.urlencode(params) if params else ''
        target = path + query
        headers = {'Accept': 'application/json', 'User-Agent': 'OKX-Mac-Reader/1.0'}
        if private:
            if not all((self.key, self.secret, self.phrase)):
                raise ValueError('请填写三个密钥字段；仅保存在本次运行内存中')
            ts = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
            sig = base64.b64encode(hmac.new(self.secret.encode(), (ts+'GET'+target).encode(), hashlib.sha256).digest()).decode()
            headers.update({'OK-ACCESS-KEY': self.key, 'OK-ACCESS-SIGN': sig,
                            'OK-ACCESS-TIMESTAMP': ts, 'OK-ACCESS-PASSPHRASE': self.phrase,
                            'x-simulated-trading': '1' if self.demo else '0'})
        # Do not forward credentials to redirects or honor proxy environment settings.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=self.ssl_context))
        req = urllib.request.Request('https://'+self.host+target, headers=headers, method='GET')
        try:
            with opener.open(req, timeout=15) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'HTTP {exc.code}：OKX拒绝了请求；请核对账户域名和网络限制') from None
        except urllib.error.URLError as exc:
            reason = str(exc.reason).replace('\n', ' ')[:180]
            raise RuntimeError('HTTPS连接失败：'+reason+'。未提交订单') from None
        except TimeoutError:
            raise RuntimeError('HTTPS连接超时：网络未能直连OKX；未提交订单') from None
        except Exception as exc:
            raise RuntimeError('HTTPS连接失败：'+type(exc).__name__+'；未提交订单') from None
        if result.get('code') != '0':
            raise RuntimeError('OKX 返回错误码 '+str(result.get('code'))+'；请核对密钥权限、IP限制和账户环境')
        return result['data']

    def candles(self, bar):
        # Multiple pages provide a >1,000-bar EMA warm-up. Only closed candles count.
        rows, after = {}, None
        for _ in range(5):
            args = {'instId': INSTRUMENT, 'bar': bar, 'limit': '300'}
            if after:
                args['after'] = after
            batch = self.get('/api/v5/market/history-candles', args)
            for row in batch:
                if row[8] == '1':
                    values = [float(v) for v in row[1:5]]
                    if not all(math.isfinite(v) and v > 0 for v in values):
                        raise ValueError('行情包含非法价格')
                    volume=float(row[5])
                    if not math.isfinite(volume) or volume < 0:
                        raise ValueError('行情包含非法成交量')
                    rows[int(row[0])] = dict(zip(('o','h','l','c'), values)); rows[int(row[0])]['v']=volume
            if not batch:
                break
            after = str(min(int(r[0]) for r in batch))
            time.sleep(.12)
        data = [dict(t=t, **rows[t]) for t in sorted(rows)]
        step = 3600000 if bar == '1H' else 900000 if bar == '15m' else 300000 if bar == '5m' else 0
        if not step or len(data) < 1000 or any(b['t']-a['t'] != step for a,b in zip(data, data[1:])):
            raise ValueError('K线不足1000根或存在缺口，暂停策略判断')
        if time.time()*1000 - (data[-1]['t']+step) > step:
            raise ValueError('K线已过期，暂停策略判断')
        return data

def ema(values, period):
    out, value = [], values[0]
    for v in values:
        value += 2/(period+1)*(v-value)
        out.append(value)
    return out

def indicators(rows):
    close = [r['c'] for r in rows]
    if len(close) < 201:
        raise ValueError('指标数据不足')
    gains = [max(b-a,0) for a,b in zip(close,close[1:])]
    losses = [max(a-b,0) for a,b in zip(close,close[1:])]
    def wilder(a):
        v = sum(a[:14])/14
        for x in a[14:]:
            v = (v*13+x)/14
        return v
    g,l = wilder(gains),wilder(losses)
    rsi = 50 if g == l == 0 else 100 if l == 0 else 100-100/(1+g/l)
    tr = [max(b['h']-b['l'],abs(b['h']-a['c']),abs(b['l']-a['c'])) for a,b in zip(rows,rows[1:])]
    mean = sum(close[-20:])/20
    sd = math.sqrt(sum((x-mean)**2 for x in close[-20:])/20)
    k=d=50.
    pk=pd=50.
    for i in range(8,len(rows)):
        low=min(r['l'] for r in rows[i-8:i+1]); high=max(r['h'] for r in rows[i-8:i+1])
        rsv=50 if high==low else 100*(close[i]-low)/(high-low)
        pk,pd=k,d
        k=(2*k+rsv)/3; d=(2*d+k)/3
    e20,e50=ema(close,20),ema(close,50)
    return dict(ema20=e20[-1],ema50=e50[-1],ema200=ema(close,200)[-1],
                up=e20[-1]>e20[-2] and e50[-1]>e50[-2],
                down=e20[-1]<e20[-2] and e50[-1]<e50[-2],rsi=rsi,atr=wilder(tr),
                upper=mean+2*sd,middle=mean,lower=mean-2*sd,k=k,d=d,j=3*k-2*d,
                cross_up=pk<=pd and k>d,cross_down=pk>=pd and k<d)

def signal(hour, quarter):
    h,m=indicators(hour),indicators(quarter)
    price=quarter[-1]['c']; side='观望'; why='趋势、回调或交叉条件未同时满足'
    long=hour[-1]['c']>h['ema200'] and h['ema20']>h['ema50'] and h['up'] and 50<=h['rsi']<=70
    short=hour[-1]['c']<h['ema200'] and h['ema20']<h['ema50'] and h['down'] and 30<=h['rsi']<50
    near=abs(price-m['ema20'])<=.55*h['atr']
    if long and near and m['cross_up'] and 20<=m['k']<=40 and m['rsi']>=50 and price>m['ema20']:
        side='做多'
    if short and near and m['cross_down'] and 60<=m['k']<=80 and m['rsi']<50 and price<m['ema20']:
        side='做空'
    plan=None
    if side!='观望':
        direction=1 if side=='做多' else -1
        stop=(min(r['l'] for r in quarter[-5:])-.15*h['atr'] if direction==1
              else max(r['h'] for r in quarter[-5:])+.15*h['atr'])
        dist=direction*(price-stop)
        if dist < .6*h['atr']:
            dist=.6*h['atr']; stop=price-direction*dist
        if dist>1.2*h['atr'] or dist<=0:
            side='观望'; why='结构止损距离超限'
        else:
            plan=dict(side=side,entry=price,sl=stop,tp1=price+direction*dist,tp2=price+direction*2*dist)
            why='已收盘K线满足趋势、动能和KDJ交叉；规则信号，不是GPT/Gemini分析'
    return dict(h=h,m=m,side=side,why=why,plan=plan)

class Ledger:
    def __init__(self,path):
        self.path=Path(path)
        self.data={'cash':10000.,'position':None,'history':[]}
        if self.path.exists():
            self.data=json.loads(self.path.read_text())
        self.fee=.0005  # Simulation assumption, not the user's fee tier.

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        tmp=self.path.with_suffix('.tmp')
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:
            json.dump(self.data,f,ensure_ascii=False,indent=2)
        os.replace(tmp,self.path)

    def open(self,side,price,atr):
        if self.data['position']:
            raise ValueError('已有模拟持仓，请先平仓')
        if side not in ('做多','做空') or not all(math.isfinite(v) and v>0 for v in (price,atr)):
            raise ValueError('无效方向、价格或ATR')
        distance=.8*atr
        cash=self.data['cash']; risk=cash*.005
        qty=min(risk/(distance+2*price*self.fee),cash*.2*5/price)
        if qty<=0:
            raise ValueError('模拟资金不足')
        direction=1 if side=='做多' else -1
        self.data['cash']-=price*qty*self.fee
        self.data['position']=dict(side=side,entry=price,qty=qty,remaining=qty,
            sl=price-direction*distance,tp1=price+direction*distance,tp2=price+direction*2*distance,tp1_done=False)
        self.save()

    def close(self,price,fraction=1.,reason='手动平仓'):
        p=self.data['position']
        if not p or not math.isfinite(price) or price<=0 or not 0<fraction<=1:
            raise ValueError('无法平仓')
        qty=p['remaining']*fraction
        pnl=(price-p['entry'])*qty*(1 if p['side']=='做多' else -1)-price*qty*self.fee
        self.data['cash']+=pnl
        self.data['history'].append(dict(time=datetime.now().isoformat(timespec='seconds'),side=p['side'],qty=qty,price=price,pnl=pnl,reason=reason))
        p['remaining']-=qty
        if p['remaining']<1e-12:
            self.data['position']=None
        self.save()

    def mark(self,price):
        p=self.data['position']
        if not p:
            return
        d=1 if p['side']=='做多' else -1
        if d*(price-p['sl'])<=0:
            self.close(price,reason='采样价触发SL'); return
        if not p['tp1_done'] and d*(price-p['tp1'])>=0:
            self.close(price,.5,'采样价触发TP1'); p['tp1_done']=True; p['sl']=p['entry']; self.save()
        if d*(price-p['tp2'])>=0:
            self.close(price,reason='采样价触发TP2')

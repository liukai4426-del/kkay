"""HTTPS adapter: strict hosts/routes; credentials remain in memory; never retry writes."""
import base64
import hashlib
import hmac
import json
import time
import socket
import ssl
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from core import Client, HOSTS, INSTRUMENT
from candles import CandleCache

class APIError(RuntimeError):
    def __init__(self,message,code='',deterministic=False,http_status=None,method='',path=''):
        super().__init__(message)
        self.code=str(code or '')
        self.deterministic=bool(deterministic)
        self.http_status=http_status
        self.method=str(method or '')
        self.path=str(path or '')
        # Only an explicit POST rejection may prove that a write was not accepted.
        # Read/query errors must never clear an ambiguous pre-submit placeholder.
        self.write_rejected=bool(self.deterministic and self.method=='POST')

class NetworkError(APIError):
    def __init__(self,message,code='',http_status=None,method='',path=''):
        super().__init__(message,code,False,http_status,method,path)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class Exchange(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.offset=0
        self.clock_anchor=None
        self.candle_cache=CandleCache(self)
        self.network_event=lambda text: None
        self.opener=urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=self.ssl_context))

    def request(self, method, path, params=None, private=False):
        attempts=3 if method=='GET' else 1
        for attempt in range(attempts):
            try:
                sent=time.time()
                result=self._request_once(method,path,params,private)
                self.last_timing=(sent,time.time())
                self.network_event('正常')
                return result
            except NetworkError:
                if attempt+1==attempts:
                    self.network_event('已暂停：连接失败')
                    raise
                self.network_event(f'重连中：只读请求 {attempt+2}/{attempts}')
                time.sleep(2**attempt)

    def _request_once(self, method, path, params=None, private=False):
        allowed={'/api/v5/trade/order','/api/v5/trade/cancel-order','/api/v5/account/set-leverage'}
        if method not in ('GET','POST') or (method=='POST' and path not in allowed):
            raise APIError('不允许此写入操作',method=method,path=path)
        if not path.startswith('/api/v5/') or '?' in path:
            raise APIError('非法路径',method=method,path=path)
        target=path+('?' + urllib.parse.urlencode(params) if method=='GET' and params else '')
        raw=json.dumps(params,separators=(',',':')) if method=='POST' else ''
        headers={'Content-Type':'application/json','User-Agent':'OKXMac/1.0','Accept':'application/json'}
        if private:
            if not all((self.key,self.secret,self.phrase)):
                raise APIError('请填写API Key、Secret、Passphrase',method=method,path=path)
            timestamp=datetime.fromtimestamp(self.server_now(),timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
            signature=base64.b64encode(hmac.new(self.secret.encode(),(timestamp+method+target+raw).encode(),hashlib.sha256).digest()).decode()
            headers.update({'OK-ACCESS-KEY':self.key,'OK-ACCESS-SIGN':signature,
                'OK-ACCESS-TIMESTAMP':timestamp,'OK-ACCESS-PASSPHRASE':self.phrase,
                'x-simulated-trading':'1' if self.demo else '0'})
        req=urllib.request.Request('https://'+self.host+target,data=raw.encode() if raw else None,headers=headers,method=method)
        try:
            with self.opener.open(req,timeout=10) as response:
                result=json.load(response)
        except urllib.error.HTTPError as exc:
            # 408/429 and conflict-style 409/425 responses are kept ambiguous for
            # trading writes. A conflict can mean the server has state related to
            # this request, so fail closed and reconcile instead of assuming reject.
            ambiguous_http={408,409,425,429}
            rejected_http=400<=exc.code<500 and exc.code not in ambiguous_http
            suffix='只读请求失败，不会下单' if method=='GET' else '写入请求被拒绝' if rejected_http else '写入结果需核对，禁止重复提交'
            code=''; msg=''
            try:
                payload=json.loads(exc.read(4096))
                value=str(payload.get('code','') or '')
                if value.isdigit(): code=value
                msg=str(payload.get('msg','') or '')[:160]
            except Exception:
                pass
            detail=(f' / OKX {code}' if code else '')+(f'：{msg}' if msg else '')
            write_rejected=bool(method=='POST' and rejected_http)
            if write_rejected:
                raise APIError(f'HTTP {exc.code}{detail}；{suffix}',code,True,exc.code,method,path) from None
            if exc.code in (408,429,500,502,503,504):
                raise NetworkError(f'HTTP {exc.code}{detail}；{suffix}',code,exc.code,method,path) from None
            raise APIError(f'HTTP {exc.code}{detail}；{suffix}',code,False,exc.code,method,path) from None
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            cause=exc.reason if isinstance(exc,urllib.error.URLError) else exc
            category=('TLS证书验证失败' if isinstance(cause,ssl.SSLCertVerificationError) else
                      'TLS连接中断' if isinstance(cause,ssl.SSLError) else
                      'DNS解析失败' if isinstance(cause,socket.gaierror) else
                      '连接超时' if isinstance(cause,TimeoutError) else '网络连接失败')
            suffix='只读请求失败，不会下单' if method=='GET' else '写入结果未知，请在OKX核对，禁止重复提交'
            if isinstance(cause,ssl.SSLCertVerificationError):
                raise APIError(category+'；'+suffix,method=method,path=path) from None
            raise NetworkError(category+'；'+suffix,method=method,path=path) from None
        except Exception as exc:
            raise APIError('HTTPS连接失败：'+type(exc).__name__+'；若刚提交订单，结果可能未知，请勿重复提交',method=method,path=path) from None
        if str(result.get('code',''))!='0':
            code=str(result.get('code') or '')
            msg=str(result.get('msg') or '')[:160]
            if code=='51603':
                raise APIError('OKX错误码 51603：订单不存在或暂未可查询',code,False,None,method,path)
            suffix=('：'+msg) if msg else ''
            raise APIError('OKX错误码 '+code+suffix,code,method=='POST',None,method,path)
        data=result.get('data')
        if not isinstance(data,list):
            raise APIError('OKX响应结构异常',method=method,path=path)
        for row in data:
            if isinstance(row,dict):
                scode=row.get('sCode')
                if scode is not None and str(scode)!='0':
                    code=str(scode or '')
                    msg=str(row.get('sMsg') or '')[:160]
                    suffix=('：'+msg) if msg else ''
                    raise APIError('OKX订单级错误码 '+code+suffix,code,method=='POST',None,method,path)
        return data

    def get(self,path,params=None,private=False):
        return self.request('GET',path,params,private)

    def post(self,path,body):
        return self.request('POST',path,body,True)

    def sync_time(self):
        start=time.time()
        server=float(self.get('/api/v5/public/time')[0]['ts'])/1000
        start,end=getattr(self,'last_timing',(start,time.time()))
        if end-start>3:
            raise APIError('时钟同步延迟过高')
        self.offset=server-(start+end)/2
        self.clock_anchor=(server+(end-start)/2,time.monotonic())

    def server_now(self):
        if self.clock_anchor is None:return time.time()+self.offset
        server,mono=self.clock_anchor
        return server+time.monotonic()-mono

    def candles(self,bar):
        if self.clock_anchor is None or time.monotonic()-self.clock_anchor[1]>300:
            self.sync_time()
        return self.candle_cache.read(bar)

    def account(self):
        return self.get('/api/v5/account/config',private=True)[0]

    def balance(self):
        data=self.get('/api/v5/account/balance',{'ccy':'USDT'},True)[0]
        detail=next((r for r in data['details'] if r['ccy']=='USDT'),None)
        if not detail:
            raise APIError('账户没有USDT余额')
        return float(detail['eq']),float(detail['availBal'])

    def positions(self):
        return [r for r in self.get('/api/v5/account/positions',{'instId':INSTRUMENT},True) if float(r.get('pos') or 0)!=0]

    def orders(self):
        return self.get('/api/v5/trade/orders-pending',{'instId':INSTRUMENT},True)

    def algos(self):
        # Preflight must see every pending algo family, not only TP/SL. Otherwise a
        # foreign trigger/trailing order on BTC could coexist with this bot unnoticed.
        return sum((self.get('/api/v5/trade/orders-algo-pending',{'instId':INSTRUMENT,'ordType':t},True)
                    for t in ('oco','conditional','trigger','move_order_stop')),[])

    def order(self,client_id='',order_id=''):
        params={'instId':INSTRUMENT}
        if order_id:
            params['ordId']=str(order_id)
        elif client_id:
            params['clOrdId']=str(client_id)
        else:
            raise APIError('订单查询缺少ordId/clOrdId')
        rows=self.get('/api/v5/trade/order',params,True)
        if not rows:
            raise APIError('OKX错误码 51603：订单不存在或暂未可查询','51603')
        return rows[0]

    def recent_orders(self):
        return self.get('/api/v5/trade/orders-history',{'instType':'SWAP','instId':INSTRUMENT,'limit':'100'},True)

    def fills(self):
        # Fills expose both ordId and clOrdId and can appear even when the order
        # detail/history route is temporarily unable to resolve a recent order.
        return self.get('/api/v5/trade/fills',{'instType':'SWAP','instId':INSTRUMENT,'limit':'100'},True)

    def ticker(self):
        r=self.get('/api/v5/market/ticker',{'instId':INSTRUMENT})[0]
        if abs(self.server_now()-float(r['ts'])/1000)>15:
            raise APIError('行情过期，禁止开仓')
        return r

    def instrument(self):
        r=self.get('/api/v5/public/instruments',{'instType':'SWAP','instId':INSTRUMENT})[0]
        if r['state']!='live' or r['ctType']!='linear' or r['ctValCcy']!='BTC' or r['settleCcy']!='USDT':
            raise APIError('不支持当前合约规格')
        return r
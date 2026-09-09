"""HTTPS adapter: strict hosts/routes; credentials remain in memory; never retry writes."""
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from core import Client, HOSTS, INSTRUMENT

class APIError(RuntimeError):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class Exchange(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.offset=0
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

    def request(self, method, path, params=None, private=False):
        allowed={'/api/v5/trade/order','/api/v5/account/set-leverage'}
        if method not in ('GET','POST') or (method=='POST' and path not in allowed):
            raise APIError('不允许此写入操作')
        if not path.startswith('/api/v5/') or '?' in path:
            raise APIError('非法路径')
        target=path+('?' + urllib.parse.urlencode(params) if method=='GET' and params else '')
        raw=json.dumps(params,separators=(',',':')) if method=='POST' else ''
        headers={'Content-Type':'application/json','User-Agent':'OKXMac/1.0','Accept':'application/json'}
        if private:
            if not all((self.key,self.secret,self.phrase)):
                raise APIError('请填写API Key、Secret、Passphrase')
            timestamp=datetime.fromtimestamp(time.time()+self.offset,timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
            signature=base64.b64encode(hmac.new(self.secret.encode(),(timestamp+method+target+raw).encode(),hashlib.sha256).digest()).decode()
            headers.update({'OK-ACCESS-KEY':self.key,'OK-ACCESS-SIGN':signature,
                'OK-ACCESS-TIMESTAMP':timestamp,'OK-ACCESS-PASSPHRASE':self.phrase,
                'x-simulated-trading':'1' if self.demo else '0'})
        req=urllib.request.Request('https://'+self.host+target,data=raw.encode() if raw else None,headers=headers,method=method)
        try:
            with self.opener.open(req,timeout=10) as response:
                result=json.load(response)
        except Exception:
            raise APIError('网络/HTTP/证书错误；若刚提交订单，结果可能未知，请勿重复提交') from None
        if result.get('code')!='0':
            raise APIError('OKX错误码 '+str(result.get('code'))+'（核对环境、权限、地区与系统时间）')
        data=result.get('data')
        if not isinstance(data,list):
            raise APIError('OKX响应结构异常')
        for row in data:
            if isinstance(row,dict) and row.get('sCode') not in (None,'0'):
                raise APIError('OKX订单级错误码 '+str(row['sCode']))
        return data

    def get(self,path,params=None,private=False):
        return self.request('GET',path,params,private)

    def post(self,path,body):
        return self.request('POST',path,body,True)

    def sync_time(self):
        start=time.time()
        server=float(self.get('/api/v5/public/time')[0]['ts'])/1000
        end=time.time()
        if end-start>3:
            raise APIError('时钟同步延迟过高')
        self.offset=server-(start+end)/2

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
        return sum((self.get('/api/v5/trade/orders-algo-pending',{'instId':INSTRUMENT,'ordType':t},True) for t in ('oco','conditional')),[])

    def order(self,client_id):
        return self.get('/api/v5/trade/order',{'instId':INSTRUMENT,'clOrdId':client_id},True)[0]

    def ticker(self):
        r=self.get('/api/v5/market/ticker',{'instId':INSTRUMENT})[0]
        if abs(time.time()+self.offset-float(r['ts'])/1000)>15:
            raise APIError('行情过期，禁止开仓')
        return r

    def instrument(self):
        r=self.get('/api/v5/public/instruments',{'instType':'SWAP','instId':INSTRUMENT})[0]
        if r['state']!='live' or r['ctType']!='linear' or r['ctValCcy']!='BTC' or r['settleCcy']!='USDT':
            raise APIError('不支持当前合约规格')
        return r

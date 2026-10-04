"""Exact-plan approval and OKX adapter. No LLM inference or strategy generation."""
import base64, hashlib, hmac, json, os, sqlite3, threading, time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen
from urllib.parse import urlencode

class Error(Exception): pass

def decimal(v):
    try: d = Decimal(str(v))
    except InvalidOperation: raise Error('Invalid decimal')
    if not d.is_finite() or d <= 0: raise Error('Prices and sizes must be finite and positive')
    return d

def canonical(x): return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def digest(x): return hashlib.sha256(canonical(x).encode()).hexdigest()

def validate(plan, environment, now=None):
    now = time.time() if now is None else now
    required = {'id','environment','confirm_before','direction','leverage','validity_seconds','strategy','fixed_invalidation','legs'}
    if set(plan) != required: raise Error('Missing or unknown plan fields')
    if plan['environment'] != environment: raise Error('Environment mismatch')
    if not isinstance(plan['id'], str) or not 1 <= len(plan['id']) <= 80: raise Error('Invalid plan ID')
    try: deadline = datetime.fromisoformat(plan['confirm_before']).timestamp()
    except (ValueError, TypeError): raise Error('Invalid confirmation deadline')
    if datetime.fromisoformat(plan['confirm_before']).tzinfo is None: raise Error('Deadline needs timezone')
    if deadline <= now: raise Error('Plan confirmation expired')
    if plan['direction'] not in ('long','short') or plan['leverage'] != 20: raise Error('Direction or leverage unsupported')
    if type(plan['validity_seconds']) is not int or not 60 <= plan['validity_seconds'] <= 10800: raise Error('Validity must be 60..10800 seconds')
    if not isinstance(plan['strategy'],str) or not plan['strategy'].strip(): raise Error('Strategy required')
    decimal(plan['fixed_invalidation'])
    if not isinstance(plan['legs'],list) or not 1 <= len(plan['legs']) <= 2: raise Error('One or two legs required')
    total = Decimal(0)
    for leg in plan['legs']:
        if set(leg) != {'entry','size','tp_trigger','tp_limit','sl_trigger','sl_limit','funding_buffer'}: raise Error('Missing or unknown leg fields')
        e,q,tp,tl,sl,sll = [decimal(leg[k]) for k in ('entry','size','tp_trigger','tp_limit','sl_trigger','sl_limit')]
        f = Decimal(str(leg['funding_buffer']))
        if not f.is_finite() or f < Decimal('0.3'): raise Error('Funding buffer must be at least 0.3 USDT')
        if plan['direction']=='long':
            if not sll < sl < e < tl <= tp: raise Error('Invalid long protection prices')
        else:
            if not tp <= tl < e < sl < sll: raise Error('Invalid short protection prices')
        if abs(abs(tp-e)-2*abs(sl-e)) > Decimal('0.1'): raise Error('TP must be 2 initial R')
        if len(plan['legs'])==2 and leg is plan['legs'][1]:
            first=decimal(plan['legs'][0]['entry'])
            first_r=abs(decimal(plan['legs'][0]['sl_trigger'])-first)
            if abs(abs(e-first)-first_r*Decimal('0.3'))>Decimal('0.1'): raise Error('Second leg spacing must be 0.3 first-leg R')
            if (plan['direction']=='long' and e>=first) or (plan['direction']=='short' and e<=first): raise Error('Second leg must be more favorable')
    return plan

class OKX:
    def __init__(self, environment='demo'):
        if environment not in ('demo','live'): raise Error('Invalid environment')
        self.environment=environment
        self.key=os.environ.get('OKX_API_KEY',''); self.secret=os.environ.get('OKX_SECRET_KEY',''); self.passphrase=os.environ.get('OKX_PASSPHRASE','')
        if not all((self.key,self.secret,self.passphrase)): raise Error('Set OKX credentials in environment')
    def request(self, method, path, payload=None):
        body=canonical(payload) if payload is not None else ''
        stamp=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
        signature=base64.b64encode(hmac.new(self.secret.encode(),(stamp+method+path+body).encode(),hashlib.sha256).digest()).decode()
        headers={'OK-ACCESS-KEY':self.key,'OK-ACCESS-SIGN':signature,'OK-ACCESS-TIMESTAMP':stamp,'OK-ACCESS-PASSPHRASE':self.passphrase,'Content-Type':'application/json'}
        if self.environment=='demo': headers['x-simulated-trading']='1'
        req=Request('https://www.okx.com'+path,data=body.encode() if body else None,headers=headers,method=method)
        try:
            with urlopen(req,timeout=12) as response: result=json.load(response)
        except Exception: raise Error('Transport failure: reconcile original order before retry') from None
        if result.get('code')!='0': raise Error('OKX rejected request: '+str(result.get('code'))+' '+str(result.get('msg',''))[:180])
        data=result.get('data',[])
        if any(x.get('sCode','0')!='0' for x in data): raise Error('OKX rejected order operation')
        return data
    def get(self,path,**query): return self.request('GET',path+('?' + urlencode(query) if query else ''))
    def post(self,path,body): return self.request('POST',path,body)

class Engine:
    def __init__(self, api, dbpath):
        self.api=api; self.lock=threading.RLock(); self.db=sqlite3.connect(dbpath,check_same_thread=False)
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, hash TEXT, body TEXT, status TEXT, preview TEXT);
        CREATE TABLE IF NOT EXISTS orders(client TEXT PRIMARY KEY, plan TEXT, leg INTEGER, status TEXT, detail TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, time REAL, action TEXT, detail TEXT);''');self.db.commit()
    def event(self,action,detail):
        self.db.execute('INSERT INTO events(time,action,detail) VALUES(?,?,?)',(time.time(),action,canonical(detail)));self.db.commit()
    def import_plan(self,plan):
        with self.lock:
            validate(plan,self.api.environment)
            try:self.db.execute('INSERT INTO plans(id,hash,body,status,preview) VALUES(?,?,?,?,?)',(plan['id'],digest(plan),canonical(plan),'draft','{}'))
            except sqlite3.IntegrityError: raise Error('Plan ID already exists; use a new version ID')
            self.db.commit();self.event('plan_imported',{'id':plan['id'],'hash':digest(plan)})
            return self.preview(plan['id'])
    def read(self,pid):
        row=self.db.execute('SELECT body,status,preview FROM plans WHERE id=?',(pid,)).fetchone()
        if not row: raise Error('Unknown plan')
        return json.loads(row[0]),row[1],json.loads(row[2])
    def snapshot(self):
        cfg=self.api.get('/api/v5/account/config')[0]
        if cfg['posMode']!='net_mode': raise Error('Only net_mode supported; do not change account mode automatically')
        positions=self.api.get('/api/v5/account/positions')
        orders=self.api.get('/api/v5/trade/orders-pending',instType='SWAP')
        algos=[]
        for kind in ('conditional','oco','trigger','move_order_stop'):
            algos+=self.api.get('/api/v5/trade/orders-algo-pending',ordType=kind)
        if any(Decimal(x['pos'])!=0 for x in positions) or orders or algos: raise Error('Account has positions or pending orders: entry blocked')
        spec=self.api.get('/api/v5/public/instruments',instType='SWAP',instId='BTC-USDT-SWAP')[0]
        fee=self.api.get('/api/v5/account/trade-fee',instType='SWAP')[0]
        quote=self.api.get('/api/v5/market/ticker',instId='BTC-USDT-SWAP')[0]
        balance=self.api.get('/api/v5/account/balance',ccy='USDT')[0]
        return cfg,spec,fee,quote,balance
    def preview(self,pid):
        with self.lock:
            plan,status,old=self.read(pid);validate(plan,self.api.environment)
            if status!='draft': raise Error('Plan already submitted or pending reconciliation')
            cfg,spec,fee,quote,balance=self.snapshot()
            if spec.get('state')!='live' or spec.get('ctValCcy')!='BTC': raise Error('Unexpected instrument specification')
            tick,lot,face=decimal(spec['tickSz']),decimal(spec['lotSz']),decimal(spec['ctVal'])*decimal(spec['ctMult'])
            maker=abs(Decimal(fee['makerU']));taker=abs(Decimal(fee['takerU']))
            rows=[];total=Decimal(0)
            for leg in plan['legs']:
                e,q,tp,tl,sl,sll=[decimal(leg[k]) for k in ('entry','size','tp_trigger','tp_limit','sl_trigger','sl_limit')]
                if q%lot or q<decimal(spec['minSz']) or any(decimal(leg[k])%tick for k in ('entry','tp_trigger','tp_limit','sl_trigger','sl_limit')): raise Error('Invalid tick or quantity step')
                if (plan['direction']=='long' and e>=decimal(quote['askPx'])) or (plan['direction']=='short' and e<=decimal(quote['bidPx'])): raise Error('Entry would cross current book; no chasing')
                notional=e*q*face;total+=notional
                if notional>1500: raise Error('Leg exceeds 1500 USDT')
                win=abs(e-tl)*q*face;loss=abs(e-sll)*q*face;fund=Decimal(str(leg['funding_buffer']))
                nw=win-(e*maker+tl*taker)*q*face-fund;nl=loss+(e*maker+sll*taker)*q*face+fund
                if nw/nl<Decimal('1.2'): raise Error('Net RR below 1.2; no implicit exception')
                rows.append({'notional':str(notional),'gross_win':str(win),'gross_loss':str(loss),'net_win':str(nw),'net_loss':str(nl),'net_rr':str(nw/nl),'initial_R':str(abs(e-sl))})
            if total>3000: raise Error('Campaign exceeds 3000 USDT')
            available=Decimal(next((x['availBal'] for x in balance['details'] if x['ccy']=='USDT'),'0'))
            if available<total/20+total*(maker+taker): raise Error('Insufficient free balance for margin and fee reserve')
            view={'plan':plan,'hash':digest(plan),'account_uid':cfg['uid'],'environment':self.api.environment,'costs':rows,'maker':str(maker),'taker':str(taker),'expires_at':time.time()+60,'expiry_cancel_authorized_by_confirmation':True,'warnings':['Limit stops may not fill. Partial entry fills require manual protection verification. Funding buffer is not a cap.']}
            view['approval']=digest(view)
            self.db.execute('UPDATE plans SET preview=? WHERE id=?',(canonical(view),pid));self.db.commit();return view
    def confirm(self,pid,approval):
        with self.lock:
            plan,status,view=self.read(pid);validate(plan,self.api.environment)
            if status!='draft' or not view or approval!=view.get('approval') or time.time()>view['expires_at']: raise Error('Confirmation invalid or expired; refresh preview')
            if self.db.execute("SELECT count(*) FROM orders WHERE status NOT IN ('filled','canceled','rejected')").fetchone()[0]: raise Error('Unresolved campaign exists')
            # Recheck account and costs; approval binds exact prices, fee tier and account.
            fresh=self.preview(pid)
            if fresh['account_uid']!=view['account_uid'] or fresh['costs']!=view['costs'] or fresh['maker']!=view['maker'] or fresh['taker']!=view['taker']: raise Error('Account or costs changed; review fresh preview')
            self.db.execute('UPDATE plans SET status=? WHERE id=?',('executing',pid));self.db.commit()
            self.event('human_confirmation',{'id':pid,'approval':approval,'plan_hash':digest(plan)})
            try:
                self.event('leverage_plan',{'plan':pid,'lever':'20','mode':'isolated'})
                self.api.post('/api/v5/account/set-leverage',{'instId':'BTC-USDT-SWAP','lever':'20','mgnMode':'isolated'})
                for i,leg in enumerate(plan['legs']):
                    if i:
                        prior=self.db.execute('SELECT client FROM orders WHERE plan=? ORDER BY leg LIMIT 1',(pid,)).fetchone()[0]
                        latest=self.api.get('/api/v5/trade/order',instId='BTC-USDT-SWAP',clOrdId=prior)[0]
                        self.store_order(prior,latest,plan)
                        if Decimal(latest.get('accFillSz','0'))>0: break
                        if latest['state']!='live': raise Error('First leg no longer live; stop second leg')
                    client='kt'+hashlib.sha256((self.api.environment+pid+str(i)).encode()).hexdigest()[:28]
                    side='buy' if plan['direction']=='long' else 'sell'
                    attached={'attachAlgoClOrdId':client+'p','tpTriggerPx':leg['tp_trigger'],'tpOrdPx':leg['tp_limit'],'tpTriggerPxType':'last','slTriggerPx':leg['sl_trigger'],'slOrdPx':leg['sl_limit'],'slTriggerPxType':'last'}
                    body={'instId':'BTC-USDT-SWAP','tdMode':'isolated','posSide':'net','side':side,'ordType':'limit','px':leg['entry'],'sz':leg['size'],'clOrdId':client,'attachAlgoOrds':[attached]}
                    # Durable intent before network; ambiguous requests are never resubmitted.
                    self.db.execute('INSERT INTO orders VALUES(?,?,?,?,?,?)',(client,pid,i,'unknown',canonical(body),None));self.db.commit();self.event('submit_intent',body)
                    self.api.post('/api/v5/trade/order',body)
                    detail=self.api.get('/api/v5/trade/order',instId='BTC-USDT-SWAP',clOrdId=client)[0]
                    self.store_order(client,detail,plan)
                    if Decimal(detail.get('accFillSz','0'))>0: break # Do not add second leg without protecting first.
                self.db.execute('UPDATE plans SET status=? WHERE id=?',('submitted',pid));self.db.commit()
            except Exception:
                self.db.execute('UPDATE plans SET status=? WHERE id=?',('needs_attention',pid));self.db.commit();self.event('needs_attention',{'plan':pid});raise Error('Execution stopped. Inspect original orders and protection; do not re-submit.') from None
            return self.state()
    def store_order(self,client,detail,plan):
        expires=int(detail['cTime'])/1000+plan['validity_seconds']
        self.db.execute('UPDATE orders SET status=?,detail=?,expires=? WHERE client=?',(detail['state'],canonical(detail),expires,client));self.db.commit();self.event('order_verified',{'client':client,'detail':detail})
        if Decimal(detail.get('accFillSz','0'))>0:
            self.event('protection_attention',{'client':client,'message':'Entry has fills: verify TP/SL on OKX. No automatic PEE or stop changes.'})
    def reconcile(self):
        with self.lock:
            for client,pid in self.db.execute("SELECT client,plan FROM orders WHERE status NOT IN ('filled','canceled','rejected')").fetchall():
                plan,_,_=self.read(pid)
                detail=self.api.get('/api/v5/trade/order',instId='BTC-USDT-SWAP',clOrdId=client)[0]
                self.store_order(client,detail,plan)
                if detail['state'] in ('live','partially_filled') and time.time()>=int(detail['cTime'])/1000+plan['validity_seconds']:
                    self.event('expiry_cancel_intent',{'client':client})
                    self.api.post('/api/v5/trade/cancel-order',{'instId':'BTC-USDT-SWAP','clOrdId':client})
                    final=self.api.get('/api/v5/trade/order',instId='BTC-USDT-SWAP',clOrdId=client)[0]
                    self.store_order(client,final,plan)
            return self.state()
    def state(self):
        return {'environment':self.api.environment,'plans':[{'id':r[0],'status':r[1]} for r in self.db.execute('SELECT id,status FROM plans')], 'orders':[{'client':r[0],'status':r[1],'detail':json.loads(r[2]),'expires':r[3]} for r in self.db.execute('SELECT client,status,detail,expires FROM orders')], 'events':[{'time':r[0],'action':r[1],'detail':json.loads(r[2])} for r in self.db.execute('SELECT time,action,detail FROM events ORDER BY seq DESC LIMIT 100')]}

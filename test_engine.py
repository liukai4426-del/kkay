import io
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from core import indicators, Ledger, Client
from exchange import Exchange, APIError
from engine import Engine, Settings, Store, Halt, make_plan, rounded

META=dict(state='live',ctType='linear',ctVal='0.01',ctMult='1',ctValCcy='BTC',settleCcy='USDT',minSz='0.01',lotSz='0.01',tickSz='0.1')
TICK=dict(last='60000',bidPx='59999.9',askPx='60000.1',ts=str(int(time.time()*1000)))

class FakeExchange:
    host='www.okx.com'; demo=True
    def __init__(self):
        self.writes=[]; self.pos=[]; self.pending=[]; self.protections=[]; self.equity=100
        self.ord={'state':'filled','accFillSz':'0.1'}; self.fail=False
    def sync_time(self): pass
    def account(self): return {'uid':'test','posMode':'long_short_mode','perm':'read_only,trade'}
    def balance(self): return self.equity,100
    def positions(self): return self.pos
    def orders(self): return self.pending
    def algos(self): return self.protections
    def instrument(self): return META
    def ticker(self): return TICK
    def get(self,path,params=None,private=False):
        return [{'posSide':'long','lever':'5','mgnMode':'isolated'},{'posSide':'short','lever':'5','mgnMode':'isolated'}]
    def post(self,path,body):
        self.writes.append((path,body))
        if self.fail and path.endswith('/order'): raise APIError('网络未知')
        return [{'ordId':'test','sCode':'0'}]
    def order(self,cid='',order_id=''): return self.ord
    def recent_orders(self): return []

class RiskTests(unittest.TestCase):
    def test_settings(self): self.assertEqual(Settings().validate().leverage,5)
    def test_invalid_settings(self):
        for kwargs in [dict(capital=0),dict(risk_pct=float('nan')),dict(leverage=11),dict(leverage=2.5),
                       dict(max_notional=1000),dict(fee_bps=0),dict(daily_loss=101)]:
            with self.subTest(kwargs=kwargs),self.assertRaises(Halt): replace(Settings(),**kwargs).validate()
    def test_long_plan(self):
        p=make_plan(Settings(),'做多',TICK,META,200,100,3)
        self.assertLess(float(p['sl']),float(p['px'])); self.assertGreater(float(p['tp']),float(p['px']))
        self.assertLessEqual(p['estimated_loss'],1); self.assertLessEqual(p['notional'],100)
    def test_short_plan(self):
        p=make_plan(Settings(),'做空',TICK,META,200,100,3)
        self.assertGreater(float(p['sl']),float(p['px'])); self.assertLess(float(p['tp']),float(p['px']))
    def test_tiny_budget_skip(self):
        with self.assertRaises(Halt): make_plan(Settings(),'做多',TICK,META,200,.01,.0001)
    def test_wide_spread_skip(self):
        with self.assertRaises(Halt): make_plan(Settings(),'做多',dict(TICK,askPx='61000'),META,200,100,3)
    def test_daily_remaining_caps_size(self):
        p=make_plan(Settings(),'做多',TICK,META,200,100,.1)
        self.assertLessEqual(p['estimated_loss'],.1)
    def test_decimal_rounding(self):
        self.assertEqual(rounded(.02999,'.01'),'0.02'); self.assertEqual(rounded(60.01,'.1',True),'60.1')
    def test_flat_rsi(self):
        result=indicators([dict(o=100,h=101,l=99,c=100) for _ in range(1200)])
        self.assertEqual(result['rsi'],50); self.assertAlmostEqual(result['atr'],2)
        self.assertFalse(result['cross_up']); self.assertFalse(result['cross_down'])
    def test_insufficient_history(self):
        with self.assertRaises(ValueError): indicators([dict(c=100)]*5)
    def test_hosts_locked(self):
        with self.assertRaises(ValueError): Client(host='attacker.example')

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.x=FakeExchange(); self.events=[]
        self.e=Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d))); self.e.connect()
        self.e.arm(Settings())
        self.e.market={'side':'做多','bar':int(time.time()//300)*300000-300000,'close':60000,'h':{'atr':2000},'m':{'atr':200},'scores':{'做多':{'gate':True,'total':8}}}
        self.e.market_at=time.time()
    def tearDown(self): self.tmp.cleanup()
    def active(self):
        self.e.cycle(); p=self.e.store.data['active']; p['submitted']=time.time()-20
        return p
    def position(self,p):
        return dict(mgnMode='isolated',posSide=p['posSide'],pos=p['sz'])
    def protections(self,p):
        opposite='sell' if p['posSide']=='long' else 'buy'
        return [
            dict(algoClOrdId=p['tp1_id'],state='live',posSide=p['posSide'],side=opposite,tdMode='isolated',tpTriggerPx=p['tp1'],sz=p['tp1_sz']),
            dict(algoClOrdId=p['tp2_id'],state='live',posSide=p['posSide'],side=opposite,tdMode='isolated',tpTriggerPx=p['tp2'],sz=p['tp2_sz']),
            dict(algoClOrdId=p['sl_id'],state='live',posSide=p['posSide'],side=opposite,tdMode='isolated',slTriggerPx=p['sl'],slOrdPx='-1',amendPxOnTriggerType='1')
        ]
    def test_read_connection_no_orders(self):
        self.assertEqual(self.x.writes,[])
    def test_attached_and_isolated(self):
        p=self.active(); path,b=self.x.writes[-1]
        self.assertEqual(b['tdMode'],'isolated'); self.assertEqual(b['ordType'],'limit')
        self.assertEqual(len(b['attachAlgoOrds']),3)
        tps=[x for x in b['attachAlgoOrds'] if x.get('tpTriggerPx')]; sl=[x for x in b['attachAlgoOrds'] if x.get('slTriggerPx')]
        self.assertEqual(len(tps),2); self.assertEqual(len(sl),1); self.assertEqual(sl[0]['amendPxOnTriggerType'],'1')
        self.assertAlmostEqual(sum(float(x['sz']) for x in tps),float(p['sz']))
        self.assertEqual(b['clOrdId'],p['client_id'])
    def test_execution_uses_15m_atr(self):
        p=self.active()
        self.assertAlmostEqual(abs(float(p['px'])-float(p['sl'])),200,delta=.2)
    def test_pending_survives_network_error(self):
        self.x.fail=True
        with self.assertRaises(APIError): self.e.cycle()
        store=Store(self.e.store.path)
        self.assertTrue(store.data['active']['client_id']); self.assertTrue(store.data['last_bar'])
    def test_no_duplicate_while_pending(self):
        self.active(); self.x.ord={'state':'live'}
        self.e.cycle()
        self.assertEqual(len([x for x in self.x.writes if x[0].endswith('/order')]),1)
    def test_protection_missing_locks(self):
        p=self.active(); self.x.pos=[self.position(p)]; self.e.cycle()
        p['filled_at']=time.time()-20; self.e.store.save(); self.e.cycle()
        self.assertFalse(self.e.enabled); self.assertIn('保护',self.e.store.data['halt'])
    def test_protection_verified(self):
        p=self.active(); self.x.pos=[self.position(p)]; self.x.protections=self.protections(p)
        self.e.cycle(); self.assertTrue(p['protected'])
    def test_undersized_protection_locks(self):
        p=self.active(); self.x.pos=[self.position(p)]; a=self.protections(p); a[1]['sz']='0.00001'; self.x.protections=a
        self.e.cycle(); p['filled_at']=time.time()-20; self.e.store.save(); self.e.cycle(); self.assertFalse(self.e.enabled)
    def test_foreign_position_halts(self):
        self.x.pos=[{'pos':'1'}]
        with self.assertRaises(Halt): self.e.cycle()
        self.assertEqual(self.x.writes,[])
    def test_cross_position_halts(self):
        p=self.active(); self.x.pos=[dict(self.position(p),mgnMode='cross')]
        with self.assertRaises(Halt): self.e.cycle()
    def test_stop_does_not_cancel_protection(self):
        p=self.active(); self.x.pos=[self.position(p)]; self.x.protections=self.protections(p)
        before=len(self.x.writes); self.e.stop(); self.e.cycle()
        self.assertEqual(len(self.x.writes),before); self.assertTrue(p['protected'])
    def test_daily_loss(self):
        self.x.equity=96
        with self.assertRaises(Halt): self.e.cycle()
        self.assertEqual(self.x.writes,[])
    def test_daily_loss_while_position_active(self):
        p=self.active(); self.x.pos=[self.position(p)]; self.x.protections=self.protections(p); self.x.equity=96
        with self.assertRaises(Halt): self.e.cycle()
        self.assertTrue(p['protected'])
    def test_streak_blocks(self):
        self.e._reset_streak_day(); self.e.store.data['streak']=3; self.e.store.save()
        before=len(self.x.writes); self.e.cycle()
        self.assertEqual(len(self.x.writes),before); self.assertTrue(self.e.enabled)
    def test_restart_preserves_dedup(self):
        p=self.active(); self.assertEqual(Store(self.e.store.path).data['active']['client_id'],p['client_id'])
    def test_limit_cancel_is_not_a_trade(self):
        self.active(); self.x.ord={'state':'canceled','accFillSz':'0'}; self.e.cycle()
        self.assertIsNone(self.e.store.data['active']); self.assertEqual(self.e.store.data['last_close'],0)
    def test_sleep_locks(self):
        self.e.poll_at=time.monotonic()-61; self.e.cycle()
        self.assertFalse(self.e.enabled); self.assertEqual(self.x.writes,[])
    def test_flatten_only_closes_same_side(self):
        p=self.active(); self.x.pos=[self.position(p)]; self.e.flatten()
        _,body=self.x.writes[-1]
        self.assertEqual(body['side'],'sell'); self.assertEqual(body['posSide'],'long'); self.assertEqual(body['tdMode'],'isolated')
    def test_flatten_not_retried(self):
        p=self.active(); self.x.pos=[self.position(p)]; self.e.flatten()
        with self.assertRaises(Halt): self.e.flatten()
    def test_no_auto_restart(self):
        e=Engine(self.x,self.tmp.name); e.connect(); self.assertFalse(e.enabled)
    def test_bad_storage_blocks(self):
        self.e.store.path.write_text('broken')
        with self.assertRaises(Halt): Store(self.e.store.path)
    def test_loss_count_persisted(self):
        self.active(); self.x.equity=99; self.e.reconcile()
        p=self.e.store.data['active']; p['filled_at']=time.time()-20; self.e.store.save(); self.e.reconcile()
        self.assertEqual(Store(self.e.store.path).data['streak'],1)

class AdapterTests(unittest.TestCase):
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self,*args): self.close()
    def test_order_level_error(self):
        x=Exchange(key='k',secret='s',phrase='p',demo=True)
        x.opener.open=lambda *a,**k:self.Response(json.dumps({'code':'0','data':[{'sCode':'51008'}]}).encode())
        with self.assertRaises(APIError): x.post('/api/v5/trade/order',{})
    def test_candles_list_not_dict(self):
        x=Exchange(); x.opener.open=lambda *a,**k:self.Response(b'{"code":"0","data":[["123"]]}')
        self.assertEqual(x.get('/api/v5/market/candles'),[['123']])
    def test_write_route_allowlist(self):
        with self.assertRaises(APIError): Exchange().post('/api/v5/asset/withdrawal',{})
    def test_signing_and_demo_header(self):
        x=Exchange(key='k',secret='s',phrase='p',demo=True); captured=[]
        def response(req,**kwargs):
            captured.append(req); return self.Response(b'{"code":"0","data":[]}')
        x.opener.open=response; x.post('/api/v5/trade/order',{'test':1})
        self.assertEqual(captured[0].get_header('X-simulated-trading'),'1')
        self.assertTrue(captured[0].get_header('Ok-access-sign'))

if __name__=='__main__': unittest.main(verbosity=2)

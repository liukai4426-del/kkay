import io
import time
import unittest
import urllib.error

from exchange import APIError, Exchange
import engine
import test_engine
import v135_patch
import v135_execution_patch

v135_patch.apply()
v135_execution_patch.apply()


class ExchangeConflictTests(unittest.TestCase):
    def test_http_409_trade_write_is_not_assumed_rejected(self):
        x=Exchange(key='k',secret='s',phrase='p',demo=True)
        def fail(req,timeout=10):
            raise urllib.error.HTTPError(req.full_url,409,'Conflict',{},io.BytesIO(b'Conflict'))
        x.opener.open=fail
        with self.assertRaises(APIError) as cm:
            x.post('/api/v5/trade/order',{'instId':'BTC-USDT-SWAP'})
        self.assertFalse(cm.exception.write_rejected)
        self.assertEqual(cm.exception.http_status,409)
        self.assertEqual(cm.exception.path,'/api/v5/trade/order')

    def test_fills_endpoint_is_scoped_to_btc_swap(self):
        x=Exchange()
        seen=[]
        def get(path,params=None,private=False):
            seen.append((path,params,private)); return []
        x.get=get
        self.assertEqual(x.fills(),[])
        self.assertEqual(seen,[('/api/v5/trade/fills',{'instType':'SWAP','instId':'BTC-USDT-SWAP','limit':'100'},True)])


class FillReconciliationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp=tempfile.TemporaryDirectory()
        self.x=test_engine.FakeExchange(); self.events=[]
        self.x.history=[]; self.x.recent_orders=lambda: list(self.x.history)
        self.x.fill_rows=[]; self.x.fills=lambda: list(self.x.fill_rows)
        self.e=engine.Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d)))
        self.e.connect(); self.e.arm(engine.Settings()); self.e.startup_buffer_until=0
        self.e.market={'side':'做多','bar':int(time.time()//300)*300000-300000,'close':60000,
                       'h':{'atr':2000},'m':{'atr':200},
                       'scores':{'做多':{'gate':True,'total':8,'position_multiplier':2.0,'level':'三级信号'}}}
        self.e.market_at=time.time(); self.e.market_monotonic=time.monotonic()
        self.e.cycle(); self.p=self.e.store.data['active']
        self.p.pop('order_id',None); self.e.store.save()
        self.x.order=lambda *args,**kwargs: (_ for _ in ()).throw(APIError('51603','51603'))
        self.x.pending=[]; self.x.history=[]

    def tearDown(self): self.tmp.cleanup()

    def test_full_fill_evidence_recovers_order_id_and_filled_state(self):
        self.x.fill_rows=[{'ordId':'fill-order','clOrdId':self.p['client_id'],'fillSz':self.p['sz']}]
        self.x.pos=[{'mgnMode':'isolated','posSide':self.p['posSide'],'pos':self.p['sz'],'avgPx':self.p['px']}]
        row=self.e._lookup_parent_order(self.p)
        self.assertEqual(row['state'],'filled')
        self.assertEqual(self.p['order_id'],'fill-order')
        self.assertEqual(self.p['phase'],'FILLED')

    def test_partial_fill_evidence_is_not_misclassified_as_full_fill(self):
        partial=self.p['tp1_sz']
        self.x.fill_rows=[{'ordId':'partial-order','clOrdId':self.p['client_id'],'fillSz':partial}]
        self.x.pos=[{'mgnMode':'isolated','posSide':self.p['posSide'],'pos':partial,'avgPx':self.p['px']}]
        row=self.e._lookup_parent_order(self.p)
        self.assertEqual(row['state'],'partially_filled')
        self.assertEqual(row['accFillSz'],partial)
        self.assertEqual(self.p['phase'],'PARTIAL_EVIDENCE')

    def test_partial_position_without_fill_route_still_triggers_cancel_first(self):
        partial=self.p['tp1_sz']; self.x.fill_rows=[]
        self.x.pos=[{'mgnMode':'isolated','posSide':self.p['posSide'],'pos':partial,'avgPx':self.p['px']}]
        before=len(self.x.writes)
        self.e.reconcile()
        writes=self.x.writes[before:]
        self.assertTrue(any(path.endswith('/cancel-order') for path,body in writes))
        self.assertFalse(any(path.endswith('/order') and body.get('ordType')=='market' for path,body in writes))
        self.assertEqual(self.p['phase'],'CANCEL_PENDING')

    def test_fill_quantity_over_plan_fails_closed(self):
        planned=float(self.p['sz'])
        self.x.fill_rows=[{'ordId':'bad','clOrdId':self.p['client_id'],'fillSz':str(planned*2)}]
        self.x.pos=[]
        with self.assertRaises(engine.Halt):
            self.e._lookup_parent_order(self.p)


if __name__=='__main__':
    unittest.main(verbosity=2)

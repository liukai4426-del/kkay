import json
import time
import unittest
from unittest.mock import patch

from engine import Engine, Settings, make_plan
from exchange import APIError, Exchange
import test_engine


class V135RiskTests(unittest.TestCase):
    def test_large_risk_settings_are_allowed_without_five_percent_cap(self):
        s=Settings(capital=100,max_notional=1000,leverage=10,risk_usdt=80,risk_pct=80,daily_loss=100)
        self.assertIs(s.validate(),s)

    def test_execution_can_exceed_former_five_percent_cap(self):
        s=Settings(capital=100,max_notional=1000,leverage=10,risk_usdt=80,risk_pct=80,daily_loss=100)
        p=make_plan(s,'做多',test_engine.TICK,test_engine.META,2000,100,100,1.0)
        self.assertGreater(p['estimated_loss'],5.0)
        self.assertLessEqual(p['estimated_loss'],p['effective_risk_budget']+1e-9)
        self.assertLessEqual(p['notional'],900+1e-9)  # available-balance 10% reserve still applies


class V135OrderReconcileTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp=tempfile.TemporaryDirectory()
        self.x=test_engine.FakeExchange(); self.events=[]
        self.x.order_queries=[]; self.x.history=[]; self.x.order_exc=None
        def order(cid='',order_id=''):
            self.x.order_queries.append((cid,order_id))
            if self.x.order_exc: raise self.x.order_exc
            return self.x.ord
        self.x.order=order
        self.x.recent_orders=lambda: list(self.x.history)
        self.e=Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d)))
        self.e.connect(); self.e.arm(Settings())
        self.e.market={'side':'做多','bar':int(time.time()//300)*300000-300000,'close':60000,
                       'h':{'atr':2000},'m':{'atr':200},
                       'scores':{'做多':{'gate':True,'total':8,'position_multiplier':2.0,'level':'三级信号'}}}
        self.e.market_at=time.time(); self.e.market_monotonic=time.monotonic()

    def tearDown(self): self.tmp.cleanup()

    def submit(self):
        self.e.cycle(); p=self.e.store.data['active']
        self.assertEqual(p.get('order_id'),'test')
        return p

    def test_successful_submit_persists_ordid_and_reconcile_prefers_it(self):
        p=self.submit(); self.x.ord={'state':'live','accFillSz':'0'}
        self.e.cycle()
        self.assertTrue(self.x.order_queries)
        self.assertEqual(self.x.order_queries[-1][1],'test')
        self.assertEqual(self.e.store.data['active']['client_id'],p['client_id'])

    def test_51603_pending_crosscheck_does_not_alarm_or_duplicate(self):
        p=self.submit()
        self.x.order_exc=APIError('OKX错误码 51603','51603')
        self.x.pending=[{'ordId':'test','clOrdId':p['client_id'],'state':'live','accFillSz':'0'}]
        before=len([w for w in self.x.writes if w[0].endswith('/order')])
        self.e.cycle()
        after=len([w for w in self.x.writes if w[0].endswith('/order')])
        self.assertEqual(before,after)
        self.assertTrue(self.e.enabled)
        self.assertFalse(self.e.store.data['halt'])
        self.assertTrue(any(k=='log' and '51603' in str(v) for k,v in self.events))

    def test_51603_legacy_empty_state_can_be_released_after_crosscheck(self):
        p=self.submit(); p.pop('order_id',None); p['submitted']=time.time()-30; self.e.store.save()
        self.x.order_exc=APIError('OKX错误码 51603','51603'); self.x.pending=[]; self.x.history=[]; self.x.pos=[]
        self.e.cycle()
        self.assertIsNone(self.e.store.data['active'])
        self.assertTrue(self.e.enabled)
        self.assertFalse(self.e.store.data['halt'])

    def test_51603_with_matching_position_keeps_management_fail_closed(self):
        p=self.submit(); self.x.order_exc=APIError('OKX错误码 51603','51603')
        self.x.pos=[dict(mgnMode='isolated',posSide=p['posSide'],pos=p['sz'],avgPx=p['px'])]
        self.x.protections=[]
        p['submitted']=time.time()-30; p['filled_at']=time.time()-30; self.e.store.save()
        self.e.cycle()
        self.assertIsNotNone(self.e.store.data['active'])
        self.assertTrue(any(k=='log' and '继续检查TP/SL' in str(v) for k,v in self.events))


class V135Legacy51603MigrationTests(unittest.TestCase):
    OLD='OKX错误码 51603（核对环境、权限、地区与系统时间）'

    def setUp(self):
        import tempfile
        self.tmp=tempfile.TemporaryDirectory()
        self.x=test_engine.FakeExchange(); self.events=[]
        self.x.recent_orders=lambda: []
        first=Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d)))
        first.connect()
        self.path=first.store.path

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self,active=None,halt=None):
        from engine import Store
        store=Store(self.path)
        store.data['active']=active
        store.data['halt']=self.OLD if halt is None else halt
        store.save()

    def reconnect(self):
        e=Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d)))
        e.connect()
        return e

    def test_old_51603_flat_missing_order_is_auto_migrated(self):
        self.seed({'client_id':'legacy-client','order_id':''})
        self.x.order=lambda cid='',order_id='': (_ for _ in ()).throw(APIError('OKX错误码 51603','51603'))
        e=self.reconnect()
        self.assertEqual(e.store.data['halt'],'')
        self.assertIsNone(e.store.data['active'])
        self.assertTrue(any(k=='log' and '旧51603故障锁' in str(v) and '已解除' in str(v) for k,v in self.events))

    def test_old_51603_with_live_exposure_stays_locked(self):
        self.seed(None)
        self.x.pos=[{'mgnMode':'isolated','posSide':'long','pos':'0.01'}]
        e=self.reconnect()
        self.assertTrue(e.store.data['halt'])
        self.assertIn('历史51603故障锁',e.store.data['halt'])

    def test_old_51603_with_historical_fill_stays_locked(self):
        self.seed({'client_id':'legacy-client','order_id':'123'})
        self.x.order=lambda cid='',order_id='': (_ for _ in ()).throw(APIError('OKX错误码 51603','51603'))
        self.x.recent_orders=lambda: [{'ordId':'123','clOrdId':'legacy-client','state':'filled','accFillSz':'0.01'}]
        e=self.reconnect()
        self.assertTrue(e.store.data['halt'])
        self.assertIsNotNone(e.store.data['active'])

    def test_new_51603_lock_is_never_auto_cleared(self):
        self.seed(None,'51603：OKX暂未找到该订单，正在核对订单与持仓状态')
        e=self.reconnect()
        self.assertTrue(e.store.data['halt'])


class V135AdapterTests(unittest.TestCase):
    class Response:
        def __init__(self,payload): self.payload=payload
        def __enter__(self):
            import io
            self.buf=io.BytesIO(self.payload); return self.buf
        def __exit__(self,*args): self.buf.close()

    def test_51603_has_structured_code_and_correct_message(self):
        x=Exchange(key='k',secret='s',phrase='p',demo=True)
        x.opener.open=lambda *a,**k:self.Response(json.dumps({'code':'51603','msg':'Order does not exist','data':[]}).encode())
        with self.assertRaises(APIError) as cm:
            x.order('client','123')
        self.assertEqual(cm.exception.code,'51603')
        self.assertIn('订单不存在',str(cm.exception))


if __name__=='__main__':
    unittest.main(verbosity=2)

import io
import time
import unittest
import urllib.error

from exchange import APIError, NetworkError, Exchange
import engine
import test_engine
import v135_patch
import v135_execution_patch

v135_patch.apply()
v135_execution_patch.apply()


class HttpWriteClassificationTests(unittest.TestCase):
    def test_http_401_without_okx_json_is_explicit_rejection(self):
        x=Exchange(key='k',secret='s',phrase='p',demo=True)
        def fail(req,timeout=10):
            raise urllib.error.HTTPError(req.full_url,401,'Unauthorized',{},io.BytesIO(b'Unauthorized'))
        x.opener.open=fail
        with self.assertRaises(APIError) as cm:
            x.post('/api/v5/trade/order',{'instId':'BTC-USDT-SWAP'})
        self.assertTrue(cm.exception.write_rejected)
        self.assertEqual(cm.exception.http_status,401)
        self.assertEqual(cm.exception.path,'/api/v5/trade/order')

    def test_http_408_write_remains_unknown(self):
        x=Exchange(key='k',secret='s',phrase='p',demo=True)
        def fail(req,timeout=10):
            raise urllib.error.HTTPError(req.full_url,408,'Timeout',{},io.BytesIO(b''))
        x.opener.open=fail
        with self.assertRaises(NetworkError) as cm:
            x.post('/api/v5/trade/order',{'instId':'BTC-USDT-SWAP'})
        self.assertFalse(cm.exception.write_rejected)

    def test_all_pending_algo_families_are_visible(self):
        x=Exchange()
        seen=[]
        def get(path,params=None,private=False):
            seen.append((path,dict(params or {})))
            return []
        x.get=get
        self.assertEqual(x.algos(),[])
        self.assertEqual({p['ordType'] for path,p in seen},{'oco','conditional','trigger','move_order_stop'})

    def test_uncertain_set_leverage_is_reconciled_by_readback(self):
        x=Exchange(key='k',secret='s',phrase='p',demo=True)
        def request(method,path,params=None,private=False):
            if method=='POST' and path=='/api/v5/account/set-leverage':
                raise NetworkError('timeout',method='POST',path=path)
            if method=='GET' and path=='/api/v5/account/leverage-info':
                return [{'posSide':'long','lever':'5','mgnMode':'isolated'}]
            raise AssertionError((method,path,params,private))
        x.request=request
        reply=x.post('/api/v5/account/set-leverage',{'instId':'BTC-USDT-SWAP','lever':'5','mgnMode':'isolated','posSide':'long'})
        self.assertEqual(reply[0]['confirmedByReadback'],'1')


class ExecutionStateTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp=tempfile.TemporaryDirectory()
        self.x=test_engine.FakeExchange()
        self.events=[]
        self.e=engine.Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d)))
        self.e.connect()
        self.e.arm(engine.Settings())
        self.e.startup_buffer_until=0
        self.e.market={'side':'做多','bar':int(time.time()//300)*300000-300000,'close':60000,
                       'h':{'atr':2000},'m':{'atr':200},
                       'scores':{'做多':{'gate':True,'total':8,'position_multiplier':2.0,'level':'三级信号'}}}
        self.e.market_at=time.time(); self.e.market_monotonic=time.monotonic()

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self):
        self.e.cycle()
        p=self.e.store.data['active']
        self.assertIsNotNone(p)
        return p

    @staticmethod
    def position(p,size=None):
        return {'mgnMode':'isolated','posSide':p['posSide'],'pos':size or p['sz'],'avgPx':p['px']}

    def test_parent_state_is_persisted_and_acknowledged(self):
        p=self.submit()
        self.assertEqual(p.get('order_id'),'test')
        self.assertEqual(p.get('phase'),'SUBMITTED')
        self.assertTrue(p.get('client_id'))

    def test_explicit_parent_rejection_clears_placeholder(self):
        def post(path,body):
            self.x.writes.append((path,body))
            if path=='/api/v5/trade/order':
                raise APIError('HTTP 401 / OKX 50123',code='50123',deterministic=True,http_status=401,method='POST',path=path)
            return [{'sCode':'0'}]
        self.x.post=post
        with self.assertRaises(engine.Halt):
            self.e.cycle()
        self.assertIsNone(self.e.store.data.get('active'))
        self.assertFalse(self.e.enabled)

    def test_unknown_parent_write_is_never_auto_released(self):
        p=self.submit()
        p.pop('order_id',None)
        p['phase']='SUBMIT_UNKNOWN'; p['write_result_unknown']=True
        p['submitted']=time.time()-v135_execution_patch.UNKNOWN_WRITE_TIMEOUT-1
        self.e.store.save()
        self.x.order=lambda *args,**kwargs: (_ for _ in ()).throw(APIError('51603','51603'))
        self.x.pending=[]; self.x.pos=[]; self.x.recent_orders=lambda: []
        with self.assertRaises(engine.Halt):
            self.e.reconcile()
        self.assertIsNotNone(self.e.store.data.get('active'))
        self.assertEqual(self.e.store.data['active']['phase'],'SUBMIT_UNKNOWN')

    def test_51603_log_is_throttled(self):
        p=self.submit()
        self.x.order=lambda *args,**kwargs: (_ for _ in ()).throw(APIError('51603','51603'))
        self.x.pending=[]; self.x.pos=[]; self.x.recent_orders=lambda: []
        p['submitted']=time.time()
        self.e.reconcile(); self.e.reconcile()
        logs=[v for k,v in self.events if k=='log' and '51603：父订单暂不可查询' in str(v)]
        self.assertEqual(len(logs),1)

    def test_manual_flatten_cancels_live_parent_before_any_close(self):
        p=self.submit()
        half=str(float(p['sz'])/2)
        self.x.ord={'state':'partially_filled','accFillSz':half}
        self.x.pos=[self.position(p,half)]
        before=len(self.x.writes)
        self.e.flatten()
        new=self.x.writes[before:]
        self.assertTrue(any(path.endswith('/cancel-order') for path,body in new))
        self.assertFalse(any(path.endswith('/order') and body.get('ordType')=='market' for path,body in new))
        self.assertTrue(p.get('manual_flatten_requested'))

    def test_ambiguous_partial_safety_close_blocks_second_manual_close(self):
        p=self.submit()
        half=str(float(p['sz'])/2)
        self.x.ord={'state':'canceled','accFillSz':half}
        self.x.pos=[self.position(p,half)]
        original=self.x.post
        def post(path,body):
            if path=='/api/v5/trade/order' and body.get('ordType')=='market':
                self.x.writes.append((path,body))
                raise NetworkError('timeout',method='POST',path=path)
            return original(path,body)
        self.x.post=post
        with self.assertRaises(NetworkError):
            self.e.reconcile()
        self.assertTrue(p.get('partial_close_id'))
        self.assertTrue(p.get('partial_close_unknown'))
        writes_before=len(self.x.writes)
        with self.assertRaises(engine.Halt):
            self.e.flatten()
        self.assertEqual(len(self.x.writes),writes_before)

    def test_protection_accepts_market_tp_and_market_sl(self):
        p=self.submit()
        self.x.ord={'state':'filled','accFillSz':p['sz']}
        self.x.pos=[self.position(p)]
        opposite='sell' if p['posSide']=='long' else 'buy'
        self.x.protections=[
            {'algoClOrdId':p['tp1_id'],'state':'live','posSide':p['posSide'],'side':opposite,'tdMode':'isolated',
             'tpTriggerPx':p['tp1'],'tpOrdPx':'-1','sz':p['tp1_sz']},
            {'algoClOrdId':p['tp2_id'],'state':'live','posSide':p['posSide'],'side':opposite,'tdMode':'isolated',
             'tpTriggerPx':p['tp2'],'tpOrdPx':'-1','sz':p['tp2_sz']},
            {'algoClOrdId':p['sl_id'],'state':'live','posSide':p['posSide'],'side':opposite,'tdMode':'isolated',
             'slTriggerPx':p['sl'],'slOrdPx':'-1','amendPxOnTriggerType':'1'}]
        self.e.reconcile()
        self.assertTrue(p.get('protected'))
        self.assertEqual(p.get('phase'),'PROTECTED')


if __name__=='__main__':
    unittest.main(verbosity=2)

import io
import json
import subprocess
import sys
import textwrap
import unittest
import urllib.error

from exchange import APIError, Exchange, NetworkError


class V135ExchangeClassificationTests(unittest.TestCase):
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()

    def test_order_level_rejection_is_deterministic(self):
        x = Exchange(key='k', secret='s', phrase='p', demo=True)
        x.opener.open = lambda *a, **k: self.Response(json.dumps({
            'code': '0', 'data': [{'sCode': '51082', 'sMsg': 'TP must use market price'}]
        }).encode())
        with self.assertRaises(APIError) as cm:
            x.post('/api/v5/trade/order', {'instId': 'BTC-USDT-SWAP'})
        self.assertEqual(cm.exception.code, '51082')
        self.assertTrue(cm.exception.deterministic)
        self.assertIn('51082', str(cm.exception))

    def test_http_401_preserves_okx_code_and_is_deterministic(self):
        x = Exchange(key='k', secret='s', phrase='p', demo=True)
        body = io.BytesIO(json.dumps({'code': '50123', 'msg': 'permission denied'}).encode())
        def fail(*a, **k):
            raise urllib.error.HTTPError('https://www.okx.com/api/v5/trade/order', 401, 'Unauthorized', {}, body)
        x.opener.open = fail
        with self.assertRaises(APIError) as cm:
            x.post('/api/v5/trade/order', {'instId': 'BTC-USDT-SWAP'})
        self.assertEqual(cm.exception.code, '50123')
        self.assertTrue(cm.exception.deterministic)
        self.assertEqual(cm.exception.http_status, 401)

    def test_http_500_remains_ambiguous_network_error(self):
        x = Exchange(key='k', secret='s', phrase='p', demo=True)
        body = io.BytesIO(json.dumps({'code': '50000', 'msg': 'server error'}).encode())
        def fail(*a, **k):
            raise urllib.error.HTTPError('https://www.okx.com/api/v5/trade/order', 500, 'Server Error', {}, body)
        x.opener.open = fail
        with self.assertRaises(NetworkError) as cm:
            x.post('/api/v5/trade/order', {'instId': 'BTC-USDT-SWAP'})
        self.assertFalse(cm.exception.deterministic)
        self.assertEqual(cm.exception.http_status, 500)


class V135RuntimeExecutionTests(unittest.TestCase):
    def run_isolated(self, source):
        result = subprocess.run(
            [sys.executable, '-c', textwrap.dedent(source)],
            text=True, capture_output=True, timeout=30,
        )
        if result.returncode:
            self.fail('isolated execution failed\nSTDOUT:\n'+result.stdout+'\nSTDERR:\n'+result.stderr)

    def test_split_tp_is_forced_to_market(self):
        self.run_isolated(r'''
            from v135_patch import apply
            apply()
            from exchange import Exchange
            x=Exchange(key='k',secret='s',phrase='p',demo=True)
            captured={}
            def request(method,path,params=None,private=False):
                captured['method']=method; captured['path']=path; captured['body']=params
                return [{'ordId':'1','sCode':'0'}]
            x.request=request
            body={'instId':'BTC-USDT-SWAP','attachAlgoOrds':[
                {'attachAlgoClOrdId':'t1','tpOrdKind':'condition','tpTriggerPx':'61000','tpOrdPx':'60990','sz':'1'},
                {'attachAlgoClOrdId':'t2','tpOrdKind':'condition','tpTriggerPx':'62000','tpOrdPx':'61990','sz':'1'},
                {'attachAlgoClOrdId':'sl','slTriggerPx':'59000','slOrdPx':'-1','amendPxOnTriggerType':'1'},
            ]}
            x.post('/api/v5/trade/order',body)
            tps=[a for a in captured['body']['attachAlgoOrds'] if a.get('tpTriggerPx')]
            assert len(tps)==2
            assert all(a['tpOrdPx']=='-1' and a['tpOrdKind']=='condition' for a in tps)
            assert captured['body']['attachAlgoOrds'][2]['slOrdPx']=='-1'
            # Do not mutate the engine-owned original body in place.
            assert body['attachAlgoOrds'][0]['tpOrdPx']=='60990'
        ''')

    def test_explicit_parent_rejection_clears_placeholder_without_51603(self):
        self.run_isolated(r'''
            import tempfile,time
            from v135_patch import apply
            apply()
            from engine import Engine,Settings,Halt
            from exchange import APIError
            from test_engine import FakeExchange
            x=FakeExchange(); events=[]
            def post(path,body):
                x.writes.append((path,body))
                if path=='/api/v5/trade/order' and body.get('attachAlgoOrds'):
                    raise APIError('OKX订单级错误码 51082','51082',True)
                return [{'ordId':'ok','sCode':'0'}]
            x.post=post
            with tempfile.TemporaryDirectory() as folder:
                e=Engine(x,folder,lambda k,d:events.append((k,d)))
                e.connect(); e.arm(Settings()); e.startup_buffer_until=0
                bar=int(time.time()//300)*300000-300000
                e.market={'side':'做多','bar':bar,'close':60000,'h':{'atr':2000},'m':{'atr':200},
                          'scores':{'做多':{'gate':True,'total':8,'position_multiplier':2.0,'level':'三级信号'}}}
                e.market_at=time.time(); e.market_monotonic=time.monotonic()
                try:
                    e.cycle()
                    raise AssertionError('expected Halt')
                except Halt as exc:
                    assert '51082' in str(exc)
                assert e.store.data['active'] is None
                assert e.store.data['last_bar']==bar
                assert not e.enabled
                assert not any('51603' in str(v) for k,v in events)
        ''')

    def test_ambiguous_parent_write_keeps_placeholder_and_never_resubmits(self):
        self.run_isolated(r'''
            import tempfile,time
            from v135_patch import apply
            apply()
            from engine import Engine,Settings
            from exchange import NetworkError
            from test_engine import FakeExchange
            x=FakeExchange()
            def post(path,body):
                x.writes.append((path,body))
                if path=='/api/v5/trade/order' and body.get('attachAlgoOrds'):
                    raise NetworkError('TLS连接中断；写入结果未知')
                return [{'ordId':'ok','sCode':'0'}]
            x.post=post
            with tempfile.TemporaryDirectory() as folder:
                e=Engine(x,folder)
                e.connect(); e.arm(Settings()); e.startup_buffer_until=0
                bar=int(time.time()//300)*300000-300000
                e.market={'side':'做多','bar':bar,'close':60000,'h':{'atr':2000},'m':{'atr':200},
                          'scores':{'做多':{'gate':True,'total':8,'position_multiplier':2.0,'level':'三级信号'}}}
                e.market_at=time.time(); e.market_monotonic=time.monotonic()
                try:
                    e.cycle()
                    raise AssertionError('expected NetworkError')
                except NetworkError:
                    pass
                p=e.store.data['active']
                assert p and p['client_id']
                assert not p.get('order_id')
                before=len([w for w in x.writes if w[0]=='/api/v5/trade/order'])
                # A persisted active placeholder blocks a second parent submission.
                x.order=lambda cid='',order_id='': (_ for _ in ()).throw(__import__('exchange').APIError('OKX错误码 51603','51603',True))
                x.pending=[]; x.recent_orders=lambda: []; x.pos=[]
                p['submitted']=time.time()
                e.store.save()
                e.enabled=False
                e.cycle()
                after=len([w for w in x.writes if w[0]=='/api/v5/trade/order'])
                assert before==after==1
        ''')

    def test_daily_drawdown_is_day_stop_not_fault_lock(self):
        self.run_isolated(r'''
            import tempfile,time
            from v135_patch import apply
            apply()
            from engine import Engine,Settings
            from test_engine import FakeExchange
            x=FakeExchange(); events=[]
            with tempfile.TemporaryDirectory() as folder:
                e=Engine(x,folder,lambda k,d:events.append((k,d)))
                e.connect(); e.arm(Settings()); e.startup_buffer_until=0
                bar=int(time.time()//300)*300000-300000
                e.market={'side':'观望','bar':bar,'close':60000,'h':{'atr':2000},'m':{'atr':200},'scores':{}}
                e.market_at=time.time(); e.market_monotonic=time.monotonic()
                x.equity=96
                e.cycle()
                assert e.store.data.get('daily_stop_day')
                assert e.store.data.get('halt','')==''
                assert not any(k=='alarm' for k,v in events)
        ''')


if __name__=='__main__':
    unittest.main(verbosity=2)

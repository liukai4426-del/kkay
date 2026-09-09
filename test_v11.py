import io
import json
import socket
import ssl
import unittest
import urllib.error
import tempfile
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from strategy import signal
from exchange import Exchange, NetworkError, APIError
from engine import Settings, Halt
from engine import Store
from history import summarize
import test_engine

class Scores(unittest.TestCase):
    def score(self, buy=True, r15=None, r1=None, **extra):
        h=dict(rsi=(20 if buy else 75))
        m=dict(rsi=(15 if buy else 80),ema20=100,atr=10,lower=90,upper=110,
               j=(-1 if buy else 101),cross_up=buy,cross_down=not buy)
        if r15 is not None: m['rsi']=r15
        if r1 is not None: h['rsi']=r1
        m.update(extra)
        rows=[dict(o=100,h=101,l=99,c=100),dict(o=85 if buy else 115,h=90,l=80,c=85 if buy else 115)]
        with patch('strategy.indicators',side_effect=[h,m,dict(ema20=100)]):
            return signal(rows,rows)

    def test_long_qualified(self):
        r=self.score(); self.assertEqual(r['side'],'做多'); self.assertEqual(r['scores']['做多']['total'],8)
    def test_short_qualified(self):
        r=self.score(False); self.assertEqual(r['side'],'做空'); self.assertEqual(r['scores']['做空']['total'],8)
    def test_gate_blocks_even_large_score(self):
        r=self.score(r1=26); self.assertFalse(r['scores']['做多']['eligible']); self.assertEqual(r['side'],'观望')
    def test_long_rsi_boundary(self):
        r=self.score(r15=20,r1=25); self.assertTrue(r['scores']['做多']['gate']); self.assertEqual(r['scores']['做多']['total'],6)
    def test_short_rsi_boundary(self):
        r=self.score(False,r15=75,r1=70); self.assertTrue(r['scores']['做空']['gate'])
    def test_outside_gate(self):
        for buy,r15,r1 in [(True,20.01,25),(True,20,25.01),(False,74.99,70),(False,75,69.99)]:
            self.assertFalse(self.score(buy,r15,r1)['scores']['做多' if buy else '做空']['gate'])
    def test_integer_points(self):
        for buy in (True,False):
            for row in self.score(buy)['scores'].values():
                self.assertLessEqual(row['total'],10)
                self.assertTrue(all(isinstance(i[1],int) for i in row['items']))
    def test_cross_required_for_kdj_points(self):
        r=self.score(cross_up=False); self.assertEqual(r['scores']['做多']['items'][3][1],0)
    def test_deviation_is_directional(self):
        r=self.score(); self.assertEqual(r['scores']['做空']['items'][5][1],0)
    def test_defaults(self):
        s=Settings(); self.assertEqual((s.score_threshold,s.stop_atr,s.reward_r),(7,.8,1.5))
    def test_threshold_validation(self):
        for n in (0,6,7.5,11):
            with self.assertRaises(Halt): replace(Settings(),score_threshold=n).validate()

class Network(unittest.TestCase):
    def setUp(self):
        self.x=Exchange(key='TEST_KEY',secret='TEST_SECRET',phrase='TEST_PHRASE',demo=True)
        self.events=[]; self.x.network_event=self.events.append
    def test_get_retry_success(self):
        with patch.object(self.x,'_request_once',side_effect=[NetworkError('TLS'),[{}]]) as req, patch('exchange.time.sleep') as sleep:
            self.assertEqual(self.x.get('/api/v5/public/time'),[{}]); self.assertEqual(req.call_count,2); sleep.assert_called_once_with(1)
    def test_three_total_attempts(self):
        with patch.object(self.x,'_request_once',side_effect=NetworkError('TLS')) as req,patch('exchange.time.sleep'):
            with self.assertRaises(NetworkError): self.x.get('/api/v5/public/time')
            self.assertEqual(req.call_count,3)
    def test_post_never_retried(self):
        with patch.object(self.x,'_request_once',side_effect=NetworkError('TLS')) as req,patch('exchange.time.sleep') as sleep:
            with self.assertRaises(NetworkError): self.x.post('/api/v5/trade/order',{})
            self.assertEqual(req.call_count,1); sleep.assert_not_called()
    def test_auth_never_retried(self):
        with patch.object(self.x,'_request_once',side_effect=APIError('HTTP401')) as req:
            with self.assertRaises(APIError): self.x.get('/api/v5/account/config',private=True)
            self.assertEqual(req.call_count,1)
    def test_tls_post_uncertain_not_false_no_order(self):
        with patch.object(self.x.opener,'open',side_effect=urllib.error.URLError(ssl.SSLError('EOF TEST_SECRET'))):
            with self.assertRaises(NetworkError) as ctx: self.x.post('/api/v5/trade/order',{})
            self.assertIn('结果未知',str(ctx.exception)); self.assertNotIn('TEST_SECRET',str(ctx.exception)); self.assertNotIn('未提交订单',str(ctx.exception))
    def test_dns_category(self):
        with patch.object(self.x.opener,'open',side_effect=urllib.error.URLError(socket.gaierror('TEST_SECRET'))),patch('exchange.time.sleep'):
            with self.assertRaisesRegex(NetworkError,'DNS'): self.x.get('/api/v5/public/time')
    def test_http_auth_code(self):
        exc=urllib.error.HTTPError('https://www.okx.com',401,'denied',{},io.BytesIO(b'{"code":"50101","msg":"TEST_SECRET"}'))
        with patch.object(self.x.opener,'open',side_effect=exc):
            with self.assertRaises(APIError) as ctx: self.x.get('/api/v5/account/config',private=True)
            self.assertIn('50101',str(ctx.exception)); self.assertNotIn('TEST_SECRET',str(ctx.exception))

class Execution(unittest.TestCase):
    setUp=test_engine.EngineTests.setUp
    tearDown=test_engine.EngineTests.tearDown
    def test_score_gate_rechecked(self):
        self.e.market['scores']['做多']['gate']=False
        self.e.cycle(); self.assertFalse(self.x.writes)
    def test_low_score_blocked(self):
        self.e.market['scores']['做多']['total']=6
        self.e.cycle(); self.assertFalse(self.x.writes)
    def test_missing_score_blocked(self):
        del self.e.market['scores']; self.e.cycle(); self.assertFalse(self.x.writes)

class History(unittest.TestCase):
    def test_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            result=summarize(Path(folder)/'missing.jsonl')
            self.assertEqual(result['count'],0); self.assertIsNone(result['win_rate'])
    def test_closed_only_deduplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'account.json')
            for cid,pnl in [('a',2),('b',-1),('c',0),('a',2)]:
                store.record('仓位归零',dict(client_id=cid,equity_change=pnl))
            store.record('FOK未成交',dict(client_id='d',equity_change=99))
            result=summarize(store.path.with_suffix('.history.jsonl'))
            self.assertEqual(result['count'],3); self.assertEqual(result['total'],1)
            self.assertAlmostEqual(result['win_rate'],100/3)
            self.assertEqual(result['breakeven'],1)

if __name__=='__main__': unittest.main()

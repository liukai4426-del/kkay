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
    def score(self, *, buy=True, full=False, strong_opposite=False, threshold=7, rsi_extreme=True):
        if buy:
            h=dict(rsi=24 if rsi_extreme else 50,ema200=90,ema20=100,ema50=95,down=False,up=False)
            if strong_opposite: h.update(ema200=110,ema20=90,ema50=100,down=True)
            m=dict(rsi=19 if rsi_extreme else 40,lower=90,upper=110)
            hour=[dict(o=100,h=101,l=99,c=100),dict(o=100,h=101,l=99,c=100)]
            quarter=[dict(o=100,h=101,l=99,c=100),dict(o=95,h=100,l=89 if full else 95,c=96)]
        else:
            h=dict(rsi=71 if rsi_extreme else 50,ema200=110,ema20=100,ema50=105,down=False,up=False)
            if strong_opposite: h.update(ema200=90,ema20=110,ema50=100,up=True)
            m=dict(rsi=76 if rsi_extreme else 60,lower=90,upper=110)
            hour=[dict(o=100,h=101,l=99,c=100),dict(o=100,h=101,l=99,c=100)]
            quarter=[dict(o=100,h=101,l=99,c=100),dict(o=105,h=111 if full else 105,l=100,c=104)]
        f=dict(rsi=50); previous={}
        indicators=[h,m,f,previous,previous,previous]
        true_feature=dict(kdj=full,ema=full,reversal=full,confirmed=full)
        false_feature=dict(kdj=False,ema=False,reversal=False,confirmed=False)
        if buy:
            periods=[true_feature,true_feature,true_feature,false_feature,false_feature,false_feature]
            volumes=[(full,1.4),(False,.8)]
        else:
            periods=[false_feature,false_feature,false_feature,true_feature,true_feature,true_feature]
            volumes=[(False,.8),(full,1.4)]
        with patch('strategy.indicators',side_effect=indicators), patch('strategy._period',side_effect=periods), patch('strategy._volume_boll_return',side_effect=volumes):
            return signal(hour,quarter,quarter,threshold)

    def test_full_long_scores_ten(self):
        r=self.score(full=True); self.assertEqual(r['scores']['做多']['total'],10); self.assertEqual(r['side'],'做多')
    def test_full_short_scores_ten(self):
        r=self.score(buy=False,full=True); self.assertEqual(r['scores']['做空']['total'],10); self.assertEqual(r['side'],'做空')
    def test_rsi_is_points_not_gate(self):
        r=self.score(full=False,rsi_extreme=False)
        self.assertTrue(r['scores']['做多']['gate']); self.assertEqual(r['scores']['做多']['items'][0][1],0)
    def test_volume_boll_return_adds_two(self):
        r=self.score(full=True)
        items=dict((name,pts) for name,pts,_ in r['scores']['做多']['items'])
        self.assertEqual(items['15m BOLL外破回归 + 成交量≥20均量×1.3'],2)
    def test_exact_seven_is_eligible(self):
        h=dict(rsi=24,ema200=90,ema20=100,ema50=95,down=False,up=False)
        m=dict(rsi=19,lower=90,upper=110); f=dict(rsi=50); p={}
        rows=[dict(o=100,h=101,l=99,c=100),dict(o=95,h=100,l=89,c=96)]
        hfeat=dict(kdj=False,ema=False,reversal=False,confirmed=False)
        mfeat=dict(kdj=True,ema=False,reversal=False,confirmed=True)
        ffeat=dict(kdj=False,ema=False,reversal=True,confirmed=True)
        off=dict(kdj=False,ema=False,reversal=False,confirmed=False)
        with patch('strategy.indicators',side_effect=[h,m,f,p,p,p]), patch('strategy._period',side_effect=[hfeat,mfeat,ffeat,off,off,off]), patch('strategy._volume_boll_return',side_effect=[(False,1.0),(False,1.0)]):
            r=signal(rows,rows,rows,7)
        self.assertEqual(r['scores']['做多']['total'],7); self.assertTrue(r['scores']['做多']['eligible'])
    def test_strong_opposite_penalty_is_minus_two(self):
        r=self.score(full=True,strong_opposite=True)
        items=dict((name,pts) for name,pts,_ in r['scores']['做多']['items'])
        self.assertEqual(items['1H 强逆势惩罚'],-2)
        self.assertEqual(r['scores']['做多']['total'],8)
    def test_integer_points_and_bounds(self):
        for buy in (True,False):
            row=self.score(buy=buy,full=True)['scores']['做多' if buy else '做空']
            self.assertGreaterEqual(row['total'],0); self.assertLessEqual(row['total'],10)
            self.assertTrue(all(isinstance(i[1],int) for i in row['items']))
    def test_defaults(self):
        s=Settings(); self.assertEqual((s.score_threshold,s.stop_atr,s.reward_r),(7,1.0,1.5))
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

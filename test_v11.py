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
    def score(self, *, buy=True, full=False, strong_opposite=False, threshold=8):
        if buy:
            h=dict(rsi=24 if full else 50,lower=90,upper=110,ema200=90,ema20=100,ema50=95,down=False,up=False)
            m=dict(rsi=19 if full else 50,lower=90,upper=110)
            f=dict(rsi=19 if full else 50,lower=90,upper=110)
            if strong_opposite: h.update(ema200=110,ema20=90,ema50=100,down=True)
            low=89 if full else 99
            hour=[dict(o=100,h=101,l=99,c=100),dict(o=95,h=101,l=low,c=100)]
            quarter=[dict(o=100,h=101,l=99,c=100),dict(o=95,h=101,l=low,c=96)]
            five=[dict(o=100,h=101,l=99,c=100),dict(o=95,h=101,l=low,c=96)]
        else:
            h=dict(rsi=71 if full else 50,lower=90,upper=110,ema200=110,ema20=100,ema50=105,down=False,up=False)
            m=dict(rsi=76 if full else 50,lower=90,upper=110)
            f=dict(rsi=76 if full else 50,lower=90,upper=110)
            if strong_opposite: h.update(ema200=90,ema20=110,ema50=100,up=True)
            high=111 if full else 101
            hour=[dict(o=100,h=101,l=99,c=100),dict(o=105,h=high,l=99,c=100)]
            quarter=[dict(o=100,h=101,l=99,c=100),dict(o=105,h=high,l=99,c=104)]
            five=[dict(o=100,h=101,l=99,c=100),dict(o=105,h=high,l=99,c=104)]
        previous={}
        indicators=[h,m,f,previous,previous,previous]
        on=dict(kdj=full,ema=full,reversal=full,confirmed=full)
        off=dict(kdj=False,ema=False,reversal=False,confirmed=False)
        periods=[on,on,on,off,off,off] if buy else [off,off,off,on,on,on]
        volumes=[(full,1.4),(False,.8)] if buy else [(False,.8),(full,1.4)]
        with patch('strategy.indicators',side_effect=indicators), patch('strategy._period',side_effect=periods), patch('strategy._volume_boll_return',side_effect=volumes):
            return signal(hour,quarter,five,threshold)

    def test_full_long_scores_nineteen(self):
        r=self.score(full=True); self.assertEqual(r['scores']['做多']['total'],19); self.assertEqual(r['scores']['做多']['level'],'高共振信号'); self.assertEqual(r['side'],'做多')
    def test_full_short_scores_nineteen(self):
        r=self.score(buy=False,full=True); self.assertEqual(r['scores']['做空']['total'],19); self.assertEqual(r['scores']['做空']['level'],'高共振信号'); self.assertEqual(r['side'],'做空')
    def test_each_independent_signal_gets_own_confluence_score(self):
        items=dict((name,pts) for name,pts,_ in self.score(full=True)['scores']['做多']['items'])
        for name in ('RSI 多周期共振（5m/15m/1H）','BOLL 多周期共振（5m/15m/1H）','KDJ 多周期共振（5m/15m/1H）','EMA20 多周期共振（5m/15m/1H）','反转K线多周期共振（5m/15m/1H）'):
            self.assertEqual(items[name],3)
    def test_volume_boll_return_adds_two(self):
        items=dict((name,pts) for name,pts,_ in self.score(full=True)['scores']['做多']['items'])
        self.assertEqual(items['15m BOLL外破回归 + 成交量≥20均量×1.3'],2)
    def test_exact_eight_is_eligible_and_ordinary(self):
        h=dict(rsi=24,lower=90,upper=110,ema200=90,ema20=100,ema50=95,down=False,up=False); m=dict(rsi=19,lower=90,upper=110); f=dict(rsi=19,lower=90,upper=110); p={}
        hour=[dict(o=100,h=101,l=99,c=100),dict(o=100,h=101,l=89,c=100)]; quarter=[dict(o=100,h=101,l=99,c=100),dict(o=95,h=101,l=89,c=96)]; five=list(quarter)
        off=dict(kdj=False,ema=False,reversal=False,confirmed=False)
        with patch('strategy.indicators',side_effect=[h,m,f,p,p,p]), patch('strategy._period',side_effect=[off]*6), patch('strategy._volume_boll_return',side_effect=[(False,1.0),(False,1.0)]): r=signal(hour,quarter,five,8)
        row=r['scores']['做多']; self.assertEqual(row['total'],8); self.assertTrue(row['eligible']); self.assertEqual(row['level'],'普通信号')
    def test_seven_is_blocked(self):
        h=dict(rsi=24,lower=90,upper=110,ema200=90,ema20=100,ema50=95,down=False,up=False); m=dict(rsi=19,lower=90,upper=110); f=dict(rsi=19,lower=90,upper=110); p={}
        hour=[dict(o=100,h=101,l=99,c=100),dict(o=100,h=101,l=95,c=100)]; quarter=[dict(o=100,h=101,l=99,c=100),dict(o=95,h=101,l=89,c=96)]; five=list(quarter)
        off=dict(kdj=False,ema=False,reversal=False,confirmed=False)
        with patch('strategy.indicators',side_effect=[h,m,f,p,p,p]), patch('strategy._period',side_effect=[off]*6), patch('strategy._volume_boll_return',side_effect=[(False,1.0),(False,1.0)]): r=signal(hour,quarter,five,8)
        row=r['scores']['做多']; self.assertEqual(row['total'],7); self.assertFalse(row['eligible'])
    def test_signal_levels(self):
        from strategy import _level
        self.assertEqual((_level(8),_level(10)),('普通信号','普通信号')); self.assertEqual((_level(11),_level(14)),('强信号','强信号')); self.assertEqual((_level(15),_level(19)),('高共振信号','高共振信号'))
    def test_strong_opposite_penalty_is_minus_three(self):
        r=self.score(full=True,strong_opposite=True); items=dict((name,pts) for name,pts,_ in r['scores']['做多']['items']); self.assertEqual(items['1H 强逆势惩罚'],-3); self.assertEqual(r['scores']['做多']['total'],16)
    def test_integer_points_and_bounds(self):
        for buy in (True,False):
            row=self.score(buy=buy,full=True)['scores']['做多' if buy else '做空']; self.assertGreaterEqual(row['total'],0); self.assertLessEqual(row['total'],19); self.assertTrue(all(isinstance(i[1],int) for i in row['items']))
    def test_defaults(self):
        s=Settings(); self.assertEqual((s.score_threshold,s.stop_atr,s.reward_r),(8,1.0,1.5))
    def test_threshold_validation(self):
        for n in (0,7,7.5,20):
            with self.assertRaises(Halt): replace(Settings(),score_threshold=n).validate()
        for n in (8,19): self.assertEqual(replace(Settings(),score_threshold=n).validate().score_threshold,n)

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
        self.e.market['scores']['做多']['total']=7
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

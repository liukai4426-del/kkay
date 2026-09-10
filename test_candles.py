import unittest
from unittest.mock import patch
from candles import CandleCache, CandlePending, CandleStale, check_latest
from exchange import Exchange

STEP=900000
BOUNDARY=2000*STEP

def rows(start,end,confirmed='1',step=STEP):
    return [[str(i*step),'100','101','99','100','1','1','1',confirmed] for i in range(end,start-1,-1)]

class API:
    def __init__(self,step=STEP):self.step=step; self.now=2000*step/1000+5; self.calls=[]; self.newest=rows(1700,1999,step=step)
    def server_now(self):return self.now
    def get(self,path,args):
        self.calls.append((path,args))
        if path.endswith('/candles'):return self.newest
        end=int(args['after'])//self.step-1
        return rows(end-299,end,step=self.step)

class Candles(unittest.TestCase):
    def test_warmup_then_no_redundant_fetch(self):
        a=API(); c=CandleCache(a)
        self.assertGreaterEqual(len(c.read('15m')),1000)
        count=len(a.calls); c.read('15m'); self.assertEqual(len(a.calls),count)

    def test_5m_supported_and_cached(self):
        step=300000; a=API(step); c=CandleCache(a)
        data=c.read('5m'); self.assertGreaterEqual(len(data),1000); self.assertEqual(data[-1]['t'],1999*step)
        count=len(a.calls); c.read('5m'); self.assertEqual(len(a.calls),count)

    def test_incremental_update_uses_one_page(self):
        a=API(); c=CandleCache(a); c.read('15m'); a.calls=[]
        a.now+=900; a.newest=rows(1701,2000)
        self.assertEqual(c.read('15m')[-1]['t'],2000*STEP)
        self.assertEqual(len(a.calls),1)

    def test_settlement_wait_recovers_without_stale_return(self):
        a=API(); c=CandleCache(a); c.read('15m'); a.now+=900
        with self.assertRaises(CandlePending):c.read('15m')
        a.newest=rows(1701,2000)
        self.assertEqual(c.read('15m')[-1]['t'],2000*STEP)

    def test_persistent_stale_halts(self):
        with self.assertRaises(CandleStale):check_latest('15m',1998*STEP,BOUNDARY/1000+46,STEP)

    def test_old_data_no_grace(self):
        with self.assertRaises(CandleStale):check_latest('15m',1997*STEP,BOUNDARY/1000+1,STEP)

    def test_unclosed_candle_never_used(self):
        a=API(); a.newest=rows(2000,2000,'0')+rows(1700,1999)
        self.assertEqual(CandleCache(a).read('15m')[-1]['t'],1999*STEP)

    def test_gap_rejected(self):
        a=API(); del a.newest[20]
        with self.assertRaises(CandleStale):CandleCache(a).read('15m')

    def test_future_rejected(self):
        with self.assertRaises(CandleStale):check_latest('15m',2000*STEP,BOUNDARY/1000+5,STEP)

    def test_server_clock_ignores_local_clock_jump(self):
        x=Exchange(); x.clock_anchor=(1000,50)
        with patch('exchange.time.monotonic',return_value=55),patch('exchange.time.time',return_value=90000):
            self.assertEqual(x.server_now(),1005)

if __name__=='__main__':unittest.main()

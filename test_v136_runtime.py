import tempfile,time,unittest
from pathlib import Path
from v136_runtime import apply,_decision
apply()
from engine import Engine,Settings,Store
from test_engine import FakeExchange

class V136RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.events=[]; self.x=FakeExchange()
        self.e=Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d)))
        self.e.connect(); self.e.arm(Settings()); self.e.startup_buffer_until=0
    def tearDown(self):self.tmp.cleanup()
    def test_waiting_signal_explains_no_order(self):
        # This test targets decision reporting only.  Keep the engine explicitly
        # armed so wall-clock/day-risk state cannot make CI skip the reporter.
        self.e.enabled=True; self.e.stopped=False; self.e.store.data['halt']=''
        bar=int(self.e.market_now()//300)*300000-300000
        self.e.market={'side':'观望','bar':bar,'scores':{},'close':60000,'h':{'atr':100},'m':{'atr':20}}
        self.e.market_at=time.time(); self.e.market_monotonic=time.monotonic(); _decision(self.e)
        self.assertTrue(any('当前5m收盘信号为观望' in str(v) for k,v in self.events if k=='log'))
        self.assertIsNone(self.e.store.data['active'])
    def test_failed_arm_cannot_look_enabled(self):
        self.e.enabled=False; self.e.stopped=True
        self.e.store.data['halt']='真实资金状态待核对'; self.e.store.save()
        with self.assertRaises(Exception):self.e.arm(Settings())
        self.assertFalse(self.e.enabled)
    def test_repeated_stop_is_noop(self):
        self.e.stop(); count=len([v for k,v in self.events if k=='log' and '已停止新开仓' in str(v)])
        self.assertFalse(self.e.stop()); self.assertEqual(count,len([v for k,v in self.events if k=='log' and '已停止新开仓' in str(v)]))

if __name__=='__main__':unittest.main(verbosity=2)

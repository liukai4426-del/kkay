import tempfile,time,unittest
from pathlib import Path
from v136_runtime import apply,_decision,_china_day
apply()
from engine import Engine,Settings,Store
from test_engine import FakeExchange

class V136RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.events=[]; self.x=FakeExchange()
        self.e=Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d)))
        self.e.connect(); self.e.arm(Settings()); self.e.startup_buffer_until=0
    def tearDown(self):self.tmp.cleanup()
    def logs(self):return [str(v) for k,v in self.events if k=='log']
    def test_waiting_signal_explains_no_order(self):
        # This test targets decision reporting only.  Keep the engine explicitly
        # armed so wall-clock/day-risk state cannot make CI skip the reporter.
        self.e.enabled=True; self.e.stopped=False; self.e.store.data['halt']=''
        bar=int(self.e.market_now()//300)*300000-300000
        self.e.market={'side':'观望','bar':bar,'scores':{},'close':60000,'h':{'atr':100},'m':{'atr':20}}
        self.e.market_at=time.time(); self.e.market_monotonic=time.monotonic(); _decision(self.e)
        self.assertTrue(any('当前5m收盘信号为观望' in v for v in self.logs()))
        self.assertIsNone(self.e.store.data['active'])
    def test_failed_arm_cannot_look_enabled(self):
        self.e.enabled=False; self.e.stopped=True
        self.e.store.data['halt']='真实资金状态待核对'; self.e.store.save()
        with self.assertRaises(Exception):self.e.arm(Settings())
        self.assertFalse(self.e.enabled)
    def test_repeated_stop_is_noop(self):
        self.e.stop(); count=len([v for v in self.logs() if '已停止新开仓' in v])
        self.assertFalse(self.e.stop()); self.assertEqual(count,len([v for v in self.logs() if '已停止新开仓' in v]))
    def test_daily_stop_start_reports_exact_reason(self):
        self.e.stop(); self.events.clear(); day=_china_day()
        self.e.store.data.update(day=day,daily_stop_day=day,daily_notice_day=day,peak=100)
        self.e.store.save()
        result=self.e.arm(Settings())
        self.assertFalse(result); self.assertFalse(self.e.enabled); self.assertTrue(self.e.stopped)
        self.assertTrue(any('自动交易启动未授权：中国时间本日已触发日内权益回撤停止' in v for v in self.logs()))
        self.assertFalse(any('请查看上一条启动检查/日内风控原因' in v for v in self.logs()))
    def test_fault_unlock_reports_preserved_daily_stop(self):
        self.e.stop(); self.events.clear(); day=_china_day()
        self.e.store.data.update(halt='测试故障锁',day=day,daily_stop_day=day,daily_notice_day=day,peak=100,active=None)
        self.e.store.save(); self.e.acknowledge()
        self.assertEqual(self.e.store.data['halt'],'')
        self.assertEqual(self.e.store.data.get('daily_stop_day'),day)
        self.assertTrue(any('V1.3.6解除锁后状态：中国时间本日已触发日内权益回撤停止' in v for v in self.logs()))
    def test_fault_unlock_without_day_risk_can_rearm(self):
        self.e.stop(); self.events.clear(); day=_china_day()
        self.e.store.data.update(halt='测试故障锁',day=day,daily_stop_day='',daily_notice_day='',streak=0,streak_day=day,active=None)
        self.e.store.save(); self.e.acknowledge(); self.e.arm(Settings())
        self.assertTrue(self.e.enabled); self.assertFalse(self.e.stopped)
        self.assertGreater(self.e.startup_buffer_until,time.monotonic())
        self.assertTrue(any('自动开仓已授权；5秒缓冲后进入信号执行' in v for v in self.logs()))

if __name__=='__main__':unittest.main(verbosity=2)

"""Regression tests for the V1.3.5 runtime stability hotfix."""
import tempfile
import unittest
from pathlib import Path

import candles
import v135_patch
from engine import Engine, Settings, Store

v135_patch.apply()


class V135DailyStopStabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.engine=Engine(None,self.tmp.name)
        self.engine.store=Store(Path(self.tmp.name)/'state.json')
        self.engine.settings=Settings(daily_loss=3)

    def tearDown(self):
        self.tmp.cleanup()

    def test_daily_drawdown_latches_without_permanent_fault(self):
        day=v135_patch._china_day()
        self.engine.store.data.update(day=day,peak=100.0,daily_stop_day='',daily_notice_day='',halt='')
        self.engine.store.save()
        with self.assertRaises(v135_patch.DailyRiskStop) as caught:
            self.engine.daily(96.0)
        self.assertEqual(self.engine.store.data['daily_stop_day'],day)
        self.assertEqual(self.engine.store.data['halt'],'')
        message=str(caught.exception)
        self.assertIn('峰值权益 100.0000',message)
        self.assertIn('当前权益 96.0000',message)
        self.assertIn('已回撤 4.0000 / 上限 3.0000',message)

    def test_manual_equity_rebound_does_not_bypass_same_day_stop(self):
        day=v135_patch._china_day()
        self.engine.store.data.update(day=day,peak=100.0,daily_stop_day=day,daily_notice_day='',halt='')
        self.engine.store.save()
        with self.assertRaises(v135_patch.DailyRiskStop):
            self.engine.daily(110.0)
        self.assertEqual(self.engine.store.data['daily_stop_day'],day)
        self.assertEqual(self.engine.store.data['halt'],'')

    def test_new_china_day_resets_daily_stop(self):
        self.engine.store.data.update(day='2000-01-01',peak=100.0,daily_stop_day='2000-01-01',
                                      daily_notice_day='2000-01-01',halt='')
        self.engine.store.save()
        self.assertEqual(self.engine.daily(110.0),3.0)
        self.assertEqual(self.engine.store.data['daily_stop_day'],'')
        self.assertEqual(self.engine.store.data['daily_notice_day'],'')
        self.assertEqual(self.engine.store.data['peak'],110.0)


class V135CandleSettlementTests(unittest.TestCase):
    def test_just_closed_15m_bar_is_normal_settlement_wait(self):
        step=900000
        boundary=2000*step
        now=boundary/1000+2
        expected=candles.expected_bar(now,step)
        with self.assertRaises(candles.CandlePending) as caught:
            candles.check_latest('15m',expected-step,now,step)
        message=str(caught.exception)
        self.assertIn('刚收盘，等待OKX确认最新K线',message)
        self.assertIn('正常结算窗口最多60秒',message)
        self.assertNotIn('持续过期',message)
        self.assertNotIn('落后应有K线',message)

    def test_app_does_not_log_normal_settlement_as_alarm(self):
        source=Path('app.py').read_text()
        self.assertIn("persistent=('持续过期' in message or '连续异常' in message)",source)
        self.assertIn('if persistent and time.monotonic()-self.candle_wait_log>=15:',source)


if __name__=='__main__':
    unittest.main(verbosity=2)

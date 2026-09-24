import json
import tempfile
import unittest
from pathlib import Path

from candles import CandleLag, CandlePending, CandleStale, check_latest
from engine import Engine, Store, normalize_halt_reason


class V122StabilityTests(unittest.TestCase):
    def test_nested_fault_lock_text_is_normalized(self):
        raw='已有故障锁：已有故障锁：K线已过期，暂停策略判断。先人工核对并解除故障锁。先人工核对并解除故障锁'
        self.assertEqual(normalize_halt_reason(raw),'K线已过期，暂停策略判断')

    def test_store_cleans_old_nested_lock_without_clearing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.json'
            path.write_text(json.dumps({
                'halt':'已有故障锁：已有故障锁：K线已过期，暂停策略判断。先人工核对并解除故障锁。先人工核对并解除故障锁',
                'streak':2,
            },ensure_ascii=False))
            store=Store(path)
            self.assertEqual(store.data['halt'],'K线已过期，暂停策略判断')
            self.assertEqual(store.data['streak'],2)
            persisted=json.loads(path.read_text())
            self.assertEqual(persisted['halt'],'K线已过期，暂停策略判断')

    def test_existing_lock_alarm_does_not_wrap_or_replace_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            events=[]
            engine=Engine(None,folder,lambda kind,data:events.append((kind,data)))
            engine.store=Store(Path(folder)/'state.json')
            engine.store.data['halt']='订单状态长时间不确定'; engine.store.save()
            engine.halt('已有故障锁：订单状态长时间不确定。先人工核对并解除故障锁')
            self.assertEqual(engine.store.data['halt'],'订单状态长时间不确定')
            self.assertEqual(events[-1][0],'alarm')
            self.assertEqual(events[-1][1],'已有故障锁：订单状态长时间不确定。先人工核对并解除故障锁')

    def test_late_closed_bar_is_transient_candle_lag(self):
        step=900000
        boundary=2000*step
        with self.assertRaises(CandleLag) as caught:
            check_latest('15m',1998*step,boundary/1000+46,step)
        self.assertIsInstance(caught.exception,CandleStale)
        self.assertIn('15m',str(caught.exception))

    def test_three_lag_confirmations_pause_without_persistent_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            events=[]
            engine=Engine(None,folder,lambda kind,data:events.append((kind,data)))
            engine.enabled=True
            for expected in (1,2):
                with self.assertRaises(CandlePending):
                    engine._handle_candle_lag(CandleLag('15m 测试晚到'))
                self.assertTrue(engine.enabled)
                self.assertEqual(engine.candle_lag_count,expected)
                self.assertFalse(engine.candle_paused)
            with self.assertRaises(CandlePending):
                engine._handle_candle_lag(CandleLag('15m 测试晚到'))
            self.assertFalse(engine.enabled)
            self.assertTrue(engine.candle_paused)
            self.assertIsNone(engine.store)
            self.assertTrue(any(kind=='log' and '未写入永久故障锁' in text for kind,text in events))
            engine._candle_recovered()
            self.assertFalse(engine.enabled)
            self.assertEqual(engine.candle_lag_count,0)
            self.assertFalse(engine.candle_paused)
            self.assertTrue(any(kind=='log' and '需重新授权' in text for kind,text in events))


if __name__=='__main__':
    unittest.main(verbosity=2)

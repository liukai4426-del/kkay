import unittest
from unittest.mock import patch

import v153_backtest_core as bt
import v153_model as model
import v153_runtime_patch as runtime


def rows_for_signal(prev_close=110.0, cur_low=99.5, cur_high=101.0, cur_close=100.5):
    rows=[]
    for i in range(202):
        close=100.0
        rows.append({'t':i*300_000,'o':100.0,'h':101.0,'l':99.0,'c':close,'v':100.0})
    rows[-2].update(c=float(prev_close),o=float(prev_close),h=float(prev_close)+1,l=float(prev_close)-1)
    rows[-1].update(o=float(cur_close),h=float(cur_high),l=float(cur_low),c=float(cur_close),v=150.0)
    return rows


def ind(middle=100.0, upper=110.0, lower=90.0, rsi=50.0, atr=10.0):
    return {'middle':middle,'upper':upper,'lower':lower,'rsi':rsi,'atr':atr}


class V153BollSignalTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(model.VERSION,'1.5.3')
        self.assertEqual(model.THRESHOLD,6.0)
        self.assertEqual(model.ENTRY_WINDOW_MS,4*60_000)
        self.assertEqual(model.MIDDLE_TOUCH_ATR,0.10)
        self.assertEqual(model.LONG_MIDDLE_RSI_MIN,30.0)
        self.assertEqual(model.SHORT_MIDDLE_RSI_MAX,70.0)
        self.assertIs(bt.model,model)

    def test_long_middle_path_is_from_above_and_rsi_at_least_30(self):
        five=rows_for_signal(prev_close=105,cur_low=99.5,cur_high=101,cur_close=100.5)
        with patch.object(model,'indicators',side_effect=[ind(rsi=30),ind(middle=100)]):
            sig=model.boll_entry_signal(five,'做多')
        self.assertEqual(sig['path'],'middle_rsi')

        with patch.object(model,'indicators',side_effect=[ind(rsi=29),ind(middle=100)]):
            self.assertIsNone(model.boll_entry_signal(five,'做多'))

        five_below=rows_for_signal(prev_close=95,cur_low=99.5,cur_high=101,cur_close=100.5)
        with patch.object(model,'indicators',side_effect=[ind(rsi=50),ind(middle=100)]):
            self.assertIsNone(model.boll_entry_signal(five_below,'做多'))

    def test_short_middle_path_is_from_below_and_rsi_at_most_70(self):
        five=rows_for_signal(prev_close=95,cur_low=99.5,cur_high=101,cur_close=100.5)
        with patch.object(model,'indicators',side_effect=[ind(rsi=70),ind(middle=100)]):
            sig=model.boll_entry_signal(five,'做空')
        self.assertEqual(sig['path'],'middle_rsi')

        with patch.object(model,'indicators',side_effect=[ind(rsi=71),ind(middle=100)]):
            self.assertIsNone(model.boll_entry_signal(five,'做空'))

        five_above=rows_for_signal(prev_close=105,cur_low=99.5,cur_high=101,cur_close=100.5)
        with patch.object(model,'indicators',side_effect=[ind(rsi=60),ind(middle=100)]):
            self.assertIsNone(model.boll_entry_signal(five_above,'做空'))

    def test_outer_band_paths_ignore_rsi_and_do_not_stack(self):
        long_five=rows_for_signal(prev_close=95,cur_low=89,cur_high=101,cur_close=95)
        with patch.object(model,'indicators',side_effect=[ind(rsi=5),ind(middle=100)]):
            self.assertEqual(model.boll_entry_signal(long_five,'做多')['path'],'lower_band')

        short_five=rows_for_signal(prev_close=105,cur_low=99,cur_high=111,cur_close=105)
        with patch.object(model,'indicators',side_effect=[ind(rsi=95),ind(middle=100)]):
            self.assertEqual(model.boll_entry_signal(short_five,'做空')['path'],'upper_band')

        both=rows_for_signal(prev_close=105,cur_low=89,cur_high=101,cur_close=100)
        with patch.object(model,'indicators',side_effect=[ind(rsi=50),ind(middle=100)]):
            self.assertEqual(model.boll_entry_signal(both,'做多')['path'],'middle_rsi')

    def test_four_one_minute_window_has_no_technical_trigger(self):
        opp={'signal_close_ms':1_000_000,'expires_ms':1_000_000+4*60_000}
        self.assertEqual(model._window_state(1_000_000,opp)[:2],(True,1))
        self.assertEqual(model._window_state(1_000_000+60_000,opp)[:2],(True,2))
        self.assertTrue(model._window_state(1_000_000+239_999,opp)[0])
        self.assertEqual(model._window_state(1_000_000+239_999,opp)[1],4)
        self.assertFalse(model._window_state(1_000_000+240_000,opp)[0])

    def test_runtime_rearm_waits_for_next_five_minute_cycle(self):
        rearm={'rearm_after_ms':600_000}
        self.assertFalse(runtime._rearm_ready(rearm,[{'t':480_000}]))  # close 540000
        self.assertTrue(runtime._rearm_ready(rearm,[{'t':540_000}]))   # close 600000

    def test_execution_required_only_boll_signal_and_window(self):
        opp={
            'id':'b153-test','side':'做多','trigger_reference':100.0,'atr5':10.0,
            'atr15':4.0,'extreme':95.0,'front_structure':None,'signal_path':'middle_rsi',
        }
        plan={'px':'100','stop_distance':10.0,'btc':1.0,'worst_roundtrip_cost':2.0}
        score={'confirmations':{'required':{'boll_entry_signal':True,'entry_window_4x1m':True}}}
        ok,_,blockers=model.execution_checks(plan,opp,score)
        self.assertTrue(ok,blockers)
        score['confirmations']['required']['entry_window_4x1m']=False
        ok,_,blockers=model.execution_checks(plan,opp,score)
        self.assertFalse(ok)
        self.assertTrue(any('BOLL信号/4分钟窗口' in x for x in blockers))


if __name__=='__main__':
    unittest.main()

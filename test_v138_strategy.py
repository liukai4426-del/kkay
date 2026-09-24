import inspect
import unittest
from unittest.mock import patch

import engine
import v138_strategy_patch as v138


class V138SettingsTests(unittest.TestCase):
    def test_fixed_threshold_and_50x_leverage(self):
        engine.Settings(score_threshold=3.5,leverage=50,max_notional=100).validate()
        with self.assertRaises(engine.Halt):
            engine.Settings(score_threshold=3.5,leverage=51,max_notional=100).validate()
        with self.assertRaises(engine.Halt):
            engine.Settings(score_threshold=4.0,leverage=5,max_notional=100).validate()


class V138ScoreTests(unittest.TestCase):
    def test_rsi_penalty_long(self):
        self.assertEqual(v138._rsi_penalty({'rsi':76},{'rsi':77},True),-1.0)
        self.assertEqual(v138._rsi_penalty({'rsi':81},{'rsi':82},True),-2.0)
        self.assertEqual(v138._rsi_penalty({'rsi':81},{'rsi':70},True),0.0)

    def test_rsi_penalty_short(self):
        self.assertEqual(v138._rsi_penalty({'rsi':24},{'rsi':23},False),-1.0)
        self.assertEqual(v138._rsi_penalty({'rsi':19},{'rsi':18},False),-2.0)
        self.assertEqual(v138._rsi_penalty({'rsi':19},{'rsi':30},False),0.0)

    def test_macd_boll_trend_is_two_independent_points(self):
        rows=[{'c':100},{'c':102}]
        current={'middle':101}; previous={'middle':100}
        with patch.object(v138,'_macd',return_value={'dif':2,'dea':1,'hist':2,'prev_hist':1}):
            score,detail=v138._trend_score(rows,current,previous,True)
        self.assertEqual(score,2.0)
        self.assertTrue(detail['macd']); self.assertTrue(detail['boll'])
        with patch.object(v138,'_macd',return_value={'dif':-2,'dea':-1,'hist':-2,'prev_hist':-1}):
            score,detail=v138._trend_score([{'c':100},{'c':98}],{'middle':99},{'middle':100},False)
        self.assertEqual(score,2.0)
        self.assertTrue(detail['macd']); self.assertTrue(detail['boll'])

    def _rows(self,tf):
        return [{'tf':tf,'c':100,'o':100,'h':101,'l':99,'v':100},{'tf':tf,'c':100,'o':100,'h':101,'l':99,'v':100}]

    def test_1m_trigger_is_score_and_hard_gate(self):
        hour=self._rows('h'); quarter=self._rows('m'); five=self._rows('f'); one=self._rows('o'); four=self._rows('q'); day=self._rows('d')
        def fake_ind(rows):
            tf=rows[0]['tf']
            rsi={'m':50,'f':50}.get(tf,50)
            return {'atr':100,'rsi':rsi,'middle':100,'ema20':100,'ema50':100,'ema200':100,'up':False,'down':False}
        common=[
            patch.object(v138,'indicators',side_effect=fake_ind),
            patch.object(v138.strategy,'_daily_ema_zone',return_value=(3.0,'EMA20',{})),
            patch.object(v138.strategy,'_setup',return_value=(1.5,{},1.0)),
            patch.object(v138,'_trend_score',return_value=(2.0,{})),
            patch.object(v138,'_favorable_15m',return_value=(.5,None,99,[])),
            patch.object(v138,'_adverse_structure',side_effect=lambda rows,current,price,buy,weight,lookback:(0.0,None,99,[])),
            patch.object(v138.strategy,'_trend_penalty',return_value=(0.0,False,{})),
            patch.object(v138.strategy,'_structure_context',return_value={'penalty':0.0,'blocked':False,'front_r':2.0}),
        ]
        with common[0],common[1],common[2],common[3],common[4],common[5],common[6],common[7], patch.object(v138.strategy,'_trigger',return_value=(0.0,{})):
            result=v138.v138_signal(hour,quarter,five,one,four=four,day=day)
        self.assertFalse(result['scores']['做多']['gate'])
        self.assertEqual(result['scores']['做多']['layers']['trigger'],0.0)
        with patch.object(v138,'indicators',side_effect=fake_ind), patch.object(v138.strategy,'_daily_ema_zone',return_value=(3.0,'EMA20',{})), patch.object(v138.strategy,'_setup',return_value=(1.5,{},1.0)), patch.object(v138,'_trend_score',return_value=(2.0,{})), patch.object(v138,'_favorable_15m',return_value=(.5,None,99,[])), patch.object(v138,'_adverse_structure',side_effect=lambda rows,current,price,buy,weight,lookback:(0.0,None,99,[])), patch.object(v138.strategy,'_trend_penalty',return_value=(0.0,False,{})), patch.object(v138.strategy,'_structure_context',return_value={'penalty':0.0,'blocked':False,'front_r':2.0}), patch.object(v138.strategy,'_trigger',return_value=(.5,{})):
            result=v138.v138_signal(hour,quarter,five,one,four=four,day=day)
        self.assertTrue(result['scores']['做多']['gate'])
        self.assertEqual(result['scores']['做多']['layers']['trigger'],.5)

    def test_adverse_structure_weights_are_fixed(self):
        with patch.object(v138.strategy,'_zones',return_value=[{'kind':'resistance','price':101}]), patch.object(v138.strategy,'_nearest',return_value={'kind':'resistance','price':101}):
            penalty,_,_,_=v138._adverse_structure([],{'atr':10},100,True,1.5,180)
        self.assertEqual(penalty,-1.5)
        with patch.object(v138.strategy,'_zones',return_value=[{'kind':'support','price':99}]), patch.object(v138.strategy,'_nearest',return_value={'kind':'support','price':99}):
            penalty,_,_,_=v138._adverse_structure([],{'atr':10},100,False,1.0,120)
        self.assertEqual(penalty,-1.0)

    def test_long_and_short_entries_are_limit_orders(self):
        self.assertIn("'ordType':'limit'",inspect.getsource(v138._submit_initial))
        self.assertIn("'ordType':'limit'",inspect.getsource(v138._submit_addon))


if __name__=='__main__':
    unittest.main()

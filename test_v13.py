import unittest
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from strategy import _environment, _setup, _trigger, _structure_context, _level, SCORE_MAX
from engine import Engine, Settings, make_plan
import test_engine


class V13ScoreTests(unittest.TestCase):
    def test_levels_and_max(self):
        self.assertEqual(SCORE_MAX,18)
        self.assertEqual((_level(8),_level(10)),('普通信号','普通信号'))
        self.assertEqual((_level(11),_level(13)),('强信号','强信号'))
        self.assertEqual((_level(14),_level(18)),('高共振信号','高共振信号'))

    def test_environment_aligned_and_countertrend(self):
        aligned={'ema20':110,'ema50':100,'ema200':90,'up':True,'down':False}
        self.assertEqual(_environment(aligned,{'c':120},True)[0],3)
        opposite={'ema20':90,'ema50':100,'ema200':110,'up':False,'down':True}
        score,strong,_=_environment(opposite,{'c':80},True)
        self.assertEqual(score,-3); self.assertTrue(strong)

    def test_setup_caps_at_eight(self):
        rows=[{'o':100,'h':101,'l':99,'c':100,'v':100} for _ in range(21)]
        rows[-2]={'o':100,'h':101,'l':99,'c':100,'v':100}
        rows[-1]={'o':95,'h':102,'l':89,'c':102,'v':140}
        current={'rsi':20,'lower':90,'upper':110,'j':20,'cross_up':True,'cross_down':False}
        total,parts,ratio=_setup(rows,current,True)
        self.assertEqual(total,8); self.assertGreaterEqual(ratio,1.3)
        self.assertEqual(sum(parts.values()),8)

    def test_trigger_is_recovery_not_oversold_requirement(self):
        rows=[{'o':100,'h':101,'l':99,'c':100},{'o':99,'h':103,'l':98,'c':103}]
        current={'ema20':101,'cross_up':True,'cross_down':False}
        previous={'ema20':100}
        total,parts=_trigger(rows,current,previous,True)
        self.assertEqual(total,4); self.assertEqual(sum(parts.values()),4)

    def test_limit_plan_and_cost_ratio(self):
        p=make_plan(Settings(), '做多', test_engine.TICK, test_engine.META, 200, 100, 3)
        self.assertEqual(float(p['px']),59999.9)
        self.assertGreaterEqual(p['cost_multiple'],1.99)

    def test_default_three_loss_guard(self):
        self.assertEqual(Settings().consecutive_losses,3)


class V13StructureAndSafetyTests(unittest.TestCase):
    def test_weak_forward_zone_does_not_block(self):
        hour=[{'c':100}]; quarter=[{'c':100}]
        h={'atr':100}; m={'atr':50}
        weak={'kind':'resistance','price':120,'tests':2,'age':2,'recency':.9,'strong':False}
        with patch('strategy._zones',side_effect=[[weak],[]]):
            ctx=_structure_context(hour,quarter,h,m,True,1.0)
        self.assertFalse(ctx['blocked']); self.assertIsNone(ctx['front'])

    def test_strong_forward_zone_under_one_r_blocks(self):
        hour=[{'c':100}]; quarter=[{'c':100}]
        h={'atr':100}; m={'atr':50}
        strong={'kind':'resistance','price':120,'tests':3,'age':2,'recency':.9,'strong':True}
        with patch('strategy._zones',side_effect=[[strong],[]]):
            ctx=_structure_context(hour,quarter,h,m,True,1.0)
        self.assertTrue(ctx['blocked']); self.assertLess(ctx['front_r'],1.0)

    def test_limit_entry_expires_and_sends_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            x=test_engine.FakeExchange(); e=Engine(x,folder); e.connect(); e.arm(Settings())
            e.market={'side':'做多','bar':int(time.time()//300)*300000-300000,'close':60000,
                      'h':{'atr':2000},'m':{'atr':200},
                      'scores':{'做多':{'gate':True,'total':8,'level':'普通信号','items':[]}}}
            e.market_at=time.time(); e.market_monotonic=time.monotonic()
            e.cycle(); p=e.store.data['active']
            p['expires']=time.time()-1; e.store.save(); x.ord={'state':'live','accFillSz':'0'}
            e.cycle()
            self.assertTrue(any(path.endswith('/cancel-order') for path,_ in x.writes))
            self.assertTrue(e.store.data['active']['cancel_requested'])

    def test_china_natural_day_resets_streak(self):
        with tempfile.TemporaryDirectory() as folder:
            x=test_engine.FakeExchange(); e=Engine(x,folder); e.connect(); e.settings=Settings()
            e.store.data.update(streak=3,streak_day='1900-01-01'); e.store.save()
            day=e._reset_streak_day()
            self.assertEqual(day,datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d'))
            self.assertEqual(e.store.data['streak'],0)


class V13LimitFillSafetyTests(unittest.TestCase):
    def make_engine(self,folder):
        x=test_engine.FakeExchange(); e=Engine(x,folder); e.connect(); e.arm(Settings())
        e.market={'side':'做多','bar':int(time.time()//300)*300000-300000,'close':60000,
                  'h':{'atr':2000},'m':{'atr':200},
                  'scores':{'做多':{'gate':True,'total':8,'level':'普通信号','items':[]}}}
        e.market_at=time.time(); e.market_monotonic=time.monotonic(); e.cycle()
        return x,e,e.store.data['active']

    def test_stop_marks_cancel_then_reconcile_cancels_unfilled(self):
        with tempfile.TemporaryDirectory() as folder:
            x,e,p=self.make_engine(folder); before=len(x.writes)
            e.stop()
            self.assertEqual(len(x.writes),before)
            self.assertTrue(e.store.data['active']['cancel_on_reconcile'])
            x.ord={'state':'live','accFillSz':'0'}; e.cycle()
            self.assertTrue(any(path.endswith('/cancel-order') for path,_ in x.writes[before:]))

    def test_partial_fill_cancel_then_market_flatten(self):
        with tempfile.TemporaryDirectory() as folder:
            x,e,p=self.make_engine(folder)
            qty=float(p['sz'])/2
            x.pos=[{'mgnMode':'isolated','posSide':p['posSide'],'pos':str(qty)}]
            x.ord={'state':'partially_filled','accFillSz':str(qty)}
            e.cycle()
            self.assertEqual(x.writes[-1][0],'/api/v5/trade/cancel-order')
            x.ord={'state':'canceled','accFillSz':str(qty)}
            e.cycle()
            path,body=x.writes[-1]
            self.assertEqual(path,'/api/v5/trade/order'); self.assertEqual(body['ordType'],'market')
            self.assertEqual(body['posSide'],p['posSide']); self.assertTrue(e.store.data['active']['partial_close_id'])

    def test_late_full_fill_gets_fresh_protection_grace(self):
        with tempfile.TemporaryDirectory() as folder:
            x,e,p=self.make_engine(folder)
            p['submitted']=time.time()-240; e.store.save()
            x.ord={'state':'filled','accFillSz':p['sz']}
            x.pos=[{'mgnMode':'isolated','posSide':p['posSide'],'pos':p['sz']}]
            x.protections=[]
            e.cycle()
            self.assertTrue(e.store.data['active']['filled'])
            self.assertGreater(e.store.data['active']['filled_at'],time.time()-5)
            self.assertEqual(e.store.data.get('halt',''),'')


if __name__=='__main__': unittest.main(verbosity=2)

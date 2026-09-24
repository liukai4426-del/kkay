import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app
import engine
import v138_strategy_patch as v138
import v154_build1543_patch as fix


class Var:
    def __init__(self, value): self.value=str(value)
    def get(self): return self.value
    def set(self, value): self.value=str(value)


class Store:
    def __init__(self):
        self.data={
            'streak':7,'streak_pause_until':9999999999.0,
            'streak_notice_day':'x','streak_day':'y','max_streak':9,
        }
        self.saved=0
    def save(self): self.saved+=1


class Build1543Tests(unittest.TestCase):
    def test_loss_pause_is_fully_cleared_but_history_can_remain(self):
        store=Store()
        self.assertTrue(fix._clear_loss_pause_state(store))
        self.assertEqual(store.data['streak'],0)
        self.assertEqual(store.data['streak_pause_until'],0.0)
        self.assertEqual(store.data['streak_notice_day'],'')
        self.assertEqual(store.data['streak_day'],'')
        self.assertEqual(store.data['max_streak'],9)

    def test_settings_force_second_signal_to_two_x_first(self):
        fields={
            'first_signal_notional':Var(40),
            'second_signal_notional':Var(999),
            'capital':Var(100),
            'leverage':Var(5),
            'cooldown_minutes':Var(30),
            'stop_atr':Var(1),
            'daily_loss':Var(3),
            'risk_usdt':Var(1),
            'risk_pct':Var(1),
        }
        owner=SimpleNamespace(fields=fields)
        settings=fix._settings_from_ui(owner)
        self.assertEqual(settings.first_signal_notional,40.0)
        self.assertEqual(settings.second_signal_notional,80.0)
        self.assertEqual(settings.max_notional,40.0)
        self.assertEqual(settings.consecutive_losses,3)  # compatibility-only, not a live blocker

    def test_total_signal_funds_cannot_exceed_capital_times_leverage(self):
        fields={
            'first_signal_notional':Var(200),
            'capital':Var(100),
            'leverage':Var(5),
            'cooldown_minutes':Var(30),
            'stop_atr':Var(1),
            'daily_loss':Var(3),
            'risk_usdt':Var(1),
            'risk_pct':Var(1),
        }
        with self.assertRaises(engine.Halt):
            fix._settings_from_ui(SimpleNamespace(fields=fields))

    def test_path_c_requires_filled_protected_long_path_a(self):
        base={'side':'做多','boll_signal_path':'middle_rsi','filled':True,'protected':True,
              'legs':[{'state':'filled'}]}
        self.assertTrue(fix._first_path_a_filled(dict(base)))
        for key,value in [('side','做空'),('boll_signal_path','lower_band'),('filled',False),('protected',False)]:
            row=dict(base); row[key]=value
            self.assertFalse(fix._first_path_a_filled(row))

    def test_path_c_candidate_is_later_lower_band_touch_and_rsi_free(self):
        bars=[{'t':i*300000,'o':100,'h':102,'l':99,'c':100,'v':10} for i in range(201)]
        bars[-1]=dict(bars[-1],t=1000000,h=103,l=94,c=98)
        candles={'5m':bars,'1H':bars,'15m':bars,'1m':bars}
        active={'signal_close_ms':1000000,'path_c_last_signal_bar_t':-1}
        fake_opp={'id':'opp-c','signal_bar_t':1000000,'trigger_detail':{},'signal_path':'lower_band'}
        with patch.object(fix,'indicators',return_value={'lower':95,'middle':100,'upper':105,'rsi':88,'atr':4}), \
             patch.object(fix.model,'new_opportunity',return_value=fake_opp.copy()):
            opp=fix._new_path_c_opportunity(active,candles)
        self.assertIsNotNone(opp)
        self.assertEqual(opp['signal_path'],fix.PATH_C_LABEL)
        self.assertFalse(opp['trigger_detail']['path_c_rsi_required'])

        # Same/older closed 5m signal cannot become Path C.
        active2={'signal_close_ms':1300000,'path_c_last_signal_bar_t':-1}
        with patch.object(fix,'indicators',return_value={'lower':95,'middle':100,'upper':105,'rsi':10,'atr':4}), \
             patch.object(fix.model,'new_opportunity',return_value=fake_opp.copy()):
            self.assertIsNone(fix._new_path_c_opportunity(active2,candles))

    def test_path_c_submission_is_limit_two_x_and_single_pending_guard_exists(self):
        plan_source=inspect.getsource(fix._make_path_c_plan)
        submit_source=inspect.getsource(fix._submit_path_c)
        maybe_source=inspect.getsource(fix._maybe_path_c)
        self.assertIn('2.0)',plan_source)
        self.assertIn('second_signal_notional',plan_source)
        self.assertIn('"ordType": "limit"',submit_source)
        self.assertIn('attachAlgoOrds',submit_source)
        self.assertIn('state in ("pending", "filled")',maybe_source)

    def test_runtime_hooks_are_live(self):
        self.assertTrue(engine.Engine._kaytrade_v154_build1543_applied)
        self.assertTrue(app.App._kaytrade_v154_build1543_applied)
        self.assertEqual(fix.VERSION,'1.5.4')
        self.assertEqual(fix.BUILD,'154.3')
        self.assertEqual(fix.model.THRESHOLD,6.0)
        self.assertIs(engine.Engine.cycle, fix.engine.Engine.cycle)


if __name__=='__main__':
    unittest.main()

from pathlib import Path
p=Path('v134_upgrade.py')
s=p.read_text()
old="""        base=make_plan(Settings(max_notional=1000), '做多', test_engine.TICK, test_engine.META, 200, 100, 10, 1.0)
        high=make_plan(Settings(max_notional=1000), '做多', test_engine.TICK, test_engine.META, 200, 100, 10, 2.0)
        self.assertGreater(float(high['sz']),float(base['sz']))
        self.assertLessEqual(high['estimated_loss'],Settings().capital*.05+1e-9)
        self.assertEqual(high['position_multiplier'],2.0)
"""
new="""        s=Settings(capital=1000,max_notional=5000,risk_usdt=10,risk_pct=1,daily_loss=30)
        base=make_plan(s, '做多', test_engine.TICK, test_engine.META, 200, 1000, 30, 1.0)
        high=make_plan(s, '做多', test_engine.TICK, test_engine.META, 200, 1000, 30, 2.0)
        self.assertGreater(float(high['sz']),float(base['sz']))
        self.assertLessEqual(high['estimated_loss'],s.capital*.05+1e-9)
        self.assertEqual(high['position_multiplier'],2.0)
"""
if old not in s:
    raise SystemExit('could not find sizing-test generator block')
s=s.replace(old,new,1)
p.write_text(s)
print('V1.3.4 sizing test parameters fixed')

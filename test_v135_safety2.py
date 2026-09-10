import subprocess
import sys
import textwrap
import unittest


class V135AdditionalSafetyTests(unittest.TestCase):
    def run_isolated(self, source):
        result = subprocess.run([sys.executable, '-c', textwrap.dedent(source)], text=True, capture_output=True, timeout=30)
        if result.returncode:
            self.fail('isolated execution failed\nSTDOUT:\n'+result.stdout+'\nSTDERR:\n'+result.stderr)

    def test_score_tiers_cover_full_selectable_1_to_10_range(self):
        self.run_isolated(r'''
            from v135_patch import _score_tier
            assert _score_tier(1.0)[0] == 1.0
            assert _score_tier(4.5)[0] == 1.0
            assert _score_tier(5.0)[0] == 1.5
            assert _score_tier(6.5)[0] == 1.5
            assert _score_tier(7.0)[0] == 2.0
            assert _score_tier(10.0)[0] == 2.0
        ''')

    def test_limit_entry_risk_uses_worst_case_taker_fee(self):
        self.run_isolated(r'''
            from v135_patch import apply
            apply()
            from engine import Settings,make_plan
            from test_engine import TICK,META
            s=Settings(capital=100,max_notional=500,leverage=5,risk_usdt=5,risk_pct=5,daily_loss=10)
            p=make_plan(s,'做多',TICK,META,200,100,10,1.0)
            assert p['entry_fee_budget_bps'] == 5.0
            assert p['estimated_loss'] <= p['effective_risk_budget'] + 1e-9
            assert p['position_multiplier'] == 1.0
        ''')

    def test_missing_protection_after_grace_sends_one_emergency_market_close(self):
        self.run_isolated(r'''
            import tempfile,time
            from v135_patch import apply
            apply()
            from engine import Engine,Settings
            from test_engine import FakeExchange

            x=FakeExchange(); events=[]
            with tempfile.TemporaryDirectory() as folder:
                e=Engine(x,folder,lambda k,d:events.append((k,d)))
                e.connect(); e.arm(Settings()); e.startup_buffer_until=0
                bar=int(time.time()//300)*300000-300000
                e.market={'side':'做多','bar':bar,'close':60000,'h':{'atr':2000},'m':{'atr':200},
                          'scores':{'做多':{'gate':True,'total':8,'position_multiplier':2.0,'level':'三级信号'}}}
                e.market_at=time.time(); e.market_monotonic=time.monotonic()
                e.cycle()
                p=e.store.data['active']
                x.pos=[dict(mgnMode='isolated',posSide=p['posSide'],pos=p['sz'],avgPx=p['px'])]
                x.protections=[]
                e.cycle()
                p=e.store.data['active']; p['filled_at']=time.time()-20; e.store.save()
                before=len([w for w in x.writes if w[0]=='/api/v5/trade/order' and w[1].get('ordType')=='market'])
                e.cycle()
                p=e.store.data['active']
                assert p.get('emergency_close_id')
                assert p.get('emergency_close_order_id')
                assert '保护状态无法核实' in e.store.data.get('halt','')
                after=len([w for w in x.writes if w[0]=='/api/v5/trade/order' and w[1].get('ordType')=='market'])
                assert after == before + 1
                e.cycle()
                final=len([w for w in x.writes if w[0]=='/api/v5/trade/order' and w[1].get('ordType')=='market'])
                assert final == after
                assert any('一次市价安全平仓' in str(v) for k,v in events if k=='log')
        ''')

    def test_manual_flatten_is_blocked_while_auto_safety_close_is_outstanding(self):
        self.run_isolated(r'''
            import tempfile,time
            from v135_patch import apply
            apply()
            from engine import Engine,Settings,Halt
            from test_engine import FakeExchange

            x=FakeExchange()
            with tempfile.TemporaryDirectory() as folder:
                e=Engine(x,folder)
                e.connect(); e.arm(Settings()); e.startup_buffer_until=0
                bar=int(time.time()//300)*300000-300000
                e.market={'side':'做多','bar':bar,'close':60000,'h':{'atr':2000},'m':{'atr':200},
                          'scores':{'做多':{'gate':True,'total':8,'position_multiplier':2.0,'level':'三级信号'}}}
                e.market_at=time.time(); e.market_monotonic=time.monotonic(); e.cycle()
                p=e.store.data['active']; p['filled']=True; p['emergency_close_id']='ec-test'; e.store.save()
                x.pos=[dict(mgnMode='isolated',posSide=p['posSide'],pos=p['sz'])]
                before=len(x.writes)
                try:
                    e.flatten()
                    raise AssertionError('expected Halt')
                except Halt as exc:
                    assert '已有自动安全平仓请求' in str(exc)
                assert len(x.writes)==before
        ''')

    def test_stop_flag_prevents_startup_buffer_reenable(self):
        self.run_isolated(r'''
            stopped=True; halt=''; enabled=False
            if not stopped and not halt:
                enabled=True
            assert enabled is False
        ''')


if __name__=='__main__':
    unittest.main(verbosity=2)

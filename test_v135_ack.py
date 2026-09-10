import time
import unittest

from exchange import APIError
import engine
import test_engine
import v135_patch
import v135_execution_patch

v135_patch.apply()
v135_execution_patch.apply()


class AcknowledgeSafetyTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp=tempfile.TemporaryDirectory()
        self.x=test_engine.FakeExchange()
        self.events=[]
        self.x.history=[]
        self.x.recent_orders=lambda: list(self.x.history)
        self.e=engine.Engine(self.x,self.tmp.name,lambda k,d:self.events.append((k,d)))
        self.e.connect()
        self.e.enabled=False

    def tearDown(self):
        self.tmp.cleanup()

    def seed_unknown(self,age):
        p={
            'client_id':'unknown-client','order_id':'','side':'做多','posSide':'long','sz':'0.10','px':'60000',
            'submitted':time.time()-age,'equity_before':100.0,'phase':'SUBMIT_UNKNOWN','score':8,
        }
        self.e.store.data['active']=p
        self.e.store.data['halt']='网络写入结果未知'
        self.e.store.save()
        self.x.pending=[]; self.x.pos=[]; self.x.protections=[]
        self.x.order=lambda *args,**kwargs: (_ for _ in ()).throw(APIError('51603','51603'))
        return p

    def test_unknown_write_cannot_be_cleared_inside_safety_window(self):
        p=self.seed_unknown(v135_execution_patch.UNKNOWN_WRITE_TIMEOUT-10)
        with self.assertRaises(engine.Halt):
            self.e.acknowledge()
        self.assertIs(self.e.store.data['active'],p)
        self.assertTrue(self.e.store.data['halt'])

    def test_unknown_write_can_be_manually_cleared_after_all_sources_are_empty(self):
        self.seed_unknown(v135_execution_patch.UNKNOWN_WRITE_TIMEOUT+5)
        daily='2099-01-01'
        self.e.store.data['daily_stop_day']=daily
        self.e.store.data['last_bar']=12345
        self.e.acknowledge()
        self.assertIsNone(self.e.store.data['active'])
        self.assertEqual(self.e.store.data['halt'],'')
        self.assertEqual(self.e.store.data['daily_stop_day'],daily)
        self.assertEqual(self.e.store.data['last_bar'],12345)
        self.assertTrue(any(k=='log' and '历史订单' in str(v) for k,v in self.events))

    def test_history_match_prevents_treating_filled_parent_as_never_existing(self):
        p=self.seed_unknown(v135_execution_patch.UNKNOWN_WRITE_TIMEOUT+5)
        self.x.history=[{'ordId':'h1','clOrdId':p['client_id'],'state':'filled','accFillSz':'0.10'}]
        self.x.equity=99
        self.e.settings=engine.Settings()
        self.e.acknowledge()
        self.assertIsNone(self.e.store.data['active'])
        self.assertEqual(self.e.store.data['streak'],1)
        self.assertTrue(any(k=='log' and '故障锁已解除' in str(v) for k,v in self.events))

    def test_nonterminal_history_evidence_refuses_unlock(self):
        p=self.seed_unknown(v135_execution_patch.UNKNOWN_WRITE_TIMEOUT+5)
        self.x.history=[{'ordId':'h1','clOrdId':p['client_id'],'state':'live','accFillSz':'0'}]
        with self.assertRaises(engine.Halt):
            self.e.acknowledge()
        self.assertIsNotNone(self.e.store.data['active'])

    def test_any_pending_algo_refuses_unlock(self):
        self.seed_unknown(v135_execution_patch.UNKNOWN_WRITE_TIMEOUT+5)
        self.x.protections=[{'algoClOrdId':'foreign','state':'live'}]
        with self.assertRaises(engine.Halt):
            self.e.acknowledge()
        self.assertIsNotNone(self.e.store.data['active'])


if __name__=='__main__':
    unittest.main(verbosity=2)

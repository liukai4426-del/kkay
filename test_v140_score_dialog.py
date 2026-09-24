import unittest

import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v139_position_patch as v139
import v140_score_dialog_patch as v140


class V140ScoreDialogTests(unittest.TestCase):
    def test_fixed_threshold(self):
        s=v140.SettingsV140(score_threshold=4.0,max_initial_notional=50,max_notional=100)
        self.assertIs(s.validate(),s)
        with self.assertRaises(engine.Halt):
            v140.SettingsV140(score_threshold=4.5,max_initial_notional=50,max_notional=100).validate()

    def test_tier_boundaries(self):
        cases=[
            (3.5,0,0.0),
            (4.0,1,1.0),(6.0,1,1.0),
            (6.5,2,1.5),(7.5,2,1.5),
            (8.0,3,2.0),(10.0,3,2.0),
        ]
        for score,tier,multiplier in cases:
            got=v140._tier(score)
            self.assertEqual((got[0],got[1]),(tier,multiplier),score)
        self.assertEqual(v137._tier(8.0)[0],3)
        self.assertEqual(v139._tier(8.0)[0],3)

    def test_addon_gate_keeps_existing_limits(self):
        active={'tier_counts':{'1':1,'2':0,'3':0},'highest_tier':1,'last_entry_bar':100,'legs':[{}]}
        ok,_=v140._allow_entry(active,2,101)
        self.assertTrue(ok)
        active['tier_counts']['2']=1
        ok,_=v140._allow_entry(active,2,102)
        self.assertFalse(ok)
        active['highest_tier']=3
        ok,_=v140._allow_entry(active,2,103)
        self.assertFalse(ok)
        active['tier_counts']['3']=1
        ok,_=v140._allow_entry(active,3,104)
        self.assertTrue(ok)
        active['tier_counts']['3']=2
        ok,_=v140._allow_entry(active,3,105)
        self.assertFalse(ok)

    def test_threshold_globals_are_updated(self):
        self.assertEqual(v140.V140_THRESHOLD,4.0)
        self.assertEqual(v139.V139_THRESHOLD,4.0)
        self.assertEqual(v138.V138_THRESHOLD,4.0)
        self.assertEqual(v137.V137_THRESHOLD,4.0)

    def test_dialog_contract(self):
        self.assertIn('connect',v140._ACTION_TEXT)
        self.assertIn('arm',v140._ACTION_TEXT)
        self.assertIn('flatten',v140._ACTION_TEXT)
        self.assertIn('ack',v140._ACTION_TEXT)
        self.assertEqual(v140._ACTION_TEXT['flatten'][0],'平仓成功')


if __name__=='__main__':
    unittest.main()

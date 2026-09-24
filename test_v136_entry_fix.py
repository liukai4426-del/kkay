import unittest

import v136_runtime
v136_runtime.apply()
import v136_entry_fix
v136_entry_fix.apply()


class V136EntryFixTests(unittest.TestCase):
    def test_expected_cost_threshold_maps_to_legacy_gate(self):
        self.assertAlmostEqual(v136_entry_fix._engine_gate_multiple(1.20),2.0)
        self.assertLess(v136_entry_fix._engine_gate_multiple(1.19),2.0)
        self.assertGreater(v136_entry_fix._engine_gate_multiple(1.21),2.0)

    def test_cost_reject_releases_newly_consumed_bar(self):
        state={'active':None,'last_bar':300000}
        plan={'expected_fee_multiple':1.10}
        changed=v136_entry_fix._release_rejected_bar(state,0,300000,plan)
        self.assertTrue(changed)
        self.assertEqual(state['last_bar'],0)

    def test_genuine_existing_dedupe_is_never_released(self):
        state={'active':None,'last_bar':300000}
        plan={'expected_fee_multiple':1.10}
        changed=v136_entry_fix._release_rejected_bar(state,300000,300000,plan)
        self.assertFalse(changed)
        self.assertEqual(state['last_bar'],300000)

    def test_active_order_is_never_released(self):
        state={'active':{'client_id':'abc'},'last_bar':300000}
        plan={'expected_fee_multiple':1.10}
        changed=v136_entry_fix._release_rejected_bar(state,0,300000,plan)
        self.assertFalse(changed)
        self.assertEqual(state['last_bar'],300000)

    def test_signal_above_new_cost_floor_keeps_dedupe(self):
        state={'active':None,'last_bar':300000}
        plan={'expected_fee_multiple':1.20}
        changed=v136_entry_fix._release_rejected_bar(state,0,300000,plan)
        self.assertFalse(changed)
        self.assertEqual(state['last_bar'],300000)


if __name__=='__main__':
    unittest.main(verbosity=2)

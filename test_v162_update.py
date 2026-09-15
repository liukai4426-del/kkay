import unittest
import v162_model as model

class V162ModelTests(unittest.TestCase):
    def row(self,state='aligned'):
        return {'total':6.5,'gate':True,'eligible':True,'position_multiplier':1.0,
                'layers':{'macd_improving':1.0},
                'confirmations':{'required':{},'4H_trend_state':state,'blockers':[]},
                'reason':'ok','level':'ok'}
    def test_middle_requires_4h_aligned(self):
        row=self.row('neutral'); model._apply_all_4h_gate(row,model.MIDDLE_PATH)
        self.assertFalse(row['eligible']); self.assertEqual(row['position_multiplier'],0.0)
    def test_outer_requires_4h_aligned(self):
        row=self.row('opposite'); model._apply_all_4h_gate(row,'lower_band')
        self.assertFalse(row['eligible'])
    def test_aligned_middle_survives_new_gate(self):
        row=self.row('aligned'); model._apply_all_4h_gate(row,model.MIDDLE_PATH)
        self.assertTrue(row['eligible'])

if __name__=='__main__': unittest.main()

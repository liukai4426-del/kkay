import unittest

import v143_score_arbitration_patch as v143


def row(score,eligible=True):
    return {'total':score,'eligible':eligible,'reason':f'{score:g}/10 达标'}


class ScoreArbitration143Tests(unittest.TestCase):
    def result(self,long_score,long_ok,short_score,short_ok):
        return {'side':'观望','why':'old','scores':{
            '做多':row(long_score,long_ok),
            '做空':row(short_score,short_ok),
        }}

    def test_only_long_eligible_selects_long(self):
        result=v143._apply_arbitration(self.result(5,True,8,False))
        self.assertEqual(result['side'],'做多')
        self.assertEqual(result['direction_arbitration']['state'],'single_eligible')

    def test_only_short_eligible_selects_short(self):
        result=v143._apply_arbitration(self.result(8,False,5,True))
        self.assertEqual(result['side'],'做空')

    def test_both_eligible_select_strictly_higher_long(self):
        result=v143._apply_arbitration(self.result(7,True,6.5,True))
        self.assertEqual(result['side'],'做多')
        self.assertFalse(result['direction_wait'])
        self.assertIn('仅选择高分方向做多',result['why'])

    def test_both_eligible_select_strictly_higher_short(self):
        result=v143._apply_arbitration(self.result(6.5,True,7,True))
        self.assertEqual(result['side'],'做空')

    def test_equal_eligible_scores_wait_without_direction(self):
        result=v143._apply_arbitration(self.result(7,True,7,True))
        self.assertEqual(result['side'],'观望')
        self.assertTrue(result['direction_wait'])
        self.assertEqual(result['direction_arbitration']['state'],'equal_score_wait')
        self.assertIn('本轮不发单',result['why'])

    def test_wait_rechecks_and_selects_later_higher_side(self):
        tied=v143._apply_arbitration(self.result(7,True,7,True))
        later=v143._apply_arbitration(self.result(7.5,True,7,True))
        self.assertTrue(tied['direction_wait'])
        self.assertEqual(later['side'],'做多')
        self.assertFalse(later['direction_wait'])

    def test_higher_unqualified_score_cannot_win(self):
        result=v143._apply_arbitration(self.result(5,True,9,False))
        self.assertEqual(result['side'],'做多')


if __name__=='__main__':
    unittest.main()

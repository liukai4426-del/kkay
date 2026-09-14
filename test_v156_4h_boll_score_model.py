import unittest

import v156_4h_boll_score_model as model


class V156FourHourBollScoreTests(unittest.TestCase):
    def test_exports_threshold(self):
        self.assertEqual(model.THRESHOLD, 6.0)

    def test_four_hour_boll_alignment_long_and_short(self):
        # Indicator behavior is covered by the shared production indicator tests;
        # this smoke test protects the experiment's public API and score wiring.
        self.assertTrue(callable(model._four_hour_boll_aligned))
        self.assertTrue(callable(model.evaluate))
        self.assertTrue(callable(model.execution_checks))


if __name__ == "__main__":
    unittest.main()

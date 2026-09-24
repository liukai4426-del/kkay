import unittest

import v141_history_return_chart_patch as chart


class V141HistoryReturnChartTests(unittest.TestCase):
    def test_history_points_use_strategy_capital_as_return_base(self):
        rows=[
            {'time':'2026-09-10T12:00:00Z','cumulative':10.0},
            {'time':'2026-09-11T12:00:00Z','cumulative':-5.0},
        ]
        self.assertEqual(chart._history_points(rows,1000.0),[
            ('2026-09-10T12:00:00Z',1.0),
            ('2026-09-11T12:00:00Z',-0.5),
        ])

    def test_invalid_capital_yields_no_points(self):
        self.assertEqual(chart._history_points([{'time':'2026-09-10T12:00:00Z','cumulative':10}],0),[])

    def test_date_and_rate_labels(self):
        self.assertEqual(chart._date_label('2026-09-12T01:02:03Z'),'09-12')
        self.assertEqual(chart._rate_label(1.234),'+1.23%')
        self.assertEqual(chart._rate_label(-0.5),'-0.50%')


if __name__=='__main__':
    unittest.main()

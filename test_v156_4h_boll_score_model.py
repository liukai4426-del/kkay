import unittest
from unittest.mock import patch

import v156_4h_boll_score_model as model


def row(total=6.0, pullback=2.0):
    return {
        "total": total,
        "raw": total,
        "gate": True,
        "eligible": total >= 6.0,
        "signal_tier": 1 if total >= 6.0 else 0,
        "position_multiplier": 1.0 if total >= 6.0 else 0.0,
        "level": "old",
        "reason": "old",
        "opportunity": {"id": "x"},
        "items": [
            ("15m正确方向回调区共振（辅助）", pullback, 2.0),
            ("5m BOLL中轨+RSI / 外轨触发【开仓必要】", 2.0, 2.0),
        ],
        "layers": {"direction_pullback": pullback, "boll_entry": 2.0},
        "confirmations": {"required": {"boll_entry_signal": True}, "blockers": []},
    }


class V156FourHourBollScoreTests(unittest.TestCase):
    def test_exports_threshold(self):
        self.assertEqual(model.THRESHOLD, 6.0)

    def test_pass_keeps_six_when_one_point_moves_from_15m_to_4h(self):
        r = row(total=6.0, pullback=2.0)
        with patch.object(model, "_four_hour_boll_aligned", return_value=True):
            model._rewrite_score_row(r, [], "做多")
        self.assertEqual(r["total"], 6.0)
        self.assertEqual(r["layers"]["direction_pullback"], 1.0)
        self.assertEqual(r["layers"]["four_hour_boll"], 1.0)
        self.assertTrue(r["confirmations"]["required"]["four_hour_boll_region"])
        self.assertTrue(r["eligible"])

    def test_fail_is_mandatory_even_if_numeric_score_is_high(self):
        r = row(total=8.0, pullback=2.0)
        with patch.object(model, "_four_hour_boll_aligned", return_value=False):
            model._rewrite_score_row(r, [], "做空")
        self.assertFalse(r["confirmations"]["required"]["four_hour_boll_region"])
        self.assertFalse(r["gate"])
        self.assertFalse(r["eligible"])
        self.assertIn("4H BOLL", r["reason"])

    def test_4h_point_can_promote_old_five_when_15m_pullback_was_zero(self):
        r = row(total=5.0, pullback=0.0)
        with patch.object(model, "_four_hour_boll_aligned", return_value=True):
            model._rewrite_score_row(r, [], "做多")
        self.assertEqual(r["total"], 6.0)
        self.assertTrue(r["eligible"])


if __name__ == "__main__":
    unittest.main()

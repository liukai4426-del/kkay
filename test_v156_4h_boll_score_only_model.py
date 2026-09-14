import unittest
from unittest.mock import patch

import v156_4h_boll_score_only_model as model


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


class V156FourHourBollScoreOnlyTests(unittest.TestCase):
    def test_exports_threshold(self):
        self.assertEqual(model.THRESHOLD, 6.0)

    def test_aligned_reallocates_one_point_and_keeps_six(self):
        r = row(total=6.0, pullback=2.0)
        with patch.object(model, "_four_hour_boll_aligned", return_value=True):
            model._rewrite_score_row(r, [], "做多")
        self.assertEqual(r["total"], 6.0)
        self.assertEqual(r["layers"]["direction_pullback"], 1.0)
        self.assertEqual(r["layers"]["four_hour_boll"], 1.0)
        self.assertNotIn("four_hour_boll_region", r["confirmations"]["required"])
        self.assertTrue(r["gate"])
        self.assertTrue(r["eligible"])

    def test_unaligned_never_becomes_hard_gate(self):
        r = row(total=8.0, pullback=2.0)
        with patch.object(model, "_four_hour_boll_aligned", return_value=False):
            model._rewrite_score_row(r, [], "做空")
        self.assertEqual(r["total"], 7.0)
        self.assertNotIn("four_hour_boll_region", r["confirmations"]["required"])
        self.assertTrue(r["gate"])
        self.assertTrue(r["eligible"])

    def test_4h_point_can_promote_old_five_when_pullback_zero(self):
        r = row(total=5.0, pullback=0.0)
        with patch.object(model, "_four_hour_boll_aligned", return_value=True):
            model._rewrite_score_row(r, [], "做多")
        self.assertEqual(r["total"], 6.0)
        self.assertTrue(r["eligible"])

    def test_unaligned_can_demote_six_only_through_score(self):
        r = row(total=6.0, pullback=2.0)
        with patch.object(model, "_four_hour_boll_aligned", return_value=False):
            model._rewrite_score_row(r, [], "做多")
        self.assertEqual(r["total"], 5.0)
        self.assertTrue(r["gate"])
        self.assertFalse(r["eligible"])
        self.assertEqual(r["level"], "评分不足")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

import v164_model as model
import v164_update_patch as runtime


def sample_row(ratio, volume_score=0.0, base_gate=True):
    layers = {
        "direction_pullback": 2.0,
        "entry_near_zone": 1.0,
        "structure_overlap": 0.0,
        "boll_entry": 2.0,
        "macd_improving": 1.0,
        "trend4h": 0.0,
        "ema50_15m_move": 0.0,
        "volume5": volume_score,
        "kdj5_signal": 0.0,
    }
    return {
        "total": sum(layers.values()),
        "raw": sum(layers.values()),
        "gate": base_gate,
        "eligible": base_gate and sum(layers.values()) >= 6.0,
        "position_multiplier": 1.0,
        "level": "旧状态",
        "reason": f"评分 {sum(layers.values()):g}/10",
        "layers": layers,
        "items": [
            ("5m BOLL中轨+RSI / 外轨触发（必须）", 2.0, 2.0),
            ("5m MACD柱连续两根改善", 1.0, 1.0),
            ("4H同向 / 中性 / 逆向", 0.0, 1.0),
            ("5m信号K线成交量≥20均量1.2×", volume_score, 0.5),
            ("5m信号KDJ对应交叉", 0.0, 0.5),
        ],
        "confirmations": {"required": {}, "blockers": []},
        "opportunity": {
            "signal_path": "lower_band",
            "five_signal_volume_ratio": ratio,
        },
    }


class V164ScoringTests(unittest.TestCase):
    def test_volume_score_removed_and_bonus_transferred_to_outer_boll(self):
        row = sample_row(1.00, volume_score=0.0)
        model._apply_v164_row(row, "lower_band", opportunity=row["opportunity"])
        self.assertNotIn("volume5", row["layers"])
        self.assertEqual(row["layers"]["boll_entry"], 2.5)
        self.assertEqual(row["total"], 6.5)
        labels = [str(x[0]) for x in row["items"]]
        self.assertTrue(any("BOLL外轨" in label for label in labels))
        self.assertFalse(any("成交量" in label for label in labels))
        self.assertFalse(any("中轨" in label for label in labels))

    def test_volume_equal_1_2_is_hard_block(self):
        row = sample_row(1.20, volume_score=0.5)
        model._apply_v164_row(row, "lower_band", opportunity=row["opportunity"])
        self.assertFalse(row["gate"])
        self.assertFalse(row["eligible"])
        self.assertFalse(row["confirmations"]["required"]["volume_below_1_2x"])
        self.assertTrue(any("Volume Hard Gate" in x for x in row["confirmations"]["blockers"]))

    def test_volume_below_1_2_allows_gate(self):
        row = sample_row(1.1999, volume_score=0.0)
        model._apply_v164_row(row, "lower_band", opportunity=row["opportunity"])
        self.assertTrue(row["confirmations"]["required"]["volume_below_1_2x"])
        self.assertTrue(row["gate"])
        self.assertTrue(row["eligible"])
        self.assertEqual(row["position_multiplier"], 1.0)

    def test_execution_check_rejects_high_volume(self):
        opp = {"signal_path": "upper_band", "five_signal_volume_ratio": 1.35}
        with patch.object(model.base, "execution_checks", return_value=(True, {}, [])):
            ok, diag, blockers = model.execution_checks({}, opp, {"confirmations": {}})
        self.assertFalse(ok)
        self.assertEqual(diag["volume_hard_gate"], 1.20)
        self.assertFalse(diag["volume_hard_gate_ok"])
        self.assertTrue(any("量能" in text for text in blockers))


class V164WiringTests(unittest.TestCase):
    def test_version_and_runtime_model_wiring(self):
        self.assertEqual(model.VERSION, "1.6.4")
        self.assertEqual(runtime.VERSION, "1.6.4")
        self.assertEqual(runtime.BUILD, "1640")
        self.assertEqual(model.VOLUME_HARD_GATE, 1.20)
        self.assertEqual(model.BOLL_OUTER_SCORE, 2.50)
        self.assertIs(runtime.v160.model, model)
        self.assertIs(runtime.v161.model, model)
        self.assertIs(runtime.v162.model, model)
        self.assertIs(runtime.v138._prepare_order, runtime.v162._prepare_order_v162)
        self.assertIs(runtime.v138._submit_initial, runtime._submit_v164)


if __name__ == "__main__":
    unittest.main()

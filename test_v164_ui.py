import types
import unittest
from unittest.mock import Mock, patch

import app
import v161_ui_status_patch as ui161
import v164_ui_patch as ui


class V164UITests(unittest.TestCase):
    def test_version_subtitle_is_current(self):
        self.assertEqual(ui.VERSION, "1.6.4")
        self.assertEqual(ui.BUILD, "1641")
        self.assertEqual(ui.VERSION_SUBTITLE, "BTC / USDT   ·   V1.6.4")
        self.assertIn("1.6.4", ui.WINDOW_TITLE)
        self.assertIn("1641", ui.WINDOW_TITLE)

    def test_opening_status_replaces_funds_with_volume_and_window_gates(self):
        labels = dict(ui161._STATUS_ITEMS)
        self.assertNotIn("funds", labels)
        self.assertEqual(labels["volume_gate"], "量能 Hard Gate")
        self.assertEqual(labels["signal_window"], "外轨信号≤4m")
        self.assertEqual(labels["boll"], "外轨BOLL")
        self.assertNotIn("中轨", " ".join(labels.values()))

    def test_volume_and_window_status_snapshot(self):
        owner = types.SimpleNamespace()
        row = {
            "total": 6.5,
            "confirmations": {
                "required": {"entry_window_4x1m": True},
                "blockers": [],
                "volume_hard_gate_ok": False,
                "signal_window_ok": True,
            },
            "opportunity": {
                "signal_path": "lower_band",
                "five_signal_volume_ratio": 1.25,
            },
            "structure": {},
        }
        data = {
            "direction": "做多",
            "scores": {"做多": row},
            "threshold": 6.0,
            "opportunity": row["opportunity"],
        }
        with patch.object(ui, "_PREVIOUS_STATUS_SNAPSHOT", return_value={}):
            snapshot = ui._status_snapshot_v164(owner, data)
        self.assertFalse(snapshot["volume_gate"])
        self.assertTrue(snapshot["signal_window"])
        self.assertNotIn("funds", snapshot)

    def test_renderer_uses_green_for_allow_and_red_for_block(self):
        green = Mock()
        red = Mock()
        expired = Mock()
        owner = types.SimpleNamespace(
            _v161_status_labels={"volume_gate": green, "score": red, "signal_window": expired},
            _v161_status_snapshot={"volume_gate": True, "score": False, "signal_window": False},
        )
        ui._render_status_v164(owner)
        self.assertEqual(green.configure.call_args.kwargs["fg"], app.GREEN)
        self.assertIn("允许开仓", green.configure.call_args.kwargs["text"])
        self.assertEqual(red.configure.call_args.kwargs["fg"], app.RED)
        self.assertEqual(expired.configure.call_args.kwargs["fg"], app.RED)
        self.assertIn("已失效", expired.configure.call_args.kwargs["text"])


if __name__ == "__main__":
    unittest.main()

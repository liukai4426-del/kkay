import json
import tempfile
import unittest
from pathlib import Path

import app
import history
import v165_model as model
import v167_15m_boll_only_fix as b1671
import v170_build1701_patch as v1701
import v171_update_patch as v171


class V171UpdateTests(unittest.TestCase):
    def test_identity_and_strict15m_safety_are_preserved(self):
        self.assertEqual(v171.VERSION, "1.7.1")
        self.assertEqual(v171.BUILD, "1710")
        self.assertEqual(model.VERSION, "1.7.1")
        self.assertEqual(model.BUILD, "1710")
        self.assertEqual(model.BOLL_TRIGGER_TIMEFRAME, "15m")
        self.assertFalse(model.FIVE_MINUTE_BOLL_ENABLED)
        self.assertTrue(getattr(model, "_kaytrade_v170_build1701_applied", False))
        self.assertTrue(getattr(model, "_kaytrade_v171_applied", False))
        self.assertTrue(getattr(app.App, "_kaytrade_v171_applied", False))

    def test_versioned_closed_round_events_are_counted_once(self):
        rows = [
            ("仓位归零", "c1", 10.0, "做多"),
            ("V1.3.7仓位归零", "c2", -5.0, "做空"),
            ("V1.5.4仓位归零", "c3", 8.0, "做多"),
            ("V1.6.8仓位归零", "c4", -7.0, "做空"),
            ("人工核对平仓", "c5", 5.0, "做多"),
            ("V1.7.0人工核对平仓", "c6", -6.3325, "做空"),
            ("V1.7.0仓位归零", "c7", -94.9380, "做多"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "account.history.jsonl"
            with path.open("w") as f:
                for i, (event, cid, pnl, side) in enumerate(rows):
                    record = {
                        "time": f"2026-10-02T22:{i:02d}:00+00:00",
                        "event": event,
                        "data": {"client_id": cid, "equity_change": pnl, "side": side},
                    }
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                # Submission/PEE events must never be double-counted as closed rounds.
                f.write(json.dumps({
                    "time": "2026-10-02T23:00:00+00:00",
                    "event": "PEE4提前退出",
                    "data": {"client_id": "c7", "equity_change": -94.9380, "side": "做多"},
                }, ensure_ascii=False) + "\n")

            result = history.summarize(path)

        self.assertEqual(result["count"], 7)
        self.assertEqual(result["wins"], 3)
        self.assertEqual(result["losses"], 4)
        self.assertEqual(result["breakeven"], 0)
        self.assertAlmostEqual(result["total"], -90.2705, places=4)
        self.assertAlmostEqual(result["win_rate"], 100 * 3 / 7, places=6)
        self.assertEqual(result["rows"][-1]["client_id"], "c7")
        self.assertAlmostEqual(result["rows"][-1]["cumulative"], -90.2705, places=4)

    def test_close_event_filter_does_not_accept_submission_events(self):
        self.assertTrue(history._is_closed_round_event("仓位归零"))
        self.assertTrue(history._is_closed_round_event("V1.7.0仓位归零"))
        self.assertTrue(history._is_closed_round_event("V1.7.1人工核对平仓"))
        self.assertFalse(history._is_closed_round_event("PEE4提前退出"))
        self.assertFalse(history._is_closed_round_event("提交开仓请求"))
        self.assertFalse(history._is_closed_round_event("限价单未成交"))

    def test_runtime_identity_is_promoted_without_removing_source_lock(self):
        text = v171._rewrite_runtime_text_v171(
            "自动交易启动；V1.7.0 Build1701：15m机会源锁：只有最新已收盘15m外轨真实触发才可创建开仓机会"
        )
        self.assertIn("V1.7.1", text)
        self.assertIn("Build1710", text)
        self.assertIn("15m机会源锁", text)
        self.assertIn("历史收益兼容", text)
        self.assertIs(model.boll_entry_signal, b1671._signal_adapter_15m_only)


if __name__ == "__main__":
    unittest.main()

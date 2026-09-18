import unittest
from unittest.mock import patch

import app
import v165_model as model
import v165_update_patch as runtime
import v167_15m_boll_only_fix as b1671
import v170_build1701_patch as v1701


class V170Build1701Tests(unittest.TestCase):
    def test_identity_and_install(self):
        self.assertEqual(v1701.VERSION, "1.7.0")
        self.assertEqual(v1701.BUILD, "1701")
        self.assertEqual(model.VERSION, "1.7.0")
        self.assertEqual(model.BUILD, "1701")
        self.assertEqual(model.BOLL_TRIGGER_TIMEFRAME, "15m")
        self.assertFalse(model.FIVE_MINUTE_BOLL_ENABLED)
        self.assertTrue(getattr(model, "_kaytrade_v170_build1701_applied", False))
        self.assertTrue(getattr(app.App, "_kaytrade_v170_build1701_applied", False))

    def test_no_strict_15m_signal_means_no_new_opportunity(self):
        with (
            patch.object(model, "trend_direction", return_value="做多"),
            patch.object(b1671, "_boll_entry_signal_15m_only", return_value=None),
        ):
            incoming, direction, signal, created = v1701._strict15_prepare(
                [], [], [], [], opportunity={"boll_timeframe": "5m"}, allow_new=True
            )
        self.assertIsNone(incoming)
        self.assertEqual(direction, "做多")
        self.assertIsNone(signal)
        self.assertFalse(created)

    def test_strict_15m_signal_is_manually_created_before_legacy_chain(self):
        signal = {"bar_t": 900000, "boll_timeframe": "15m"}
        candidate = {
            "id": "strict-long",
            "side": "做多",
            "signal_path": "lower_band",
            "signal_bar_t": 900000,
            "boll_timeframe": "15m",
        }
        with (
            patch.object(model, "trend_direction", return_value="做多"),
            patch.object(b1671, "_boll_entry_signal_15m_only", return_value=signal),
            patch.object(b1671, "_new_opportunity_15m_only", return_value=candidate),
            patch.object(b1671, "_valid_15m_evidence", side_effect=lambda x: x is candidate),
        ):
            incoming, direction, returned_signal, created = v1701._strict15_prepare(
                [], [], [], [], opportunity=None, allow_new=True
            )
        self.assertIs(incoming, candidate)
        self.assertEqual(direction, "做多")
        self.assertIs(returned_signal, signal)
        self.assertTrue(created)

    def test_legacy_evaluator_is_never_allowed_to_create_opportunity(self):
        seen = {}

        def previous(*args, **kwargs):
            seen["allow_new"] = kwargs.get("allow_new")
            seen["opportunity"] = kwargs.get("opportunity")
            return {
                "scores": {},
                "side": "观望",
                "direction": "做多",
                "opportunity": None,
            }, None, None

        with (
            patch.object(v1701, "_strict15_prepare", return_value=(None, "做多", None, False)),
            patch.object(v1701, "_PREVIOUS_MODEL_EVALUATE", side_effect=previous),
        ):
            result, opp, transition = v1701._evaluate_v1701(
                [], [], [], [], [], opportunity=None, allow_new=True, now_ms=1
            )
        self.assertFalse(seen["allow_new"])
        self.assertIsNone(seen["opportunity"])
        self.assertIsNone(opp)
        self.assertIsNone(transition)
        self.assertTrue(result["strict15_opportunity_source_lock"])

    def test_execution_boundary_rejects_non15m_opportunity(self):
        with patch.object(
            v1701, "_PREVIOUS_EXECUTION_CHECKS", return_value=(True, {}, [])
        ):
            ok, diag, blockers = v1701._execution_checks_v1701(
                {}, {"boll_timeframe": "5m"}, {}
            )
        self.assertFalse(ok)
        self.assertFalse(diag["strict15_evidence_valid"])
        self.assertTrue(any("已收盘15m BOLL外轨触发证据" in x for x in blockers))

    def test_final_entry_guard_rejects_non15m_opportunity(self):
        owner = object()
        market = {"opportunity": {"boll_timeframe": "5m"}}
        with (
            patch.object(v1701, "_PREVIOUS_PRE_SUBMIT_GUARD", return_value=[]),
            patch.object(runtime, "_opportunity_from", return_value=market["opportunity"]),
        ):
            blockers = v1701._pre_submit_guard_v1701(owner, market, {})
        self.assertTrue(any("Final Entry Guard" in x and "15m BOLL" in x for x in blockers))

    def test_direction_is_added_to_run_log(self):
        long_text = v1701._with_direction(
            "V1.7.0未开仓 15m BOLL信号有效期 · 第5分钟", "做多"
        )
        short_text = v1701._with_direction(
            "V1.7.0 Final Entry Guard禁止开仓：成本过高", "做空"
        )
        self.assertTrue(long_text.startswith("方向：做多 · "))
        self.assertTrue(short_text.startswith("方向：做空 · "))
        self.assertEqual(
            v1701._with_direction(long_text, "做多").count("方向：做多"), 1
        )

    def test_transition_detail_carries_direction(self):
        self.assertEqual(
            v1701._direction_detail("1H方向失效", "做空"),
            "方向：做空｜1H方向失效",
        )

    def test_runtime_text_marks_build1701_and_source_lock(self):
        text = v1701._rewrite_runtime_text_v1701(
            "自动交易启动；V1.7.0 Build1700：15m BOLL外轨"
        )
        self.assertIn("Build1701", text)
        self.assertIn("15m机会源锁", text)
        self.assertIn("5m BOLL", text)
        self.assertIn("不得创建开仓机会", text)


if __name__ == "__main__":
    unittest.main()

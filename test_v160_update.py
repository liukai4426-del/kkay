from dataclasses import dataclass
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v152_strategy_patch as v152_runtime
import v153_runtime_patch as v153_runtime
import v153_backtest_core as v153_bt
import v154_runtime_patch as v154_runtime
import v154_limit_log_fix as v154_limit
import v160_model as model
import v160_update_patch as v160


class V160ModelTests(unittest.TestCase):
    def _row(self, macd, eligible=True):
        return {
            "total": 6.5,
            "gate": True,
            "eligible": eligible,
            "position_multiplier": 1.0 if eligible else 0.0,
            "level": "开仓信号 · 1×仓位",
            "reason": "原规则通过",
            "layers": {"macd_improving": 1.0 if macd else 0.0},
            "confirmations": {"required": {"boll_entry_signal": True}},
        }

    def test_middle_requires_existing_macd_improving(self):
        blocked = self._row(False)
        model._apply_row_rules(blocked, "middle_rsi")
        self.assertFalse(blocked["gate"])
        self.assertFalse(blocked["eligible"])
        self.assertEqual(blocked["position_multiplier"], 0.0)
        self.assertFalse(blocked["confirmations"]["required"]["middle_macd_improving"])
        self.assertIn("禁止开仓", blocked["reason"])

        allowed = self._row(True)
        model._apply_row_rules(allowed, "middle_rsi")
        self.assertTrue(allowed["gate"])
        self.assertTrue(allowed["eligible"])
        self.assertEqual(allowed["position_multiplier"], 1.0)
        self.assertTrue(allowed["confirmations"]["required"]["middle_macd_improving"])
        self.assertIn("中轨1×基础仓位", allowed["level"])

    def test_outer_band_keeps_trigger_rule_and_marks_2x(self):
        for path in ("lower_band", "upper_band"):
            row = self._row(False)
            original_required = dict(row["confirmations"]["required"])
            model._apply_row_rules(row, path)
            self.assertTrue(row["gate"])
            self.assertTrue(row["eligible"])
            self.assertEqual(row["position_multiplier"], 2.0)
            self.assertEqual(row["boll_position_multiplier"], 2.0)
            self.assertEqual(row["confirmations"]["required"], original_required)
            self.assertNotIn("middle_macd_improving", row["confirmations"]["required"])
            self.assertIn("外轨2×基础仓位", row["level"])

    def test_middle_block_does_not_mutate_or_reclassify_opportunity(self):
        opp = {
            "id": "same-opportunity",
            "signal_path": "middle_rsi",
            "signal_bar_t": 123,
            "consumed": False,
        }
        before = dict(opp)
        result = {
            "side": "做多",
            "opportunity": opp,
            "scores": {"做多": self._row(False), "做空": self._row(False, eligible=False)},
        }
        model._rewrite_result(result, opportunity=opp)
        self.assertEqual(opp, before)
        self.assertEqual(result["opportunity"]["signal_path"], "middle_rsi")
        self.assertEqual(result["side"], "观望")
        self.assertFalse(result["scores"]["做多"]["eligible"])

    def test_execution_checks_rechecks_middle_macd_immediately_before_submit(self):
        opp = {"id": "m1", "signal_path": "middle_rsi"}
        score = self._row(False)
        with patch.object(model.base, "execution_checks", return_value=(True, {"base": True}, [])):
            ok, diag, blockers = model.execution_checks({}, opp, score)
        self.assertFalse(ok)
        self.assertTrue(any("macd_improving" in item for item in blockers))
        self.assertEqual(diag["boll_signal_path"], "middle_rsi")
        self.assertTrue(diag["middle_macd_required"])
        self.assertFalse(diag["middle_macd_improving"])

    def test_outer_execution_check_does_not_add_macd_gate(self):
        opp = {"id": "o1", "signal_path": "upper_band"}
        score = self._row(False)
        with patch.object(model.base, "execution_checks", return_value=(True, {}, [])):
            ok, diag, blockers = model.execution_checks({}, opp, score)
        self.assertTrue(ok)
        self.assertEqual(blockers, [])
        self.assertEqual(diag["position_multiplier"], 2.0)
        self.assertFalse(diag["middle_macd_required"])


@dataclass(frozen=True)
class FakeSettings:
    max_notional: float = 100.0
    max_initial_notional: float = 100.0
    first_signal_notional: float = 20.0
    second_signal_notional: float = 60.0


class V160RuntimeTests(unittest.TestCase):
    def test_runtime_facades_use_v160_model(self):
        self.assertIs(v152_runtime.model, model)
        self.assertIs(v153_runtime.model, model)
        self.assertIs(v153_bt.model, model)
        self.assertIs(v154_runtime.model, model)
        self.assertIs(v154_limit.model, model)
        self.assertIs(v138._prepare_order, v160._prepare_order_v160)
        self.assertTrue(engine.Engine._kaytrade_v160_applied)

    def test_outer_settings_double_only_the_temporary_sizing_view(self):
        original = FakeSettings()
        doubled = v160._outer_settings(original)
        self.assertEqual(original.max_notional, 100.0)
        self.assertEqual(original.max_initial_notional, 100.0)
        self.assertEqual(doubled.max_notional, 200.0)
        self.assertEqual(doubled.max_initial_notional, 200.0)
        self.assertEqual(doubled.second_signal_notional, 40.0)

    def test_middle_macd_failure_stops_before_underlying_prepare(self):
        owner = SimpleNamespace(settings=FakeSettings(), emit=lambda *_: None)
        score = {"layers": {"macd_improving": 0.0}, "opportunity": {"signal_path": "middle_rsi"}}
        market = {"opportunity": {"signal_path": "middle_rsi"}}
        with patch.object(v160, "_PREVIOUS_PREPARE", side_effect=AssertionError("must not call")):
            prepared = v160._prepare_order_v160(owner, "做多", score, market, 1000, 1000, 100)
        self.assertIsNone(prepared)
        self.assertIs(owner.settings.__class__, FakeSettings)

    def test_outer_prepare_uses_one_2x_plan_and_restores_runtime_state(self):
        original = FakeSettings()
        owner = SimpleNamespace(settings=original, emit=lambda *_: None)
        score = {"total": 6.5, "layers": {"macd_improving": 0.0}, "opportunity": {"signal_path": "upper_band"}}
        market = {"opportunity": {"signal_path": "upper_band"}}
        calls = []
        original_tier = v137._tier

        def previous_prepare(self, side, score, market, equity, available, remaining):
            tier, multiplier, level = v137._tier(score["total"])
            calls.append({"max_notional": self.settings.max_notional, "multiplier": multiplier})
            return {"notional": self.settings.max_notional, "position_multiplier": multiplier}, tier, multiplier, level

        with patch.object(v160, "_PREVIOUS_PREPARE", previous_prepare):
            plan, tier, multiplier, level = v160._prepare_order_v160(owner, "做空", score, market, 1000, 1000, 100)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_notional"], 200.0)
        self.assertEqual(calls[0]["multiplier"], 2.0)
        self.assertIs(owner.settings, original)
        self.assertIs(v137._tier, original_tier)
        self.assertEqual(multiplier, 2.0)
        self.assertEqual(plan["position_rule"], "outer_band_2x_single_order")
        self.assertEqual(plan["base_position_notional"], 100.0)
        self.assertEqual(plan["requested_position_notional"], 200.0)
        self.assertIn("外轨2×基础仓位", level)


if __name__ == "__main__":
    unittest.main()

import inspect
import unittest

import v182_ui_patch as v182
import visual


class V182UISourceContractTests(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(v182.VERSION, "1.8.2")
        self.assertEqual(v182.BUILD, "1820")
        self.assertTrue(v182.PAPER_ONLY)
        self.assertTrue(v182.LIMIT_ONLY)

    def test_rounded_panel_component_exists(self):
        self.assertTrue(hasattr(visual, "RoundedPanel"))
        source = inspect.getsource(visual.RoundedPanel)
        self.assertIn("create_polygon", source)
        self.assertIn("smooth=True", source)

    def test_overview_install_order(self):
        source = inspect.getsource(v182._install_v182_overview)
        quote = source.index("quote.pack")
        auto = source.index("_install_auto_card")
        plan = source.index("_install_plan_card")
        execution = source.index("_install_execution_card")
        self.assertLess(quote, auto)
        self.assertLess(auto, plan)
        self.assertLess(plan, execution)

    def test_entry_label_is_stacked_and_colored_by_direction(self):
        plan_source = inspect.getsource(v182._install_plan_card)
        refresh_source = inspect.getsource(v182._refresh_v182_dashboard)
        self.assertIn("建议 / 委托入场", plan_source)
        self.assertIn("_v182_entry_value_label", plan_source)
        self.assertIn("visual.GREEN if direction == \"long\"", refresh_source)
        self.assertIn("visual.RED if direction == \"short\"", refresh_source)
        self.assertIn("_v182_entry_value_label.configure(fg=accent)", refresh_source)

    def test_ai_plan_nested_panels_are_rounded(self):
        source = inspect.getsource(v182._install_plan_card)
        self.assertGreaterEqual(source.count("visual.RoundedPanel("), 6)
        self.assertIn("止盈 TP · 限价", source)
        self.assertIn("止损 SL · 限价", source)

    def test_recent_plan_has_no_market_fallback(self):
        source = inspect.getsource(v182._refresh_v182_dashboard)
        self.assertNotIn('"MKT"', source)
        self.assertIn('"INVALID"', source)
        self.assertIn('owner._v180_order_type_var.set("限价")', source)

    def test_paper_status_rewrite(self):
        self.assertEqual(
            v182._rewrite_v182_text("全自动运行 / 模拟盘"),
            "Paper运行 / 模拟盘",
        )
        self.assertEqual(
            v182._rewrite_v182_text("AI Only运行 / OKX模拟盘"),
            "Paper运行 / OKX模拟盘",
        )

    def test_legacy_controls_are_hidden(self):
        source = inspect.getsource(v182._clean_legacy_overview)
        self.assertIn("仅平本程序仓位", source)
        self.assertIn("核对后解除故障锁", source)
        self.assertIn("_find_label_for_var", source)


if __name__ == "__main__":
    unittest.main()

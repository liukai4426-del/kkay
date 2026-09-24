import types
import unittest

import app
import v161_ui_status_patch as ui161
import v165_ui_patch as ui


class DummySettings:
    leverage = 5
    risk_usdt = 20
    capital = 2000
    risk_pct = 1
    max_notional = 1000


class DummyEngine:
    x = types.SimpleNamespace(demo=True)


class DummyOwner:
    engine = DummyEngine()


class V165UiTests(unittest.TestCase):
    def test_version_and_status_labels_are_current(self):
        self.assertEqual(ui.VERSION, "1.6.5")
        self.assertEqual(ui.BUILD, "1650")
        labels = dict(ui161._STATUS_ITEMS)
        self.assertNotIn("funds", labels)
        self.assertEqual(labels["signal_window"], "最新5m信号周期")
        self.assertEqual(labels["volume_gate"], "Volume<1.20×")

    def test_authorization_text_contains_only_current_strategy(self):
        text = ui._authorization_text(DummyOwner(), DummySettings())
        self.assertIn("V1.6.5 当前生效策略", text)
        self.assertIn("最新已收盘K线", text)
        self.assertIn("下一根5m收盘", text)
        self.assertIn("1H / 15m", text)
        self.assertIn("4H必须", text)
        self.assertNotIn("1D EMA", text)
        self.assertNotIn("中轨", text)
        self.assertNotIn("1m Trigger", text)
        self.assertNotIn("请输入", text)
        self.assertNotIn("DEMO", text)
        self.assertNotIn("LIVE", text)

    def test_final_app_arm_is_v165_confirmation_flow(self):
        self.assertIs(app.App.arm, ui.app_arm_v165)


if __name__ == "__main__":
    unittest.main()

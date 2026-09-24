import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v154_model as model
import v154_limit_log_fix as fix


class FakeStore:
    def __init__(self):
        self.data = {"last_bar": None, "active": None}
        self.saved = 0
        self.records = []

    def save(self):
        self.saved += 1

    def record(self, kind, data):
        self.records.append((kind, data))


class FakeExchange:
    def __init__(self):
        self.posts = []

    def post(self, path, body):
        self.posts.append((path, body))
        return [{"ordId": "123456"}]


class FakeText:
    def __init__(self):
        self.value = ""
        self.state = "disabled"
        self.top = 0.0
        self.seen_end = False

    def yview(self):
        return (self.top, 1.0 if self.top == 0 else min(1.0, self.top + .3))

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]

    def delete(self, start, end):
        self.value = ""

    def insert(self, where, text, tag=None):
        self.value += text

    def see(self, where):
        self.seen_end = True

    def yview_moveto(self, value):
        self.top = float(value)


class V154LimitLogRepairTests(unittest.TestCase):
    def test_production_hooks_point_to_repair(self):
        self.assertEqual(model.ENTRY_WINDOW_MS, 60_000)
        self.assertIs(v137._v137_make_plan, fix._v154_limit_make_plan)
        self.assertIs(v138._submit_initial, fix._submit_initial_limit)
        self.assertIs(app.App.render_logs, fix._render_logs_fixed)
        self.assertTrue(engine.Engine._kaytrade_v154_limit_log_fix_applied)

    def test_limit_submit_is_strictly_inside_first_minute(self):
        store = FakeStore()
        exchange = FakeExchange()
        emitted = []
        fake = SimpleNamespace(
            market_now=lambda: 1010.0,
            emit=lambda kind, data: emitted.append((kind, data)),
            enabled=True,
            _verify_latest=lambda *args: None,
            store=store,
            x=exchange,
        )
        market = {
            "side": "做多",
            "bar": 999_000,
            "opportunity": {"signal_close_ms": 1_000_000},
        }
        score = {"total": 6.0}
        plan = {
            "exchange_side": "buy", "posSide": "long", "px": "100.0", "sz": "1",
            "tp": "102.0", "sl": "99.0", "side": "做多",
        }
        prepared = (plan, 1, 1.0, "开仓信号")
        with patch.object(v138, "_prepare_order", return_value=prepared), patch.object(v138, "_set_leverage", return_value=True):
            fix._submit_initial_limit(fake, market, score, 10000.0, 10000.0, 100.0)
        self.assertEqual(len(exchange.posts), 1)
        path, body = exchange.posts[0]
        self.assertEqual(path, "/api/v5/trade/order")
        self.assertEqual(body["ordType"], "limit")
        self.assertEqual(body["px"], "100.0")
        self.assertEqual(store.data["active"]["order_type"], "limit")
        self.assertEqual(store.data["active"]["legs"][0]["order_type"], "limit")
        self.assertIn("限价", emitted[-2][1])

    def test_limit_submit_rejects_second_minute(self):
        store = FakeStore()
        exchange = FakeExchange()
        emitted = []
        fake = SimpleNamespace(
            market_now=lambda: 1060.0,
            emit=lambda kind, data: emitted.append((kind, data)),
            enabled=True,
            _verify_latest=lambda *args: None,
            store=store,
            x=exchange,
        )
        market = {"side": "做多", "bar": 999_000, "opportunity": {"signal_close_ms": 1_000_000}}
        fix._submit_initial_limit(fake, market, {"total": 6.0}, 10000.0, 10000.0, 100.0)
        self.assertFalse(exchange.posts)
        self.assertIn("窗口已结束", emitted[-1][1])

    def test_limit_plan_uses_maker_expected_entry_cost(self):
        source = inspect.getsource(fix._v154_limit_make_plan)
        self.assertIn('limit * maker', source)
        self.assertIn('entry_order_type="limit"', source)
        self.assertIn('entry_slippage_budget_bps=0.0', source)
        self.assertNotIn('entry_order_type="market"', source)

    def test_persisted_events_are_restored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.log"
            path.write_text(
                "2026-09-14 01:00:00 自动交易启动\n"
                "2026-09-14 01:01:00 警报：测试告警\n",
                encoding="utf-8",
            )
            fake = SimpleNamespace(folder=Path(folder), log_lines=[])
            count = fix._load_recent_logs(fake)
            self.assertEqual(count, 2)
            self.assertEqual(fake.log_lines[0][0], "log")
            self.assertEqual(fake.log_lines[1][0], "alarm")

    def test_log_renderer_always_renders_current_model(self):
        widget = FakeText()
        fake = SimpleNamespace(
            log=widget,
            log_filter=SimpleNamespace(get=lambda: "全部"),
            log_lines=[("log", "普通日志"), ("alarm", "警报日志")],
        )
        fix._render_logs_fixed(fake)
        self.assertIn("普通日志", widget.value)
        self.assertIn("警报日志", widget.value)
        self.assertEqual(widget.state, "disabled")

        widget2 = FakeText()
        fake2 = SimpleNamespace(
            log=widget2,
            log_filter=SimpleNamespace(get=lambda: "警报"),
            log_lines=[("log", "普通日志"), ("alarm", "警报日志")],
        )
        fix._render_logs_fixed(fake2)
        self.assertNotIn("普通日志", widget2.value)
        self.assertIn("警报日志", widget2.value)


if __name__ == "__main__":
    unittest.main()

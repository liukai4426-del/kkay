import time
import unittest

import app
import engine
import exchange
import v161_ui_status_patch as ui161
import v165_model as model
import v170_update_patch as v170


def flat_then_spike(count=230, spike=112.0):
    rows = []
    for i in range(count):
        close = 100.0
        rows.append({
            "t": i * 300_000,
            "o": close,
            "h": close + 0.5,
            "l": close - 0.5,
            "c": close,
            "v": 100.0,
        })
    rows[-1] = {
        "t": (count - 1) * 300_000,
        "o": 100.0,
        "h": spike + 0.5,
        "l": 99.5,
        "c": spike,
        "v": 100.0,
    }
    return rows


def short_opp(signal_close_ms):
    return {
        "id": "v170-short",
        "side": "做空",
        "signal_path": "upper_band",
        "boll_timeframe": "15m",
        "signal_close_ms": signal_close_ms,
        "created_ms": signal_close_ms,
    }


def row(opp):
    return {
        "total": 6.0,
        "raw": 6.0,
        "gate": True,
        "eligible": True,
        "required": 6.0,
        "position_multiplier": 1.0,
        "level": "旧",
        "reason": "评分 6/10",
        "items": [
            ("15m BOLL外轨信号（有效至下一根15m收盘）", 2.0, 2.0),
            ("1H EMA9/26趋势", 1.0, 1.0),
            ("15m EMA50顺交易方向推进", 1.0, 1.0),
        ],
        "layers": {
            "boll_entry": 2.0,
            "trend4h": 1.0,
            "ema9_26_1h": 1.0,
            "ema50_15m_move": 1.0,
            "entry_near_zone": 1.0,
        },
        "confirmations": {
            "required": {"outer_4h_aligned": True, "signal_window_15m": True},
            "1H_ema9": 101.0,
            "1H_ema26": 100.0,
            "1H_ema9_26_trend_ok": True,
            "blockers": [],
        },
        "opportunity": dict(opp),
    }


class V170Tests(unittest.TestCase):
    def test_identity_and_runtime_hooks(self):
        self.assertEqual(v170.VERSION, "1.7.0")
        self.assertEqual(v170.BUILD, "1700")
        self.assertEqual(model.VERSION, "1.7.0")
        self.assertEqual(model.BUILD, "1700")
        self.assertEqual(model.THRESHOLD, 6.0)
        self.assertTrue(getattr(model, "_kaytrade_v170_applied", False))
        self.assertTrue(getattr(engine.Engine, "_kaytrade_v170_applied", False))
        self.assertTrue(getattr(exchange.Exchange, "_kaytrade_v170_applied", False))
        self.assertTrue(getattr(app.App, "_kaytrade_v170_applied", False))

    def test_noema_and_boll5_overextension_score(self):
        five = flat_then_spike()
        now_ms = 100_000_000
        opp = short_opp(now_ms - 60_000)
        result = {
            "side": "做空",
            "scores": {
                "做多": row(None) if False else {
                    "total": 0.0, "raw": 0.0, "gate": False, "eligible": False,
                    "items": [], "layers": {}, "confirmations": {}, "reason": "",
                },
                "做空": row(opp),
            },
        }
        out, returned = v170._apply_noema_overext(result, opp, five, now_ms)
        short = out["scores"]["做空"]
        self.assertNotIn("ema9_26_1h", short["layers"])
        self.assertEqual(short["layers"]["boll5_overextension"], 1.0)
        self.assertTrue(short["confirmations"]["5m_boll_overextension_latched"])
        self.assertFalse(short["confirmations"]["1H_ema9_26_score_enabled"])
        labels = [item[0] for item in short["items"]]
        self.assertFalse(any("1H EMA9/26" in label for label in labels))
        self.assertTrue(any("BOLL超伸" in label for label in labels))
        self.assertTrue(returned["boll5_overext010_latched"])
        self.assertEqual(out["side"], "做空")

    def test_pee4_tiers_match_research_boolean_rules(self):
        strict = v170._pee4_decision(
            60_000, -0.65, 0.1,
            {"opposite_closed_1h": True, "trend_reversal_15m": True},
        )
        self.assertEqual(strict["tier"], "严格")
        self.assertTrue(strict["allow"])

        strict_only_15 = v170._pee4_decision(
            60_000, -0.65, 0.1,
            {"trend_reversal_15m": True},
        )
        self.assertFalse(strict_only_15["allow"])

        strong = v170._pee4_decision(
            90_000, -0.75, 0.1,
            {"structure_break_15m": True},
        )
        self.assertEqual(strong["tier"], "强")
        self.assertTrue(strong["allow"])

        ordinary = v170._pee4_decision(
            120_000, -0.85, 0.1,
            {"boll5_persistent_failure": True},
        )
        self.assertEqual(ordinary["tier"], "普通")
        self.assertTrue(ordinary["allow"])

        self.assertIsNone(v170._pee4_decision(60_000, -0.65, 0.60, {}))
        self.assertIsNone(v170._pee4_decision(4 * 60 * 60_000 + 1, -0.85, 0.0, {}))
        self.assertIsNone(v170._pee4_decision(60_000, -0.59, 0.0, {}))

    def test_ui_status_replaces_retired_ema_with_boll5(self):
        items = dict(ui161._STATUS_ITEMS)
        self.assertNotIn("ema1h", items)
        self.assertEqual(items["boll5_overext"], "5m BOLL超伸 +1")
        text = v170._authorization_text_v170(
            type("Owner", (), {"engine": type("E", (), {"x": type("X", (), {"demo": True})()})()})(),
            type("S", (), {
                "leverage": 5, "risk_usdt": 20.0, "capital": 2000.0,
                "risk_pct": 1.0, "max_notional": 2000.0,
            })(),
        )
        self.assertIn("删除1H EMA9/26 +1评分", text)
        self.assertIn("0.10×ATR14", text)
        self.assertIn("PEE4", text)
        self.assertIn("Lock1H", text)

    def test_lock_remaining_reads_persisted_global_lock(self):
        owner = type("Owner", (), {})()
        owner.store = type("Store", (), {"data": {"pee4_lock_until": time.time() + 120}})()
        remaining = v170._lock_remaining(owner)
        self.assertGreater(remaining, 100)
        self.assertLessEqual(remaining, 120.5)

    def test_runtime_text_is_v170_and_no_old_ema_score_copy(self):
        text = v170._rewrite_runtime_text_v170(
            "自动交易启动；V1.6.8 Build1681：1H EMA9/26趋势+1"
        )
        self.assertIn("V1.7.0 Build1700", text)
        self.assertIn("删除1H EMA9/26", text)
        self.assertIn("0.10×ATR14", text)
        self.assertNotIn("EMA9/26趋势+1", text)

    def test_pee4_close_success_sets_lock_and_uses_single_market_close(self):
        class Store:
            def __init__(self):
                self.data = {}
                self.saved = 0
                self.records = []
            def save(self):
                self.saved += 1
            def record(self, kind, payload):
                self.records.append((kind, payload))
        class X:
            def __init__(self):
                self.posts = []
            def post(self, path, body):
                self.posts.append((path, dict(body)))
                return [{"ordId": "close-123"}]
        owner = type("Owner", (), {})()
        owner.store = Store()
        owner.x = X()
        owner.logs = []
        owner.emit = lambda kind, data: owner.logs.append((kind, data))
        active = {"posSide": "short", "side": "做空", "sz": 0.01, "client_id": "entry-1"}
        positions = [{"mgnMode": "isolated", "posSide": "short", "pos": "0.01"}]
        snapshot = {"current_r": -0.75, "mfe_r": 0.1, "tier": "强", "path": "strong_070_080"}

        self.assertTrue(v170._pee4_submit_close(owner, active, positions, snapshot))
        self.assertEqual(len(owner.x.posts), 1)
        path, body = owner.x.posts[0]
        self.assertEqual(path, "/api/v5/trade/order")
        self.assertEqual(body["ordType"], "market")
        self.assertEqual(body["side"], "buy")
        self.assertEqual(body["posSide"], "short")
        self.assertTrue(active.get("pee4_close_id"))
        self.assertEqual(active.get("pee4_close_order_id"), "close-123")
        self.assertGreater(owner.store.data.get("pee4_lock_until", 0), time.time() + 3500)
        self.assertEqual(owner.store.records[0][0], "PEE4提前退出")

    def test_pee4_explicit_rejection_clears_close_id_for_safe_retry(self):
        class Rejected(Exception):
            write_rejected = True
        class Store:
            def __init__(self):
                self.data = {}
            def save(self):
                pass
            def record(self, *args):
                pass
        class X:
            def post(self, path, body):
                raise Rejected("rejected")
        owner = type("Owner", (), {})()
        owner.store = Store()
        owner.x = X()
        owner.emit = lambda *args: None
        active = {"posSide": "long", "side": "做多", "sz": 0.01}
        positions = [{"mgnMode": "isolated", "posSide": "long", "pos": "0.01"}]

        self.assertFalse(v170._pee4_submit_close(owner, active, positions, {"current_r": -0.7, "mfe_r": 0.0, "tier": "强"}))
        self.assertNotIn("pee4_close_id", active)
        self.assertNotIn("pee4_lock_until", owner.store.data)

    def test_pee4_ambiguous_write_preserves_unique_id_and_never_retries_blindly(self):
        class Ambiguous(Exception):
            write_rejected = False
        class Store:
            def __init__(self):
                self.data = {}
            def save(self):
                pass
            def record(self, *args):
                pass
        class X:
            def post(self, path, body):
                raise Ambiguous("timeout after send")
        owner = type("Owner", (), {})()
        owner.store = Store()
        owner.x = X()
        owner.emit = lambda *args: None
        active = {"posSide": "long", "side": "做多", "sz": 0.01}
        positions = [{"mgnMode": "isolated", "posSide": "long", "pos": "0.01"}]

        with self.assertRaises(Ambiguous):
            v170._pee4_submit_close(owner, active, positions, {"current_r": -0.7, "mfe_r": 0.0, "tier": "强"})
        self.assertTrue(active.get("pee4_close_id"))
        self.assertNotIn("pee4_lock_until", owner.store.data)


if __name__ == "__main__":
    unittest.main()

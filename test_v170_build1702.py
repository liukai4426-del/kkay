import time
import unittest
from unittest.mock import patch

import app
import exchange
import v137_strategy_patch as v137
import v165_model as model
import v170_build1702_patch as v1702


class _Store:
    def __init__(self):
        self.data = {}
        self.saved = 0
        self.records = []
    def save(self):
        self.saved += 1
    def record(self, name, data):
        self.records.append((name, data))


class _X:
    def instrument(self):
        return {"tickSz": "0.1", "lotSz": "0.0001"}


class _Owner:
    def __init__(self):
        self.x = _X()
        self.store = _Store()
        self.logs = []
    def emit(self, kind, data):
        self.logs.append((kind, data))


class V170Build1702Tests(unittest.TestCase):
    def test_identity_and_install(self):
        self.assertEqual(v1702.VERSION, "1.7.0")
        self.assertEqual(v1702.BUILD, "1702")
        self.assertEqual(model.VERSION, "1.7.0")
        self.assertEqual(model.BUILD, "1702")
        self.assertTrue(getattr(model, "_kaytrade_v170_build1702_applied", False))
        self.assertTrue(getattr(exchange.Exchange, "_kaytrade_v170_build1702_applied", False))
        self.assertTrue(getattr(app.App, "_kaytrade_v170_build1702_applied", False))

    def test_algo_id_alias_is_normalized(self):
        row = v1702._normalize_algo_row({
            "attachAlgoClOrdId": "br123",
            "state": "live",
        })
        self.assertEqual(row["algoClOrdId"], "br123")

    def test_fresh_rest_accepts_exact_attach_id_bracket(self):
        owner = _Owner()
        p = {
            "posSide": "long",
            "exchange_side": "buy",
            "tp": "81850.1",
            "sl": "80409.2",
            "legs": [{
                "state": "filled",
                "bracket_id": "br123",
                "sz": "0.1663",
                "tp": "81850.1",
                "sl": "80409.2",
            }],
        }
        rows = [{
            "instId": "BTC-USDT-SWAP",
            "attachAlgoClOrdId": "br123",
            "state": "live",
            "tdMode": "isolated",
            "posSide": "long",
            "side": "sell",
            "sz": "0.1663",
            "tpTriggerPx": "81850.1",
            "tpOrdPx": "-1",
            "slTriggerPx": "80409.2",
            "slOrdPx": "-1",
        }]
        with patch.object(v1702.a167, "_snapshot_rows", return_value=rows):
            ok, diag = v1702._fresh_rest_protection(owner, p, 0.1663)
        self.assertTrue(ok)
        self.assertAlmostEqual(diag["protected_qty"], 0.1663)
        self.assertEqual(diag["matched_brackets"][0]["bracket_id"], "br123")

    def test_proven_rest_protection_suppresses_emergency_flatten(self):
        owner = _Owner()
        p = {
            "client_id": "mac1",
            "order_id": "ord1",
            "posSide": "long",
            "exchange_side": "buy",
            "legs": [{
                "state": "filled",
                "bracket_id": "br123",
                "sz": "0.1663",
                "tp": "81850.1",
                "sl": "80409.2",
            }],
        }
        with (
            patch.object(v1702, "_fresh_rest_protection", return_value=(True, {
                "qty": 0.1663,
                "protected_qty": 0.1663,
                "matched_brackets": [{"bracket_id": "br123"}],
            })),
            patch.object(v1702, "_PREVIOUS_EMERGENCY_FLATTEN") as previous,
        ):
            v1702._emergency_flatten_v1702(
                owner, p, 0.1663,
                "V1.3.7发现已成交仓位超过可核实的TP/SL保护数量",
            )
        previous.assert_not_called()
        self.assertTrue(p["protected"])
        self.assertTrue(any("取消本次误触发安全平仓" in str(x[1]) for x in owner.logs))

    def test_confirmed_missing_protection_keeps_fail_closed_flatten(self):
        owner = _Owner()
        p = {"posSide": "long", "legs": []}
        with (
            patch.object(v1702, "_fresh_rest_protection", return_value=(False, {
                "qty": 0.1663, "protected_qty": 0.0, "matched_brackets": []
            })),
            patch.object(v1702, "_PREVIOUS_EMERGENCY_FLATTEN") as previous,
        ):
            v1702._emergency_flatten_v1702(
                owner, p, 0.1663,
                "V1.3.7发现已成交仓位超过可核实的TP/SL保护数量",
            )
        previous.assert_called_once()

    def test_transient_read_failure_retries_before_emergency(self):
        owner = _Owner()
        p = {"posSide": "long", "legs": []}
        with (
            patch.object(v1702, "_fresh_rest_protection", side_effect=RuntimeError("read fail")),
            patch.object(v1702, "_PREVIOUS_EMERGENCY_FLATTEN") as previous,
        ):
            v1702._emergency_flatten_v1702(
                owner, p, 0.1663,
                "V1.3.7发现已成交仓位超过可核实的TP/SL保护数量",
            )
        previous.assert_not_called()
        self.assertEqual(p["v1702_protection_read_failed_count"], 1)

    def test_other_emergency_reason_is_unchanged(self):
        owner = _Owner()
        p = {}
        with patch.object(v1702, "_PREVIOUS_EMERGENCY_FLATTEN") as previous:
            v1702._emergency_flatten_v1702(owner, p, 1.0, "其他保护异常")
        previous.assert_called_once_with(owner, p, 1.0, "其他保护异常")


if __name__ == "__main__":
    unittest.main()

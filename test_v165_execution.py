import unittest

from candles import expected_bar
import v165_model as model
import v165_update_patch as runtime


class DummyStore:
    def __init__(self):
        self.data = {"halt": "", "active": None}


class DummyExchange:
    clock_sync_degraded = False


class DummyOwner:
    def __init__(self, now_s):
        self._now = float(now_s)
        self.enabled = True
        self.stopped = False
        self.store = DummyStore()
        self.x = DummyExchange()
        self.market = None

    def market_now(self):
        return self._now


def fresh_market(now_s, opp):
    return {
        "side": "做多",
        "opportunity": opp,
        "bar1m": expected_bar(now_s, 60_000),
        "bar5m": expected_bar(now_s, 300_000),
        "bar15": expected_bar(now_s, 900_000),
        "bar1h": expected_bar(now_s, 3_600_000),
        "bar4h": expected_bar(now_s, 14_400_000),
    }


def valid_score(opp):
    return {
        "gate": True,
        "eligible": True,
        "total": 6.5,
        "opportunity": opp,
        "layers": {"macd_improving": 1.0},
        "confirmations": {
            "required": {
                "boll_entry_signal": True,
                "entry_window_4x1m": True,
                "signal_window_5m": True,
                "volume_below_1_2x": True,
                "outer_rsi_30_70": True,
                "outer_macd_improving": True,
                "boll_4h_aligned": True,
            },
            "4H_trend_state": "aligned",
            "rsi5": 50.0,
        },
    }


class V165ExecutionTests(unittest.TestCase):
    def test_fresh_all_timeframes_can_pass_final_guard(self):
        now_s = 288_123.0
        opp = {
            "signal_close_ms": int(now_s * 1000) - 60_000,
            "signal_path": "lower_band",
            "five_signal_volume_ratio": 1.05,
        }
        owner = DummyOwner(now_s)
        blockers = runtime._pre_submit_guard(owner, fresh_market(now_s, opp), valid_score(opp))
        self.assertEqual(blockers, [])

    def test_crossed_higher_timeframe_boundary_is_blocked(self):
        now_s = 288_123.0
        opp = {
            "signal_close_ms": int(now_s * 1000) - 60_000,
            "signal_path": "lower_band",
            "five_signal_volume_ratio": 1.05,
        }
        owner = DummyOwner(now_s)
        market = fresh_market(now_s, opp)
        market["bar1h"] -= 3_600_000
        blockers = runtime._pre_submit_guard(owner, market, valid_score(opp))
        self.assertTrue(any("1H已出现新的收盘K线" in item for item in blockers))

    def test_next_5m_close_expires_old_signal(self):
        now_s = 288_123.0
        now_ms = int(now_s * 1000)
        opp = {
            "signal_close_ms": now_ms - model.ENTRY_WINDOW_MS,
            "signal_path": "lower_band",
            "five_signal_volume_ratio": 1.05,
        }
        owner = DummyOwner(now_s)
        blockers = runtime._pre_submit_guard(owner, fresh_market(now_s, opp), valid_score(opp))
        self.assertTrue(any("下一根5m收盘" in item for item in blockers))

    def test_volume_boundary_1_20_is_blocked(self):
        now_s = 288_123.0
        opp = {
            "signal_close_ms": int(now_s * 1000) - 60_000,
            "signal_path": "lower_band",
            "five_signal_volume_ratio": 1.20,
        }
        owner = DummyOwner(now_s)
        blockers = runtime._pre_submit_guard(owner, fresh_market(now_s, opp), valid_score(opp))
        self.assertTrue(any("1.20×" in item for item in blockers))


if __name__ == "__main__":
    unittest.main()

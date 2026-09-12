import time
import unittest
from pathlib import Path

import app
import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v138_user_update as update


class FakeStore:
    def __init__(self, data):
        self.data = data
        self.saved = 0

    def save(self):
        self.saved += 1


class V138UserUpdateTests(unittest.TestCase):
    def test_only_two_signal_levels(self):
        self.assertEqual(v138.V138_THRESHOLD, 6.0)
        self.assertEqual(v137._tier(5.5)[0], 0)
        self.assertEqual(v137._tier(6.0), (1, 1.0, '开仓信号'))
        self.assertEqual(v137._tier(7.5), (1, 1.0, '开仓信号'))
        self.assertEqual(v137._tier(8.0), (2, 2.0, '强信号'))
        self.assertEqual(v137._tier(10.0), (2, 2.0, '强信号'))
        self.assertEqual(v137._tier_limit(1), 1)
        self.assertEqual(v137._tier_limit(2), 1)
        self.assertEqual(v137._tier_limit(3), 0)

    def test_position_ratio_and_total_cap(self):
        self.assertEqual(update._entry_notional_cap(100, 1, False), 100)
        self.assertEqual(update._entry_notional_cap(100, 2, False), 200)
        self.assertEqual(update._entry_notional_cap(100, 2, True), 300)

    def test_settings_require_room_for_two_signal_cycle(self):
        ok = engine.Settings(
            capital=100, max_notional=30, leverage=1, risk_usdt=1, risk_pct=1,
            daily_loss=3, consecutive_losses=3, cooldown_minutes=30,
            stop_atr=1.0, reward_r=2.0, score_threshold=6.0,
            fee_bps=2.0, taker_fee_bps=5.0, slippage_bps=5.0,
        )
        ok.validate()
        bad = engine.Settings(
            capital=100, max_notional=40, leverage=1, risk_usdt=1, risk_pct=1,
            daily_loss=3, consecutive_losses=3, cooldown_minutes=30,
            stop_atr=1.0, reward_r=2.0, score_threshold=6.0,
            fee_bps=2.0, taker_fee_bps=5.0, slippage_bps=5.0,
        )
        with self.assertRaises(engine.Halt):
            bad.validate()

    def test_six_hour_pause_replaces_daily_reset(self):
        now = time.time()
        fake = engine.Engine.__new__(engine.Engine)
        fake.store = FakeStore({
            'streak': 3,
            'streak_day': '',
            'streak_notice_day': '',
            'last_close': now,
            'loss_pause_until': 0,
        })
        fake.emit = lambda kind, data: None
        fake.settings = engine.Settings(score_threshold=6.0, max_notional=30, capital=100, leverage=1)
        fake._reset_streak_day()
        until = fake.store.data['loss_pause_until']
        self.assertGreater(until, now + 21590)
        self.assertEqual(fake.store.data['streak'], 3)

        fake.store.data['loss_pause_until'] = time.time() - 1
        fake._reset_streak_day()
        self.assertEqual(fake.store.data['streak'], 0)
        self.assertEqual(fake.store.data['loss_pause_until'], 0.0)

    def test_runtime_log_lines_include_timestamp(self):
        # App.drain is wrapped by runtime patches, so verify the canonical UI
        # source rather than the final wrapper returned by inspect.getsource().
        source = Path(app.__file__).read_text()
        self.assertIn("time.strftime('%Y-%m-%d %H:%M:%S')", source)
        self.assertIn('self.log_lines.append((kind,line))', source)
        self.assertIn("f.write(line+'\\n')", source)


if __name__ == '__main__':
    unittest.main()

import inspect
import unittest
from types import SimpleNamespace

import app
import v152_ui_busy_fix as fix


class _Store:
    def __init__(self, active=None):
        self.data = {'active': active}


class V152UiBusyFixTests(unittest.TestCase):
    @staticmethod
    def _engine(settings=None, active=None):
        return SimpleNamespace(settings=settings, store=_Store(active))

    def test_runtime_hook_owns_worker(self):
        self.assertEqual(app.App.worker.__module__, 'v152_ui_busy_fix')
        self.assertTrue(getattr(app.App, '_kaytrade_v152_ui_busy_fix_applied', False))

    def test_connect_and_arm_release_before_strategy_cycle(self):
        e = self._engine(settings=object())
        self.assertFalse(fix._should_cycle('connect', e))
        self.assertFalse(fix._should_cycle('arm', e))

    def test_idle_connected_tick_waits_for_user_authorization(self):
        self.assertFalse(fix._should_cycle('tick', self._engine(settings=None)))

    def test_armed_idle_tick_runs_strategy_cycle(self):
        self.assertTrue(fix._should_cycle('tick', self._engine(settings=object())))

    def test_persisted_active_state_still_reconciles_before_authorization(self):
        self.assertTrue(fix._should_cycle('tick', self._engine(settings=None, active={'client_id': 'x'})))

    def test_connect_path_has_no_candle_warmup_or_ticker_before_done(self):
        source = inspect.getsource(fix.worker_v152)
        connect_block = source.split("if kind == 'connect':", 1)[1].split("elif kind == 'arm':", 1)[0]
        self.assertNotIn('refresh_market', connect_block)
        self.assertNotIn('.ticker(', connect_block)
        self.assertIn("self.emit('done', None)", source)


if __name__ == '__main__':
    unittest.main()

from __future__ import annotations

import inspect
import unittest

import v154_build1543_patch as v1543
import v154_build1544_patch as v1544


class Build1544Tests(unittest.TestCase):
    def test_build_identity_and_path_c_hard_off(self):
        self.assertEqual(v1544.BUILD, "154.4")
        self.assertFalse(v1544.PATH_C_ENABLED)
        self.assertIs(v1543._maybe_path_c, v1544._path_c_disabled)
        self.assertIsNone(v1543._maybe_path_c(object()))

    def test_settings_no_longer_reserve_second_signal_capital(self):
        src = inspect.getsource(v1544._settings_without_path_c)
        self.assertIn('values["max_notional"] = first', src)
        self.assertIn('first > float(settings.capital) * float(settings.leverage)', src)
        self.assertNotIn('first + second >', src)

    def test_cycle_marks_path_c_disabled_and_preserves_existing_legs(self):
        src = inspect.getsource(v1544.apply)
        self.assertIn('active["path_c_enabled"] = False', src)
        self.assertIn('has_path_c_leg', src)
        self.assertIn('not has_path_c_leg', src)
        self.assertNotIn('_submit_path_c(', src)

    def test_ui_explicitly_states_path_c_is_closed(self):
        src = inspect.getsource(v1544._mark_path_c_disabled)
        self.assertIn('路径C已关闭', src)
        self.assertIn('Build 1544', src)


if __name__ == "__main__":
    unittest.main()

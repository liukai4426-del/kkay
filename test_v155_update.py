import unittest
from dataclasses import asdict
from types import SimpleNamespace

import app
import engine
import v154_build1544_patch as b1544
import v155_update_patch as v155


class Var:
    def __init__(self, value):
        self.value = str(value)
    def get(self):
        return self.value
    def set(self, value):
        self.value = str(value)


class V155UpdateTests(unittest.TestCase):
    def owner(self):
        defaults = asdict(app.Settings())
        fields = {key: Var(value) for key, value in defaults.items()}
        fields['first_signal_notional'] = Var(200)
        fields['leverage'] = Var(5)
        fields['capital'] = Var(99999)
        fields['score_threshold'] = Var(6)
        fields['stop_atr'] = Var(1)
        fields['cooldown_minutes'] = Var(30)
        return SimpleNamespace(fields=fields)

    def test_path_c_stays_off_and_hidden_second_is_three_x(self):
        self.assertFalse(v155.PATH_C_ENABLED)
        self.assertFalse(b1544.PATH_C_ENABLED)
        self.assertEqual(v155.PATH_C_COMPAT_MULTIPLIER, 3.0)
        owner = self.owner()
        settings = v155._settings_from_ui_v155(owner)
        self.assertEqual(settings.first_signal_notional, 200)
        self.assertEqual(settings.second_signal_notional, 600)

    def test_margin_times_leverage_becomes_readonly_strategy_notional(self):
        owner = self.owner()
        settings = v155._settings_from_ui_v155(owner)
        self.assertEqual(settings.capital, 1000)
        self.assertEqual(settings.max_initial_notional, 1000)
        self.assertEqual(settings.max_notional, 1000)
        self.assertEqual(settings.leverage, 5)
        self.assertEqual(settings.score_threshold, 6.0)
        self.assertEqual(settings.stop_atr, 1.0)

    def test_invalid_margin_is_rejected(self):
        owner = self.owner()
        owner.fields['first_signal_notional'].set(0)
        with self.assertRaises(engine.Halt):
            v155._settings_from_ui_v155(owner)

    def test_editable_and_readonly_field_sets_are_explicit(self):
        self.assertIn('first_signal_notional', v155.EDITABLE_FIELDS)
        self.assertIn('leverage', v155.EDITABLE_FIELDS)
        self.assertIn('capital', v155.READONLY_FIELDS)
        self.assertIn('score_threshold', v155.READONLY_FIELDS)
        self.assertIn('stop_atr', v155.READONLY_FIELDS)
        self.assertNotIn('second_signal_notional', v155.EDITABLE_FIELDS)


if __name__ == '__main__':
    unittest.main()

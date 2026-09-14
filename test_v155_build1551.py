import unittest
from pathlib import Path
from types import SimpleNamespace

import app
import engine
import v154_build1544_patch as b1544
import v155_build1551_patch as b1551


class Build1551Tests(unittest.TestCase):
    def test_identity_and_packaging_wiring(self):
        self.assertEqual(b1551.VERSION, '1.5.5')
        self.assertEqual(b1551.BUILD, '1551')
        self.assertFalse(b1551.COOLDOWN_ENABLED)
        self.assertFalse(b1544.PATH_C_ENABLED)
        spec = Path('OKXLocal.spec').read_text()
        self.assertNotIn("str(root / 'v155_update_patch.py')", spec)
        self.assertNotIn("v155_log_binding_fix", spec)
        if "CFBundleVersion': '1551'" in spec:
            # Native Build1551 packaging: Build1551 itself is the single hook.
            self.assertIn("runtime_hooks=[str(root / 'v155_build1551_patch.py')]", spec)
        else:
            # Later releases inherit Build1551 as a normal module and promote only
            # their final overlay to the single runtime hook. This preserves the
            # recursion fix without forcing a historical bundle version forever.
            self.assertIn("'v155_build1551_patch'", spec)
            self.assertEqual(spec.count('runtime_hooks=['), 2)  # one legacy comment + one live Analysis entry
            self.assertTrue(
                "runtime_hooks=[str(root / 'v156_ui_patch.py')]" in spec
                or "runtime_hooks=[str(root / 'v156_build1561_patch.py')]" in spec
                or "runtime_hooks=[str(root / 'v160_update_patch.py')]" in spec
            )

    def test_cooldown_gate_sees_zero_last_close_then_restores_history_value(self):
        state = {'last_close': 12345}
        owner = SimpleNamespace(store=SimpleNamespace(data=state))
        seen = []

        def previous(obj):
            seen.append(obj.store.data['last_close'])
            return 'ok'

        self.assertEqual(b1551._call_without_cooldown(owner, previous), 'ok')
        self.assertEqual(seen, [0])
        self.assertEqual(state['last_close'], 12345)

    def test_new_close_written_by_inherited_cycle_is_preserved(self):
        state = {'last_close': 12345}
        owner = SimpleNamespace(store=SimpleNamespace(data=state))

        def previous(obj):
            self.assertEqual(obj.store.data['last_close'], 0)
            obj.store.data['last_close'] = 99999

        b1551._call_without_cooldown(owner, previous)
        self.assertEqual(state['last_close'], 99999)

    def test_runtime_flags_are_live(self):
        self.assertTrue(engine.Engine._kaytrade_v1551_applied)
        self.assertTrue(app.App._kaytrade_v1551_applied)


if __name__ == '__main__':
    unittest.main()

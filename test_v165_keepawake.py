import queue
import unittest
from unittest.mock import MagicMock, patch

import app
import v165_keepawake_patch as keep


class DummyOwner:
    def __init__(self):
        self._v165_keepawake_proc = None
        self.logs = []
    def emit(self, kind, data):
        self.logs.append((kind, data))


class KeepAwakeTests(unittest.TestCase):
    def test_darwin_starts_caffeinate_without_forcing_display_awake(self):
        owner = DummyOwner()
        proc = MagicMock()
        proc.poll.return_value = None
        with patch.object(keep.sys, 'platform', 'darwin'), patch.object(keep.subprocess, 'Popen', return_value=proc) as popen:
            self.assertTrue(keep._start_keepawake(owner))
        args = popen.call_args.args[0]
        self.assertEqual(args[0], '/usr/bin/caffeinate')
        self.assertIn('-i', args)
        self.assertIn('-m', args)
        self.assertIn('-w', args)
        self.assertNotIn('-d', args)
        self.assertNotIn('-u', args)
        self.assertEqual(owner._v165_keepawake_proc, proc)
        self.assertTrue(any('屏幕仍可自动熄灭' in text for _kind, text in owner.logs))

    def test_non_darwin_never_spawns_caffeinate(self):
        owner = DummyOwner()
        with patch.object(keep.sys, 'platform', 'linux'), patch.object(keep.subprocess, 'Popen') as popen:
            self.assertFalse(keep._start_keepawake(owner))
            popen.assert_not_called()

    def test_duplicate_start_reuses_live_assertion(self):
        owner = DummyOwner()
        proc = MagicMock()
        proc.poll.return_value = None
        owner._v165_keepawake_proc = proc
        with patch.object(keep.sys, 'platform', 'darwin'), patch.object(keep.subprocess, 'Popen') as popen:
            self.assertTrue(keep._start_keepawake(owner))
            popen.assert_not_called()

    def test_stop_terminates_assertion(self):
        owner = DummyOwner()
        proc = MagicMock()
        proc.poll.return_value = None
        owner._v165_keepawake_proc = proc
        self.assertTrue(keep._stop_keepawake(owner, '已停止自动交易'))
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()
        self.assertIsNone(owner._v165_keepawake_proc)

    def test_real_arm_submit_starts_keepawake(self):
        # Build an App instance without Tk. The patched submit still delegates to
        # the original queueing implementation, so this proves keep-awake starts
        # only when an actual arm task is accepted.
        owner = object.__new__(app.App)
        owner.busy = False
        owner.tasks = queue.Queue()
        owner.engine = object()
        owner.network_paused = False
        owner._v165_keepawake_proc = None
        owner.logs = []
        owner.emit = lambda kind, data: owner.logs.append((kind, data))
        proc = MagicMock()
        proc.poll.return_value = None
        with patch.object(keep.sys, 'platform', 'darwin'), patch.object(keep.subprocess, 'Popen', return_value=proc):
            app.App.submit(owner, 'arm', {'x': 1})
        self.assertTrue(owner.busy)
        self.assertEqual(owner.tasks.get_nowait(), ('arm', {'x': 1}))
        self.assertIs(owner._v165_keepawake_proc, proc)


if __name__ == '__main__':
    unittest.main()

"""V1.3.5 hotfix: an empty manual-flatten request is a no-op, never a fault lock."""

from v135_patch import apply as apply_v135_patch
apply_v135_patch()

from v135_execution_patch import apply as apply_v135_execution_patch
apply_v135_execution_patch()

import engine


def apply():
    if getattr(engine.Engine, '_kaytrade_v135_flatten_noop_hotfix_applied', False):
        return

    original_flatten = engine.Engine.flatten

    def flatten(self):
        p = self.store.data.get('active') if self.store else None
        if not p:
            # App.flatten already stops new entries before submitting this action.
            # With nothing owned by this strategy there is nothing to close; this
            # must not raise Halt because the UI's global exception handler would
            # persist that benign condition as a fault lock.
            self.enabled = False
            self.stopped = True
            if hasattr(self, 'startup_buffer_until'):
                self.startup_buffer_until = 0.0
            self.emit('log', '当前没有本程序仓位/活动订单；无需平仓，未写入故障锁')
            return False
        return original_flatten(self)

    engine.Engine.flatten = flatten
    engine.Engine._kaytrade_v135_flatten_noop_hotfix_applied = True


apply()

"""V1.5.5 final renderer binding fix.

V1.5.4's record-card repair stores an instance-level render_logs method during
App construction.  That instance attribute shadows the V1.5.5 class override.
Rebind the finished App instance to the V1.5.5 scrollable/timestamp renderer
only after the complete inherited initializer stack returns.
"""
from v155_update_patch import apply as apply_v155
apply_v155()

import app
import engine
import v155_update_patch as v155

_PREVIOUS_INIT = app.App.__init__


def apply():
    if getattr(app.App, "_kaytrade_v155_log_binding_fix_applied", False):
        return

    def app_init(self, *args, **kwargs):
        _PREVIOUS_INIT(self, *args, **kwargs)
        self.render_logs = v155._render_logs_v155.__get__(self, app.App)
        self.render_logs()
        self._v155_final_log_renderer_bound = True

    app.App.__init__ = app_init
    app.App.render_logs = v155._render_logs_v155
    app.App._kaytrade_v155_log_binding_fix_applied = True
    engine.Engine._kaytrade_v155_log_binding_fix_applied = True


apply()

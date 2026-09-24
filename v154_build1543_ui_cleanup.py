"""Final UI cleanup for KAYTRADE V1.5.4 Build 1543.

The Build 1543 strategy layer already disables the consecutive-loss pause and
removes its field from settings.  This final presentation hook destroys the
legacy Tk widgets instead of merely unmapping them, so the obsolete control and
help text no longer exist in the Risk-page widget tree.
"""
from v154_build1543_patch import apply as apply_build1543
apply_build1543()

import app
import engine


def _walk(root):
    try:
        children = root.winfo_children()
    except Exception:
        return
    for child in children:
        yield child
        yield from _walk(child)


def _destroy_obsolete_loss_widgets(owner):
    exact = {
        "中国时间连续亏损停开次数",
        "3（固定）",
    }
    victims = []
    for widget in list(_walk(owner.root)):
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            continue
        if text in exact or ("连续亏损停开次数" in text and "其余风险" in text):
            victims.append(widget)
    destroyed = []
    for widget in victims:
        try:
            destroyed.append(str(widget))
            widget.destroy()
        except Exception:
            pass
    owner.fields.pop("consecutive_losses", None)
    owner._v1543_loss_widgets_destroyed = len(destroyed)
    owner._v1543_loss_control_removed = True
    return len(destroyed)


def apply():
    if getattr(app.App, "_kaytrade_v1543_ui_cleanup_applied", False):
        return
    previous_init = app.App.__init__

    def app_init(self, *args, **kwargs):
        previous_init(self, *args, **kwargs)
        _destroy_obsolete_loss_widgets(self)

    app.App.__init__ = app_init
    app.App._kaytrade_v1543_ui_cleanup_applied = True
    engine.Engine._kaytrade_v1543_ui_cleanup_applied = True


apply()

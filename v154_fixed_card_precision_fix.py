"""Precision UI fix for the V1.5.4 fixed execution-cost card.

The round-two pass initially searched descendants recursively, so an outer Card
containing the fixed-cost Card could also be tagged/ recolored.  This final
layer keeps the gutter fix only on the Card whose *direct body child* is the
"固定执行成本" label and restores any ancestor accidentally touched by the
previous pass to its own parent background.
"""
from __future__ import annotations

from v154_record_card_boll_fix import apply as apply_round2
apply_round2()

import app
import visual
import v154_record_card_boll_fix as round2


def _direct_fixed_cost_card(widget):
    if not isinstance(widget, visual.Card):
        return False
    try:
        children = widget.body.winfo_children()
    except Exception:
        return False
    for child in children:
        try:
            if str(child.cget("text") or "") == "固定执行成本":
                return True
        except Exception:
            continue
    return False


def _precision_clean(owner):
    targets = []
    for widget in round2._walk(owner.root):
        if not isinstance(widget, visual.Card):
            continue
        direct = _direct_fixed_cost_card(widget)
        if direct:
            try:
                widget.configure(bg=app.PANEL, highlightthickness=0, borderwidth=0)
                widget._v154_fixed_cost_gutter_removed = True
                targets.append(widget)
            except Exception:
                pass
            continue

        # Undo the overly broad ancestor match from the preceding round-two
        # initializer, without changing the rounded surface fill itself.
        if getattr(widget, "_v154_fixed_cost_gutter_removed", False):
            try:
                parent_bg = widget.master.cget("bg")
            except Exception:
                parent_bg = visual.BG
            try:
                widget.configure(bg=parent_bg, highlightthickness=0, borderwidth=0)
            except Exception:
                pass
            try:
                delattr(widget, "_v154_fixed_cost_gutter_removed")
            except Exception:
                pass

    owner._v154_fixed_cost_targets = tuple(targets)
    owner._v154_fixed_cost_card_clean = len(targets) == 1
    return len(targets)


def apply():
    if getattr(app.App, "_kaytrade_v154_fixed_card_precision_applied", False):
        return
    previous_app_init = app.App.__init__

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        _precision_clean(self)
        self._v154_fixed_card_precision_ready = True

    app.App.__init__ = app_init
    app.App._kaytrade_v154_fixed_card_precision_applied = True


apply()

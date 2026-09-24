"""KAYTRADE V1.5.6 Build1561 UI follow-up.

Build1561 changes presentation only:
- Run Log is newest-first: the latest event stays at the top and history is below.
- Scrolling down reads older records; while the user is reading history, refreshes
  preserve the current scroll position instead of forcing a jump to the top.
- Remove the obsolete Risk Settings consecutive-loss fixed row ("3（固定）") and
  its stale explanatory note. The backend compatibility value remains fixed at 3
  inside inherited settings construction; live consecutive-loss pause remains OFF.

Trading rules, score model, LIMIT entry, 51054 execution hotfix, Path C OFF and
post-close cooldown OFF are preserved unchanged.
"""
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path

from v156_ui_patch import apply as apply_previous
apply_previous()

import app
import engine
import v156_ui_patch as v156

VERSION = "1.5.6"
BUILD = "1561"
PATH_C_ENABLED = False
COOLDOWN_ENABLED = False
CONSECUTIVE_LOSS_PAUSE_ENABLED = False

# The inherited V1.5.6 init writes its own startup probe. Suppress that success
# write so CI sees only the final Build1561 state; inherited error probes remain.
v156._write_probe = lambda: None

_PREVIOUS_INIT = app.App.__init__
_PREVIOUS_CYCLE = engine.Engine.cycle

FIXED_LOSS_MARKERS = (
    "中国时间连续亏损停开次数",
    "3（固定）",
    "除连续亏损停开次数外",
)


def _ordered_rows(lines):
    """Return at most 500 records with the newest event first."""
    return list(reversed(list(lines or [])[-500:]))


def _purge_fixed_loss_control(owner):
    """Remove the obsolete fixed 3 row and its now-inaccurate explanatory note."""
    v156._destroy_grid_rows(owner, FIXED_LOSS_MARKERS)
    owner.fields.pop("consecutive_losses", None)
    owner._v1561_fixed_loss_control_removed = True


def _update_log_hint(owner):
    surface = getattr(owner, "_v156_log_surface", None)
    if surface is None:
        return
    for widget in v156._walk(surface):
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            continue
        if text == "所有策略循环与安全拦截":
            try:
                widget.configure(text="最新记录在上 · 向下滚动查看历史")
            except Exception:
                pass
            break


def _render_logs_v1561(self):
    text = getattr(self, "_v156_log_text", None)
    if text is None:
        return

    force_top = bool(getattr(self, "_v1561_force_top", False))
    try:
        view = text.yview()
        at_top = force_top or not view or float(view[0]) <= 0.005
        top = float(view[0]) if view else 0.0
    except Exception:
        at_top, top = True, 0.0

    try:
        text.configure(state="normal")
        text.delete("1.0", "end")
        rows = _ordered_rows(getattr(self, "log_lines", []) or [])
        if not rows:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            text.insert("end", "● 等待  ", "wait")
            text.insert("end", stamp + "   ", "time")
            text.insert("end", "等待运行记录\n", "title")
            text.insert("end", "策略尚未产生新的运行事件。\n", "detail")
        else:
            for index, row in enumerate(rows):
                try:
                    kind, line = row
                except Exception:
                    kind, line = "log", str(row)
                stamp, message = v156._extract_timestamp(line)
                status, tag = v156._status_for(kind, message)
                title, detail = v156._split_title_detail(message)
                text.insert("end", "● " + status + "  ", tag)
                text.insert("end", stamp + "   ", "time")
                text.insert("end", title + "\n", "title")
                if detail:
                    text.insert("end", detail + "\n", "detail")
                if index != len(rows) - 1:
                    text.insert("end", "\n", "detail")
    finally:
        try:
            text.configure(state="disabled")
        except Exception:
            pass

    if at_top:
        try:
            text.yview_moveto(0.0)
        except Exception:
            pass
    else:
        try:
            text.yview_moveto(top)
        except Exception:
            pass
    self._v1561_force_top = False
    self._v156_log_at_top = at_top


def _probe_target():
    return os.environ.get("KAYTRADE_STARTUP_PROBE", "").strip()


def _write_probe():
    target = _probe_target()
    if not target:
        return
    try:
        Path(target).write_text(
            f"PASS KAYTRADE {VERSION} Build {BUILD} compact_log=on newest_log=top "
            "fixed_loss_control=off legacy_controls=off cooldown=off path_c=off\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _write_probe_error():
    target = _probe_target()
    if not target:
        return
    try:
        Path(target + ".error").write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


def apply():
    if getattr(engine.Engine, "_kaytrade_v1561_applied", False):
        return

    def app_init(self, *args, **kwargs):
        try:
            _PREVIOUS_INIT(self, *args, **kwargs)
            _purge_fixed_loss_control(self)
            _update_log_hint(self)
            self.render_logs = _render_logs_v1561.__get__(self, app.App)
            self._v1561_force_top = True
            self.render_logs()
            try:
                self.root.title("KAYTRADE 1.5.6 · BTC 策略控制台 · Build 1561")
            except Exception:
                pass
            self._v1561_ready = True
            _write_probe()
        except Exception:
            _write_probe_error()
            raise

    def cycle(self):
        result = _PREVIOUS_CYCLE(self)
        if self.store:
            active = self.store.data.get("active")
            if isinstance(active, dict):
                changed = active.get("version") != VERSION or active.get("build") != BUILD
                active["version"] = VERSION
                active["build"] = BUILD
                active["path_c_enabled"] = False
                active["cooldown_enabled"] = False
                active["consecutive_loss_pause_enabled"] = False
                if changed:
                    self.store.save()
        return result

    app.App.__init__ = app_init
    app.App.render_logs = _render_logs_v1561
    engine.Engine.cycle = cycle
    engine.Engine._kaytrade_v1561_applied = True
    app.App._kaytrade_v1561_applied = True


apply()

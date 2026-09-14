"""KAYTRADE V1.6.1 compact score-panel UI refresh.

Presentation-only overlay on top of the verified V1.6.1 runtime:
- keep the current score values and trading semantics unchanged;
- shorten the score-detail indicator names so they match the current strategy;
- add a compact two-row opening-status bar above the score table;
- green means the condition is currently satisfied, muted gray means false/N/A.
"""
from __future__ import annotations

import copy
import math
import re
import tkinter as tk

from v161_update_patch import apply as apply_previous
apply_previous()

import app
import v161_model as model

VERSION = "1.6.1"
UI_REVISION = "score-status-compact"

GREEN = app.GREEN
MUTED = app.MUTED
PANEL_ALT = app.PANEL_ALT
TEXT = "#eef5f7"

_PREVIOUS_INIT = app.App.__init__
_PREVIOUS_EMIT = app.App.emit
_PREVIOUS_DRAIN = app.App.drain

_SCORE_LABELS = (
    ("15m正确方向回调区共振", "15m 顺势回调区共振"),
    ("计划入场价接近15m回调区", "入场位置接近15m回调区"),
    ("确认水平支撑/阻力与15m回调区重合", "15m回调区 × 水平结构共振"),
    ("5m BOLL中轨+RSI / 外轨触发", "5m BOLL 中轨+RSI / 外轨触发"),
    ("5m MACD柱连续两根改善", "5m MACD 动能改善"),
    ("4H同向 / 中性 / 逆向", "4H 趋势状态"),
    ("15m EMA50较3根前向交易方向移动", "15m EMA50 趋势推进"),
    ("5m信号K线成交量≥20均量1.2×", "5m 成交量放大 ≥1.2×"),
    ("5m信号KDJ对应交叉", "5m KDJ 动能交叉"),
)

_STATUS_ITEMS = (
    ("score", "评分≥6"),
    ("direction", "主方向"),
    ("boll", "BOLL信号"),
    ("middle_macd", "中轨MACD"),
    ("outer_4h", "外轨4H"),
    ("macd_clean", "MACD无逆恶化"),
    ("entry_drift", "入场未偏离"),
    ("front_space", "前方≥1.5R"),
    ("stop_structure", "止损结构"),
    ("cost", "成本≤0.30R"),
    ("adverse_4h", "4H结构安全"),
    ("daily_risk", "日内风险"),
    ("funds", "可用资金"),
    ("clock", "时钟同步"),
)

_ROLE_TAGS = ("【开仓必要】", "【辅助评分】", "（辅助）", "（必须）")


def _clean_score_label(value):
    text = str(value or "")
    for tag in _ROLE_TAGS:
        text = text.replace(tag, "")
    text = text.strip()
    for old, new in _SCORE_LABELS:
        if old in text:
            return new
    return text


def _rename_score_items(data):
    if not isinstance(data, dict):
        return data
    for row in (data.get("scores") or {}).values():
        if not isinstance(row, dict):
            continue
        renamed = []
        for item in row.get("items") or []:
            if not isinstance(item, (tuple, list)) or len(item) < 3:
                renamed.append(item)
                continue
            values = list(item)
            values[0] = _clean_score_label(values[0])
            renamed.append(tuple(values) if isinstance(item, tuple) else values)
        row["items"] = renamed
    return data


def _contains(blockers, needle):
    needle = str(needle)
    return any(needle in str(value) for value in blockers or [])


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _candidate(data):
    scores = data.get("scores") or {}
    direction = str(data.get("direction") or "")
    if direction in ("做多", "做空") and isinstance(scores.get(direction), dict):
        return direction, scores[direction]
    rows = [(side, row) for side, row in scores.items() if isinstance(row, dict)]
    if not rows:
        return "", {}
    return max(rows, key=lambda pair: float(pair[1].get("total") or 0.0))


def _runtime_daily(owner):
    engine_obj = getattr(owner, "engine", None)
    store = getattr(engine_obj, "store", None) if engine_obj is not None else None
    settings = getattr(engine_obj, "settings", None) if engine_obj is not None else None
    if store is None or settings is None:
        return None
    state = getattr(store, "data", {}) or {}
    if state.get("halt"):
        return False
    try:
        text = str(owner.equity.get())
        nums = re.findall(r"[-+]?\d+(?:\.\d+)?", text.replace(",", ""))
        equity = float(nums[-1]) if nums else None
        peak = float(state.get("peak") or 0.0)
        limit = float(settings.daily_loss)
    except Exception:
        return None
    if equity is None or equity <= 0 or peak <= 0 or limit <= 0:
        return None
    return (limit - max(0.0, peak - equity)) > 0.0


def _runtime_clock(owner):
    engine_obj = getattr(owner, "engine", None)
    exchange_obj = getattr(engine_obj, "x", None) if engine_obj is not None else None
    if exchange_obj is None:
        return None
    if getattr(exchange_obj, "clock_anchor", None) is None:
        return None
    return not bool(getattr(exchange_obj, "clock_sync_degraded", False))


def _status_snapshot(owner, data):
    if not isinstance(data, dict):
        return {key: None for key, _ in _STATUS_ITEMS}

    side, row = _candidate(data)
    confirmations = row.get("confirmations") or {}
    required = confirmations.get("required") or {}
    blockers = list(confirmations.get("blockers") or [])
    structure = row.get("structure") or {}
    opportunity = row.get("opportunity") or data.get("opportunity") or {}
    path = str((opportunity or {}).get("signal_path") or (opportunity or {}).get("path") or "")
    has_opportunity = isinstance(opportunity, dict) and bool(opportunity)

    total = _finite(row.get("total"))
    threshold = _finite(data.get("threshold"))
    if threshold is None:
        threshold = float(getattr(model, "THRESHOLD", 6.0))

    score_ok = None if total is None else total >= threshold
    direction_ok = side in ("做多", "做空") and str(data.get("direction") or "") == side
    boll_ok = bool(required.get("boll_entry_signal")) if has_opportunity else False

    middle_macd = None
    if path == model.MIDDLE_PATH:
        middle_macd = bool(required.get("middle_macd_improving"))

    outer_4h = None
    if path in model.OUTER_PATHS:
        outer_4h = bool(required.get("outer_4h_aligned"))

    macd_adverse = bool(confirmations.get("5m_macd_adverse", False)) or _contains(blockers, "MACD柱连续两根向交易反方向恶化")
    macd_clean = (not macd_adverse) if has_opportunity else None
    entry_drift = (not _contains(blockers, "偏离5m BOLL触发位置过远")) if has_opportunity else None

    front_state = str(structure.get("front_space_status") or "")
    front_space = True if front_state == "充足" else False if front_state == "不足" else None

    stop_value = structure.get("stop_buffer_ok")
    stop_structure = bool(stop_value) if stop_value is not None and has_opportunity else None

    cost_r = _finite(structure.get("cost_r"))
    cost_ok = None if cost_r is None or not has_opportunity else cost_r <= float(getattr(model, "COST_MAX_R", 0.30))

    if has_opportunity and "adverse_4h_hard_block" in structure:
        adverse_4h = not bool(structure.get("adverse_4h_hard_block"))
    else:
        adverse_4h = None

    funds = getattr(owner, "_v161_funds_ok", None)
    return {
        "score": score_ok,
        "direction": direction_ok,
        "boll": boll_ok,
        "middle_macd": middle_macd,
        "outer_4h": outer_4h,
        "macd_clean": macd_clean,
        "entry_drift": entry_drift,
        "front_space": front_space,
        "stop_structure": stop_structure,
        "cost": cost_ok,
        "adverse_4h": adverse_4h,
        "daily_risk": _runtime_daily(owner),
        "funds": funds,
        "clock": _runtime_clock(owner),
    }


def _build_status_bar(owner):
    table = getattr(owner, "score_table", None)
    if table is None:
        return
    parent = table.master
    frame = tk.Frame(parent, bg=PANEL_ALT, bd=0, highlightthickness=0, padx=7, pady=4)
    try:
        frame.pack(side="top", fill="x", pady=(0, 5), before=table)
    except Exception:
        frame.pack(side="top", fill="x", pady=(0, 5))

    title = tk.Label(
        frame, text="开仓状态", bg=PANEL_ALT, fg=TEXT,
        font=("Helvetica", 9, "bold"), anchor="w", bd=0,
    )
    title.pack(anchor="w", pady=(0, 2))

    grid = tk.Frame(frame, bg=PANEL_ALT, bd=0, highlightthickness=0)
    grid.pack(fill="x")
    labels = {}
    columns = 7
    for index, (key, text) in enumerate(_STATUS_ITEMS):
        row, column = divmod(index, columns)
        label = tk.Label(
            grid, text="● " + text, bg=PANEL_ALT, fg=MUTED,
            font=("Helvetica", 8, "bold"), anchor="w", bd=0,
            padx=3, pady=1,
        )
        label.grid(row=row, column=column, sticky="w", padx=(0, 5), pady=0)
        grid.columnconfigure(column, weight=1, uniform="v161status")
        labels[key] = label

    try:
        table.configure(height=7)
    except Exception:
        pass
    owner._v161_status_surface = frame
    owner._v161_status_labels = labels
    owner._v161_status_snapshot = {key: None for key, _ in _STATUS_ITEMS}


def _render_status(owner):
    labels = getattr(owner, "_v161_status_labels", None)
    if not isinstance(labels, dict):
        return
    snapshot = getattr(owner, "_v161_status_snapshot", {}) or {}
    for key, widget in labels.items():
        try:
            widget.configure(fg=GREEN if snapshot.get(key) is True else MUTED)
        except Exception:
            pass


def app_init(self, *args, **kwargs):
    _PREVIOUS_INIT(self, *args, **kwargs)
    _build_status_bar(self)
    self._v161_ui_ready = True


def emit(self, kind, data):
    payload = data
    if kind == "market" and isinstance(data, dict):
        payload = _rename_score_items(copy.deepcopy(data))
        self._v161_status_snapshot = _status_snapshot(self, payload)
        # A fresh market cycle has not yet passed live sizing/funds checks.
        self._v161_funds_ok = None
    elif kind == "plan" and isinstance(data, dict):
        # Reaching plan emission means live daily-risk and available-funds sizing passed.
        self._v161_funds_ok = True
        current = dict(getattr(self, "_v161_status_snapshot", {}) or {})
        current["daily_risk"] = True
        current["funds"] = True
        current["clock"] = _runtime_clock(self)
        self._v161_status_snapshot = current
    elif kind in ("alarm", "log"):
        text = str(data or "")
        current = dict(getattr(self, "_v161_status_snapshot", {}) or {})
        if "日内权益回撤上限" in text or "日内风险" in text:
            current["daily_risk"] = False
        if any(token in text for token in ("风险预算不足", "最小下单量", "可用余额", "保证金不足", "余额不足")):
            self._v161_funds_ok = False
            current["funds"] = False
        if "时钟同步" in text:
            current["clock"] = _runtime_clock(self)
        self._v161_status_snapshot = current
    return _PREVIOUS_EMIT(self, kind, payload)


def drain(self):
    result = _PREVIOUS_DRAIN(self)
    # Previous drain has already rendered score rows; repaint only the compact
    # status labels on Tk's UI thread.
    try:
        current = dict(getattr(self, "_v161_status_snapshot", {}) or {})
        if current:
            current["daily_risk"] = _runtime_daily(self) if _runtime_daily(self) is not None else current.get("daily_risk")
            current["clock"] = _runtime_clock(self) if _runtime_clock(self) is not None else current.get("clock")
            current["funds"] = getattr(self, "_v161_funds_ok", current.get("funds"))
            self._v161_status_snapshot = current
        _render_status(self)
    except Exception:
        pass
    return result


def apply():
    if getattr(app.App, "_kaytrade_v161_ui_status_applied", False):
        return
    app.App.__init__ = app_init
    app.App.emit = emit
    app.App.drain = drain
    app.App._kaytrade_v161_ui_status_applied = True


apply()

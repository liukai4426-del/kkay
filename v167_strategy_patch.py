"""KAYTRADE V1.6.7 strategy/UI overlay.

Confirmed V1.6.7 strategy changes:
- keep 15m BOLL outer-only trigger and the existing 5m execution window;
- 5m MACD no longer has to improve as a hard gate: only explicit adverse/opposite
  MACD blocks a new entry;
- the existing MACD momentum/improvement +1 score is retained;
- remove the 5m KDJ +0.5 score entirely; KDJ is not converted into a hard gate;
- keep 4H aligned hard gate, 5m RSI 30..70, Volume <1.20x hard gate, score >=6;
- synchronize score detail, opening-status UI, authorization copy and runtime text.
"""
from __future__ import annotations

import math
import re
import tkinter as tk
from tkinter import messagebox

from v166_readonly_resilience_patch import apply as apply_previous
apply_previous()

import app
import visual
import v161_ui_status_patch as ui161
import v163_model as v163
import v164_model as v164
import v165_model as model
import v165_ui_patch as ui165
import v165_update_patch as runtime
import v166_ui_patch as ui166

VERSION = "1.6.7"
BUILD = "1670"
THRESHOLD = 6.0

_PREVIOUS_EVALUATE = model.evaluate
_PREVIOUS_EXECUTION_CHECKS = model.execution_checks
_PREVIOUS_PRE_SUBMIT_GUARD = runtime._pre_submit_guard
_PREVIOUS_RUNTIME_TEXT = runtime._rewrite_runtime_text
_PREVIOUS_INIT = app.App.__init__
_PREVIOUS_STATUS_SNAPSHOT = ui161._status_snapshot


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _finite(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _macd_adverse(score):
    conf = (score or {}).get("confirmations") or {}
    if "5m_macd_adverse" in conf:
        return bool(conf.get("5m_macd_adverse"))
    for key in ("macd5_adverse", "macd_adverse"):
        if key in conf:
            return bool(conf.get(key))
    for key in ("macd5_state", "5m_macd_state", "macd_state"):
        state = str(conf.get(key) or "").lower()
        if state:
            return any(token in state for token in ("opposite", "adverse", "逆向", "恶化"))
    return False


def _macd_allowed(score):
    """V1.6.7 hard gate: neutral/non-improving is allowed; explicit adverse is not."""
    return not _macd_adverse(score)


def _is_old_macd_gate_text(text):
    s = str(text or "")
    return (
        "MACD柱连续向交易方向改善" in s
        or "MACD柱连续两根改善" in s
        or "MACD未连续向交易方向改善" in s
        or "外轨开仓要求5m MACD" in s
        or "外轨MACD连续改善（必须）" in s
    )


def _reprice_without_kdj(row):
    layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
    layers.pop("kdj5_signal", None)
    row["layers"] = layers

    rewritten = []
    for item in row.get("items") or []:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            rewritten.append(item)
            continue
        label = str(item[0])
        if "KDJ" in label:
            continue
        values = list(item)
        if "MACD" in label:
            values[0] = "5m MACD动能改善（+1）"
            values[2] = 1.0
        rewritten.append(tuple(values) if isinstance(item, tuple) else values)
    row["items"] = rewritten

    numeric = []
    for value in layers.values():
        n = _finite(value)
        if n is not None:
            numeric.append(n)
    raw = sum(numeric)
    total = max(0.0, min(float(getattr(model, "SCORE_MAX", 10.0)), round(raw * 2.0) / 2.0))
    row["raw"] = raw
    row["total"] = total
    row["required"] = THRESHOLD

    reason = str(row.get("reason") or "")
    reason = re.sub(r"评分\s+[0-9.]+/10", f"评分 {total:g}/10", reason)
    row["reason"] = reason
    return total


def _apply_v167_row(row, result=None, opportunity=None):
    if not isinstance(row, dict):
        return row

    total = _reprice_without_kdj(row)
    conf = row.setdefault("confirmations", {})
    required = conf.setdefault("required", {})
    path = model._path_from(row, result=result, opportunity=opportunity)

    # Remove the obsolete "must improve" requirement key and replace it with
    # the actual V1.6.7 gate.  MACD improvement remains in score layers (+1).
    for key in list(required):
        if "macd" in str(key).lower():
            required.pop(key, None)

    if path in model.OUTER_PATHS:
        adverse = _macd_adverse(row)
        required["outer_macd_non_adverse"] = not adverse
        conf["macd_gate_rule"] = "5m MACD only explicit adverse/opposite blocks; improvement remains +1 momentum score"
        conf["macd_explicit_adverse"] = adverse
        conf["kdj_score_enabled"] = False

        blockers = [b for b in conf.get("blockers") or [] if not _is_old_macd_gate_text(b)]
        if adverse:
            blockers.append("V1.6.7：5m MACD明确逆向恶化，禁止开仓")
        conf["blockers"] = _dedupe(blockers)

        required_ok = all(bool(v) for v in required.values()) if required else True
        row["gate"] = bool(required_ok and not conf["blockers"])
        row["eligible"] = bool(row["gate"] and total >= THRESHOLD)
        row["position_multiplier"] = 1.0 if row["eligible"] else 0.0
        if adverse:
            row["level"] = "MACD明确逆向 · 禁止开仓"
        elif row["eligible"]:
            row["level"] = "V1.6.7外轨信号 · MACD非逆向 · 1×"
        elif row["gate"] and total < THRESHOLD:
            row["level"] = "未达开仓线"
    else:
        conf["kdj_score_enabled"] = False

    return row


def _reselect(scores):
    qualified = [
        side for side, row in (scores or {}).items()
        if isinstance(row, dict) and row.get("eligible")
    ]
    if len(qualified) == 1:
        return qualified[0]
    if len(qualified) == 2:
        a, b = qualified
        sa = float(scores[a].get("total") or 0.0)
        sb = float(scores[b].get("total") or 0.0)
        if sa != sb:
            return a if sa > sb else b
    return "观望"


def _evaluate_v167(*args, **kwargs):
    result, opp, transition = _PREVIOUS_EVALUATE(*args, **kwargs)
    if not isinstance(result, dict):
        return result, opp, transition
    scores = result.get("scores") or {}
    for row in scores.values():
        _apply_v167_row(row, result=result, opportunity=opp)
    selected = _reselect(scores)
    result["side"] = selected
    if selected != "观望":
        result["why"] = str((scores.get(selected) or {}).get("reason") or "")
    result["strategy_version"] = VERSION
    result["macd_gate"] = "explicit_adverse_only"
    result["macd_momentum_score_retained"] = True
    result["kdj_score_enabled"] = False
    result["threshold"] = THRESHOLD
    return result, opp, transition


def _execution_checks_v167(plan, opportunity, score):
    ok, diag, blockers = _PREVIOUS_EXECUTION_CHECKS(plan, opportunity, score)
    adverse = _macd_adverse(score)
    blockers = [b for b in (blockers or []) if not _is_old_macd_gate_text(b)]
    if adverse:
        blockers.append("V1.6.7开仓禁止：5m MACD明确逆向恶化")
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "macd_gate": "explicit_adverse_only",
        "macd_explicit_adverse": adverse,
        "macd_momentum_score_retained": True,
        "kdj_score_enabled": False,
    })
    blockers = _dedupe(blockers)
    return not blockers, diag, blockers


def _pre_submit_guard_v167(owner, market, score):
    blockers = list(_PREVIOUS_PRE_SUBMIT_GUARD(owner, market, score) or [])
    adverse = _macd_adverse(score)
    blockers = [b for b in blockers if not _is_old_macd_gate_text(b)]
    if adverse:
        blockers.append("V1.6.7 Final Entry Guard：5m MACD明确逆向恶化")
    return _dedupe(blockers)


def _rewrite_runtime_text_v167(data):
    text = _PREVIOUS_RUNTIME_TEXT(data)
    if not isinstance(text, str):
        return text
    for old in ("V1.6.6", "V1.6.5", "V1.6.4", "V1.6.3"):
        text = text.replace(old, VERSION)
    text = text.replace("5m MACD必须连续向交易方向改善", "5m MACD明确逆向时禁止开仓；改善仍保留+1动能分")
    text = text.replace("5m MACD改善", "5m MACD非逆向；改善+1动能分")
    text = text.replace("MACD改善", "MACD非逆向；改善+1动能分")
    text = text.replace("RSI/Volume/KDJ", "RSI/Volume（KDJ不计分）")
    text = text.replace("RSI / Volume / KDJ", "RSI / Volume（KDJ不计分）")
    if text.startswith("自动交易启动；"):
        return (
            "自动交易启动；V1.6.7 Build1670：所有指标仅使用各自周期最新已收盘K线；"
            "1H/15m定方向→15m BOLL外轨2.5分→5m RSI30-70 / Volume<1.20×→4H同向；"
            "5m MACD仅明确逆向时禁止开仓，MACD动能改善继续+1分；KDJ不再计分；"
            "15m BOLL触发后沿用5m执行窗口；评分≥6，前方结构≥1.5R、成本≤0.30R及止损结构继续限制；"
            "下单前Final Entry Guard再次检查各周期新鲜度和全部动态Hard Gate；"
            "首次开仓固定1× LIMIT；1×1H ATR止损、2R整仓止盈、No-BE"
        )
    return text


def _status_snapshot_v167(owner, data):
    snapshot = dict(_PREVIOUS_STATUS_SNAPSHOT(owner, data) or {})
    if not isinstance(data, dict):
        return snapshot
    _side, row = ui161._candidate(data)
    conf = (row or {}).get("confirmations") or {}
    rsi = _finite(conf.get("rsi5"))
    snapshot["rsi_gate"] = None if rsi is None else model.RSI_MIN <= rsi <= model.RSI_MAX
    snapshot["macd_clean"] = None if not row else (not _macd_adverse(row))
    snapshot.pop("middle_macd", None)
    return snapshot


def _authorization_text_v167(owner, settings):
    env = "OKX模拟盘" if owner.engine.x.demo else "真实账户"
    base_risk = min(settings.risk_usdt, settings.capital * settings.risk_pct / 100)
    return (
        f"{env}  ·  BTC-USDT-SWAP  ·  逐仓 {settings.leverage}×\n\n"
        "V1.6.7 当前生效策略\n"
        "• 所有技术指标仅使用各自周期最新已收盘K线，不使用未收盘K线。\n"
        "• 1H / 15m 确认主方向；仅使用最新已收盘15m BOLL上下外轨触发，中轨不作为开仓触发。\n"
        "• 15m BOLL外轨触发计2.5分；5m RSI必须30–70；4H必须与交易方向同向。\n"
        "• 5m MACD不再要求连续改善；只有明确逆向/恶化时Hard Gate禁止开仓。MACD动能改善的+1评分保留。\n"
        "• 删除5m KDJ +0.5评分；KDJ不作为Hard Gate，也不再影响总分。\n"
        "• 5m Volume必须 < 前20根5m均量1.20×；达到或超过1.20×时Hard Gate禁止开仓，Volume不计分。\n"
        "• 15m BOLL触发后沿用5m执行窗口；新周期到达后必须重新判断，不使用旧信号。\n"
        "• 评分必须 ≥6；前方强结构至少1.5R；成本≤0.30R；止损结构和入场偏离限制必须通过。\n"
        "• 下单前 Final Entry Guard 再次确认各周期数据新鲜度与全部动态Hard Gate。\n"
        "• 首次开仓固定1× LIMIT；止损=1×1H ATR；整仓2R止盈；No-BE。\n\n"
        f"资金预算 {settings.capital:g} USDT  ·  最大名义仓位 {settings.max_notional:g} USDT  ·  "
        f"基础单笔风险≤{base_risk:g} USDT\n\n"
        "确认后将启动自动交易；取消则保持停止新开仓。"
    )


def _show_authorization_dialog_v167(owner, settings):
    result = {"confirmed": False}
    win = tk.Toplevel(owner.root)
    win.title("启动自动交易 · V1.6.7")
    win.configure(bg=visual.BG)
    win.transient(owner.root)
    win.resizable(False, False)
    win.protocol("WM_DELETE_WINDOW", win.destroy)

    outer = tk.Frame(win, bg=visual.BG, padx=18, pady=18)
    outer.pack(fill="both", expand=True)
    card = tk.Frame(outer, bg=visual.PANEL, padx=24, pady=22)
    card.pack(fill="both", expand=True)
    visual.label(card, text="启动自动交易", size=20, bold=True, color=visual.TEXT, bg=visual.PANEL).pack(anchor="w")
    visual.label(card, text="V1.6.7 · 当前正式策略确认", size=11, color=visual.GREEN, bg=visual.PANEL).pack(anchor="w", pady=(5, 16))
    tk.Label(
        card, text=_authorization_text_v167(owner, settings), justify="left", anchor="nw",
        wraplength=600, bg=visual.PANEL, fg=visual.TEXT,
        font=("Helvetica", 11), bd=0, highlightthickness=0,
    ).pack(fill="x", anchor="w")

    buttons = tk.Frame(card, bg=visual.PANEL)
    buttons.pack(fill="x", pady=(22, 0))

    def cancel():
        win.destroy()

    def confirm():
        result["confirmed"] = True
        win.destroy()

    visual.RoundedButton(buttons, text="取消", command=cancel, variant="neutral", width=150, height=44, radius=16).pack(side="right")
    visual.RoundedButton(buttons, text="确认", command=confirm, variant="accent", width=150, height=44, radius=16).pack(side="right", padx=(0, 10))
    win.update_idletasks()
    width = 680
    height = max(590, min(760, win.winfo_reqheight()))
    try:
        x = owner.root.winfo_rootx() + max(0, (owner.root.winfo_width() - width) // 2)
        y = owner.root.winfo_rooty() + max(0, (owner.root.winfo_height() - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")
    except Exception:
        win.geometry(f"{width}x{height}")
    win.grab_set()
    win.focus_force()
    owner._v167_authorization_dialog = win
    owner.root.wait_window(win)
    return bool(result["confirmed"])


def _app_init_v167(self, *args, **kwargs):
    _PREVIOUS_INIT(self, *args, **kwargs)
    try:
        self.root.title(f"KAYTRADE {VERSION} · BTC 策略控制台 · Build {BUILD}")
    except Exception:
        pass
    try:
        self.signal.set(
            "V1.6.7 · 15m外轨2.5 / 5m RSI30–70 / MACD非逆向Hard Gate + 改善1分 / KDJ不计分 / Volume<1.20× / 4H同向"
        )
    except Exception:
        pass
    for widget in ui166._walk(self.root):
        try:
            text = str(widget.cget("text") or "")
        except Exception:
            continue
        if text.startswith("BTC / USDT"):
            try:
                widget.configure(text=f"BTC / USDT   ·   V{VERSION}")
                self._v167_version_label = widget
            except Exception:
                pass
    self._v167_strategy_ui_ready = True


def apply():
    if getattr(model, "_kaytrade_v167_strategy_applied", False):
        return

    # Hard-gate route used by V1.6.3 -> V1.6.7.  This preserves the original
    # MACD improvement component in v153 scoring while changing only eligibility.
    v163._macd_improving = _macd_allowed
    v164._macd_improving = _macd_allowed
    model._macd_improving = _macd_allowed

    model.evaluate = _evaluate_v167
    model.execution_checks = _execution_checks_v167
    model.VERSION = VERSION
    model.THRESHOLD = THRESHOLD

    runtime._pre_submit_guard = _pre_submit_guard_v167
    runtime._rewrite_runtime_text = _rewrite_runtime_text_v167
    runtime.VERSION = VERSION
    runtime.BUILD = BUILD

    ui161._STATUS_ITEMS = (
        ("score", "评分≥6"),
        ("direction", "1H/15m主方向"),
        ("boll", "15m BOLL外轨触发"),
        ("signal_window", "5m执行窗口"),
        ("rsi_gate", "5m RSI 30–70"),
        ("outer_4h", "4H同向"),
        ("macd_clean", "MACD非逆向"),
        ("entry_drift", "入场未偏离"),
        ("front_space", "前方≥1.5R"),
        ("stop_structure", "止损结构"),
        ("cost", "成本≤0.30R"),
        ("adverse_4h", "4H结构安全"),
        ("daily_risk", "日内风险"),
        ("volume_gate", "Volume<1.20×"),
        ("clock", "时钟同步"),
    )
    ui161._status_snapshot = _status_snapshot_v167

    ui165._authorization_text = _authorization_text_v167
    ui165._show_authorization_dialog = _show_authorization_dialog_v167
    ui165.VERSION = VERSION
    ui165.BUILD = BUILD

    ui166.VERSION = VERSION
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = f"KAYTRADE {VERSION} · BTC 策略控制台 · Build {BUILD}"
    ui166.VERSION_SUBTITLE = f"BTC / USDT   ·   V{VERSION}"

    app.App.__init__ = _app_init_v167

    model._kaytrade_v167_strategy_applied = True
    app.App._kaytrade_v167_strategy_applied = True


apply()

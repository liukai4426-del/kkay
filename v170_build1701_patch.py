"""KAYTRADE V1.7.0 Build1701 strict 15m opportunity-source hotfix.

Build1701 changes no score/risk/exit rule. It fixes the live opportunity creation
boundary and run-log observability:
- a new BOLL opportunity can be created ONLY from strict evidence on the latest
  CLOSED 15m candle (long: low <= lower; short: high >= upper);
- the inherited evaluator is always called with allow_new=False, so historical
  5m BOLL generators cannot create a transient candidate before later rejection;
- existing stored opportunities are accepted only when their immutable 15m
  evidence validates; malformed/legacy opportunities are discarded at source;
- execution checks and Final Entry Guard independently fail closed if strict
  15m evidence is absent;
- run-log entry/blocked/invalidated messages carry 做多/做空 direction.
"""
from __future__ import annotations

from v170_update_patch import apply as apply_previous
apply_previous()

import app
import v165_model as model
import v165_update_patch as runtime
import v166_ui_patch as ui166
import v167_15m_boll_only_fix as b1671
import v168_update_patch as v168
import v168_build1681_patch as b1681
import v170_update_patch as v170

VERSION = "1.7.0"
BUILD = "1701"

_PREVIOUS_MODEL_EVALUATE = model.evaluate
_PREVIOUS_EXECUTION_CHECKS = model.execution_checks
_PREVIOUS_PRE_SUBMIT_GUARD = runtime._pre_submit_guard
_PREVIOUS_RUNTIME_TEXT = runtime._rewrite_runtime_text
_PREVIOUS_APP_EMIT = app.App.emit
_PREVIOUS_APP_INIT = app.App.__init__

_DIRECTION_VALUES = ("做多", "做空")
_DIRECTION_LOG_MARKERS = (
    "未开仓",
    "禁止开仓",
    "机会失效",
    "回调机会",
    "BOLL信号",
    "BOLL外轨",
    "提交开仓",
    "限价开仓",
)


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _strict_direction(hour, quarter):
    try:
        value = str(model.trend_direction(hour, quarter) or "观望")
    except Exception:
        return "观望"
    return value if value in _DIRECTION_VALUES else "观望"


def _strict15_prepare(hour, quarter, five, one, opportunity=None, allow_new=True):
    """Return only a valid 15m opportunity; never delegate creation to legacy 5m code."""
    b1671._pin_15m_only()
    direction = _strict_direction(hour, quarter)

    incoming = None
    if isinstance(opportunity, dict) and b1671._valid_15m_evidence(opportunity):
        incoming = dict(opportunity)

    signal = None
    if direction in _DIRECTION_VALUES:
        signal = b1671._boll_entry_signal_15m_only(quarter, five, direction)

    created = False
    if bool(allow_new) and isinstance(signal, dict):
        try:
            signal_t = int(signal.get("bar_t", -1))
            old_t = int((incoming or {}).get("signal_bar_t", -1))
        except (TypeError, ValueError):
            signal_t, old_t = -1, -1
        if incoming is None or signal_t > old_t:
            candidate = b1671._new_opportunity_15m_only(
                hour, quarter, five, one, direction, signal
            )
            if isinstance(candidate, dict) and b1671._valid_15m_evidence(candidate):
                incoming = candidate
                created = True

    return incoming, direction, signal, created


def _direction_detail(detail, side):
    text = str(detail or "")
    if side not in _DIRECTION_VALUES:
        return text
    if "方向：做多" in text or "方向：做空" in text:
        return text
    return f"方向：{side}｜{text}" if text else f"方向：{side}"


def _evaluate_v1701(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                    maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                    now_ms=None, allow_new=True):
    incoming, direction, strict_signal, created = _strict15_prepare(
        hour, quarter, five, one, opportunity=opportunity, allow_new=allow_new
    )

    # Critical Build1701 source lock: the inherited chain is evaluation-only.
    # It can update/invalidate the strict opportunity, but can never create one.
    result, opp, transition = _PREVIOUS_MODEL_EVALUATE(
        hour, quarter, five, one, four,
        opportunity=incoming,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=False,
    )
    b1671._pin_15m_only()

    # Defense in depth: a malformed/legacy opportunity must never reach UI or execution.
    suppressed = False
    if isinstance(opp, dict) and not b1671._valid_15m_evidence(opp):
        suppressed = True
        result, opp, _ignored = _PREVIOUS_MODEL_EVALUATE(
            hour, quarter, five, one, four,
            opportunity=None,
            stop_atr=stop_atr,
            maker_bps=maker_bps,
            taker_bps=taker_bps,
            slippage_bps=slippage_bps,
            now_ms=now_ms,
            allow_new=False,
        )
        transition = None
        b1671._pin_15m_only()

    if isinstance(opp, dict) and created and not (
        isinstance(transition, tuple) and transition and transition[0] == "invalidated"
    ):
        transition = ("created", _direction_detail(opp.get("id", ""), direction))
    elif isinstance(transition, tuple) and len(transition) > 1:
        side = str((incoming or {}).get("side") or direction)
        transition = (transition[0], _direction_detail(transition[1], side))

    if isinstance(result, dict):
        result["strict15_opportunity_source_lock"] = True
        result["strict15_direction"] = direction
        result["strict15_trigger_present"] = isinstance(strict_signal, dict)
        result["strict15_legacy_candidate_suppressed"] = bool(suppressed)
        result["build"] = BUILD
        result["strategy_version"] = VERSION
        if isinstance(opp, dict):
            result["opportunity"] = dict(opp)
        else:
            result["opportunity"] = None
            result["opportunity_id"] = ""

    return result, opp, transition


def _execution_checks_v1701(plan, opportunity, score):
    ok, diag, blockers = _PREVIOUS_EXECUTION_CHECKS(plan, opportunity, score)
    blockers = list(blockers or [])
    valid = bool(isinstance(opportunity, dict) and b1671._valid_15m_evidence(opportunity))
    if not valid:
        blockers.append(
            "V1.7.0 Build1701开仓禁止：缺少有效已收盘15m BOLL外轨触发证据"
        )
    diag = dict(diag or {})
    diag.update({
        "strategy_version": VERSION,
        "build": BUILD,
        "strict15_opportunity_source_lock": True,
        "strict15_evidence_valid": valid,
    })
    blockers = _dedupe(blockers)
    return not blockers, diag, blockers


def _pre_submit_guard_v1701(owner, market, score):
    blockers = list(_PREVIOUS_PRE_SUBMIT_GUARD(owner, market, score) or [])
    try:
        opportunity = runtime._opportunity_from(market, score)
    except Exception:
        opportunity = None
    if not (isinstance(opportunity, dict) and b1671._valid_15m_evidence(opportunity)):
        blockers.append(
            "V1.7.0 Build1701 Final Entry Guard：缺少有效已收盘15m BOLL外轨触发证据"
        )
    return _dedupe(blockers)


def _rewrite_runtime_text_v1701(data):
    text = _PREVIOUS_RUNTIME_TEXT(data)
    if not isinstance(text, str):
        return text
    text = text.replace("Build1700", "Build1701").replace("Build 1700", "Build 1701")
    if text.startswith("自动交易启动；") and "15m机会源锁" not in text:
        text += (
            "；Build1701 15m机会源锁：只有最新已收盘15m外轨真实触发才可创建开仓机会，"
            "5m BOLL仅用于超伸加分/持仓风控，不得创建开仓机会"
        )
    return text


def _runtime_direction(owner):
    eng = getattr(owner, "engine", None)
    market = getattr(eng, "market", None) if eng is not None else None
    if not isinstance(market, dict):
        return ""
    opp = market.get("opportunity")
    candidates = [
        (opp or {}).get("side") if isinstance(opp, dict) else None,
        market.get("direction"),
        market.get("side"),
    ]
    for value in candidates:
        side = str(value or "")
        if side in _DIRECTION_VALUES:
            return side
    return ""


def _with_direction(text, side):
    value = str(text or "")
    if side not in _DIRECTION_VALUES or not value:
        return value
    if "方向：做多" in value or "方向：做空" in value:
        return value
    if not any(marker in value for marker in _DIRECTION_LOG_MARKERS):
        return value
    return f"方向：{side} · {value}"


def _app_emit_v1701(self, kind, data):
    if isinstance(data, str):
        outgoing = _rewrite_runtime_text_v1701(data)
        if str(kind) in ("log", "alarm"):
            outgoing = _with_direction(outgoing, _runtime_direction(self))
        return _PREVIOUS_APP_EMIT(self, kind, outgoing)
    return _PREVIOUS_APP_EMIT(self, kind, data)


def _app_init_v1701(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    try:
        self.root.title("KAYTRADE 1.7.0 · BTC 策略控制台 · Build 1701")
    except Exception:
        pass
    self._v170_build1701_ready = True


def apply():
    if getattr(model, "_kaytrade_v170_build1701_applied", False):
        return

    b1671._pin_15m_only()
    model.evaluate = _evaluate_v1701
    model.execution_checks = _execution_checks_v1701
    model.VERSION = VERSION
    model.BUILD = BUILD
    model.BOLL_TRIGGER_TIMEFRAME = "15m"
    model.FIVE_MINUTE_BOLL_ENABLED = False

    runtime._pre_submit_guard = _pre_submit_guard_v1701
    runtime._rewrite_runtime_text = _rewrite_runtime_text_v1701
    runtime.VERSION = VERSION
    runtime.BUILD = BUILD

    v170.BUILD = BUILD
    v168.BUILD = BUILD
    b1681.BUILD = BUILD
    ui166.BUILD = BUILD
    ui166.WINDOW_TITLE = "KAYTRADE 1.7.0 · BTC 策略控制台 · Build 1701"

    app.App.emit = _app_emit_v1701
    app.App.__init__ = _app_init_v1701

    model._kaytrade_v170_build1701_applied = True
    app.App._kaytrade_v170_build1701_applied = True


apply()

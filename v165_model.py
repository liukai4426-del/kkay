"""KAYTRADE V1.6.5 strategy overlay.

Production semantics:
- every technical indicator uses the latest CLOSED candle of its own timeframe;
- a 5m BOLL outer signal and its 5m RSI/Volume/KDJ signal snapshot remain valid
  until the next 5m candle closes (exactly five minutes after signal close);
- at the next closed 5m candle the previous signal expires and the entire 5m
  signal package is evaluated again from the new closed candle;
- 15m / 1H / 4H state is refreshed when a new candle of that timeframe closes;
- Volume >= 1.20x prior-20 5m average is a hard opening gate;
- outer BOLL is 2.5 points, Volume is not a score component;
- outer-only RSI 30-70, formal 5m MACD improvement and 4H alignment remain.
"""
from __future__ import annotations

import v164_model as base

VERSION = "1.6.5"
ENTRY_WINDOW_MS = 5 * 60 * 1000
TIME_WINDOW_ENABLED = True
THRESHOLD = base.THRESHOLD
SIGNAL_DRIFT_ATR = base.SIGNAL_DRIFT_ATR
FRONT_MIN_R = base.FRONT_MIN_R
COST_MAX_R = base.COST_MAX_R
STOP_BUFFER_ATR = base.STOP_BUFFER_ATR
OVERLAP_ATR_TOL = base.OVERLAP_ATR_TOL
OUTER_PATHS = base.OUTER_PATHS
MIDDLE_PATH = base.MIDDLE_PATH
SCORE_MAX = base.SCORE_MAX
RSI_MIN = base.RSI_MIN
RSI_MAX = base.RSI_MAX
VOLUME_HARD_GATE = base.VOLUME_HARD_GATE
BOLL_OUTER_BONUS = base.BOLL_OUTER_BONUS
BOLL_OUTER_SCORE = base.BOLL_OUTER_SCORE

trend_direction = base.trend_direction
four_hour_state = base.four_hour_state
boll_entry_signal = base.boll_entry_signal
_entry_ok = base._entry_ok
_macd_improving = base._macd_improving
_path_from = base._path_from
_dedupe = base._dedupe
_finite = base._finite
_effective_now_ms = base._effective_now_ms
_signal_close_ms = base._signal_close_ms


def _signal_window(opportunity, now_ms):
    start = _signal_close_ms(opportunity)
    now = int(now_ms or 0)
    end = start + ENTRY_WINDOW_MS if start > 0 else 0
    opened = bool(start > 0 and start <= now < end)
    remaining_ms = max(0, end - now) if end else 0
    age_ms = max(0, now - start) if start else 0
    return opened, age_ms, remaining_ms, end


def _window_state_5m(now_ms, opportunity):
    opened, age_ms, remaining_ms, _end = _signal_window(opportunity, now_ms)
    minute = 0
    if opened:
        minute = min(5, max(1, int(age_ms // 60_000) + 1))
    return opened, minute, remaining_ms


def _normalize_opportunity_5m(opportunity):
    if not isinstance(opportunity, dict):
        return opportunity
    opp = dict(opportunity)
    start = _signal_close_ms(opp)
    if start > 0:
        opp["expires_ms"] = start + ENTRY_WINDOW_MS
        opp["time_window_enabled"] = True
        opp["signal_valid_until_next_5m_close"] = True
    return opp


def _patch_v164_base():
    """Replace only the final lifecycle semantics used by the V1.6.4 core."""
    base.VERSION = VERSION
    base.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    base.TIME_WINDOW_ENABLED = True
    base._window_state_4m = _window_state_5m
    base._normalize_opportunity_4m = _normalize_opportunity_5m
    base.signal_core.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    base.signal_core._window_state = _window_state_5m
    base.window_base.ENTRY_WINDOW_MS = ENTRY_WINDOW_MS
    base.window_base.TIME_WINDOW_ENABLED = True
    base.window_base._window_state = _window_state_5m
    # Re-install the historical facade using the new five-minute functions.
    base._install_window_patch()


def _rewrite_text(text):
    if not isinstance(text, str):
        return text
    out = text.replace("V1.6.4", "V1.6.5")
    out = out.replace("4分钟执行窗口", "当前5m信号周期")
    out = out.replace("超过4分钟", "已到下一根5m收盘")
    out = out.replace("4分钟有效", "有效至下一根5m收盘")
    out = out.replace("4分钟信号窗口", "5m闭合周期信号")
    out = out.replace("4分钟内", "下一根5m收盘前")
    return out


def _rewrite_row(row):
    if not isinstance(row, dict):
        return row
    row["level"] = _rewrite_text(row.get("level"))
    row["reason"] = _rewrite_text(row.get("reason"))
    rewritten = []
    for item in row.get("items") or []:
        if isinstance(item, tuple) and item:
            rewritten.append((_rewrite_text(item[0]), *item[1:]))
        elif isinstance(item, list) and item:
            rewritten.append([_rewrite_text(item[0]), *item[1:]])
        else:
            rewritten.append(item)
    row["items"] = rewritten
    confirmations = row.setdefault("confirmations", {})
    required = confirmations.setdefault("required", {})
    window_ok = bool(confirmations.get("signal_window_ok"))
    required["signal_window_5m"] = window_ok
    required.pop("signal_window_4m", None)
    confirmations["signal_lifecycle"] = "latest closed 5m signal valid until next 5m close"
    blockers = [_rewrite_text(x) for x in confirmations.get("blockers") or []]
    confirmations["blockers"] = _dedupe(blockers)
    return row


def _rewrite_result(result, opportunity=None):
    if not isinstance(result, dict):
        return result
    result["strategy_version"] = VERSION
    result["time_window_enabled"] = True
    result["signal_lifecycle"] = "closed_5m_until_next_close"
    result["why"] = _rewrite_text(result.get("why"))
    result["status"] = _rewrite_text(result.get("status"))
    if isinstance(opportunity, dict):
        opportunity = _normalize_opportunity_5m(opportunity)
        result["opportunity"] = dict(opportunity)
    for row in (result.get("scores") or {}).values():
        _rewrite_row(row)
    return result


def new_opportunity(hour, quarter, five, one, side, signal):
    _patch_v164_base()
    return _normalize_opportunity_5m(base.new_opportunity(hour, quarter, five, one, side, signal))


def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
             maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
             now_ms=None, allow_new=True):
    _patch_v164_base()
    incoming = _normalize_opportunity_5m(opportunity)
    result, opp, transition = base.evaluate(
        hour, quarter, five, one, four,
        opportunity=incoming,
        stop_atr=stop_atr,
        maker_bps=maker_bps,
        taker_bps=taker_bps,
        slippage_bps=slippage_bps,
        now_ms=now_ms,
        allow_new=allow_new,
    )
    opp = _normalize_opportunity_5m(opp)
    if isinstance(transition, tuple) and len(transition) > 1:
        transition = (transition[0], _rewrite_text(transition[1]))
    return _rewrite_result(result, opp), opp, transition


def execution_checks(plan, opportunity, score):
    _patch_v164_base()
    opportunity = _normalize_opportunity_5m(opportunity)
    ok, diag, blockers = base.execution_checks(plan, opportunity, score)
    diag = dict(diag or {})
    blockers = [_rewrite_text(x) for x in (blockers or [])]
    now_ms = int(diag.get("now_ms") or 0)
    if now_ms > 0 and isinstance(opportunity, dict):
        opened, age_ms, remaining_ms, end_ms = _signal_window(opportunity, now_ms)
        diag.update(signal_window_ok=opened, signal_age_ms=age_ms,
                    signal_remaining_ms=remaining_ms, signal_expires_ms=end_ms)
        if not opened:
            blockers.append("V1.6.5开仓禁止：5m外轨信号已到下一根5m收盘，必须使用最新已收盘5m重新判断")
    diag.update(strategy_version=VERSION, entry_window_ms=ENTRY_WINDOW_MS,
                signal_lifecycle="closed_5m_until_next_close")
    blockers = _dedupe(blockers)
    return not blockers, diag, blockers


_patch_v164_base()

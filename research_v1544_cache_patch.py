from __future__ import annotations

import v152_model as base_model
import v153_model as signal_model

_orig_indicators = signal_model.indicators
_orig_zones = base_model.strategy._zones
_orig_macd_hist = base_model._macd_hist
_orig_ema50_move = base_model._ema50_move
_orig_ema = base_model.ema
_indicator_cache = {}
_zone_cache = {}
_macd_cache = {}
_ema50_cache = {}
_one_minute_ema20 = {}
_one_minute_window = 1000


def _rows_key(rows):
    return (0, 0, 0) if not rows else (len(rows), int(rows[0]['t']), int(rows[-1]['t']))


def _is_one_minute(rows):
    return len(rows) >= 2 and int(rows[-1]['t']) - int(rows[-2]['t']) == 60_000


def _exact_ema20(rows):
    return float(_orig_ema([float(r['c']) for r in rows], 20)[-1])


def prime_one_minute(rows, window=1000):
    global _one_minute_window
    _one_minute_window = int(window)
    _one_minute_ema20.clear()
    if not rows:
        return
    closes = [float(r['c']) for r in rows]
    n = min(_one_minute_window, len(rows))
    alpha = 2.0 / 21.0
    q = 1.0 - alpha
    value = closes[0]
    for x in closes[1:n]:
        value += alpha * (x - value)
    _one_minute_ema20[int(rows[n-1]['t'])] = float(value)
    if len(rows) <= n:
        return
    qn = q ** n
    for end in range(n, len(rows)):
        old0 = closes[end-n]
        old1 = closes[end-n+1]
        new = closes[end]
        value = q * value + qn * (old1 - old0) + alpha * new
        _one_minute_ema20[int(rows[end]['t'])] = float(value)


def indicators_cached(rows):
    key = _rows_key(rows)
    hit = _indicator_cache.get(key)
    if hit is None:
        if _is_one_minute(rows):
            last_t = int(rows[-1]['t'])
            e = _one_minute_ema20.get(last_t)
            if e is None or len(rows) != _one_minute_window:
                e = _exact_ema20(rows)
            hit = {'ema20': float(e)}
        else:
            hit = _orig_indicators(rows)
        _indicator_cache[key] = hit
    return hit


def zones_cached(rows, atr, lookback):
    key = (_rows_key(rows), float(atr), int(lookback))
    hit = _zone_cache.get(key)
    if hit is None:
        hit = _orig_zones(rows, atr, lookback)
        _zone_cache[key] = hit
    return hit


def macd_hist_cached(rows):
    key = _rows_key(rows)
    hit = _macd_cache.get(key)
    if hit is None:
        hit = _orig_macd_hist(rows)
        _macd_cache[key] = hit
    return hit


def ema50_move_cached(rows, side):
    key = (_rows_key(rows), str(side))
    hit = _ema50_cache.get(key)
    if hit is None:
        hit = _orig_ema50_move(rows, side)
        _ema50_cache[key] = hit
    return hit


signal_model.indicators = indicators_cached
base_model.indicators = indicators_cached
base_model.strategy._zones = zones_cached
base_model._macd_hist = macd_hist_cached
base_model._ema50_move = ema50_move_cached


def stats():
    return {
        'indicator_entries': len(_indicator_cache),
        'zone_entries': len(_zone_cache),
        'macd_entries': len(_macd_cache),
        'ema50_entries': len(_ema50_cache),
        'precomputed_1m_ema20': len(_one_minute_ema20),
    }

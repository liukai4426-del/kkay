"""Pure memoization helpers for V1.5.2 research backtests.

This module does not alter strategy values. It only caches deterministic results
for identical closed-candle inputs so the 30-day replay can reuse repeated
1H/15m/5m/4H calculations and the baseline/stress pass can share work.
"""
from __future__ import annotations

import v152_model as model

_orig_indicators = model.indicators
_orig_zones = model.strategy._zones
_orig_macd_hist = model._macd_hist
_orig_ema50_move = model._ema50_move

_indicator_cache = {}
_zone_cache = {}
_macd_cache = {}
_ema50_cache = {}


def _rows_key(rows):
    if not rows:
        return (0, 0, 0)
    return (len(rows), int(rows[0]['t']), int(rows[-1]['t']))


def indicators_cached(rows):
    key = _rows_key(rows)
    hit = _indicator_cache.get(key)
    if hit is None:
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


def apply():
    model.indicators = indicators_cached
    model.strategy._zones = zones_cached
    model._macd_hist = macd_hist_cached
    model._ema50_move = ema50_move_cached


def stats():
    return {
        'indicator_entries': len(_indicator_cache),
        'zone_entries': len(_zone_cache),
        'macd_entries': len(_macd_cache),
        'ema50_entries': len(_ema50_cache),
    }

apply()

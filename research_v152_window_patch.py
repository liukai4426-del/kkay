"""Zero-copy candle-window adapter for V1.5.2 research replay.

The original backtest copied up to 1,000 candle dictionaries for every timeframe
on every 1m step. This adapter returns a read-only sequence view over the same
underlying candle list. Values, indexes and close-time cutoffs are unchanged.
"""
from __future__ import annotations

import bisect


class CandleWindow:
    __slots__ = ('_data', '_start', '_stop')

    def __init__(self, data, start, stop):
        self._data = data
        self._start = int(start)
        self._stop = int(stop)

    def __len__(self):
        return self._stop - self._start

    def __iter__(self):
        data = self._data
        for i in range(self._start, self._stop):
            yield data[i]

    def __getitem__(self, key):
        n = len(self)
        if isinstance(key, slice):
            start, stop, step = key.indices(n)
            if step == 1:
                return CandleWindow(self._data, self._start + start, self._start + stop)
            return [self._data[self._start + i] for i in range(start, stop, step)]
        i = int(key)
        if i < 0:
            i += n
        if i < 0 or i >= n:
            raise IndexError(i)
        return self._data[self._start + i]


_view_cache = {}
_stats = {'calls': 0, 'hits': 0, 'created': 0}


def install(backtest_module):
    def closed_slice_view(data, ts, tf, close_ms):
        _stats['calls'] += 1
        cutoff = int(close_ms) - backtest_module.base.BAR_MS[tf]
        idx = bisect.bisect_right(ts[tf], cutoff) - 1
        if idx < 200:
            return None
        start = max(0, idx - backtest_module.MODEL_WINDOW + 1)
        key = (str(tf), int(start), int(idx + 1))
        hit = _view_cache.get(key)
        if hit is not None:
            _stats['hits'] += 1
            return hit
        view = CandleWindow(data[tf], start, idx + 1)
        _view_cache[key] = view
        _stats['created'] += 1
        return view

    backtest_module.closed_slice = closed_slice_view


def stats():
    return dict(_stats)

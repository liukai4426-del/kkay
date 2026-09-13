"""Persistent raw-market cache for V1.5.2 research backtests.

This module only replaces repeated OKX public-data downloads with gzip JSON
files keyed by the exact test window and warm-up profile. It does not alter any
candle, funding, instrument, strategy, scoring, risk, or execution value.
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

CACHE_DIR = Path(os.environ.get('V152_MARKET_CACHE_DIR', '.research_market_cache'))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_stats = {'hits': 0, 'misses': 0, 'writes': 0}


def _token(base):
    return f"{int(base.START_MS)}_{int(base.END_MS)}_w{int(getattr(base, 'WARMUP_BARS', 1600))}"


def _path(base, kind):
    safe = str(kind).replace('/', '_')
    return CACHE_DIR / f"btc-usdt-swap_{_token(base)}_{safe}.json.gz"


def _read(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return json.load(f)


def _write(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with gzip.open(tmp, 'wt', encoding='utf-8', compresslevel=6) as f:
        json.dump(value, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)
    _stats['writes'] += 1


def install(base, cache_patch=None):
    orig_candles = base.fetch_candles
    orig_funding = base.fetch_funding
    orig_instrument = base.fetch_instrument

    def fetch_candles(bar):
        path = _path(base, f'candles_{bar}')
        if path.is_file():
            rows = _read(path)
            _stats['hits'] += 1
            print(f'MARKET_CACHE HIT {bar}: {len(rows):,} bars', flush=True)
        else:
            _stats['misses'] += 1
            rows = orig_candles(bar)
            _write(path, rows)
            print(f'MARKET_CACHE WRITE {bar}: {len(rows):,} bars', flush=True)
        if bar == '1m' and cache_patch is not None:
            cache_patch.prime_one_minute(rows, window=1000)
        return rows

    def fetch_funding():
        path = _path(base, 'funding')
        if path.is_file():
            raw = _read(path)
            rows = [(int(t), float(rate)) for t, rate in raw]
            _stats['hits'] += 1
            print(f'MARKET_CACHE HIT funding: {len(rows)} settlements', flush=True)
            return rows
        _stats['misses'] += 1
        rows = orig_funding()
        _write(path, rows)
        print(f'MARKET_CACHE WRITE funding: {len(rows)} settlements', flush=True)
        return rows

    def fetch_instrument():
        path = CACHE_DIR / 'btc-usdt-swap_instrument.json.gz'
        if path.is_file():
            _stats['hits'] += 1
            return _read(path)
        _stats['misses'] += 1
        value = orig_instrument()
        _write(path, value)
        return value

    base.fetch_candles = fetch_candles
    base.fetch_funding = fetch_funding
    base.fetch_instrument = fetch_instrument


def stats():
    return dict(_stats)

from __future__ import annotations

import gzip
import os
import pickle
from pathlib import Path

CACHE_VERSION = "v1"
DEFAULT_ROOT = Path(os.environ.get("BACKTEST_MARKET_CACHE", ".backtest_cache"))


def _safe(text):
    return str(text).replace("/", "-").replace(":", "-").replace(" ", "_")


def _window_key(base):
    inst = _safe(getattr(base, "INST", "instrument"))
    start = int(getattr(base, "START_MS"))
    end = int(getattr(base, "END_MS"))
    warmup = int(getattr(base, "WARMUP_BARS", 0))
    return f"{CACHE_VERSION}_{inst}_{start}_{end}_w{warmup}"


def _load(path):
    with gzip.open(path, "rb") as fh:
        return pickle.load(fh)


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wb", compresslevel=3) as fh:
        pickle.dump(value, fh, protocol=5)
    tmp.replace(path)


def _get_or_fetch(path, label, fetcher):
    if path.exists():
        value = _load(path)
        print(f"MARKET_CACHE_HIT {label} {path} size={path.stat().st_size}", flush=True)
        return value, True
    print(f"MARKET_CACHE_MISS {label} {path}", flush=True)
    value = fetcher()
    _save(path, value)
    print(f"MARKET_CACHE_SAVED {label} {path} size={path.stat().st_size}", flush=True)
    return value, False


def load_market(base, timeframes=("1m", "5m", "15m", "1H", "4H"), root=None):
    """Load an exact historical market snapshot, fetching only missing pieces.

    Cache identity includes instrument, START_MS, END_MS, WARMUP_BARS and cache
    version. The backtest's own fetch functions remain the source of truth.
    """
    root = Path(root or DEFAULT_ROOT)
    key = _window_key(base)
    folder = root / key
    folder.mkdir(parents=True, exist_ok=True)

    hits = 0
    meta, hit = _get_or_fetch(folder / "instrument.pkl.gz", "instrument", base.fetch_instrument)
    hits += int(hit)

    data = {}
    for tf in timeframes:
        value, hit = _get_or_fetch(folder / f"candles_{_safe(tf)}.pkl.gz", f"candles:{tf}", lambda tf=tf: base.fetch_candles(tf))
        data[tf] = value
        hits += int(hit)

    funding, hit = _get_or_fetch(folder / "funding.pkl.gz", "funding", base.fetch_funding)
    hits += int(hit)

    manifest = {
        "cache_version": CACHE_VERSION,
        "cache_key": key,
        "root": str(folder),
        "hits": hits,
        "objects": 2 + len(timeframes),
        "start_ms": int(getattr(base, "START_MS")),
        "end_ms": int(getattr(base, "END_MS")),
        "warmup_bars": int(getattr(base, "WARMUP_BARS", 0)),
        "timeframes": list(timeframes),
        "bars": {tf: len(rows) for tf, rows in data.items()},
        "funding_rows": len(funding),
    }
    print("MARKET_CACHE_MANIFEST", manifest, flush=True)
    return meta, data, funding, manifest

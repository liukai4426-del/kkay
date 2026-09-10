"""Exact V1.3.2 six-month runner with memoized repeated calculations only."""
import core
import strategy
import analysis_v132_6m_10x as b

_original_indicators = core.indicators
_original_zones = strategy._zones
_indicator_cache = {}
_zones_cache = {}


def cached_indicators(rows):
    if not rows:
        return _original_indicators(rows)
    key=(int(rows[0]['t']),int(rows[-1]['t']),len(rows))
    value=_indicator_cache.get(key)
    if value is None:
        value=_original_indicators(rows)
        _indicator_cache[key]=value
    return value


def cached_zones(rows,atr,lookback):
    key=(int(rows[0]['t']),int(rows[-1]['t']),len(rows),float(atr),int(lookback))
    value=_zones_cache.get(key)
    if value is None:
        value=_original_zones(rows,atr,lookback)
        _zones_cache[key]=value
    return value


# signal() resolves these globals at call time; this changes only memoization, not strategy behavior.
strategy.indicators=cached_indicators
strategy._zones=cached_zones
b.indicators=cached_indicators

if __name__=='__main__':
    b.main()

#!/usr/bin/env python3
"""V1.6.8 R2 NoEMA1H 180D: 3m BOLL overextension threshold grid.

Only research delta versus the audited NoEMA1H replacement-score setup:
- derive CLOSED 3m bars strictly from audited CLOSED 1m history;
- during the exact current 15m BOLL opportunity, add latched +1 when the
  direction-side 3m BOLL outer-band overextension reaches the configured
  multiple of 3m Wilder ATR(14);
- thresholds are supplied by BOLL3_OVEREXT_ATR_MULT.

All original V1.6.8 R2 hard gates/execution remain unchanged:
15m BOLL opportunity, 4H aligned hard gate/+1, 5m MACD adverse gate and
improving +1, 5m RSI 30-70, 5m volume >=1.20x, score threshold 6,
LIMIT entry, 1H ATR x1 stop, 2R target, no BE/PEE/ADX.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import run_v168_boll5_overext035_180d as prev

THREE_MS = 180_000
OVEREXT_SCORE = 1.0


def _f(row, key, default=0.0):
    try:
        return float(row.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def aggregate_closed_3m(one, now_ms):
    """Aggregate only fully CLOSED UTC-aligned 3m bars from CLOSED 1m bars."""
    if not one:
        return []
    groups = {}
    # 180 one-minute bars = 60 three-minute bars, ample for BOLL20/ATR14.
    for r in one[-180:]:
        t = int(r["t"])
        bucket = (t // THREE_MS) * THREE_MS
        if bucket + THREE_MS > int(now_ms):
            continue
        groups.setdefault(bucket, []).append(r)

    out = []
    for bucket in sorted(groups):
        g = sorted(groups[bucket], key=lambda r: int(r["t"]))
        expected = [bucket, bucket + 60_000, bucket + 120_000]
        if len(g) != 3 or [int(r["t"]) for r in g] != expected:
            continue
        row = {
            "t": bucket,
            "o": _f(g[0], "o"),
            "h": max(_f(r, "h") for r in g),
            "l": min(_f(r, "l") for r in g),
            "c": _f(g[-1], "c"),
            "vol": sum(_f(r, "vol") for r in g),
            "volCcy": sum(_f(r, "volCcy") for r in g),
            "volCcyQuote": sum(_f(r, "volCcyQuote") for r in g),
            "confirm": "1",
        }
        out.append(row)
    return out


def _boll3_state(side, one, now_ms, threshold):
    three = aggregate_closed_3m(one, now_ms)
    if len(three) < 30:
        return False, None
    ind = prev.base.proven.v163.indicators(three)
    bar = three[-1]
    close = float(bar["c"])
    lower = float(ind["lower"])
    upper = float(ind["upper"])
    atr14 = float(ind["atr"])
    if not all(math.isfinite(x) for x in (close, lower, upper, atr14)) or atr14 <= 0.0:
        return False, None
    if side == "做多":
        deviation = max(0.0, lower - close)
    elif side == "做空":
        deviation = max(0.0, close - upper)
    else:
        return False, None
    ratio = deviation / atr14
    diag = {
        "three_bar_t": int(bar["t"]),
        "close": close,
        "lower": lower,
        "upper": upper,
        "atr14": atr14,
        "deviation": deviation,
        "deviation_atr": ratio,
    }
    return ratio >= threshold, diag


def _rescore3_noema(result, opp, one, now_ms, threshold, version, build):
    if not isinstance(result, dict):
        return result
    scores = result.get("scores") or {}
    opp_side = str((opp or {}).get("side") or "") if isinstance(opp, dict) else ""
    opened = False
    if isinstance(opp, dict):
        opened, _, _, _ = prev.base.proven._signal_window_15m(opp, now_ms)
        if opened and opp_side in ("做多", "做空"):
            hit, diag = _boll3_state(opp_side, one, now_ms, threshold)
            if hit:
                opp["boll3_overext_latched"] = True
                opp["boll3_overext_first"] = opp.get("boll3_overext_first") or dict(diag or {})
            if diag:
                opp["boll3_overext_latest"] = dict(diag)

    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue
        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}
        active = bool(opened and side == opp_side and isinstance(opp, dict) and opp.get("boll3_overext_latched"))
        layers["boll3_overextension"] = OVEREXT_SCORE if active else 0.0
        # Remove the old 1H EMA9/26 +1 replacement target.
        layers["ema9_26_1h"] = 0.0
        # Defensive cleanup if a prior wrapper ever injected the old key.
        layers.pop("boll5_overextension", None)
        row["layers"] = layers

        conf = row.setdefault("confirmations", {})
        conf["1H_ema9_26_score_enabled"] = False
        conf["1H_ema9_26_score_value"] = 0.0
        conf["3m_boll_overextension_enabled"] = True
        conf["3m_boll_overextension_threshold_atr14"] = threshold
        conf["3m_boll_overextension_latched"] = active
        if side == opp_side and isinstance(opp, dict):
            first = opp.get("boll3_overext_first") or {}
            latest = opp.get("boll3_overext_latest") or {}
            conf["3m_boll_overextension_first_bar_t"] = first.get("three_bar_t")
            conf["3m_boll_overextension_first_atr_ratio"] = first.get("deviation_atr")
            conf["3m_boll_overextension_latest_atr_ratio"] = latest.get("deviation_atr")

        raw = 0.0
        for value in layers.values():
            try:
                n = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(n):
                raw += n
        total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
        row["raw"] = raw
        row["total"] = total
        row["required"] = prev.base.THRESHOLD
        row["eligible"] = bool(row.get("gate") and total >= prev.base.THRESHOLD)
        row["position_multiplier"] = 1.0 if row["eligible"] else 0.0

    qualified = [s for s, r in scores.items() if isinstance(r, dict) and r.get("eligible")]
    selected = "观望"
    if len(qualified) == 1:
        selected = qualified[0]
    elif len(qualified) == 2:
        a, b = qualified
        av = float(scores[a].get("total") or 0.0)
        bv = float(scores[b].get("total") or 0.0)
        if av != bv:
            selected = a if av > bv else b
    result["side"] = selected
    result.update({
        "strategy_version": version,
        "build": build,
        "1h_ema9_26_score_enabled": False,
        "3m_boll_overextension_score_enabled": True,
        "3m_boll_overextension_threshold_atr14": threshold,
        "3m_boll_overextension_latched_for_15m_opportunity": True,
    })
    return result


def main():
    threshold = float(os.environ.get("BOLL3_OVEREXT_ATR_MULT", "0.10"))
    allowed = {0.10, 0.15, 0.20, 0.25, 0.35}
    if round(threshold, 2) not in allowed:
        raise SystemExit(f"unsupported threshold: {threshold}")
    tag = f"{int(round(threshold * 100)):03d}"
    version = f"1.6.8-BOLL3-OVEREXT{tag}-NOEMA1H-research"
    build = f"1680-boll3-overext{tag}-noema1h-180d"
    out = Path(f"backtest_output_v168_boll3_overext{tag}_noema1h_180d")

    class HistoricalV168Boll3Model(prev.base.HistoricalV168Model):
        VERSION = version
        BUILD = build

        @staticmethod
        def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                     maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                     now_ms=None, allow_new=True):
            result, opp, transition = prev.base.HistoricalV168Model.evaluate(
                hour, quarter, five, one, four,
                opportunity=opportunity,
                stop_atr=stop_atr,
                maker_bps=maker_bps,
                taker_bps=taker_bps,
                slippage_bps=slippage_bps,
                now_ms=now_ms,
                allow_new=allow_new,
            )
            effective_now = int(now_ms if now_ms is not None else (int(one[-1]["t"]) + 60_000 if one else 0))
            result = _rescore3_noema(result, opp, one, effective_now, threshold, version, build)
            return result, opp, transition

        @staticmethod
        def execution_checks(plan, opportunity, score):
            ok, diag, blockers = prev.base.HistoricalV168Model.execution_checks(plan, opportunity, score)
            diag = dict(diag or {})
            diag.update({
                "strategy_version": version,
                "build": build,
                "3m_boll_overextension_score_enabled": True,
                "3m_boll_overextension_threshold_atr14": threshold,
                "3m_boll_overextension_latched": bool(((score or {}).get("confirmations") or {}).get("3m_boll_overextension_latched")),
            })
            return ok, diag, blockers

    def submit3(self, result, now_ms, mark):
        old_model = prev.base.v165runner.production
        prev.base.v165runner.production = HistoricalV168Boll3Model
        try:
            before = self.pending
            prev.base.v165runner._submit_v165(self, result, now_ms, mark)
            if self.pending is not None and self.pending is not before:
                side = str((result.get("opportunity") or {}).get("side") or "")
                row = (result.get("scores") or {}).get(side) or {}
                conf = row.get("confirmations") or {}
                opp = result.get("opportunity") or {}
                first = opp.get("boll3_overext_first") or {}
                latest = opp.get("boll3_overext_latest") or {}
                active = bool(conf.get("3m_boll_overextension_latched"))
                self.pending.factors.update({
                    "strategy_version": version,
                    "build": build,
                    "boll_timeframe": "15m",
                    "signal_window_ms": prev.base.BOLL_WINDOW_MS,
                    "boll_outer_score": prev.base.BOLL_OUTER_SCORE,
                    "4h_trend_state_actual": str(conf.get("4H_trend_state") or ""),
                    "ema9_26_1h_ok": bool(conf.get("1H_ema9_26_trend_ok")),
                    "macd_explicit_adverse": bool(conf.get("5m_macd_adverse")),
                    "boll3_overext_latched": active,
                    "boll3_overext_first_atr_ratio": first.get("deviation_atr"),
                    "boll3_overext_latest_atr_ratio": latest.get("deviation_atr"),
                    "boll3_overext_first_bar_t": first.get("three_bar_t"),
                    "boll3_first_close": first.get("close"),
                    "boll3_first_upper": first.get("upper"),
                    "boll3_first_lower": first.get("lower"),
                    "boll3_first_atr14": first.get("atr14"),
                    "boll3_first_deviation": first.get("deviation"),
                    # Compatibility aliases so the inherited report counts the triggered rows.
                    "boll5_overext035_latched": active,
                    "boll5_overext035_first_atr_ratio": first.get("deviation_atr"),
                    "boll5_overext035_latest_atr_ratio": latest.get("deviation_atr"),
                    "boll5_overext035_first_bar_t": first.get("three_bar_t"),
                    "r2_proven_signal_path": True,
                })
        finally:
            prev.base.v165runner.production = old_model

    # Patch the inherited runner only for this research process.
    prev.OVEREXT_ATR_MULT = threshold
    prev.OVEREXT_SCORE = OVEREXT_SCORE
    prev.VERSION = version
    prev.BUILD = build
    prev.OUT = out
    prev.HistoricalV168Boll5OverextModel = HistoricalV168Boll3Model
    prev._submit = submit3

    def verify_lock(start, end):
        assert prev.DAYS == 180 and (end - start).days == 180
        assert abs(prev.OVEREXT_ATR_MULT - threshold) < 1e-12
        assert prev.OVEREXT_SCORE == 1.0
        assert prev.base.THRESHOLD == 6.0
        assert prev.base.BOLL_WINDOW_MS == 900_000
        assert HistoricalV168Boll3Model.ENTRY_WINDOW_MS == 900_000
        assert HistoricalV168Boll3Model.BOLL_TRIGGER_TIMEFRAME == "15m"
        assert HistoricalV168Boll3Model.FIVE_MINUTE_BOLL_ENABLED is False
        assert prev.base.BOLL_OUTER_SCORE == 2.0
        # 3m aggregation must not include an incomplete current 3m bar.
        sample = [
            {"t": 0, "o": 1, "h": 2, "l": 1, "c": 2},
            {"t": 60_000, "o": 2, "h": 3, "l": 2, "c": 3},
            {"t": 120_000, "o": 3, "h": 4, "l": 3, "c": 4},
            {"t": 180_000, "o": 4, "h": 5, "l": 4, "c": 5},
        ]
        a = aggregate_closed_3m(sample, 240_000)
        assert len(a) == 1 and int(a[0]["t"]) == 0 and float(a[0]["c"]) == 4.0

    prev.verify_lock = verify_lock
    prev.main()

    source_json = out / "result_v168_boll5_overext035_180d.json"
    payload = json.loads(source_json.read_text(encoding="utf-8"))
    payload["research"] = (
        f"V1.6.8 R2 180D: remove 1H EMA9/26 +1, add 3m BOLL overextension "
        f">={threshold:.2f} x 3m ATR14 +1"
    )
    s = payload["strategy_lock"]
    s["1h_ema9_26_score_enabled"] = False
    s["1h_ema9_26_score"] = 0.0
    s.pop("5m_boll_overextension_score", None)
    s.pop("5m_boll_overextension_rule", None)
    s.pop("5m_boll_overextension_threshold_atr14", None)
    s["3m_boll_overextension_score"] = 1.0
    s["3m_boll_overextension_rule"] = (
        f"closed 3m close beyond direction-side 3m outer band by >={threshold:.2f} x Wilder 3m ATR14; "
        "latch until exact 15m opportunity expiry"
    )
    s["3m_boll_overextension_threshold_atr14"] = threshold
    s["3m_bars_source"] = "strict UTC-aligned aggregation from audited closed 1m bars"
    payload["metrics"]["strategy_version"] = version
    payload["metrics"]["build"] = build
    payload["correction"]["only_strategy_delta"] = (
        f"replace 1H EMA9/26 aligned +1 with latched 3m BOLL overextension >= {threshold:.2f} x 3m ATR14 +1"
    )
    payload["grid_threshold_atr14"] = threshold
    payload["overextension_timeframe"] = "3m"
    payload["boll3_overextension"] = payload.pop("boll5_overextension", {})

    target_json = out / f"result_v168_boll3_overext{tag}_noema1h_180d.json"
    target_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if source_json.exists() and source_json != target_json:
        source_json.unlink()

    source_csv = out / "trades_v168_boll5_overext035_180d.csv"
    target_csv = out / f"trades_v168_boll3_overext{tag}_noema1h_180d.csv"
    if source_csv.exists():
        source_csv.rename(target_csv)

    print("GRID3_RESULT", json.dumps({
        "threshold": threshold,
        "trades": payload["metrics"].get("trades"),
        "wins": payload["metrics"].get("wins"),
        "win_rate_pct": payload["metrics"].get("win_rate_pct"),
        "net_pnl": payload["metrics"].get("net_pnl"),
        "profit_factor": payload["metrics"].get("profit_factor"),
        "max_drawdown_pct": payload["metrics"].get("max_drawdown_pct"),
        "boll3_plus1_filled": (payload.get("boll3_overextension") or {}).get("filled_trade_count_with_plus1"),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

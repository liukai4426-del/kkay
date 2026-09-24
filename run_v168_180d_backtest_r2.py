#!/usr/bin/env python3
"""KAYTRADE V1.6.8 Build1680 180D backtest R2.

R2 deliberately reuses the proven V1.6.6 research signal-generation path that
successfully produced trades with 15m BOLL + 15m lifecycle + MACD adverse-only.
Only the confirmed V1.6.8 strategy deltas are applied on top:
- 15m BOLL outer score = 2.0;
- 4H aligned remains mandatory and scores +1;
- 1H EMA9/26 aligned scores +1 (not a hard gate);
- 5m MACD blocks only explicit adverse/opposite; improvement scores +1;
- KDJ score removed;
- 5m RSI 30..70 and Volume <1.20x remain hard gates;
- threshold 6.0, fixed 1x LIMIT, 1H ATR x1 SL, 2R full TP, No-BE.

This is research/backtest-only plumbing. Production V1.6.8 files are not changed.
"""
from __future__ import annotations

import csv
import json
import math
import time
from collections import defaultdict
from datetime import timedelta, timezone, datetime
from pathlib import Path

from core import ema
import run_v166_15m_boll_macd_adverse_only_360d_fast as proven
import run_v166_15m_boll_macd_adverse_only_360d_fast_r2 as proven_r2
import run_v165_1000d_2000u as v165runner
from research_backtest_data_cache import load_market

research = proven.research

DAYS = 180
CAPITAL = 2_000.0
BASE_POSITION_NOTIONAL = 2_000.0
LEVERAGE = 5
RISK_USDT = 20.0
RISK_PCT = 1.0
DAILY_LOSS = 60.0
STOP_ATR = 1.0
REWARD_R = 2.0
COOLDOWN_MS = 0
THRESHOLD = 6.0
BOLL_WINDOW_MS = 15 * 60 * 1000
BOLL_OUTER_SCORE = 2.0
EMA_FAST = 9
EMA_SLOW = 26
FIXED_END = v165runner.FIXED_END
FIXED_START = FIXED_END - timedelta(days=DAYS)
TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H")
OUT = Path("backtest_output_v168_180d")


def _finite(value, default=0.0):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _dedupe(values):
    out = []
    for value in values or []:
        text = str(value)
        if text and text not in out:
            out.append(text)
    return out


def _ema9_26(hour):
    if not hour or len(hour) < EMA_SLOW:
        return None, None
    closes = [float(row["c"]) for row in hour]
    return float(ema(closes, EMA_FAST)[-1]), float(ema(closes, EMA_SLOW)[-1])


def _ema_ok(side, e9, e26):
    if e9 is None or e26 is None:
        return False
    return (e9 > e26) if side == "做多" else (e9 < e26) if side == "做空" else False


def _four_state(four, side):
    try:
        return str(proven.prod.four_hour_state(four, side) or "neutral")
    except Exception:
        return "neutral"


def _decorate_v168(result, opp, hour, four, now_ms):
    if not isinstance(result, dict):
        return result
    scores = result.get("scores") or {}
    e9, e26 = _ema9_26(hour)

    for side in ("做多", "做空"):
        row = scores.get(side)
        if not isinstance(row, dict):
            continue

        own_opp = opp if isinstance(opp, dict) and str(opp.get("side") or "") == side else None
        path = str((own_opp or {}).get("signal_path") or (own_opp or {}).get("path") or "")
        is_outer = bool(own_opp and path in proven.Model.OUTER_PATHS)
        conf = row.setdefault("confirmations", {})
        required = conf.setdefault("required", {})
        layers = row.get("layers") if isinstance(row.get("layers"), dict) else {}

        # V1.6.8 scoring lock.
        layers.pop("kdj5_signal", None)
        layers.pop("volume5", None)
        qstate = _four_state(four, side)
        aligned = qstate == "aligned"
        ema_ok = _ema_ok(side, e9, e26)
        layers["trend4h"] = 1.0 if aligned else 0.0
        layers["ema9_26_1h"] = 1.0 if ema_ok else 0.0
        if is_outer:
            layers["boll_entry"] = BOLL_OUTER_SCORE
        elif "boll_entry" in layers:
            layers["boll_entry"] = 0.0
        row["layers"] = layers

        adverse = proven._macd_adverse(row)
        for key in list(required):
            low = str(key).lower()
            if "macd" in low or "signal_window_5m" in low or "signal_window_4m" in low:
                required.pop(key, None)

        opened = False
        age_ms = remaining_ms = end_ms = 0
        if is_outer:
            opened, age_ms, remaining_ms, end_ms = proven._signal_window_15m(own_opp, now_ms)
            required["signal_window_15m"] = opened
            required["outer_macd_non_adverse"] = not adverse
            required["outer_4h_aligned"] = aligned

        conf["4H_trend_state"] = qstate
        conf["4H_aligned"] = aligned
        conf["4H_hard_gate_required"] = True
        conf["1H_ema9"] = e9
        conf["1H_ema26"] = e26
        conf["1H_ema9_26_trend_ok"] = ema_ok
        conf["5m_macd_adverse"] = adverse
        conf["macd_explicit_adverse"] = adverse
        conf["macd_gate_rule"] = "explicit adverse/opposite only; improvement retains +1 score"
        conf["kdj_score_enabled"] = False
        conf["signal_window_ok"] = opened
        conf["signal_age_ms"] = age_ms
        conf["signal_remaining_ms"] = remaining_ms
        conf["signal_expires_ms"] = end_ms

        blockers = []
        for item in conf.get("blockers") or []:
            text = str(item)
            low = text.lower()
            if "macd" in low and not adverse:
                continue
            if "5m外轨信号" in text and "下一根5m" in text:
                continue
            blockers.append(text)
        if is_outer and adverse:
            blockers.append("V1.6.8：5m MACD明确逆向恶化，禁止开仓")
        if is_outer and not aligned:
            blockers.append("V1.6.8：4H非同向 Hard Gate，禁止开仓")
        if is_outer and not opened:
            blockers.append("V1.6.8：15m BOLL信号已到下一根15m收盘，禁止使用旧信号")
        conf["blockers"] = _dedupe(blockers)

        numeric = []
        for value in layers.values():
            try:
                n = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(n):
                numeric.append(n)
        raw = sum(numeric)
        total = max(0.0, min(10.0, round(raw * 2.0) / 2.0))
        row["raw"] = raw
        row["total"] = total
        row["required"] = THRESHOLD

        # Preserve every proven R2 dynamic gate, then add the V1.6.8 gates.
        base_gate = bool(row.get("gate"))
        if is_outer:
            row["gate"] = bool(base_gate and opened and aligned and not adverse)
            row["eligible"] = bool(row["gate"] and total >= THRESHOLD)
            row["position_multiplier"] = 1.0 if row["eligible"] else 0.0
        else:
            row["gate"] = False
            row["eligible"] = False
            row["position_multiplier"] = 0.0

    qualified = [
        side for side, row in scores.items()
        if isinstance(row, dict) and row.get("eligible")
    ]
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
    result["opportunity"] = dict(opp) if isinstance(opp, dict) else None
    result.update({
        "strategy_version": "1.6.8",
        "build": "1680-r2",
        "threshold": THRESHOLD,
        "boll_trigger_timeframe": "15m",
        "entry_window_ms": BOLL_WINDOW_MS,
        "boll_outer_score": BOLL_OUTER_SCORE,
        "4h_aligned_hard_gate": True,
        "1h_ema9_26_score_enabled": True,
        "kdj_score_enabled": False,
        "macd_gate": "explicit_adverse_only",
    })
    return result


class HistoricalV168Model:
    VERSION = "1.6.8"
    BUILD = "1680"
    THRESHOLD = THRESHOLD
    ENTRY_WINDOW_MS = BOLL_WINDOW_MS
    TIME_WINDOW_ENABLED = True
    BOLL_OUTER_SCORE = BOLL_OUTER_SCORE
    BOLL_TRIGGER_TIMEFRAME = "15m"
    FIVE_MINUTE_BOLL_ENABLED = False
    OUTER_PATHS = proven.Model.OUTER_PATHS
    MIDDLE_PATH = proven.Model.MIDDLE_PATH
    RSI_MIN = proven.Model.RSI_MIN
    RSI_MAX = proven.Model.RSI_MAX
    VOLUME_HARD_GATE = proven.Model.VOLUME_HARD_GATE
    _signal_window = staticmethod(proven._signal_window_15m)
    _finite = staticmethod(proven.Model._finite)
    _path_from = staticmethod(proven.Model._path_from)
    _macd_improving = staticmethod(proven._macd_allowed)

    @staticmethod
    def evaluate(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                 maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0,
                 now_ms=None, allow_new=True):
        result, opp, transition = proven.Model.evaluate(
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
        return _decorate_v168(result, opp, hour, four, effective_now), opp, transition

    @staticmethod
    def execution_checks(plan, opportunity, score):
        ok, diag, blockers = proven_r2.corrected_execution_checks(plan, opportunity, score)
        diag = dict(diag or {})
        blockers = list(blockers or [])
        conf = (score or {}).get("confirmations") or {}
        if str(conf.get("4H_trend_state") or "") != "aligned":
            blockers.append("V1.6.8 R2：4H非同向 Hard Gate")
        blockers = _dedupe(blockers)
        diag.update({
            "strategy_version": "1.6.8",
            "build": "1680-r2",
            "entry_window_ms": BOLL_WINDOW_MS,
            "boll_outer_score": BOLL_OUTER_SCORE,
            "r2_proven_signal_path": True,
        })
        return not blockers, diag, blockers


def _submit_v168_r2(self, result, now_ms, mark):
    old_model = v165runner.production
    v165runner.production = HistoricalV168Model
    try:
        before = self.pending
        v165runner._submit_v165(self, result, now_ms, mark)
        if self.pending is not None and self.pending is not before:
            side = str((result.get("opportunity") or {}).get("side") or "")
            row = (result.get("scores") or {}).get(side) or {}
            conf = row.get("confirmations") or {}
            self.pending.factors.update({
                "strategy_version": "1.6.8",
                "build": "1680-r2",
                "boll_timeframe": "15m",
                "signal_window_ms": BOLL_WINDOW_MS,
                "boll_outer_score": BOLL_OUTER_SCORE,
                "4h_trend_state_actual": str(conf.get("4H_trend_state") or ""),
                "ema9_26_1h_ok": bool(conf.get("1H_ema9_26_trend_ok")),
                "ema9_1h": conf.get("1H_ema9"),
                "ema26_1h": conf.get("1H_ema26"),
                "macd_explicit_adverse": bool(conf.get("5m_macd_adverse")),
                "r2_proven_signal_path": True,
            })
    finally:
        v165runner.production = old_model


def configure():
    start, end = FIXED_START, FIXED_END
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    for name, value in {
        "START": start,
        "END": end,
        "START_MS": start_ms,
        "END_MS": end_ms,
        "CAPITAL": CAPITAL,
        "LEVERAGE": LEVERAGE,
        "RISK_USDT": RISK_USDT,
        "RISK_PCT": RISK_PCT,
        "DAILY_LOSS": DAILY_LOSS,
        "COOLDOWN_MINUTES": 0,
        "STOP_ATR": STOP_ATR,
        "REWARD_R": REWARD_R,
    }.items():
        setattr(research.base, name, value)

    research.START = start
    research.END = end
    research.START_MS = start_ms
    research.END_MS = end_ms
    research.SPLIT_MS = int((start + (end - start) / 2).timestamp() * 1000)
    research.CAPITAL = CAPITAL
    research.LEVERAGE = LEVERAGE
    research.RISK_USDT = RISK_USDT
    research.RISK_PCT = RISK_PCT
    research.DAILY_LOSS = DAILY_LOSS
    research.FIRST_SIGNAL_NOTIONAL = BASE_POSITION_NOTIONAL
    research.STOP_ATR = STOP_ATR
    research.REWARD_R = REWARD_R
    research.COOLDOWN_MS = COOLDOWN_MS
    research.VERSION = "1.6.8"
    research.BUILD = "1680-r2"
    research.model = HistoricalV168Model
    research.Simulator.submit = _submit_v168_r2
    research.Simulator.process_pending = proven._ORIG_PENDING
    research.Simulator.process_exit = proven._ORIG_EXIT
    research.Simulator.finish = proven._ORIG_FINISH
    if hasattr(research, "build1544"):
        research.build1544.PATH_C_ENABLED = False
    return start, end


def verify_lock():
    assert DAYS == 180
    assert (FIXED_END - FIXED_START).days == 180
    assert HistoricalV168Model.VERSION == "1.6.8"
    assert HistoricalV168Model.BUILD == "1680"
    assert HistoricalV168Model.THRESHOLD == 6.0
    assert HistoricalV168Model.ENTRY_WINDOW_MS == 900_000
    assert HistoricalV168Model.BOLL_OUTER_SCORE == 2.0
    assert HistoricalV168Model.BOLL_TRIGGER_TIMEFRAME == "15m"
    assert HistoricalV168Model.FIVE_MINUTE_BOLL_ENABLED is False
    assert proven.Model._macd_improving({"confirmations": {"5m_macd_adverse": False}}) is True
    assert proven.Model._macd_improving({"confirmations": {"5m_macd_adverse": True}}) is False


def _pf(rows):
    gp = sum(max(_finite(r.get("net_pnl")), 0.0) for r in rows)
    gl = -sum(min(_finite(r.get("net_pnl")), 0.0) for r in rows)
    return gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0)


def _stats(rows):
    rows = list(rows)
    n = len(rows)
    wins = sum(_finite(r.get("net_pnl")) > 0 for r in rows)
    net = sum(_finite(r.get("net_pnl")) for r in rows)
    return {
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate_pct": 100.0 * wins / n if n else 0.0,
        "net_pnl": net,
        "profit_factor": _pf(rows),
        "expectancy": net / n if n else 0.0,
    }


def _analysis(rows):
    by_side = defaultdict(list)
    by_score = defaultdict(list)
    by_month = defaultdict(list)
    by_ema = defaultdict(list)
    by_h4 = defaultdict(list)
    for row in rows:
        by_side[str(row.get("side") or "unknown")].append(row)
        by_score[f"{_finite(row.get('score')):.1f}"].append(row)
        ts = int(row.get("entry_time") or 0) / 1000.0
        if ts > 0:
            by_month[datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")].append(row)
        factors = row.get("factors") or row
        by_ema["aligned" if bool(factors.get("ema9_26_1h_ok")) else "not_aligned"].append(row)
        by_h4[str(factors.get("4h_trend_state_actual") or "unknown")].append(row)
    return {
        "by_side": {k: _stats(v) for k, v in by_side.items()},
        "by_score": {k: _stats(v) for k, v in sorted(by_score.items(), key=lambda kv: float(kv[0]))},
        "by_month_utc": {k: _stats(v) for k, v in sorted(by_month.items())},
        "by_1h_ema9_26": {k: _stats(v) for k, v in by_ema.items()},
        "by_4h_state": {k: _stats(v) for k, v in by_h4.items()},
    }


def _write_csv(rows, path):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            item = dict(row)
            for key, value in list(item.items()):
                if isinstance(value, (dict, list, tuple)):
                    item[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            writer.writerow(item)


def main():
    started = time.perf_counter()
    start, end = configure()
    verify_lock()
    OUT.mkdir(parents=True, exist_ok=True)
    print(
        "V168_180D_R2_CONFIG",
        f"start={start.isoformat()}", f"end={end.isoformat()}",
        "signal_path=proven_v166_r2", "boll=15m_outer_only", "boll_score=2.0",
        "signal_lifecycle=15m", "4h=aligned_required_plus1", "ema1h=EMA9_26_plus1",
        "macd=5m_explicit_adverse_only_plus1_if_improving", "kdj_score=0",
        "rsi=5m_30..70", "volume_lt_1.20x=HARD_GATE", "threshold=6",
        "position=1x", "entry=LIMIT", "sl=1H_ATR_x1", "tp=2R", "be=OFF",
        flush=True,
    )

    market_started = time.perf_counter()
    meta, data, funding, cache_manifest = load_market(research.base, TIMEFRAMES)
    market_elapsed = time.perf_counter() - market_started
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    research.cache_patch.prime_one_minute(data["1m"], research.MODEL_WINDOW)

    sim_started = time.perf_counter()
    sim, raw_metrics = research.run(data, ts, meta, funding, variant="baseline")
    sim_elapsed = time.perf_counter() - sim_started
    rows = list(sim.trades)
    metrics = dict(raw_metrics)
    analysis = _analysis(rows)
    elapsed = time.perf_counter() - started

    payload = {
        "research": "KAYTRADE V1.6.8 Build1680 180D R2 on proven V1.6.6 R2 historical signal path",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": DAYS},
        "capital": CAPITAL,
        "strategy_lock": {
            "version": "1.6.8",
            "build": "1680",
            "boll_timeframe": "15m latest CLOSED outer only",
            "five_minute_boll_enabled": False,
            "boll_signal_lifetime_ms": BOLL_WINDOW_MS,
            "boll_signal_lifetime": "until next closed 15m candle",
            "boll_outer_score": BOLL_OUTER_SCORE,
            "4h": "aligned mandatory Hard Gate and +1 score; neutral/opposite blocked",
            "1h_ema": "EMA9>EMA26 long / EMA9<EMA26 short = +1; scoring only",
            "5m_macd": "explicit adverse/opposite blocks; improvement +1 retained",
            "5m_kdj_score": 0.0,
            "5m_rsi": "30-70 Hard Gate",
            "5m_volume": "<1.20x prior-20 average Hard Gate; no score",
            "score_threshold": THRESHOLD,
            "position": "fixed 1x",
            "entry": "LIMIT, audited closed-1m historical fill proxy",
            "stop": "1H ATR x1",
            "tp": "2R full position",
            "break_even": False,
            "early_exit": False,
        },
        "metrics": metrics,
        "analysis": analysis,
        "simulator_stats": dict(sim.stats),
        "top_blockers": sim.blockers.most_common(30),
        "transitions": dict(sim.transitions),
        "cache": cache_manifest,
        "correction": {
            "revision": "R2",
            "proven_signal_path_reused": True,
            "execution_window_missing_now_ms_fix_retained": True,
            "production_strategy_variables_changed": False,
            "reason": "V1.6.8 first 180D runner produced zero trades while the same historical window had valid trades on the proven 15m-BOLL R2 path",
        },
        "timing_sec": {"market_load": market_elapsed, "simulation": sim_elapsed, "total": elapsed},
        "limitations": [
            "Historical LIMIT fills use the audited closed-1m proxy; order-book queue position is unavailable.",
            "R2 changes only historical backtest plumbing; it does not modify production V1.6.8 files.",
        ],
    }
    (OUT / "result_v168_180d.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(rows, OUT / "trades_v168_180d.csv")
    (OUT / "README.txt").write_text(
        "KAYTRADE V1.6.8 Build1680 180D R2 backtest.\n"
        "Historical plumbing uses the proven V1.6.6 R2 15m-BOLL signal path; V1.6.8 score/gate deltas are overlaid exactly.\n",
        encoding="utf-8",
    )
    print("V168_180D_R2_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"V168_180D_R2_TIMING total={elapsed:.2f}s market={market_elapsed:.2f}s sim={sim_elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    main()

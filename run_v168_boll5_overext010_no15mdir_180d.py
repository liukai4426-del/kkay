#!/usr/bin/env python3
"""V1.6.8 R2 180D A/B: BOLL5 overextension 0.10 ATR14, remove 15m from main-direction Hard Gate.

Control basis:
- exact audited V1.6.8 R2 NoEMA1H replacement-score path;
- 5m BOLL overextension >= 0.10 * 5m Wilder ATR(14) adds +1 and latches for the
  current 15m BOLL opportunity lifecycle;
- 1H EMA9/26 +1 remains removed;
- 4H aligned Hard Gate +1 remains;
- 15m EMA50 direction-move +1 scoring remains;
- 5m MACD adverse-only gate / improving +1, RSI/Volume gates remain;
- threshold 6.0, fixed 1x LIMIT, 1H ATR x1 SL, 2R full TP, no PEE/ADX/BE.

Only A/B delta in this runner:
- original main direction requires BOTH 1H trend structure and 15m EMA20/EMA50
  alignment;
- this variant removes ONLY the 15m EMA20/EMA50 alignment from that Hard Gate;
- main direction is therefore decided by 1H only:
  long: close > EMA200, EMA20 > EMA50, 1H up;
  short: close < EMA200, EMA20 < EMA50, 1H down.

Production files are unchanged.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import run_v168_boll5_overext035_noema1h_180d as x

THRESHOLD_ATR = 0.10
DAYS = 180
VERSION = "1.6.8-BOLL5-OVEREXT010-NOEMA1H-NO15MDIR-research"
BUILD = "1680-boll5-overext010-noema1h-no15mdir-180d"
OUT = Path("backtest_output_v168_boll5_overext010_noema1h_no15mdir_180d")


def _trend_direction_1h_only(hour, quarter):
    """Keep the existing 1H trend definition, remove only 15m EMA20/50 alignment."""
    h = x.prev.base.proven.v163.indicators(hour)
    hc = float(hour[-1]["c"])
    long_ok = (
        hc > float(h["ema200"])
        and float(h["ema20"]) > float(h["ema50"])
        and bool(h["up"])
    )
    short_ok = (
        hc < float(h["ema200"])
        and float(h["ema20"]) < float(h["ema50"])
        and bool(h["down"])
    )
    if long_ok and not short_ok:
        return "做多"
    if short_ok and not long_ok:
        return "做空"
    return "观望"


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fifteen_alignment_from_result(result, side):
    m = (result or {}).get("m") or {}
    e20 = _f(m.get("ema20"), 0.0)
    e50 = _f(m.get("ema50"), 0.0)
    if e20 == 0.0 and e50 == 0.0:
        return None
    if side == "做多":
        return e20 > e50
    if side == "做空":
        return e20 < e50
    return None


def install_variant():
    # Exact prior replacement-score install, then patch only the requested A/B delta.
    x.prev.OVEREXT_ATR_MULT = THRESHOLD_ATR
    x.VERSION = VERSION
    x.BUILD = BUILD
    x.OUT = OUT
    x.prev.VERSION = VERSION
    x.prev.BUILD = BUILD
    x.prev.OUT = OUT
    x.prev.HistoricalV168Boll5OverextModel.VERSION = VERSION
    x.prev.HistoricalV168Boll5OverextModel.BUILD = BUILD

    signal_core = x.prev.base.proven.prod.base.signal_core  # v153_model module
    signal_core.trend_direction = _trend_direction_1h_only

    # Capture whether each submitted trade would have passed the removed 15m
    # EMA20/50 direction filter, for clean post-run attribution.
    original_submit = x.prev._submit

    def submit_with_direction_diag(self, result, now_ms, mark):
        before = self.pending
        original_submit(self, result, now_ms, mark)
        if self.pending is not None and self.pending is not before:
            side = str((result.get("opportunity") or {}).get("side") or "")
            aligned15 = _fifteen_alignment_from_result(result, side)
            self.pending.factors.update({
                "main_direction_gate": "1H_only",
                "15m_direction_hard_gate_removed": True,
                "15m_ema20_ema50_would_align": aligned15,
            })

    x.prev._submit = submit_with_direction_diag

    def verify_prev_lock(start, end):
        assert x.prev.DAYS == DAYS and (end - start).days == DAYS
        assert abs(x.prev.OVEREXT_ATR_MULT - THRESHOLD_ATR) < 1e-12
        assert x.prev.OVEREXT_SCORE == 1.0
        assert x.prev.base.THRESHOLD == 6.0
        assert x.prev.base.BOLL_WINDOW_MS == 900_000
        assert x.prev.HistoricalV168Boll5OverextModel.ENTRY_WINDOW_MS == 900_000
        assert x.prev.HistoricalV168Boll5OverextModel.BOLL_TRIGGER_TIMEFRAME == "15m"
        assert x.prev.base.BOLL_OUTER_SCORE == 2.0
        # 1H-only direction sanity checks on synthetic indicator outputs are
        # covered structurally here by asserting the patched function identity.
        assert signal_core.trend_direction is _trend_direction_1h_only

    def verify_replacement_lock():
        assert x.prev.DAYS == DAYS
        assert abs(x.prev.OVEREXT_ATR_MULT - THRESHOLD_ATR) < 1e-12
        assert x.prev.OVEREXT_SCORE == 1.0
        assert x.prev.base.THRESHOLD == 6.0
        assert x.prev.base.BOLL_WINDOW_MS == 900_000
        assert signal_core.trend_direction is _trend_direction_1h_only

    x.prev.verify_lock = verify_prev_lock
    x.verify_replacement_lock = verify_replacement_lock


def _rewrite_trade_csv(path):
    if not path.exists():
        return {"trades": 0, "would_fail_removed_15m_gate": 0}
    with path.open("r", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    fail = 0
    for row in rows:
        raw = str(row.get("15m_ema20_ema50_would_align") or "").strip().lower()
        if raw in ("false", "0"):
            fail += 1
    return {"trades": len(rows), "would_fail_removed_15m_gate": fail}


def main():
    install_variant()
    x.main()

    # x.main writes the historical 035 filenames; normalize them for this A/B.
    source_json = OUT / "result_v168_boll5_overext035_noema1h_180d.json"
    target_json = OUT / "result_v168_boll5_overext010_noema1h_no15mdir_180d.json"
    source_csv = OUT / "trades_v168_boll5_overext035_noema1h_180d.csv"
    target_csv = OUT / "trades_v168_boll5_overext010_noema1h_no15mdir_180d.csv"

    if source_csv.exists() and source_csv != target_csv:
        source_csv.rename(target_csv)

    payload = json.loads(source_json.read_text(encoding="utf-8"))
    payload["research"] = (
        "V1.6.8 R2 180D: NoEMA1H + BOLL5 overextension >=0.10 ATR14 +1; "
        "remove only 15m EMA20/50 alignment from main-direction Hard Gate; retain 1H direction gate"
    )
    lock = payload["strategy_lock"]
    lock["1h_ema9_26_score_enabled"] = False
    lock["1h_ema9_26_score"] = 0.0
    lock["5m_boll_overextension_score"] = 1.0
    lock["5m_boll_overextension_threshold_atr14"] = THRESHOLD_ATR
    lock["main_direction_hard_gate"] = "1H only"
    lock["1h_main_direction_rule"] = (
        "long: close>EMA200 and EMA20>EMA50 and up; "
        "short: close<EMA200 and EMA20<EMA50 and down"
    )
    lock["15m_ema20_ema50_direction_hard_gate"] = False
    lock["15m_ema50_move_score_retained"] = True
    payload["metrics"]["strategy_version"] = VERSION
    payload["metrics"]["build"] = BUILD
    payload["grid_threshold_atr14"] = THRESHOLD_ATR
    payload["direction_gate_ab"] = _rewrite_trade_csv(target_csv)
    payload["correction"]["only_strategy_delta"] = (
        "versus BOLL5 0.10 NoEMA1H control: remove 15m EMA20/EMA50 alignment from main-direction Hard Gate only; "
        "1H direction gate and 15m EMA50 move +1 score remain"
    )
    target_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if source_json != target_json and source_json.exists():
        source_json.unlink()

    m = payload["metrics"]
    print("NO15MDIR_RESULT", json.dumps({
        "trades": m.get("trades"),
        "wins": m.get("wins"),
        "win_rate_pct": m.get("win_rate_pct"),
        "net_pnl": m.get("net_pnl"),
        "profit_factor": m.get("profit_factor"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "boll5_plus1_filled": (payload.get("boll5_overextension") or {}).get("filled_trade_count_with_plus1"),
        "direction_gate_ab": payload.get("direction_gate_ab"),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

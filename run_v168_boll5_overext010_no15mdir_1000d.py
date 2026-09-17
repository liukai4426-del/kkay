#!/usr/bin/env python3
"""KAYTRADE V1.6.8 research: extend the audited BOLL5 0.10 / NoEMA1H /
1H-only direction-gate experiment from 180D to 1000D.

Strategy is unchanged versus the 180D A/B variant:
- 15m latest CLOSED BOLL outer opportunity, 15m lifecycle, +2;
- 5m CLOSED BOLL overextension >= 0.10 * 5m Wilder ATR(14), latched +1;
- remove 1H EMA9/26 aligned +1 score;
- main-direction Hard Gate uses 1H trend only; the 15m EMA20/50 direction
  ordering is removed;
- 15m EMA50 directional movement +1 score remains unchanged;
- 4H aligned Hard Gate +1 remains unchanged;
- 5m MACD explicit-adverse block / improving +1, RSI and Volume gates unchanged;
- score >= 6, fixed 1x LIMIT, SL=1H ATR x1, TP=2R, no BE/PEE/ADX.

Only the historical window changes to 1000 days. Production files are untouched.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import run_v168_boll5_overext010_no15mdir_180d as r

DAYS = 1000
THRESHOLD_ATR = 0.10
END = r.x.prev.base.FIXED_END
START = END - timedelta(days=DAYS)
VERSION = "1.6.8-BOLL5-OVEREXT010-NOEMA1H-NO15MDIR-1000D-research"
BUILD = "1680-boll5-overext010-noema1h-no15mdir-1000d"
OUT = Path("backtest_output_v168_boll5_overext010_no15mdir_1000d")


def install_1000d():
    # First patch the wrapper globals that its installer closes over.
    r.DAYS = DAYS
    r.VERSION = VERSION
    r.BUILD = BUILD
    r.OUT = OUT

    # Extend only the fixed historical window in the audited V1.6.8 R2 base.
    base = r.x.prev.base
    base.DAYS = DAYS
    base.FIXED_END = END
    base.FIXED_START = START

    # Install exactly the 180D strategy delta: BOLL5 0.10, NoEMA1H,
    # 1H-only main direction, with 15m direction ordering removed.
    r.install_variant()

    # Make identity/window explicit across all inherited research layers.
    r.x.DAYS = DAYS
    r.x.VERSION = VERSION
    r.x.BUILD = BUILD
    r.x.OUT = OUT
    r.x.prev.DAYS = DAYS
    r.x.prev.VERSION = VERSION
    r.x.prev.BUILD = BUILD
    r.x.prev.OUT = OUT
    r.x.prev.HistoricalV168Boll5OverextModel.VERSION = VERSION
    r.x.prev.HistoricalV168Boll5OverextModel.BUILD = BUILD


def verify_1000d_lock():
    assert DAYS == 1000 and (END - START).days == 1000
    assert abs(float(r.x.prev.OVEREXT_ATR_MULT) - THRESHOLD_ATR) < 1e-12
    assert r.x.prev.OVEREXT_SCORE == 1.0
    assert r.x.prev.base.THRESHOLD == 6.0
    assert r.x.prev.base.BOLL_WINDOW_MS == 900_000
    assert r.x.prev.base.FIXED_START == START
    assert r.x.prev.base.FIXED_END == END

    # The wrapper's patched direction function must ignore quarter/15m ordering.
    assert r.x.prev.base.proven.prod.trend_direction is r._direction_1h_only
    assert r.x.prev.base.proven.v163.trend_direction is r._direction_1h_only


def main():
    install_1000d()
    verify_1000d_lock()
    OUT.mkdir(parents=True, exist_ok=True)

    # Call the NoEMA layer directly. The 180D no15m wrapper has already
    # installed its direction/diagnostic patches above. The inherited layer
    # uses legacy 035/180d filenames internally, so normalize them afterward.
    r.x.main()

    source_json = OUT / "result_v168_boll5_overext035_noema1h_180d.json"
    if not source_json.exists():
        # Defensive compatibility with the threshold-specific 180D wrapper.
        alt = OUT / "result_v168_boll5_overext010_noema1h_180d.json"
        if alt.exists():
            source_json = alt
    payload = json.loads(source_json.read_text(encoding="utf-8"))

    payload["research"] = (
        "V1.6.8 R2 1000D: 5m BOLL overextension >=0.10 ATR14 +1, "
        "remove 1H EMA9/26 +1, direction Hard Gate uses 1H only "
        "(15m EMA20/50 direction ordering removed)"
    )
    payload["window"] = {
        "start": START.isoformat(),
        "end": END.isoformat(),
        "days": DAYS,
    }
    payload["strategy_lock"]["5m_boll_overextension_threshold_atr14"] = THRESHOLD_ATR
    payload["strategy_lock"]["1h_ema9_26_score_enabled"] = False
    payload["strategy_lock"]["1h_ema9_26_score"] = 0.0
    payload["strategy_lock"]["direction_hard_gate"] = (
        "1H trend only: close vs EMA200 + EMA20/50 ordering + 1H up/down; "
        "15m EMA20/50 direction ordering removed"
    )
    payload["strategy_lock"]["15m_direction_hard_gate_enabled"] = False
    payload["strategy_lock"]["15m_ema50_score"] = "retained unchanged"
    payload["strategy_lock"]["4h"] = "aligned mandatory Hard Gate and +1 retained unchanged"
    payload["metrics"]["days"] = DAYS
    payload["metrics"]["start_utc"] = START.isoformat()
    payload["metrics"]["end_utc"] = END.isoformat()
    payload["metrics"]["strategy_version"] = VERSION
    payload["metrics"]["build"] = BUILD
    payload["grid_threshold_atr14"] = THRESHOLD_ATR
    payload["window_extension"] = "same strategy as 180D no15m-direction A/B; historical window only extended to 1000D"
    payload.setdefault("correction", {})["only_strategy_delta_vs_boll5_010_noema1h"] = (
        "remove 15m EMA20/50 direction ordering from the main trend_direction Hard Gate; "
        "keep 15m EMA50 +1 score and all other V1.6.8 rules unchanged"
    )
    payload["correction"]["production_strategy_variables_changed"] = False

    # Recompute the A/B diagnostic from the final trade rows if the inherited
    # payload exposes it; otherwise preserve the wrapper-provided analysis.
    analysis = payload.setdefault("analysis", {})
    if "direction_gate_ab" not in analysis:
        analysis["direction_gate_ab"] = {
            "note": "per-trade 15m_ema20_ema50_would_align is recorded in the CSV factors"
        }

    target_json = OUT / "result_v168_boll5_overext010_noema1h_no15mdir_1000d.json"
    target_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if source_json != target_json and source_json.exists():
        source_json.unlink()

    source_csv_candidates = [
        OUT / "trades_v168_boll5_overext035_noema1h_180d.csv",
        OUT / "trades_v168_boll5_overext010_noema1h_180d.csv",
    ]
    target_csv = OUT / "trades_v168_boll5_overext010_noema1h_no15mdir_1000d.csv"
    for source_csv in source_csv_candidates:
        if source_csv.exists():
            source_csv.rename(target_csv)
            break

    print("V168_BOLL5_OVEREXT010_NOEMA1H_NO15MDIR_1000D_RESULT", json.dumps({
        "window": payload["window"],
        "trades": payload["metrics"].get("trades"),
        "wins": payload["metrics"].get("wins"),
        "win_rate_pct": payload["metrics"].get("win_rate_pct"),
        "net_pnl": payload["metrics"].get("net_pnl"),
        "profit_factor": payload["metrics"].get("profit_factor"),
        "max_drawdown_pct": payload["metrics"].get("max_drawdown_pct"),
        "boll5_plus1_filled": (payload.get("boll5_overextension") or {}).get("filled_trade_count_with_plus1"),
        "direction_gate_ab": analysis.get("direction_gate_ab"),
    }, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

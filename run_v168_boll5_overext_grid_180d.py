#!/usr/bin/env python3
"""Run V1.6.8 R2 NoEMA1H 180D with a parameterized 5m BOLL overextension threshold.

Only variable: BOLL5_OVEREXT_ATR_MULT (expected 0.10/0.15/0.20/0.25).
All other strategy and execution settings are inherited unchanged from the audited
V1.6.8 R2 replacement-score experiment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import run_v168_boll5_overext035_noema1h_180d as x


def main():
    threshold = float(os.environ.get("BOLL5_OVEREXT_ATR_MULT", "0.20"))
    allowed = {0.10, 0.15, 0.20, 0.25}
    if round(threshold, 2) not in allowed:
        raise SystemExit(f"unsupported threshold: {threshold}")

    tag = f"{int(round(threshold * 100)):03d}"
    version = f"1.6.8-BOLL5-OVEREXT{tag}-NOEMA1H-research"
    build = f"1680-boll5-overext{tag}-noema1h-180d"
    out = Path(f"backtest_output_v168_boll5_overext{tag}_noema1h_180d")

    # Patch only the research threshold/identity. Scoring direction and all
    # inherited V1.6.8 R2 gates remain untouched.
    x.prev.OVEREXT_ATR_MULT = threshold
    x.VERSION = version
    x.BUILD = build
    x.OUT = out
    x.prev.VERSION = version
    x.prev.BUILD = build
    x.prev.OUT = out
    x.prev.HistoricalV168Boll5OverextModel.VERSION = version
    x.prev.HistoricalV168Boll5OverextModel.BUILD = build

    def verify_prev_lock(start, end):
        assert x.prev.DAYS == 180 and (end - start).days == 180
        assert abs(x.prev.OVEREXT_ATR_MULT - threshold) < 1e-12
        assert x.prev.OVEREXT_SCORE == 1.0
        assert x.prev.base.THRESHOLD == 6.0
        assert x.prev.base.BOLL_WINDOW_MS == 900_000
        assert x.prev.HistoricalV168Boll5OverextModel.ENTRY_WINDOW_MS == 900_000
        assert x.prev.HistoricalV168Boll5OverextModel.BOLL_TRIGGER_TIMEFRAME == "15m"
        assert x.prev.HistoricalV168Boll5OverextModel.FIVE_MINUTE_BOLL_ENABLED is False
        assert x.prev.base.BOLL_OUTER_SCORE == 2.0

    def verify_replacement_lock():
        assert x.prev.DAYS == 180
        assert abs(x.prev.OVEREXT_ATR_MULT - threshold) < 1e-12
        assert x.prev.OVEREXT_SCORE == 1.0
        assert x.prev.base.THRESHOLD == 6.0
        assert x.prev.base.BOLL_WINDOW_MS == 900_000

    x.prev.verify_lock = verify_prev_lock
    x.verify_replacement_lock = verify_replacement_lock

    x.main()

    # Correct threshold-specific metadata and produce unambiguous filenames.
    source_json = out / "result_v168_boll5_overext035_noema1h_180d.json"
    payload = json.loads(source_json.read_text(encoding="utf-8"))
    payload["research"] = (
        f"V1.6.8 R2 180D: remove 1H EMA9/26 +1, add 5m BOLL overextension "
        f">={threshold:.2f} ATR14 +1"
    )
    payload["strategy_lock"]["5m_boll_overextension_threshold_atr14"] = threshold
    payload["metrics"]["strategy_version"] = version
    payload["metrics"]["build"] = build
    payload["correction"]["only_strategy_delta"] = (
        f"replace 1H EMA9/26 aligned +1 with latched 5m BOLL overextension "
        f">={threshold:.2f} x ATR14 +1"
    )
    payload["grid_threshold_atr14"] = threshold

    target_json = out / f"result_v168_boll5_overext{tag}_noema1h_180d.json"
    target_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if source_json != target_json and source_json.exists():
        source_json.unlink()

    source_csv = out / "trades_v168_boll5_overext035_noema1h_180d.csv"
    target_csv = out / f"trades_v168_boll5_overext{tag}_noema1h_180d.csv"
    if source_csv.exists() and source_csv != target_csv:
        source_csv.rename(target_csv)

    print(
        "GRID_RESULT",
        json.dumps({
            "threshold": threshold,
            "trades": payload["metrics"].get("trades"),
            "wins": payload["metrics"].get("wins"),
            "win_rate_pct": payload["metrics"].get("win_rate_pct"),
            "net_pnl": payload["metrics"].get("net_pnl"),
            "profit_factor": payload["metrics"].get("profit_factor"),
            "max_drawdown_pct": payload["metrics"].get("max_drawdown_pct"),
            "boll5_plus1_filled": (payload.get("boll5_overextension") or {}).get("filled_trade_count_with_plus1"),
        }, ensure_ascii=False),
        flush=True,
    )


if __name__ == "__main__":
    main()

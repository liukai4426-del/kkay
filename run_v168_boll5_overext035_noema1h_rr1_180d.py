#!/usr/bin/env python3
"""V1.6.8 R2 180D conditional RR test.

Scoring:
- remove 1H EMA9/26 aligned +1 score;
- add latched +1 when CLOSED 5m close overextends beyond direction-side
  5m BOLL outer band by >= 0.35 * Wilder ATR(14) during the exact current
  15m BOLL opportunity lifecycle.

Conditional exit geometry:
- if the BOLL5 overextension +1 is latched for the submitted order, keep the
  original 1H ATR x1 stop and set TP to 1R (risk:reward = 1:1);
- otherwise keep original V1.6.8 TP at 2R (risk:reward = 1:2).

All other V1.6.8 R2 gates and execution behavior are unchanged. No PEE/ADX/BE.
"""
from __future__ import annotations

import json
from pathlib import Path

import run_v168_boll5_overext035_noema1h_180d as src

DAYS = 180
VERSION = "1.6.8-BOLL5-OVEREXT035-NOEMA1H-RR1-research"
BUILD = "1680-boll5-overext035-noema1h-rr1-180d"
OUT = Path("backtest_output_v168_boll5_overext035_noema1h_rr1_180d")
_ORIGINAL_SUBMIT = src.prev._submit


def _boll5_active(result):
    opp = result.get("opportunity") if isinstance(result, dict) else None
    side = str((opp or {}).get("side") or "") if isinstance(opp, dict) else ""
    row = ((result.get("scores") or {}).get(side) or {}) if isinstance(result, dict) else {}
    conf = row.get("confirmations") or {}
    return bool(conf.get("5m_boll_overextension_latched"))


def _conditional_rr_submit(self, result, now_ms, mark):
    active = _boll5_active(result)
    old_reward = src.prev.base.research.REWARD_R
    try:
        src.prev.base.research.REWARD_R = 1.0 if active else 2.0
        before = self.pending
        _ORIGINAL_SUBMIT(self, result, now_ms, mark)
        if self.pending is not None and self.pending is not before:
            risk = abs(float(self.pending.limit) - float(self.pending.stop))
            reward = abs(float(self.pending.target) - float(self.pending.limit))
            rr = reward / risk if risk > 0 else 0.0
            self.pending.factors.update({
                "conditional_rr_rule_enabled": True,
                "boll5_overext035_rr1_triggered": bool(active),
                "reward_r_applied": 1.0 if active else 2.0,
                "risk_reward_ratio": rr,
            })
            if active:
                assert abs(rr - 1.0) < 1e-9, f"conditional RR1 target mismatch: {rr}"
            else:
                assert abs(rr - 2.0) < 1e-9, f"baseline RR2 target mismatch: {rr}"
    finally:
        src.prev.base.research.REWARD_R = old_reward


def install():
    src.install()
    src.VERSION = VERSION
    src.BUILD = BUILD
    src.OUT = OUT
    src.prev.VERSION = VERSION
    src.prev.BUILD = BUILD
    src.prev.OUT = OUT
    src.prev.HistoricalV168Boll5OverextModel.VERSION = VERSION
    src.prev.HistoricalV168Boll5OverextModel.BUILD = BUILD
    src.prev._submit = _conditional_rr_submit


def verify_lock():
    assert src.prev.DAYS == 180
    assert src.prev.OVEREXT_ATR_MULT == 0.35
    assert src.prev.OVEREXT_SCORE == 1.0
    assert src.prev.base.THRESHOLD == 6.0
    assert src.prev.base.BOLL_WINDOW_MS == 900_000
    assert src.prev.base.REWARD_R == 2.0
    # Replacement-score lock inherited from the prior research runner.
    src.verify_replacement_lock()
    # Active flag must only come from the BOLL5 overextension confirmation.
    yes = {"opportunity":{"side":"做多"},"scores":{"做多":{"confirmations":{"5m_boll_overextension_latched":True}}}}
    no = {"opportunity":{"side":"做多"},"scores":{"做多":{"confirmations":{"5m_boll_overextension_latched":False}}}}
    assert _boll5_active(yes) is True
    assert _boll5_active(no) is False


def main():
    install()
    verify_lock()
    # Run the audited BOLL5 runner with the no-EMA rescore and conditional submit installed.
    src.prev.main()

    old_json = OUT / "result_v168_boll5_overext035_180d.json"
    new_json = OUT / "result_v168_boll5_overext035_noema1h_rr1_180d.json"
    payload = json.loads(old_json.read_text(encoding="utf-8"))
    payload["research"] = (
        "V1.6.8 R2 180D: remove 1H EMA9/26 +1; BOLL5 overextension >=0.35 ATR14 +1; "
        "orders with BOLL5 +1 use 1H ATR x1 SL and 1R TP, all others retain 2R TP"
    )
    lock = payload["strategy_lock"]
    lock["1h_ema9_26_score_enabled"] = False
    lock["1h_ema9_26_score"] = 0.0
    lock["5m_boll_overextension_score"] = 1.0
    lock["5m_boll_overextension_threshold_atr14"] = 0.35
    lock["conditional_rr"] = {
        "when": "5m_boll_overextension_latched == true",
        "stop": "1H ATR x1 unchanged",
        "target_when_triggered": "1R",
        "target_otherwise": "2R",
    }
    payload["correction"]["only_strategy_delta"] = (
        "replace 1H EMA9/26 aligned +1 with latched 5m BOLL overextension >=0.35 x ATR14 +1; "
        "if that BOLL5 score is latched on the submitted order, TP=1R while SL remains 1H ATR x1; otherwise TP=2R"
    )
    new_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    old_json.unlink()

    old_csv = OUT / "trades_v168_boll5_overext035_180d.csv"
    new_csv = OUT / "trades_v168_boll5_overext035_noema1h_rr1_180d.csv"
    if old_csv.exists():
        old_csv.rename(new_csv)
    print("V168_BOLL5_OVEREXT035_NOEMA1H_RR1_180D_RESULT", json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

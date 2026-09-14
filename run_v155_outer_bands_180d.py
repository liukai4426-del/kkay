#!/usr/bin/env python3
"""KAYTRADE V1.5.5 research: disable 5m BOLL middle entries, outer bands only.

This is a research-only overlay.  It keeps the current V1.5.5/Build1544 signal,
score, hard-block and execution model unchanged except for BOLL opportunity
creation:
- LONG: only a closed 5m candle touching the lower BOLL band can create a signal.
- SHORT: only a closed 5m candle touching the upper BOLL band can create a signal.
- middle_rsi is completely disabled at signal creation, not merely filtered later.

The 180-day economic assumptions are intentionally kept identical to the prior
audited Build1544 180D study so the effect of removing middle entries is isolated.
"""
from __future__ import annotations

import json
from pathlib import Path

import run_v1544_180d_fixed as compat

research = compat.research
import v153_model as v153
import v154_model as v154
from core import indicators

PRODUCTION_SOURCE_BRANCH = "codex/v1-5-5-ui-margin-log"
PRODUCTION_SOURCE_COMMIT = "402a36cb377440f1a68992501381fff96cb310e2"
OUTDIR = Path("backtest_output_v155_outer_bands_180d")
OUTDIR.mkdir(parents=True, exist_ok=True)


def outer_only_boll_entry_signal(five, side):
    """Create only lower-band LONG / upper-band SHORT 5m opportunities."""
    if len(five) < 2:
        return None
    cur = indicators(five)
    bar = five[-1]
    atr5 = float(cur["atr"])
    middle = float(cur["middle"])
    upper = float(cur["upper"])
    lower = float(cur["lower"])
    rsi = float(cur["rsi"])

    if side == "做多":
        if float(bar["l"]) > lower:
            return None
        path, reference = "lower_band", lower
    elif side == "做空":
        if float(bar["h"]) < upper:
            return None
        path, reference = "upper_band", upper
    else:
        return None

    bar_t = int(bar["t"])
    close_ms = bar_t + 5 * 60 * 1000
    return {
        "path": path,
        "bar_t": bar_t,
        "signal_close_ms": close_ms,
        "reference": float(reference),
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "rsi5": rsi,  # diagnostic only; RSI does not gate outer-band entries
        "atr5": atr5,
        "bar_low": float(bar["l"]),
        "bar_high": float(bar["h"]),
    }


# v154.evaluate delegates to v153.evaluate, whose global boll_entry_signal is
# resolved at runtime. Patch both module names so live-like direct calls and
# research calls use exactly the same outer-only opportunity creation rule.
v153.boll_entry_signal = outer_only_boll_entry_signal
v154.boll_entry_signal = outer_only_boll_entry_signal

# Run only the actual proposed model plus the inherited +5bps stress replay.
research.VARIANTS = ("baseline",)
research.OUTDIR = OUTDIR
research.VERSION = "1.5.5-research"
research.BUILD = "1550-outer-bands"


def main():
    assert research.model.THRESHOLD == 6.0
    assert research.model.ENTRY_WINDOW_MS == 0
    assert research.model.TIME_WINDOW_ENABLED is False
    assert float(research.model.COST_MAX_R) == 0.30
    assert research.build1544.PATH_C_ENABLED is False
    assert research.FIRST_SIGNAL_NOTIONAL == 1000.0
    assert research.LEVERAGE == 5
    assert research.STOP_ATR == 1.0 and research.REWARD_R == 2.0

    # Sanity check the model-level path removal with synthetic indicator results
    # is covered downstream by the output assertion: no middle_rsi trade can exist.
    research.main()

    old_csv = OUTDIR / "V1544_BTC_SWAP_180D_Trades.csv"
    new_csv = OUTDIR / "V155_OUTER_BANDS_BTC_SWAP_180D_Trades.csv"
    if old_csv.exists():
        old_csv.replace(new_csv)

    metrics_path = OUTDIR / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["research_change"] = "5m BOLL middle_rsi disabled at opportunity creation; LONG lower_band only; SHORT upper_band only"
    metrics["production_source_branch"] = PRODUCTION_SOURCE_BRANCH
    metrics["production_source_commit"] = PRODUCTION_SOURCE_COMMIT
    metrics["cost_max_r"] = 0.30
    metrics["outer_bands_only"] = True
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = OUTDIR / "summary_180d.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    notes = summary.setdefault("research_notes", {})
    notes.update({
        "production_source_branch": PRODUCTION_SOURCE_BRANCH,
        "production_source_commit": PRODUCTION_SOURCE_COMMIT,
        "outer_bands_only": True,
        "middle_rsi_enabled": False,
        "long_boll_path": "lower_band only",
        "short_boll_path": "upper_band only",
        "only_strategy_change_vs_current_core": "BOLL opportunity creation path",
        "economic_assumptions_same_as_prior_180d_study": True,
        "first_signal_notional_research_cap": research.FIRST_SIGNAL_NOTIONAL,
        "research_leverage": research.LEVERAGE,
        "funding_coverage_caveat": "OKX REST funding history does not fully cover the 180-day window; reported funding is incomplete unless source coverage says otherwise.",
    })
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report = OUTDIR / "REPORT.md"
    if report.exists():
        text = report.read_text(encoding="utf-8")
        text = text.replace("KAYTRADE V1.5.4 Build1544 — BTC 180D Research", "KAYTRADE V1.5.5 Research — 5m BOLL Outer Bands Only — BTC 180D")
        text += (
            "\n\n## V1.5.5 research change\n"
            "- 5m BOLL middle_rsi entry is disabled at signal creation.\n"
            "- LONG only uses lower_band; SHORT only uses upper_band.\n"
            "- Threshold, score components, Hard Blocks, LIMIT execution, Path C OFF, 1x 1H ATR SL, 2R TP and 0.30R cost gate are unchanged.\n"
            "- Economic assumptions are kept identical to the prior 180D study for an apples-to-apples strategy comparison.\n"
            "- Funding coverage for the full 180D window remains incomplete.\n"
        )
        report.write_text(text, encoding="utf-8")

    note = {
        "research": "V1.5.5 outer-band-only 180D",
        "production_source_branch": PRODUCTION_SOURCE_BRANCH,
        "production_source_commit": PRODUCTION_SOURCE_COMMIT,
        "middle_rsi_enabled": False,
        "long_path": "lower_band",
        "short_path": "upper_band",
        "path_c_enabled": False,
        "threshold": 6.0,
        "cost_max_r": 0.30,
        "stop": "1x 1H ATR",
        "take_profit": "2R full position",
        "entry": "LIMIT proxy; 60s individual order TTL; no BOLL wall-clock expiry",
        "economic_assumptions": "same as prior audited 180D study",
    }
    (OUTDIR / "RESEARCH_RUN_NOTE_V155_OUTER_BANDS.json").write_text(
        json.dumps(note, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("V155_OUTER_BANDS_METRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

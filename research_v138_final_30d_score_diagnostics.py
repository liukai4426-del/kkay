#!/usr/bin/env python3
"""Score-component diagnostics for the exact V1.3.8 FINAL 30-day BTC backtest.

Trading behavior is unchanged. This script only records the score composition that
already exists in V138FinalMarket.score() and expands the 15m setup sub-components.
It uses the exact baseline window 2026-08-13 10:57 UTC -> 2026-09-12 10:57 UTC
with the normal 3-loss -> 6-hour pause ENABLED.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import research_v138_final_30d_backtest as d30

v138 = d30.v138
base = v138.base

START = datetime(2026, 8, 13, 10, 57, tzinfo=timezone.utc)
END = datetime(2026, 9, 12, 10, 57, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path("backtest_output_v138_final_30d_score_diagnostics")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Pin the exact comparison window and a separate output destination.
for mod in (d30, v138, base):
    mod.START = START
    mod.END = END
    mod.START_MS = START_MS
    mod.END_MS = END_MS
    mod.OUTDIR = OUTDIR

d30.OUTDIR = OUTDIR

TARGET_SCORES = [6.0, 6.5, 7.0, 7.5, 8.0, 8.5]
COMPONENTS = [
    "setup", "fav15", "macd5", "boll5", "macd15", "boll15",
    "trend1h", "trend4h", "rsi", "struct1h", "struct4h",
]


def setup_detail(market, i15: int, buy: bool) -> dict:
    data = market.data["15m"]
    inds = market.inds["15m"]
    if i15 < 20:
        return {
            "setup_boll_touch": 0, "setup_volume_boll": 0,
            "setup_kdj": 0, "setup_reversal": 0, "setup_volume_ratio": 0.0,
        }
    c = data[i15]
    cur = inds[i15]
    boll = c["l"] <= cur["lower"] if buy else c["h"] >= cur["upper"]
    hist = [float(data[j]["v"]) for j in range(i15 - 20, i15)]
    avg = sum(hist) / 20
    ratio = float(c["v"]) / avg if avg > 0 else 0.0
    returned = (c["l"] <= cur["lower"] and c["c"] > cur["lower"]) if buy else (
        c["h"] >= cur["upper"] and c["c"] < cur["upper"]
    )
    volume_boll = returned and ratio >= 1.3
    kdj = (cur["j"] <= 30 and cur["cross_up"]) if buy else (
        cur["j"] >= 70 and cur["cross_down"]
    )
    rev = base.reversal(data, i15, buy)
    return {
        "setup_boll_touch": int(bool(boll)),
        "setup_volume_boll": int(bool(volume_boll)),
        "setup_kdj": int(bool(kdj)),
        "setup_reversal": int(bool(rev)),
        "setup_volume_ratio": float(ratio),
    }


_original_submit = v138.V138Simulator.submit


def diagnostic_submit(self, sig, i1, price):
    before = len(self.legs)
    side = sig.get("side")
    row = sig.get("scores", {}).get(side) if side and side != "观望" else None
    extra = None
    if row is not None:
        i15 = sig["idx"]["15m"]
        extra = setup_detail(self.m, i15, side == "做多")
    _original_submit(self, sig, i1, price)
    if row is None or len(self.legs) <= before:
        return
    for leg in self.legs[before:]:
        leg.diag_components = dict(row.get("components") or {})
        leg.diag_front_r = float(row.get("front_r", math.inf))
        leg.diag_hstate = row.get("hstate", "")
        leg.diag_qstate = row.get("qstate", "")
        leg.diag_setup = dict(extra or {})


v138.V138Simulator.submit = diagnostic_submit


def leg_net(l) -> float:
    gross = (l.exit_price - l.entry) * l.qty * l.direction() if l.exit_price is not None else 0.0
    return gross - l.entry_fee - l.exit_fee + l.funding


def active_component_key(l) -> str:
    comp = getattr(l, "diag_components", {})
    labels = []
    for k in COMPONENTS:
        v = float(comp.get(k, 0.0) or 0.0)
        if abs(v) > 1e-12:
            labels.append(f"{k}={v:+g}")
    return " | ".join(labels) if labels else "none"


def trigger_key(l) -> str:
    comp = getattr(l, "diag_components", {})
    names = []
    if comp.get("trigger_ema_reclaim"): names.append("EMA20回收")
    if comp.get("trigger_kdj"): names.append("1m KDJ")
    if comp.get("trigger_reversal"): names.append("1m反转")
    return "+".join(names) if names else "未知"


def setup_key(l) -> str:
    d = getattr(l, "diag_setup", {})
    names = []
    if d.get("setup_boll_touch"): names.append("BOLL触边")
    if d.get("setup_volume_boll"): names.append("BOLL回归+量能")
    if d.get("setup_kdj"): names.append("15m KDJ")
    if d.get("setup_reversal"): names.append("15m反转")
    return "+".join(names) if names else "无Setup子项"


def summarize_group(rows):
    n = len(rows)
    pnls = [leg_net(l) for l in rows]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    out = {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": (len(wins) / n * 100 if n else 0.0),
        "net_pnl": sum(pnls),
        "avg_pnl": (sum(pnls) / n if n else 0.0),
        "long_trades": sum(1 for l in rows if l.side == "做多"),
        "short_trades": sum(1 for l in rows if l.side == "做空"),
    }
    comp_stats = {}
    for k in COMPONENTS:
        vals = [float(getattr(l, "diag_components", {}).get(k, 0.0) or 0.0) for l in rows]
        win_vals = [v for l, v in zip(rows, vals) if leg_net(l) > 0]
        loss_vals = [v for l, v in zip(rows, vals) if leg_net(l) < 0]
        if k in ("rsi", "struct1h", "struct4h", "trend4h"):
            occurrence = sum(1 for v in vals if abs(v) > 1e-12)
        else:
            occurrence = sum(1 for v in vals if v > 1e-12)
        comp_stats[k] = {
            "mean": (sum(vals) / n if n else 0.0),
            "occurrence_pct": (occurrence / n * 100 if n else 0.0),
            "win_mean": (sum(win_vals) / len(win_vals) if win_vals else 0.0),
            "loss_mean": (sum(loss_vals) / len(loss_vals) if loss_vals else 0.0),
        }
    out["components"] = comp_stats
    return out


def combo_table(rows, key_fn, topn=20):
    bucket = defaultdict(list)
    for l in rows:
        bucket[key_fn(l)].append(l)
    table = []
    for key, xs in bucket.items():
        pnls = [leg_net(l) for l in xs]
        table.append({
            "combo": key,
            "trades": len(xs),
            "win_rate_pct": sum(1 for x in pnls if x > 0) / len(xs) * 100,
            "net_pnl": sum(pnls),
        })
    table.sort(key=lambda r: (-r["trades"], r["net_pnl"]))
    return table[:topn]


_original_write_report = v138.write_report


def diagnostic_write_report(sim, metrics, leg_net_fn, cycles, closed):
    _original_write_report(sim, metrics, leg_net_fn, cycles, closed)

    # Rich trade-level diagnostics.
    path = OUTDIR / "V138_FINAL_BTC_SWAP_30D_ScoreDiagnostics.csv"
    fields = [
        "leg_id", "side", "score", "signal_utc", "exit_reason", "net_pnl",
        "setup", "fav15", "macd5", "boll5", "macd15", "boll15",
        "trend1h", "trend4h", "rsi", "struct1h", "struct4h",
        "trigger_ema_reclaim", "trigger_kdj", "trigger_reversal",
        "front_r", "hstate", "qstate",
        "setup_boll_touch", "setup_volume_boll", "setup_kdj", "setup_reversal", "setup_volume_ratio",
        "component_combo", "trigger_combo", "setup_combo",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for l in closed:
            c = getattr(l, "diag_components", {})
            sd = getattr(l, "diag_setup", {})
            row = {
                "leg_id": l.leg_id, "side": l.side, "score": l.score,
                "signal_utc": base.fmt_dt(l.signal_ms), "exit_reason": l.reason,
                "net_pnl": leg_net(l), "front_r": getattr(l, "diag_front_r", math.inf),
                "hstate": getattr(l, "diag_hstate", ""), "qstate": getattr(l, "diag_qstate", ""),
                "component_combo": active_component_key(l), "trigger_combo": trigger_key(l), "setup_combo": setup_key(l),
            }
            for k in COMPONENTS:
                row[k] = c.get(k, 0.0)
            for k in ("trigger_ema_reclaim", "trigger_kdj", "trigger_reversal"):
                row[k] = c.get(k, 0.0)
            for k in ("setup_boll_touch", "setup_volume_boll", "setup_kdj", "setup_reversal", "setup_volume_ratio"):
                row[k] = sd.get(k, 0.0)
            w.writerow(row)

    by_score = {}
    for s in TARGET_SCORES:
        xs = [l for l in closed if abs(float(l.score) - s) <= 1e-9]
        by_score[str(s)] = summarize_group(xs)

    xs75 = [l for l in closed if abs(float(l.score) - 7.5) <= 1e-9]
    summary = {
        "period_start": START.isoformat(), "period_end": END.isoformat(),
        "loss_pause_enabled": True,
        "by_score": by_score,
        "score_7_5": {
            "component_combos": combo_table(xs75, active_component_key),
            "trigger_combos": combo_table(xs75, trigger_key),
            "setup_combos": combo_table(xs75, setup_key),
        },
    }
    (OUTDIR / "score_diagnostics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # Human-readable comparison report.
    lines = [
        "# V1.3.8 30天评分诊断", "",
        f"区间：{START:%Y-%m-%d %H:%M} UTC → {END:%Y-%m-%d %H:%M} UTC；3连亏暂停6小时保持开启。", "",
        "## 各评分表现", "",
        "| 分数 | 成交 | 胜率 | 净PnL | 做多/做空 |", "|---:|---:|---:|---:|---:|",
    ]
    for s in TARGET_SCORES:
        g = by_score[str(s)]
        lines.append(f"| {s:.1f} | {g['trades']} | {g['win_rate_pct']:.2f}% | {g['net_pnl']:+.2f}U | {g['long_trades']}/{g['short_trades']} |")
    lines += ["", "## 7.5分的分项出现率", "", "| 分项 | 平均分 | 出现率 | 盈利单均值 | 亏损单均值 |", "|---|---:|---:|---:|---:|"]
    g75 = by_score["7.5"]
    for k in COMPONENTS:
        x = g75["components"][k]
        lines.append(f"| {k} | {x['mean']:+.2f} | {x['occurrence_pct']:.1f}% | {x['win_mean']:+.2f} | {x['loss_mean']:+.2f} |")
    lines += ["", "## 7.5分常见评分组合", "", "| 组合 | 笔数 | 胜率 | PnL |", "|---|---:|---:|---:|"]
    for r in summary["score_7_5"]["component_combos"][:12]:
        lines.append(f"| {r['combo']} | {r['trades']} | {r['win_rate_pct']:.1f}% | {r['net_pnl']:+.2f}U |")
    lines += ["", "## 7.5分 Trigger组合", "", "| Trigger | 笔数 | 胜率 | PnL |", "|---|---:|---:|---:|"]
    for r in summary["score_7_5"]["trigger_combos"]:
        lines.append(f"| {r['combo']} | {r['trades']} | {r['win_rate_pct']:.1f}% | {r['net_pnl']:+.2f}U |")
    lines += ["", "## 7.5分 Setup组合", "", "| Setup | 笔数 | 胜率 | PnL |", "|---|---:|---:|---:|"]
    for r in summary["score_7_5"]["setup_combos"]:
        lines.append(f"| {r['combo']} | {r['trades']} | {r['win_rate_pct']:.1f}% | {r['net_pnl']:+.2f}U |")
    (OUTDIR / "V138_FINAL_BTC_SWAP_30D_ScoreDiagnostics_Report.md").write_text("\n".join(lines), encoding="utf-8")


v138.write_report = diagnostic_write_report


def main():
    d30.main()
    print("--- SCORE DIAGNOSTICS REPORT ---")
    print((OUTDIR / "V138_FINAL_BTC_SWAP_30D_ScoreDiagnostics_Report.md").read_text(encoding="utf-8"))
    print("--- SCORE DIAGNOSTICS JSON ---")
    print((OUTDIR / "score_diagnostics.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

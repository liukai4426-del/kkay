#!/usr/bin/env python3
"""30-day A/B: 5-point entry, single signal, no add-on, NO consecutive-loss pause."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import research_v138_final_30d_entry5_single_signal_backtest as s

v138 = s.v138
base = s.base

START = datetime(2026, 8, 13, 10, 57, tzinfo=timezone.utc)
END = datetime(2026, 9, 12, 10, 57, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTDIR = Path("backtest_output_v138_final_30d_entry5_single_signal_no_losspause")
OUTDIR.mkdir(parents=True, exist_ok=True)

for mod in (s, v138, base):
    mod.START = START
    mod.END = END
    mod.START_MS = START_MS
    mod.END_MS = END_MS
    mod.OUTDIR = OUTDIR


class NoLossPauseSimulator(v138.V138Simulator):
    """Preserve daily risk + 30m cooldown, but never pause after consecutive losses."""

    def _refresh_loss_pause(self, signal_ms):
        self.loss_pause_until_ms = 0

    def can_signal(self, side, ti, signal_ms):
        if ti <= 0:
            return False
        day = datetime.fromtimestamp(signal_ms/1000, tz=timezone.utc).astimezone(base.SH_TZ).strftime("%Y-%m-%d")
        if self.daily_block_day == day:
            self.stats["daily_risk_blocks"] += 1
            return False
        if self.pending_legs():
            return False
        if self.active is None:
            if signal_ms - self.last_close_ms < v138.COOLDOWN_MINUTES*60_000:
                self.stats["cooldown_blocks"] += 1
                return False
            return True
        if side != self.active.side:
            return False
        if ti < self.active.highest_tier:
            return False
        if self.active.tier_counts[ti] >= 1:
            return False
        if ti == 1 and self.active.legs:
            return False
        return True

    def cleanup_cycle(self, ms):
        c = self.active
        if c is None:
            return
        alive = any(l.status in ("pending", "open") or l.stop_child_pending for l in c.legs)
        if alive:
            return
        filled = [l for l in c.legs if l.fill_ms is not None]
        if not filled:
            self.cycles.remove(c)
            self.active = None
            return
        c.end_ms = ms
        c.pnl = self.cash - c.start_equity
        if c.pnl < 0:
            self.loss_streak += 1
        else:
            self.loss_streak = 0
        self.loss_pause_until_ms = 0
        self.last_close_ms = ms
        self.active = None


v138.V138Simulator = NoLossPauseSimulator


def postprocess():
    p = OUTDIR / "metrics.json"
    metrics = json.loads(p.read_text(encoding="utf-8"))
    metrics["window"] = "fixed_identical_30_days"
    metrics["entry_threshold"] = 5.0
    metrics["signal_tiers"] = 1
    metrics["single_signal_only"] = True
    metrics["add_on_enabled"] = False
    metrics["position_cap_usdt"] = 1000.0
    metrics["consecutive_loss_pause_enabled"] = False
    metrics["loss_pause_hours"] = 0
    metrics["loss_pause_triggers"] = 0
    metrics["loss_pause_blocked_signals"] = 0
    metrics["comparison"] = "same 5-point single-signal baseline; only 3-loss/6h pause disabled"
    p.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    renames = {
        "V138_FINAL_BTC_SWAP_3M_Trades.csv": "V138_FINAL_BTC_SWAP_30D_ENTRY5_SINGLE_NOPAUSE_Trades.csv",
        "V138_FINAL_BTC_SWAP_3M_Cycles.csv": "V138_FINAL_BTC_SWAP_30D_ENTRY5_SINGLE_NOPAUSE_Cycles.csv",
        "V138_FINAL_BTC_SWAP_3M_DailyEquity.csv": "V138_FINAL_BTC_SWAP_30D_ENTRY5_SINGLE_NOPAUSE_DailyEquity.csv",
    }
    for old, new in renames.items():
        src = OUTDIR / old
        if src.exists():
            src.replace(OUTDIR / new)
    old_report = OUTDIR / "V138_FINAL_BTC_SWAP_3M_Backtest_Report.md"
    if old_report.exists():
        old_report.unlink()

    report = f"""# KAYTRADE V1.3.8 FINAL — 30天 / 5分 / 单信号 / 无连续亏损暂停

**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC  
**仓位：** >=5.0仅一个信号，最大1000U；不加仓。  
**唯一改动：** 关闭连续亏损暂停；不论连续亏损多少笔，都不会因此暂停新开仓。  
**保持不变：** 30分钟冷却、日内风险限制、Hard Gate、>=1.3R、15m ATR、1R SL / 2R TP、60秒LIMIT。

| 指标 | 结果 |
|---|---:|
| 交易轮次 | {metrics['cycles']} |
| 胜率 | {metrics['cycle_win_rate_pct']:.2f}% |
| 净收益 | {metrics['net_pnl']:+.2f} U |
| 净收益率 | {metrics['net_return_pct']:+.3f}% |
| Profit Factor | {metrics['profit_factor']:.3f} |
| 每轮期望 | {metrics['expectancy_cycle']:+.4f} U |
| 最大回撤 | {metrics['max_drawdown_usdt']:.2f} U / {metrics['max_drawdown_pct']:.2f}% |
| 手续费 | {metrics['fees']:.2f} U |
| 压力情景收益率 | {metrics['stress_return_pct']:+.3f}% |
"""
    (OUTDIR / "V138_FINAL_BTC_SWAP_30D_ENTRY5_SINGLE_NOPAUSE_Backtest_Report.md").write_text(report, encoding="utf-8")


def main():
    v138.main()
    postprocess()
    print("FINAL_30D_ENTRY5_SINGLE_NOPAUSE_METRICS")
    print((OUTDIR / "metrics.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

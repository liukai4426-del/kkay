#!/usr/bin/env python3
"""V1.3.8 FINAL rolling 30-day BTC backtest with ONLY the 3-loss/6h pause disabled."""
from __future__ import annotations

import json
from pathlib import Path

import research_v138_final_30d_backtest as d30

v138 = d30.v138
base = v138.base
OUTDIR = Path("backtest_output_v138_final_30d_no_loss_pause")
OUTDIR.mkdir(parents=True, exist_ok=True)

# Keep the same 30-day window and every strategy/execution parameter from d30.
d30.OUTDIR = OUTDIR
v138.OUTDIR = OUTDIR
base.OUTDIR = OUTDIR


def no_pause_can_signal(self, side, ti, signal_ms):
    """Exact V1.3.8 can_signal flow, except consecutive losses never block entries."""
    if ti <= 0:
        return False
    day = d30.datetime.fromtimestamp(signal_ms/1000, tz=d30.timezone.utc).astimezone(base.SH_TZ).strftime("%Y-%m-%d")
    if self.daily_block_day == day:
        self.stats["daily_risk_blocks"] += 1
        return False
    # INTENTIONALLY DISABLED: loss_pause_until / 3-loss / 6-hour block.
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


def no_pause_cleanup_cycle(self, ms):
    """Close cycles normally and track loss streak, but never start a pause."""
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
        self.stats["max_observed_loss_streak"] = max(self.stats["max_observed_loss_streak"], self.loss_streak)
    else:
        self.loss_streak = 0
    self.last_close_ms = ms
    self.active = None


v138.V138Simulator.can_signal = no_pause_can_signal
v138.V138Simulator.cleanup_cycle = no_pause_cleanup_cycle


def main():
    v138.main()
    d30._postprocess()
    metrics_path = OUTDIR / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["loss_pause_enabled"] = False
    metrics["loss_pause_triggers"] = 0
    metrics["loss_pause_blocked_signals"] = 0
    metrics["comparison"] = "same final V1.3.8 30d strategy; ONLY 3-loss/6-hour pause disabled"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = OUTDIR / "V138_FINAL_BTC_SWAP_30D_Backtest_Report.md"
    if report_path.exists():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("**策略：** 与已验证92天回测完全相同，仅缩短样本窗口。",
                            "**策略：** 与上一轮30天V1.3.8完全相同；唯一变化是关闭‘连续亏损3次→暂停6小时’。")
        import re
        text = re.sub(r"3连亏→暂停6小时触发 .*?次；暂停期拦截合格信号 .*?次。",
                      "连续亏损暂停机制：已关闭；不会因3连亏暂停6小时。", text)
        report_path.write_text(text, encoding="utf-8")
    print("FINAL_30D_NO_LOSS_PAUSE_METRICS")
    print(metrics_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

"""V1.5.2 backtest execution primitives.

This module deliberately delegates all direction/opportunity/score/hard-gate work
to v152_model. It only models data integrity, limit fills, post-fill exits,
costs, funding and risk-state timing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import v152_model as model

ONE_MINUTE_MS = 60_000
LOSS_PAUSE_MS = 60 * 60 * 1000
COOLDOWN_MS = 30 * 60 * 1000


def validate_candle_continuity(rows, step_ms, name="K线"):
    if len(rows) < 2:
        raise ValueError(f"{name}数据不足")
    for a, b in zip(rows, rows[1:]):
        if int(b["t"]) - int(a["t"]) != int(step_ms):
            raise ValueError(f"{name}存在缺口：{a['t']} → {b['t']}")
    return True


def validate_funding_coverage(funding, start_ms, end_ms, max_gap_ms=9 * 60 * 60 * 1000):
    """Require funding observations to cover the test range without >9h gaps."""
    stamps = sorted(int(x.get("t", x.get("fundingTime", 0))) for x in funding if int(x.get("t", x.get("fundingTime", 0)) or 0) > 0)
    if not stamps:
        raise ValueError("资金费数据为空")
    if stamps[0] > int(start_ms) + max_gap_ms or stamps[-1] < int(end_ms) - max_gap_ms:
        raise ValueError("资金费覆盖不足")
    if any(b - a > max_gap_ms for a, b in zip(stamps, stamps[1:])):
        raise ValueError("资金费时间序列存在异常缺口")
    return True


@dataclass
class RiskState:
    consecutive_losses: int = 0
    max_consecutive_losses: int = 0
    loss_pause_until_ms: int = 0
    last_close_ms: int = 0
    day_peak: float = 0.0
    day_key: str = ""

    def refresh(self, now_ms):
        if self.loss_pause_until_ms and int(now_ms) >= self.loss_pause_until_ms:
            self.consecutive_losses = 0
            self.loss_pause_until_ms = 0

    def entry_block_reason(self, now_ms):
        self.refresh(now_ms)
        if self.loss_pause_until_ms and int(now_ms) < self.loss_pause_until_ms:
            return "连续3次净亏损暂停1小时"
        if self.last_close_ms and int(now_ms) - self.last_close_ms < COOLDOWN_MS:
            return "平仓后30分钟冷却"
        return ""

    def close_trade(self, now_ms, net_pnl):
        self.last_close_ms = int(now_ms)
        if float(net_pnl) < 0:
            self.consecutive_losses += 1
            self.max_consecutive_losses = max(self.max_consecutive_losses, self.consecutive_losses)
            if self.consecutive_losses >= 3:
                self.loss_pause_until_ms = int(now_ms) + LOSS_PAUSE_MS
        else:
            self.consecutive_losses = 0
        return self.loss_pause_until_ms


@dataclass
class PendingEntry:
    opportunity_id: str
    side: str
    limit: float
    stop: float
    target: float
    quantity_btc: float
    score: float
    submitted_bar_t: int
    expires_ms: int
    score_components: dict = field(default_factory=dict)
    trigger_combination: dict = field(default_factory=dict)
    front_r: float = math.inf
    front_space_status: str = "空间未知"
    cost_r: float = math.inf


@dataclass
class Position:
    opportunity_id: str
    side: str
    entry: float
    stop: float
    target: float
    quantity_btc: float
    score: float
    filled_bar_t: int
    entry_fee: float = 0.0
    funding_pnl: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    score_components: dict = field(default_factory=dict)
    trigger_combination: dict = field(default_factory=dict)
    front_r: float = math.inf
    front_space_status: str = "空间未知"
    cost_r: float = math.inf

    @property
    def stop_distance(self):
        return abs(self.entry - self.stop)

    def update_excursion(self, bar):
        high, low = float(bar["h"]), float(bar["l"])
        if self.side == "做多":
            self.mfe = max(self.mfe, max(0.0, high - self.entry))
            self.mae = max(self.mae, max(0.0, self.entry - low))
        else:
            self.mfe = max(self.mfe, max(0.0, self.entry - low))
            self.mae = max(self.mae, max(0.0, high - self.entry))


def limit_touched(pending, bar):
    low, high = float(bar["l"]), float(bar["h"])
    return low <= float(pending.limit) <= high


def fill_entry(pending, bar, maker_bps=2.0):
    """Fill at the resting limit; do NOT inspect this bar for TP/SL afterwards."""
    if int(bar["t"]) * 1 >= int(pending.expires_ms):
        return None
    if not limit_touched(pending, bar):
        return None
    entry = float(pending.limit)
    fee = entry * float(pending.quantity_btc) * float(maker_bps) / 10000.0
    return Position(
        opportunity_id=pending.opportunity_id, side=pending.side, entry=entry,
        stop=float(pending.stop), target=float(pending.target), quantity_btc=float(pending.quantity_btc),
        score=float(pending.score), filled_bar_t=int(bar["t"]), entry_fee=fee,
        score_components=dict(pending.score_components), trigger_combination=dict(pending.trigger_combination),
        front_r=float(pending.front_r), front_space_status=str(pending.front_space_status), cost_r=float(pending.cost_r),
    )


def _exit_price_with_slippage(trigger_price, side, reason, slippage_bps):
    px = float(trigger_price)
    slip = float(slippage_bps) / 10000.0
    if side == "做多":
        return px * (1.0 - slip)  # selling to close
    return px * (1.0 + slip)      # buying to close


def exit_on_bar(position, bar, taker_bps=5.0, slippage_bps=5.0, stress_extra_bps=0.0):
    """Evaluate exits only on bars AFTER the fill bar.

    OHLC does not preserve intrabar ordering. If a later bar touches both target and
    stop, choose SL conservatively. This prevents pre-fill highs/lows from being
    incorrectly counted as post-fill TP/SL.
    """
    if int(bar["t"]) <= int(position.filled_bar_t):
        return None
    position.update_excursion(bar)
    low, high = float(bar["l"]), float(bar["h"])
    if position.side == "做多":
        hit_sl = low <= position.stop
        hit_tp = high >= position.target
    else:
        hit_sl = high >= position.stop
        hit_tp = low <= position.target
    if not hit_sl and not hit_tp:
        return None
    reason = "SL" if hit_sl else "TP2R"
    trigger = position.stop if reason == "SL" else position.target
    exit_px = _exit_price_with_slippage(trigger, position.side, reason, float(slippage_bps) + float(stress_extra_bps))
    qty = position.quantity_btc
    direction = 1.0 if position.side == "做多" else -1.0
    gross = (exit_px - position.entry) * qty * direction
    exit_fee = exit_px * qty * float(taker_bps) / 10000.0
    net = gross - position.entry_fee - exit_fee + position.funding_pnl
    r = position.stop_distance * qty
    return {
        "reason": reason, "exit": exit_px, "gross_pnl": gross, "entry_fee": position.entry_fee,
        "exit_fee": exit_fee, "funding_pnl": position.funding_pnl, "net_pnl": net,
        "mfe_r": position.mfe / position.stop_distance if position.stop_distance > 0 else 0.0,
        "mae_r": position.mae / position.stop_distance if position.stop_distance > 0 else 0.0,
        "realized_r": net / r if r > 0 else 0.0,
        "opportunity_id": position.opportunity_id, "score": position.score,
        "score_components": dict(position.score_components), "trigger_combination": dict(position.trigger_combination),
        "front_r": position.front_r, "front_space_status": position.front_space_status, "cost_r": position.cost_r,
    }


def apply_funding(position, funding_rate, mark_price):
    """Positive funding means longs pay and shorts receive."""
    rate = float(funding_rate)
    amount = float(mark_price) * float(position.quantity_btc) * rate
    position.funding_pnl += -amount if position.side == "做多" else amount
    return position.funding_pnl


def evaluate_signal(hour, quarter, five, one, four, opportunity=None, stop_atr=1.0,
                    maker_bps=2.0, taker_bps=5.0, slippage_bps=5.0, allow_new=True):
    """Backtest strategy decision uses the exact live V1.5.2 model."""
    return model.evaluate(hour, quarter, five, one, four, opportunity=opportunity,
                          stop_atr=stop_atr, maker_bps=maker_bps,
                          taker_bps=taker_bps, slippage_bps=slippage_bps,
                          allow_new=allow_new)

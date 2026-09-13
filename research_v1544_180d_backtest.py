#!/usr/bin/env python3
from __future__ import annotations

import bisect
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

import research_v146_backtest as base
import research_v1544_cache_patch as cache_patch
import v154_model as model
import v154_build1544_patch as build1544

VERSION = "1.5.4"
BUILD = "1544"
END = datetime(2026, 9, 13, 3, 16, tzinfo=timezone.utc)
START = END - timedelta(days=180)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
SPLIT_MS = int((END - timedelta(days=60)).timestamp() * 1000)

CAPITAL = 10_000.0
LEVERAGE = 5
RISK_USDT = 100.0
RISK_PCT = 1.0
DAILY_LOSS = 300.0
FIRST_SIGNAL_NOTIONAL = 1_000.0
STOP_ATR = 1.0
REWARD_R = 2.0
MAKER_BPS = 2.0
TAKER_BPS = 5.0
SLIPPAGE_BPS = 5.0
EXPECTED_COST_MIN = 1.20
ORDER_TTL_MS = 60_000
COOLDOWN_MS = 30 * 60_000
MODEL_WINDOW = 1000
SH_TZ = ZoneInfo("Asia/Shanghai")

OUTDIR = Path("backtest_output_v1544_btc_180d")
OUTDIR.mkdir(parents=True, exist_ok=True)

for name, value in {
    "START": START, "END": END, "START_MS": START_MS, "END_MS": END_MS,
    "CAPITAL": CAPITAL, "LEVERAGE": LEVERAGE, "RISK_USDT": RISK_USDT,
    "RISK_PCT": RISK_PCT, "DAILY_LOSS": DAILY_LOSS,
    "COOLDOWN_MINUTES": 30, "STOP_ATR": STOP_ATR, "REWARD_R": REWARD_R,
}.items():
    setattr(base, name, value)


class CandleWindow:
    __slots__ = ("data", "start", "stop")

    def __init__(self, data, start, stop):
        self.data = data
        self.start = int(start)
        self.stop = int(stop)

    def __len__(self):
        return self.stop - self.start

    def __iter__(self):
        for i in range(self.start, self.stop):
            yield self.data[i]

    def __getitem__(self, key):
        n = len(self)
        if isinstance(key, slice):
            start, stop, step = key.indices(n)
            if step == 1:
                return CandleWindow(self.data, self.start + start, self.start + stop)
            return [self.data[self.start + i] for i in range(start, stop, step)]
        i = int(key)
        if i < 0:
            i += n
        if i < 0 or i >= n:
            raise IndexError(i)
        return self.data[self.start + i]


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def closed_slice(data, ts, tf, close_ms):
    cutoff = int(close_ms) - base.BAR_MS[tf]
    idx = bisect.bisect_right(ts[tf], cutoff) - 1
    if idx < 200:
        return None
    start = max(0, idx - MODEL_WINDOW + 1)
    return CandleWindow(data[tf], start, idx + 1)


def side_dir(side):
    return 1.0 if side == "做多" else -1.0


def local_dt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(SH_TZ)


def local_day(ms):
    return local_dt(ms).strftime("%Y-%m-%d")


def round_contract_btc(raw_btc, meta):
    unit = float(meta["ctVal"]) * float(meta.get("ctMult") or 1.0)
    lot = Decimal(str(meta["lotSz"]))
    min_sz = Decimal(str(meta["minSz"]))
    contracts = Decimal(str(raw_btc / unit))
    contracts = (contracts / lot).to_integral_value(rounding=ROUND_DOWN) * lot
    if contracts < min_sz:
        return 0.0
    return float(contracts) * unit


def pf(rows):
    pos = sum(float(r["net_pnl"]) for r in rows if float(r["net_pnl"]) > 0)
    neg = -sum(float(r["net_pnl"]) for r in rows if float(r["net_pnl"]) < 0)
    return pos / neg if neg > 0 else (math.inf if pos > 0 else 0.0)


def pack_rows(rows):
    rows = list(rows)
    wins = [r for r in rows if float(r["net_pnl"]) > 0]
    pnl = sum(float(r["net_pnl"]) for r in rows)
    return {
        "trades": len(rows),
        "wins": len(wins),
        "win_rate_pct": 100.0 * len(wins) / len(rows) if rows else 0.0,
        "net_pnl": pnl,
        "expectancy": pnl / len(rows) if rows else 0.0,
        "profit_factor": pf(rows),
    }


@dataclass
class PendingEntry:
    side: str
    limit: float
    stop: float
    target: float
    quantity_btc: float
    score: float
    opportunity_id: str
    signal_bar_t: int
    signal_close_ms: int
    submitted_ms: int
    expires_ms: int
    factors: dict
    score_components: dict
    trigger_combination: dict
    front_r: float
    cost_r: float


@dataclass
class Position:
    pending: PendingEntry
    fill_bar_t: int
    fill_time: int
    entry_fee: float
    funding_pnl: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0


VARIANTS = (
    "baseline",
    "no_middle_rsi",
    "score_ge_6_5",
    "require_macd",
    "middle_requires_macd",
    "middle_requires_macd_and_6_5",
    "middle_requires_macd_4h_nonnegative",
    "outer_only_score_ge_6_5",
)


def variant_accept(name, score, opp):
    layers = score.get("layers") or {}
    total = float(score.get("total") or 0.0)
    path = str(opp.get("signal_path") or "")
    macd = float(layers.get("macd_improving") or 0.0) > 0
    trend4h = float(layers.get("trend4h") or 0.0)
    if name == "baseline":
        return True
    if name == "no_middle_rsi":
        return path != "middle_rsi"
    if name == "score_ge_6_5":
        return total >= 6.5
    if name == "require_macd":
        return macd
    if name == "middle_requires_macd":
        return path != "middle_rsi" or macd
    if name == "middle_requires_macd_and_6_5":
        return path != "middle_rsi" or (macd and total >= 6.5)
    if name == "middle_requires_macd_4h_nonnegative":
        return path != "middle_rsi" or (macd and trend4h >= 0)
    if name == "outer_only_score_ge_6_5":
        return path != "middle_rsi" and total >= 6.5
    raise ValueError(name)


class Simulator:
    def __init__(self, meta, funding, variant="baseline", stress_extra_bps=0.0):
        self.meta = meta
        self.funding = list(funding)
        self.funding_i = 0
        self.variant = variant
        self.stress_extra_bps = float(stress_extra_bps)
        self.cash = CAPITAL
        self.pending = None
        self.position = None
        self.opportunity = None
        self.last_consumed_signal_bar_t = -1
        self.last_close_ms = 0
        self.daily_day = ""
        self.daily_peak = CAPITAL
        self.daily_block_day = ""
        self.trades = []
        self.equity_curve = []
        self.stats = Counter()
        self.blockers = Counter()
        self.transitions = Counter()

    def equity(self, mark):
        eq = float(self.cash)
        if self.position:
            p = self.position
            leg = p.pending
            eq += (float(mark) - leg.limit) * leg.quantity_btc * side_dir(leg.side)
            eq -= p.entry_fee
            eq += p.funding_pnl
        return eq

    def mark_risk(self, ms, mark):
        eq = self.equity(mark)
        day = local_day(ms)
        if day != self.daily_day:
            self.daily_day = day
            self.daily_peak = eq
            self.daily_block_day = ""
        self.daily_peak = max(self.daily_peak, eq)
        if self.daily_peak - eq >= DAILY_LOSS:
            self.daily_block_day = day
        self.equity_curve.append((int(ms), float(eq)))
        return eq

    def daily_remaining(self, ms, mark):
        eq = self.equity(mark)
        if local_day(ms) != self.daily_day:
            return DAILY_LOSS
        return max(0.0, DAILY_LOSS - max(0.0, self.daily_peak - eq))

    def apply_funding_until(self, ms, mark):
        while self.funding_i < len(self.funding) and int(self.funding[self.funding_i][0]) <= int(ms):
            _, rate = self.funding[self.funding_i]
            if self.position:
                leg = self.position.pending
                amount = float(mark) * leg.quantity_btc * float(rate)
                self.position.funding_pnl += -amount if leg.side == "做多" else amount
            self.funding_i += 1

    def process_pending(self, bar):
        if not self.pending:
            return
        p = self.pending
        if int(bar["t"]) >= int(p.expires_ms):
            self.stats["limit_unfilled"] += 1
            self.pending = None
            return
        if not (float(bar["l"]) <= p.limit <= float(bar["h"])):
            return
        fee = p.limit * p.quantity_btc * MAKER_BPS / 10000.0
        self.position = Position(p, int(bar["t"]), int(bar["t"]), fee)
        self.pending = None
        self.opportunity = None
        self.last_consumed_signal_bar_t = max(self.last_consumed_signal_bar_t, int(p.signal_bar_t))
        self.stats["limit_filled"] += 1

    def process_exit(self, bar):
        if not self.position:
            return
        pos = self.position
        p = pos.pending
        if int(bar["t"]) <= int(pos.fill_bar_t):
            return
        high, low = float(bar["h"]), float(bar["l"])
        if p.side == "做多":
            pos.mfe = max(pos.mfe, max(0.0, high - p.limit))
            pos.mae = max(pos.mae, max(0.0, p.limit - low))
            hit_sl = low <= p.stop
            hit_tp = high >= p.target
        else:
            pos.mfe = max(pos.mfe, max(0.0, p.limit - low))
            pos.mae = max(pos.mae, max(0.0, high - p.limit))
            hit_sl = high >= p.stop
            hit_tp = low <= p.target
        if not (hit_sl or hit_tp):
            return
        reason = "SL" if hit_sl else "TP2R"
        trigger = p.stop if reason == "SL" else p.target
        slip = (SLIPPAGE_BPS + self.stress_extra_bps) / 10000.0
        exit_px = trigger * (1.0 - slip if p.side == "做多" else 1.0 + slip)
        gross = (exit_px - p.limit) * p.quantity_btc * side_dir(p.side)
        exit_fee = exit_px * p.quantity_btc * TAKER_BPS / 10000.0
        net = gross - pos.entry_fee - exit_fee + pos.funding_pnl
        exit_ms = int(bar["t"]) + 60_000
        self.cash += net
        stop_money = abs(p.limit - p.stop) * p.quantity_btc
        row = {
            "variant": self.variant, "side": p.side, "score": p.score,
            "boll_path": p.factors["boll_path"], "entry": p.limit, "stop": p.stop,
            "target": p.target, "exit": exit_px, "reason": reason,
            "quantity_btc": p.quantity_btc, "net_pnl": net, "gross_pnl": gross,
            "entry_fee": pos.entry_fee, "exit_fee": exit_fee, "funding_pnl": pos.funding_pnl,
            "realized_r": net / stop_money if stop_money > 0 else 0.0,
            "mfe_r": pos.mfe / abs(p.limit - p.stop) if abs(p.limit - p.stop) > 0 else 0.0,
            "mae_r": pos.mae / abs(p.limit - p.stop) if abs(p.limit - p.stop) > 0 else 0.0,
            "front_r": p.front_r, "cost_r": p.cost_r,
            "entry_time": pos.fill_time, "exit_time": exit_ms,
            "hold_min": (exit_ms - pos.fill_time) / 60_000.0,
            "opportunity_id": p.opportunity_id,
            "score_components": dict(p.score_components),
            "trigger_combination": dict(p.trigger_combination),
        }
        row.update(p.factors)
        self.trades.append(row)
        self.position = None
        self.last_close_ms = exit_ms
        self.stats["closed"] += 1

    def can_submit(self, now_ms):
        if self.position or self.pending:
            return False
        if self.daily_block_day == local_day(now_ms):
            self.stats["daily_risk_blocks"] += 1
            return False
        if self.last_close_ms and int(now_ms) - int(self.last_close_ms) < COOLDOWN_MS:
            self.stats["cooldown_blocks"] += 1
            return False
        return True

    def _factors(self, score, opp, now_ms, entry):
        layers = dict(score.get("layers") or {})
        confirmations = dict(score.get("confirmations") or {})
        trigger = dict(confirmations.get("trigger") or opp.get("trigger_detail") or {})
        atr5 = max(float(opp.get("atr5") or 0.0), 1e-12)
        ref = float(opp.get("trigger_reference") or entry)
        dt = local_dt(now_ms)
        return {
            "boll_path": str(opp.get("signal_path") or ""),
            "direction_pullback": float(layers.get("direction_pullback") or 0.0),
            "entry_near_zone": float(layers.get("entry_near_zone") or 0.0),
            "structure_overlap": float(layers.get("structure_overlap") or 0.0),
            "macd_improving": float(layers.get("macd_improving") or 0.0),
            "trend4h": float(layers.get("trend4h") or 0.0),
            "ema50_15m_move": float(layers.get("ema50_15m_move") or 0.0),
            "volume5": float(layers.get("volume5") or 0.0),
            "kdj5_signal": float(layers.get("kdj5_signal") or 0.0),
            "rsi5": float(trigger.get("rsi5") or 0.0),
            "signal_age_min": max(0.0, (int(now_ms) - int(opp.get("signal_close_ms") or now_ms)) / 60_000.0),
            "entry_drift_atr5": abs(float(entry) - ref) / atr5,
            "atr5_pct": 100.0 * atr5 / max(float(entry), 1e-12),
            "atr1h_pct": 100.0 * float(opp.get("atr1h") or 0.0) / max(float(entry), 1e-12),
            "local_hour_cn": dt.hour,
            "weekday_cn": dt.strftime("%a"),
            "month_cn": dt.strftime("%Y-%m"),
        }

    def submit(self, result, now_ms, mark):
        opp = result.get("opportunity")
        if not isinstance(opp, dict):
            return
        side = str(opp.get("side") or "")
        score = (result.get("scores") or {}).get(side) or {}
        if not score.get("eligible") or float(score.get("total") or 0.0) < model.THRESHOLD:
            return
        self.stats["eligible_states"] += 1
        if not variant_accept(self.variant, score, opp):
            self.stats["variant_filter_blocks"] += 1
            return
        if not self.can_submit(now_ms):
            return

        entry = float(mark)
        stop_distance = float(opp["atr1h"]) * STOP_ATR
        direction = side_dir(side)
        stop = entry - direction * stop_distance
        target = entry + direction * stop_distance * REWARD_R
        eq = self.equity(mark)
        risk_capital = min(CAPITAL, eq)
        base_risk = min(RISK_USDT, risk_capital * RISK_PCT / 100.0)
        risk_budget = min(base_risk, self.daily_remaining(now_ms, mark))
        if risk_budget <= 0:
            self.stats["sizing_skips"] += 1
            return

        maker = MAKER_BPS / 10000.0
        taker = TAKER_BPS / 10000.0
        slip = SLIPPAGE_BPS / 10000.0
        risk_entry_fee = max(maker, taker)
        per_btc = stop_distance + entry * risk_entry_fee + stop * (taker + slip)
        notional_cap = min(FIRST_SIGNAL_NOTIONAL, risk_capital * LEVERAGE, max(eq, 0.0) * 0.9 * LEVERAGE)
        btc = round_contract_btc(min(risk_budget / per_btc, notional_cap / entry), self.meta)
        if btc <= 0:
            self.stats["sizing_skips"] += 1
            return

        expected_cost_per_btc = entry * maker + target * taker
        expected_fee_multiple = 2.0 * stop_distance / expected_cost_per_btc if expected_cost_per_btc > 0 else math.inf
        if expected_fee_multiple < EXPECTED_COST_MIN:
            self.stats["fee_multiple_blocks"] += 1
            return

        worst_roundtrip_cost = btc * (entry * max(maker, taker) + target * (taker + slip))
        plan = {"px": str(entry), "stop_distance": stop_distance, "btc": btc,
                "worst_roundtrip_cost": worst_roundtrip_cost}
        ok, diag, blockers = model.execution_checks(plan, opp, score)
        if not ok:
            self.stats["execution_gate_blocks"] += 1
            for reason in blockers:
                self.blockers[reason] += 1
            return

        factors = self._factors(score, opp, now_ms, entry)
        self.pending = PendingEntry(
            side=side, limit=entry, stop=stop, target=target, quantity_btc=btc,
            score=float(score["total"]), opportunity_id=str(opp["id"]),
            signal_bar_t=int(opp.get("signal_bar_t") or 0),
            signal_close_ms=int(opp.get("signal_close_ms") or 0), submitted_ms=int(now_ms),
            expires_ms=int(now_ms) + ORDER_TTL_MS, factors=factors,
            score_components=dict(score.get("layers") or {}),
            trigger_combination=dict((score.get("confirmations") or {}).get("trigger") or {}),
            front_r=float(diag.get("front_r", math.inf)), cost_r=float(diag.get("cost_r", math.inf)),
        )
        self.stats["limit_submitted"] += 1

    def finish(self, last_bar):
        self.pending = None
        if not self.position:
            return
        pos = self.position
        p = pos.pending
        slip = (SLIPPAGE_BPS + self.stress_extra_bps) / 10000.0
        exit_px = float(last_bar["c"]) * (1.0 - slip if p.side == "做多" else 1.0 + slip)
        gross = (exit_px - p.limit) * p.quantity_btc * side_dir(p.side)
        exit_fee = exit_px * p.quantity_btc * TAKER_BPS / 10000.0
        net = gross - pos.entry_fee - exit_fee + pos.funding_pnl
        exit_ms = int(last_bar["t"]) + 60_000
        self.cash += net
        stop_money = abs(p.limit - p.stop) * p.quantity_btc
        row = {
            "variant": self.variant, "side": p.side, "score": p.score,
            "boll_path": p.factors["boll_path"], "entry": p.limit, "stop": p.stop,
            "target": p.target, "exit": exit_px, "reason": "EOD",
            "quantity_btc": p.quantity_btc, "net_pnl": net, "gross_pnl": gross,
            "entry_fee": pos.entry_fee, "exit_fee": exit_fee, "funding_pnl": pos.funding_pnl,
            "realized_r": net / stop_money if stop_money > 0 else 0.0,
            "mfe_r": pos.mfe / abs(p.limit-p.stop) if abs(p.limit-p.stop) > 0 else 0.0,
            "mae_r": pos.mae / abs(p.limit-p.stop) if abs(p.limit-p.stop) > 0 else 0.0,
            "front_r": p.front_r, "cost_r": p.cost_r,
            "entry_time": pos.fill_time, "exit_time": exit_ms,
            "hold_min": (exit_ms - pos.fill_time) / 60_000.0,
            "opportunity_id": p.opportunity_id,
            "score_components": dict(p.score_components),
            "trigger_combination": dict(p.trigger_combination),
        }
        row.update(p.factors)
        self.trades.append(row)
        self.position = None


def metrics(sim, first_px, last_px):
    rows = sim.trades
    packed = pack_rows(rows)
    peak = -math.inf
    max_dd = max_dd_pct = 0.0
    for _, eq in sim.equity_curve:
        peak = max(peak, eq)
        dd = max(0.0, peak - eq)
        max_dd = max(max_dd, dd)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, 100.0 * dd / peak)
    wins = [float(r["net_pnl"]) for r in rows if float(r["net_pnl"]) > 0]
    losses = [float(r["net_pnl"]) for r in rows if float(r["net_pnl"]) < 0]
    result = {
        "strategy_version": VERSION, "build": BUILD, "variant": sim.variant, "days": 180,
        "start_utc": START.isoformat(), "end_utc": END.isoformat(), "capital": CAPITAL,
        "ending_equity": sim.cash, "net_pnl": sim.cash - CAPITAL,
        "net_return_pct": (sim.cash / CAPITAL - 1.0) * 100.0,
        "trades": packed["trades"], "wins": packed["wins"], "losses": len(losses),
        "win_rate_pct": packed["win_rate_pct"], "profit_factor": packed["profit_factor"],
        "expectancy": packed["expectancy"],
        "avg_win": statistics.mean(wins) if wins else 0.0,
        "avg_loss": statistics.mean(losses) if losses else 0.0,
        "payoff_ratio": statistics.mean(wins) / abs(statistics.mean(losses)) if wins and losses else math.inf,
        "max_drawdown_usdt": max_dd, "max_drawdown_pct": max_dd_pct,
        "fees": sum(float(r["entry_fee"]) + float(r["exit_fee"]) for r in rows),
        "funding_pnl": sum(float(r["funding_pnl"]) for r in rows),
        "avg_hold_min": statistics.mean([float(r["hold_min"]) for r in rows]) if rows else 0.0,
        "median_hold_min": statistics.median([float(r["hold_min"]) for r in rows]) if rows else 0.0,
        "btc_buy_hold_pct": (last_px / first_px - 1.0) * 100.0,
        "funnel": dict(sim.stats), "execution_blockers": dict(sim.blockers),
        "path_c_enabled": False, "loss_pause_enabled": False, "cooldown_minutes": 30,
        "entry_order_type": "LIMIT", "pending_order_ttl_seconds": 60,
        "boll_wall_clock_expiry_enabled": False,
        "historical_limit_reference": "closed 1m close proxy; not historical orderbook bid/ask",
        "stop_atr_timeframe": "1H", "stop_atr_multiplier": STOP_ATR, "reward_r": REWARD_R,
    }
    for key, selector in {
        "by_side": lambda r: r["side"],
        "by_boll_path": lambda r: r["boll_path"],
        "by_score": lambda r: f'{float(r["score"]):.1f}',
        "by_month_cn": lambda r: r["month_cn"],
        "by_weekday_cn": lambda r: r["weekday_cn"],
    }.items():
        groups = defaultdict(list)
        for row in rows:
            groups[selector(row)].append(row)
        result[key] = {k: pack_rows(v) for k, v in sorted(groups.items())}
    return result


def run(data, ts, meta, funding, variant="baseline", stress_extra_bps=0.0):
    sim = Simulator(meta, funding, variant=variant, stress_extra_bps=stress_extra_bps)
    d1 = data["1m"]
    start_i = bisect.bisect_left(ts["1m"], START_MS)
    last_bar = None
    for i in range(start_i, len(d1)):
        bar = d1[i]
        if int(bar["t"]) >= END_MS:
            break
        last_bar = bar
        bar_close = int(bar["t"]) + 60_000
        sim.apply_funding_until(int(bar["t"]), float(bar["o"]))
        sim.process_pending(bar)
        sim.process_exit(bar)
        sim.apply_funding_until(bar_close, float(bar["c"]))
        sim.mark_risk(bar_close, float(bar["c"]))
        if sim.position or sim.pending:
            continue

        slices = {}
        valid = True
        for tf in ("1m", "5m", "15m", "1H", "4H"):
            rows = closed_slice(data, ts, tf, bar_close)
            if rows is None:
                valid = False
                break
            slices[tf] = rows
        if not valid:
            continue

        old_opp = dict(sim.opportunity) if isinstance(sim.opportunity, dict) else None
        result, new_opp, transition = model.evaluate(
            slices["1H"], slices["15m"], slices["5m"], slices["1m"], slices["4H"],
            opportunity=sim.opportunity, stop_atr=STOP_ATR,
            maker_bps=MAKER_BPS, taker_bps=TAKER_BPS, slippage_bps=SLIPPAGE_BPS,
            now_ms=bar_close, allow_new=True,
        )
        if transition:
            sim.transitions[transition[0]] += 1
        if isinstance(new_opp, dict) and int(new_opp.get("signal_bar_t") or -1) <= sim.last_consumed_signal_bar_t:
            new_opp = None
            result["opportunity"] = None
        sim.opportunity = new_opp
        if old_opp and new_opp is None and transition and transition[0] == "invalidated":
            sim.stats["opportunity_invalidated"] += 1
        sim.submit(result, bar_close, float(bar["c"]))

        if (i - start_i) % 20_000 == 0:
            print(f"{variant} progress {i-start_i:,}, equity={sim.equity(bar['c']):.2f}, trades={len(sim.trades)}, pending={bool(sim.pending)}, opp={bool(sim.opportunity)}", flush=True)

    if last_bar is None:
        raise RuntimeError("no test bars")
    sim.finish(last_bar)
    first = next(r for r in d1 if int(r["t"]) >= START_MS)
    return sim, metrics(sim, float(first["c"]), float(last_bar["c"]))


def bucket(value, bounds, labels):
    v = float(value)
    for edge, label in zip(bounds, labels):
        if v < edge:
            return label
    return labels[-1]


def factor_analysis(rows):
    rows = list(rows)
    binary = {}
    binary_specs = {
        "15m_direction_pullback": lambda r: float(r["direction_pullback"]) > 0,
        "entry_near_15m_zone": lambda r: float(r["entry_near_zone"]) > 0,
        "structure_overlap": lambda r: float(r["structure_overlap"]) > 0,
        "macd_improving": lambda r: float(r["macd_improving"]) > 0,
        "ema50_15m_move": lambda r: float(r["ema50_15m_move"]) > 0,
        "volume_confirm": lambda r: float(r["volume5"]) > 0,
        "kdj_cross": lambda r: float(r["kdj5_signal"]) > 0,
        "4h_aligned": lambda r: float(r["trend4h"]) > 0,
        "4h_nonnegative": lambda r: float(r["trend4h"]) >= 0,
    }
    ranking = []
    for name, fn in binary_specs.items():
        on = [r for r in rows if fn(r)]
        off = [r for r in rows if not fn(r)]
        a, b = pack_rows(on), pack_rows(off)
        binary[name] = {"on": a, "off": b}
        if len(on) >= 10 and len(off) >= 10:
            ranking.append({
                "factor": name, "on_trades": len(on), "off_trades": len(off),
                "expectancy_delta": a["expectancy"] - b["expectancy"],
                "win_rate_delta_pct": a["win_rate_pct"] - b["win_rate_pct"],
                "pf_on": a["profit_factor"], "pf_off": b["profit_factor"],
            })
    ranking.sort(key=lambda x: x["expectancy_delta"], reverse=True)

    categorical = {}
    cats = {
        "boll_path": lambda r: r["boll_path"],
        "trend4h_state": lambda r: "aligned" if float(r["trend4h"]) > 0 else "opposite" if float(r["trend4h"]) < 0 else "neutral",
        "score": lambda r: f'{float(r["score"]):.1f}',
        "signal_age": lambda r: bucket(r["signal_age_min"], [1, 5, 15, 30, math.inf], ["<1m", "1-5m", "5-15m", "15-30m", ">=30m"]),
        "front_r": lambda r: "unknown" if not math.isfinite(float(r["front_r"])) else bucket(r["front_r"], [2, 3, 5, math.inf], ["1.5-2R", "2-3R", "3-5R", ">=5R"]),
        "cost_r": lambda r: bucket(r["cost_r"], [0.15, 0.20, 0.25, math.inf], ["<0.15R", "0.15-0.20R", "0.20-0.25R", "0.25-0.30R"]),
        "session_cn": lambda r: "00-07" if int(r["local_hour_cn"]) < 8 else "08-15" if int(r["local_hour_cn"]) < 16 else "16-23",
        "weekday_cn": lambda r: r["weekday_cn"],
    }
    for name, fn in cats.items():
        groups = defaultdict(list)
        for row in rows:
            groups[fn(row)].append(row)
        categorical[name] = {k: pack_rows(v) for k, v in sorted(groups.items())}

    interactions = {}
    inter_specs = {
        "path_x_macd": lambda r: f'{r["boll_path"]}|macd={int(float(r["macd_improving"])>0)}',
        "path_x_4h": lambda r: f'{r["boll_path"]}|4h={"A" if float(r["trend4h"])>0 else "O" if float(r["trend4h"])<0 else "N"}',
        "path_x_score": lambda r: f'{r["boll_path"]}|score={"7+" if float(r["score"])>=7 else "6.5" if float(r["score"])>=6.5 else "6.0"}',
        "path_x_pullback": lambda r: f'{r["boll_path"]}|15mpb={int(float(r["direction_pullback"])>0)}',
    }
    for name, fn in inter_specs.items():
        groups = defaultdict(list)
        for row in rows:
            groups[fn(row)].append(row)
        interactions[name] = {k: pack_rows(v) for k, v in sorted(groups.items()) if len(v) >= 5}
    return {"binary": binary, "binary_ranking_by_expectancy_delta": ranking,
            "categorical": categorical, "interactions_min5": interactions}


def period_pack(rows):
    train = [r for r in rows if int(r["entry_time"]) < SPLIT_MS]
    validation = [r for r in rows if int(r["entry_time"]) >= SPLIT_MS]
    return {"train_first120d": pack_rows(train), "validation_last60d": pack_rows(validation)}


def write_csv(rows, path):
    fields = [
        "entry_time", "exit_time", "side", "score", "boll_path", "entry", "stop", "target", "exit",
        "reason", "quantity_btc", "net_pnl", "gross_pnl", "entry_fee", "exit_fee", "funding_pnl",
        "realized_r", "mfe_r", "mae_r", "front_r", "cost_r", "hold_min", "opportunity_id",
        "direction_pullback", "entry_near_zone", "structure_overlap", "macd_improving", "trend4h",
        "ema50_15m_move", "volume5", "kdj5_signal", "rsi5", "signal_age_min", "entry_drift_atr5",
        "atr5_pct", "atr1h_pct", "local_hour_cn", "weekday_cn", "month_cn",
        "score_components", "trigger_combination",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            out = {k: row.get(k, "") for k in fields}
            out["entry_time"] = fmt(int(row["entry_time"]))
            out["exit_time"] = fmt(int(row["exit_time"]))
            out["score_components"] = json.dumps(row.get("score_components") or {}, ensure_ascii=False, sort_keys=True)
            out["trigger_combination"] = json.dumps(row.get("trigger_combination") or {}, ensure_ascii=False, sort_keys=True)
            w.writerow(out)


def main():
    print("V1.5.4 Build1544 Path-C-OFF fixed 180D backtest", START, END, flush=True)
    assert build1544.PATH_C_ENABLED is False
    assert model.ENTRY_WINDOW_MS == 0 and model.TIME_WINDOW_ENABLED is False
    meta = base.fetch_instrument()
    data = {tf: base.fetch_candles(tf) for tf in ("1m", "5m", "15m", "1H", "4H")}
    funding = base.fetch_funding()
    for tf, rows in data.items():
        relevant = [r for r in rows if START_MS <= int(r["t"]) < END_MS]
        if relevant:
            prev = int(relevant[0]["t"])
            for row in relevant[1:]:
                cur = int(row["t"])
                if cur - prev != base.BAR_MS[tf]:
                    raise RuntimeError(f"{tf} gap {fmt(prev)} -> {fmt(cur)}")
                prev = cur
    ts = {tf: [int(r["t"]) for r in rows] for tf, rows in data.items()}
    cache_patch.prime_one_minute(data["1m"], MODEL_WINDOW)

    results, sims = {}, {}
    for name in VARIANTS:
        sim, m = run(data, ts, meta, funding, variant=name)
        sims[name], results[name] = sim, m
        print("VARIANT", name, json.dumps({"trades": m["trades"], "net_pnl": m["net_pnl"],
              "return_pct": m["net_return_pct"], "win_rate": m["win_rate_pct"],
              "pf": m["profit_factor"], "dd": m["max_drawdown_usdt"]}, ensure_ascii=False), flush=True)

    baseline = sims["baseline"]
    baseline_m = results["baseline"]
    _, stress_m = run(data, ts, meta, funding, variant="baseline", stress_extra_bps=5.0)
    baseline_m["stress_extra_exit_bps"] = 5.0
    baseline_m["stress_net_pnl"] = stress_m["net_pnl"]
    baseline_m["stress_return_pct"] = stress_m["net_return_pct"]

    factor = factor_analysis(baseline.trades)
    comparison = {name: {"metrics": results[name], "periods": period_pack(sims[name].trades)} for name in VARIANTS}
    robust = []
    for name, block in comparison.items():
        m = block["metrics"]
        tr = block["periods"]["train_first120d"]
        va = block["periods"]["validation_last60d"]
        robust.append({"variant": name, "trades": m["trades"], "net_pnl": m["net_pnl"],
                       "profit_factor": m["profit_factor"], "max_drawdown_usdt": m["max_drawdown_usdt"],
                       "train_pf": tr["profit_factor"], "train_pnl": tr["net_pnl"],
                       "validation_pf": va["profit_factor"], "validation_pnl": va["net_pnl"],
                       "validation_trades": va["trades"]})
    robust.sort(key=lambda x: (min(x["train_pf"] if math.isfinite(x["train_pf"]) else 9.0,
                                   x["validation_pf"] if math.isfinite(x["validation_pf"]) else 9.0),
                               x["net_pnl"]), reverse=True)

    summary = {
        "baseline": baseline_m,
        "factor_analysis": factor,
        "variant_comparison": comparison,
        "robustness_ranking": robust,
        "research_notes": {
            "path_c_enabled": False,
            "factor_analysis_is_associational": True,
            "variant_search_is_in_sample_with_120d_60d_diagnostic_split": True,
            "historical_limit_reference": "closed 1m close proxy; no historical bid/ask orderbook",
        },
        "cache_stats": cache_patch.stats(),
    }
    (OUTDIR / "metrics.json").write_text(json.dumps(baseline_m, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTDIR / "factor_analysis.json").write_text(json.dumps(factor, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTDIR / "variant_comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTDIR / "summary_180d.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(baseline.trades, OUTDIR / "V1544_BTC_SWAP_180D_Trades.csv")

    with (OUTDIR / "variant_summary.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["variant", "trades", "win_rate_pct", "net_pnl", "net_return_pct", "profit_factor",
                  "expectancy", "max_drawdown_usdt", "train_pf", "train_pnl", "validation_pf",
                  "validation_pnl", "validation_trades"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in robust:
            m = results[row["variant"]]
            w.writerow({"variant": row["variant"], "trades": m["trades"], "win_rate_pct": m["win_rate_pct"],
                        "net_pnl": m["net_pnl"], "net_return_pct": m["net_return_pct"],
                        "profit_factor": m["profit_factor"], "expectancy": m["expectancy"],
                        "max_drawdown_usdt": m["max_drawdown_usdt"], "train_pf": row["train_pf"],
                        "train_pnl": row["train_pnl"], "validation_pf": row["validation_pf"],
                        "validation_pnl": row["validation_pnl"], "validation_trades": row["validation_trades"]})

    lines = [
        "# KAYTRADE V1.5.4 Build1544 — BTC 180D Research", "",
        f"- Window: {START.isoformat()} -> {END.isoformat()}",
        "- Path C: OFF", "- Consecutive-loss pause: OFF",
        "- Entry: LIMIT proxy from closed 1m close; pending TTL 60s",
        "- Stop: 1x 1H ATR; TP: 2R; 30m post-close cooldown", "", "## Baseline",
        f"- Trades: {baseline_m['trades']}",
        f"- Net PnL: {baseline_m['net_pnl']:+.2f} U ({baseline_m['net_return_pct']:+.3f}%)",
        f"- Win rate: {baseline_m['win_rate_pct']:.2f}%", f"- PF: {baseline_m['profit_factor']:.3f}",
        f"- Max DD: {baseline_m['max_drawdown_usdt']:.2f} U / {baseline_m['max_drawdown_pct']:.3f}%",
        f"- Stress +5bps exit: {baseline_m['stress_net_pnl']:+.2f} U ({baseline_m['stress_return_pct']:+.3f}%)",
        "", "## Binary factor ranking (expectancy delta, ON minus OFF)",
    ]
    for row in factor["binary_ranking_by_expectancy_delta"]:
        lines.append(f"- {row['factor']}: Δexp {row['expectancy_delta']:+.3f} U, Δwin {row['win_rate_delta_pct']:+.2f}pp, PF on/off {row['pf_on']:.3f}/{row['pf_off']:.3f}, n={row['on_trades']}/{row['off_trades']}")
    lines += ["", "## Variant robustness ranking"]
    for row in robust:
        lines.append(f"- {row['variant']}: PnL {row['net_pnl']:+.2f}, PF {row['profit_factor']:.3f}, DD {row['max_drawdown_usdt']:.2f}, train PF {row['train_pf']:.3f}, validation PF {row['validation_pf']:.3f} / PnL {row['validation_pnl']:+.2f}")
    lines += ["", "## Caveat",
              "- This is historical simulation, not future performance.",
              "- Factor effects are associations; correlated indicators can look predictive without being causal.",
              "- Variant search is exploratory. Use a fresh holdout/walk-forward window before changing production rules.",
              "- Historical bid/ask orderbook is unavailable here; LIMIT entries use the closed 1m close as a proxy and fill only if the next 1m range touches it."]
    (OUTDIR / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    print("V1544_180D_BASELINE")
    print(json.dumps(baseline_m, ensure_ascii=False, indent=2), flush=True)
    print("V1544_180D_FACTOR_RANKING")
    print(json.dumps(factor["binary_ranking_by_expectancy_delta"], ensure_ascii=False, indent=2), flush=True)
    print("V1544_180D_ROBUSTNESS")
    print(json.dumps(robust, ensure_ascii=False, indent=2), flush=True)
    print("CACHE_STATS", cache_patch.stats(), flush=True)
    print("OUTPUT_DIR", OUTDIR.resolve(), flush=True)


if __name__ == "__main__":
    main()

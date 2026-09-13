#!/usr/bin/env python3
from __future__ import annotations

import bisect
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

import research_v146_backtest as base
import v154_model as model
import v154_build1543_patch as build1543
import research_v1543_cache_patch as cache_patch

VERSION = '1.5.4'
BUILD = '1543'
START = datetime(2026, 6, 15, 3, 16, tzinfo=timezone.utc)
END = datetime(2026, 9, 13, 3, 16, tzinfo=timezone.utc)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
CAPITAL = 10_000.0
LEVERAGE = 5
RISK_USDT = 100.0
RISK_PCT = 1.0
DAILY_LOSS = 300.0
FIRST_SIGNAL_NOTIONAL = 1_000.0
SECOND_SIGNAL_NOTIONAL = FIRST_SIGNAL_NOTIONAL * 2.0
STOP_ATR = 1.0
REWARD_R = 2.0
MAKER_BPS = 2.0
TAKER_BPS = 5.0
SLIPPAGE_BPS = 5.0
EXPECTED_COST_MIN = 1.20
ORDER_TTL_MS = 60_000
COOLDOWN_MS = 30 * 60_000
MODEL_WINDOW = 1000
SH_TZ = ZoneInfo('Asia/Shanghai')
OUTDIR = Path('backtest_output_v1543_btc_90d')
OUTDIR.mkdir(parents=True, exist_ok=True)

for name, value in {
    'START': START, 'END': END, 'START_MS': START_MS, 'END_MS': END_MS,
    'CAPITAL': CAPITAL, 'LEVERAGE': LEVERAGE, 'RISK_USDT': RISK_USDT,
    'RISK_PCT': RISK_PCT, 'DAILY_LOSS': DAILY_LOSS,
    'COOLDOWN_MINUTES': 30, 'STOP_ATR': STOP_ATR, 'REWARD_R': REWARD_R,
}.items():
    setattr(base, name, value)


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace('+00:00', 'Z')


def closed_slice(data, ts, tf, close_ms):
    cutoff = int(close_ms) - base.BAR_MS[tf]
    idx = bisect.bisect_right(ts[tf], cutoff) - 1
    if idx < 200:
        return None
    start = max(0, idx - MODEL_WINDOW + 1)
    return data[tf][start:idx+1]


def side_dir(side):
    return 1.0 if side == '做多' else -1.0


def round_contract_btc(raw_btc, meta):
    unit = float(meta['ctVal']) * float(meta.get('ctMult') or 1.0)
    lot = Decimal(str(meta['lotSz']))
    min_sz = Decimal(str(meta['minSz']))
    contracts = Decimal(str(raw_btc / unit))
    contracts = (contracts / lot).to_integral_value(rounding=ROUND_DOWN) * lot
    if contracts < min_sz:
        return 0.0
    return float(contracts) * unit


def local_day(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(SH_TZ).strftime('%Y-%m-%d')


@dataclass
class Leg:
    cycle_id: int
    signal_type: str
    boll_path: str
    opportunity_id: str
    side: str
    limit: float
    stop: float
    target: float
    quantity_btc: float
    score: float
    submitted_ms: int
    expires_ms: int
    estimated_loss: float
    signal_close_ms: int
    score_components: dict = field(default_factory=dict)
    trigger_combination: dict = field(default_factory=dict)
    front_r: float = math.inf
    front_space_status: str = '空间未知'
    cost_r: float = math.inf
    state: str = 'pending'
    fill_bar_t: int = 0
    fill_time: int = 0
    entry_fee: float = 0.0
    funding_pnl: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    exit_time: int = 0
    net_pnl: float = 0.0

    @property
    def stop_distance(self):
        return abs(float(self.limit) - float(self.stop))


@dataclass
class Cycle:
    cycle_id: int
    side: str
    created_ms: int
    first_path: str
    first_opportunity_id: str
    first_signal_close_ms: int
    equity_before: float
    legs: list[Leg] = field(default_factory=list)
    path_c_opportunity: dict | None = None
    path_c_last_checked_bar_t: int = -1
    path_c_last_signal_bar_t: int = -1
    had_fill: bool = False
    first_fill_time: int = 0
    realized_pnl: float = 0.0


class Simulator:
    def __init__(self, meta, funding, stress_extra_bps=0.0):
        self.meta = meta
        self.funding = list(funding)
        self.funding_i = 0
        self.stress_extra_bps = float(stress_extra_bps)
        self.cash = CAPITAL
        self.opportunity = None
        self.rearm_until_ms = 0
        self.last_close_ms = 0
        self.daily_day = ''
        self.daily_peak = CAPITAL
        self.daily_block_day = ''
        self.cycle: Cycle | None = None
        self.next_cycle_id = 1
        self.trades = []
        self.cycles = []
        self.equity_curve = []
        self.stats = Counter()
        self.blockers = Counter()
        self.transitions = Counter()

    def active_legs(self):
        if not self.cycle:
            return []
        return [x for x in self.cycle.legs if x.state in ('pending', 'filled')]

    def filled_legs(self):
        if not self.cycle:
            return []
        return [x for x in self.cycle.legs if x.state == 'filled']

    def pending_leg(self, signal_type=None):
        for leg in self.active_legs():
            if leg.state == 'pending' and (signal_type is None or leg.signal_type == signal_type):
                return leg
        return None

    def path_c_filled(self):
        return bool(self.cycle and any(x.signal_type == 'path_c_second' and x.state in ('filled', 'closed') for x in self.cycle.legs))

    def first_leg(self):
        if not self.cycle:
            return None
        return next((x for x in self.cycle.legs if x.signal_type == 'first_signal'), None)

    def equity(self, mark):
        eq = float(self.cash)
        for leg in self.filled_legs():
            d = side_dir(leg.side)
            eq += (float(mark) - leg.limit) * leg.quantity_btc * d
            eq -= leg.entry_fee
            eq += leg.funding_pnl
        return eq

    def mark_risk(self, ms, mark):
        eq = self.equity(mark)
        day = local_day(ms)
        if day != self.daily_day:
            self.daily_day = day
            self.daily_peak = eq
            self.daily_block_day = ''
        self.daily_peak = max(self.daily_peak, eq)
        if self.daily_peak - eq >= DAILY_LOSS:
            self.daily_block_day = day
        self.equity_curve.append((int(ms), float(eq)))
        return eq

    def daily_remaining(self, ms, mark):
        eq = self.equity(mark)
        day = local_day(ms)
        if day != self.daily_day:
            return DAILY_LOSS
        return max(0.0, DAILY_LOSS - max(0.0, self.daily_peak - eq))

    def apply_funding_until(self, ms, mark):
        while self.funding_i < len(self.funding) and int(self.funding[self.funding_i][0]) <= int(ms):
            _, rate = self.funding[self.funding_i]
            for leg in self.filled_legs():
                amount = float(mark) * leg.quantity_btc * float(rate)
                leg.funding_pnl += -amount if leg.side == '做多' else amount
            self.funding_i += 1

    def _fill_leg(self, leg, bar):
        if int(bar['t']) >= int(leg.expires_ms):
            leg.state = 'canceled'
            self.stats[f'{leg.signal_type}_limit_unfilled'] += 1
            return False
        if not (float(bar['l']) <= leg.limit <= float(bar['h'])):
            return False
        leg.state = 'filled'
        leg.fill_bar_t = int(bar['t'])
        leg.fill_time = int(bar['t'])
        leg.entry_fee = leg.limit * leg.quantity_btc * MAKER_BPS / 10000.0
        self.cycle.had_fill = True
        if leg.signal_type == 'first_signal' and not self.cycle.first_fill_time:
            self.cycle.first_fill_time = int(bar['t'])
        self.stats[f'{leg.signal_type}_limit_filled'] += 1
        return True

    def process_pending(self, bar):
        if not self.cycle:
            return
        for leg in list(self.cycle.legs):
            if leg.state == 'pending':
                self._fill_leg(leg, bar)

    def _close_leg(self, leg, bar, reason):
        trigger = leg.stop if reason == 'SL' else leg.target
        slip = (SLIPPAGE_BPS + self.stress_extra_bps) / 10000.0
        exit_px = trigger * (1.0 - slip if leg.side == '做多' else 1.0 + slip)
        direction = side_dir(leg.side)
        gross = (exit_px - leg.limit) * leg.quantity_btc * direction
        exit_fee = exit_px * leg.quantity_btc * TAKER_BPS / 10000.0
        net = gross - leg.entry_fee - exit_fee + leg.funding_pnl
        exit_ms = int(bar['t']) + 60_000
        leg.state = 'closed'
        leg.exit_time = exit_ms
        leg.net_pnl = net
        self.cash += net
        self.cycle.realized_pnl += net
        r_denom = leg.stop_distance * leg.quantity_btc
        row = {
            'cycle_id': leg.cycle_id, 'signal_type': leg.signal_type, 'boll_path': leg.boll_path,
            'side': leg.side, 'score': leg.score, 'entry': leg.limit, 'stop': leg.stop,
            'target': leg.target, 'exit': exit_px, 'reason': reason,
            'quantity_btc': leg.quantity_btc, 'net_pnl': net, 'gross_pnl': gross,
            'entry_fee': leg.entry_fee, 'exit_fee': exit_fee, 'funding_pnl': leg.funding_pnl,
            'realized_r': net / r_denom if r_denom > 0 else 0.0,
            'mfe_r': leg.mfe / leg.stop_distance if leg.stop_distance > 0 else 0.0,
            'mae_r': leg.mae / leg.stop_distance if leg.stop_distance > 0 else 0.0,
            'front_r': leg.front_r, 'front_space_status': leg.front_space_status,
            'cost_r': leg.cost_r, 'entry_time': leg.fill_time, 'exit_time': exit_ms,
            'hold_min': (exit_ms - leg.fill_time) / 60_000.0,
            'opportunity_id': leg.opportunity_id,
            'score_components': dict(leg.score_components),
            'trigger_combination': dict(leg.trigger_combination),
        }
        self.trades.append(row)
        self.stats[f'{leg.signal_type}_closed'] += 1

    def process_exits(self, bar):
        if not self.cycle:
            return
        for leg in list(self.filled_legs()):
            if int(bar['t']) <= int(leg.fill_bar_t):
                continue
            high, low = float(bar['h']), float(bar['l'])
            if leg.side == '做多':
                leg.mfe = max(leg.mfe, max(0.0, high - leg.limit))
                leg.mae = max(leg.mae, max(0.0, leg.limit - low))
                hit_sl, hit_tp = low <= leg.stop, high >= leg.target
            else:
                leg.mfe = max(leg.mfe, max(0.0, leg.limit - low))
                leg.mae = max(leg.mae, max(0.0, high - leg.limit))
                hit_sl, hit_tp = high >= leg.stop, low <= leg.target
            if hit_sl or hit_tp:
                self._close_leg(leg, bar, 'SL' if hit_sl else 'TP2R')

    def _cycle_can_end(self):
        return bool(self.cycle and not any(x.state in ('pending', 'filled') for x in self.cycle.legs))

    def maybe_finish_cycle(self, now_ms):
        if not self._cycle_can_end():
            return
        c = self.cycle
        self.last_close_ms = int(now_ms)
        if c.had_fill:
            closed = [x for x in c.legs if x.state == 'closed']
            first_entry = min((x.fill_time for x in closed if x.fill_time), default=c.created_ms)
            path_c_used = any(x.signal_type == 'path_c_second' and x.state == 'closed' for x in closed)
            record = {
                'cycle_id': c.cycle_id, 'side': c.side, 'first_path': c.first_path,
                'path_c_used': path_c_used, 'legs': len(closed), 'net_pnl': sum(x.net_pnl for x in closed),
                'entry_time': first_entry, 'exit_time': int(now_ms),
                'hold_min': (int(now_ms) - first_entry) / 60_000.0,
            }
            self.cycles.append(record)
            self.stats['cycles_closed'] += 1
            if path_c_used:
                self.stats['cycles_with_path_c'] += 1
        else:
            self.stats['zero_fill_cycles'] += 1
        self.cycle = None

    def entry_block_reason(self, now_ms):
        if self.daily_block_day == local_day(now_ms):
            return 'daily_drawdown'
        if self.last_close_ms and int(now_ms) - int(self.last_close_ms) < COOLDOWN_MS:
            return 'cooldown'
        return ''

    def build_plan(self, side, entry, opp, score, now_ms, mark, multiplier, notional_cap, risk_remaining_override=None):
        stop_distance = float(opp['atr1h']) * STOP_ATR
        d = side_dir(side)
        stop = entry - d * stop_distance
        target = entry + d * stop_distance * REWARD_R
        eq = self.equity(mark)
        daily_remaining = self.daily_remaining(now_ms, mark)
        if risk_remaining_override is not None:
            daily_remaining = min(daily_remaining, float(risk_remaining_override))
        risk_capital = min(CAPITAL, eq)
        base_risk = min(RISK_USDT, risk_capital * RISK_PCT / 100.0)
        risk_budget = min(base_risk * float(multiplier), daily_remaining)
        if risk_budget <= 0:
            return None, ['风险预算不足']
        maker = MAKER_BPS / 10000.0
        taker = TAKER_BPS / 10000.0
        slip = SLIPPAGE_BPS / 10000.0
        risk_entry_fee = max(maker, taker)
        per_btc = stop_distance + entry * risk_entry_fee + stop * (taker + slip)
        cap = min(float(notional_cap), risk_capital * LEVERAGE, max(eq, 0.0) * 0.9 * LEVERAGE)
        raw_btc = min(risk_budget / per_btc, cap / entry)
        btc = round_contract_btc(raw_btc, self.meta)
        if btc <= 0:
            return None, ['低于交易所最小下单量']
        expected_cost_per_btc = entry * maker + target * taker
        expected_fee_multiple = (2.0 * stop_distance / expected_cost_per_btc) if expected_cost_per_btc > 0 else math.inf
        if expected_fee_multiple < EXPECTED_COST_MIN:
            return None, [f'2R/预计手续费 {expected_fee_multiple:.2f}x < {EXPECTED_COST_MIN:.2f}x']
        worst_cost_per_btc = entry * risk_entry_fee + target * (taker + slip)
        worst_roundtrip_cost = btc * worst_cost_per_btc
        estimated_loss = btc * per_btc
        plan = {
            'px': str(entry), 'sl': stop, 'tp': target, 'btc': btc,
            'stop_distance': stop_distance, 'notional': btc * entry,
            'estimated_loss': estimated_loss, 'worst_roundtrip_cost': worst_roundtrip_cost,
            'expected_fee_multiple': expected_fee_multiple,
        }
        ok, diag, blockers = model.execution_checks(plan, opp, score)
        if not ok:
            return None, list(blockers)
        plan.update(diag)
        return plan, []

    def submit_first(self, result, now_ms, mark):
        side = result.get('side')
        if side not in ('做多', '做空'):
            return False
        score = (result.get('scores') or {}).get(side) or {}
        if not score.get('eligible') or float(score.get('total') or 0.0) < float(model.THRESHOLD):
            return False
        self.stats['first_eligible_states'] += 1
        reason = self.entry_block_reason(now_ms)
        if reason:
            self.stats[f'{reason}_blocks'] += 1
            return False
        opp = result.get('opportunity') or score.get('opportunity')
        if not isinstance(opp, dict):
            self.stats['missing_first_opportunity'] += 1
            return False
        plan, blockers = self.build_plan(side, float(mark), opp, score, now_ms, mark, 1.0, FIRST_SIGNAL_NOTIONAL)
        if not plan:
            self.stats['first_execution_blocks'] += 1
            for text in blockers:
                self.blockers['first: ' + text] += 1
            return False
        cid = self.next_cycle_id
        self.next_cycle_id += 1
        c = Cycle(
            cycle_id=cid, side=side, created_ms=int(now_ms), first_path=str(opp.get('signal_path') or ''),
            first_opportunity_id=str(opp.get('id') or ''), first_signal_close_ms=int(opp.get('signal_close_ms') or 0),
            equity_before=self.equity(mark),
        )
        trig = dict((score.get('confirmations') or {}).get('trigger') or {})
        trig['boll_path'] = str(opp.get('signal_path') or trig.get('boll_path') or '')
        leg = Leg(
            cycle_id=cid, signal_type='first_signal', boll_path=str(opp.get('signal_path') or ''),
            opportunity_id=str(opp.get('id') or ''), side=side, limit=float(mark), stop=float(plan['sl']),
            target=float(plan['tp']), quantity_btc=float(plan['btc']), score=float(score['total']),
            submitted_ms=int(now_ms), expires_ms=int(now_ms) + ORDER_TTL_MS,
            estimated_loss=float(plan['estimated_loss']), signal_close_ms=int(opp.get('signal_close_ms') or 0),
            score_components=dict(score.get('layers') or {}), trigger_combination=trig,
            front_r=float(plan.get('front_r', math.inf)), front_space_status=str(plan.get('front_space_status', '空间未知')),
            cost_r=float(plan.get('cost_r', math.inf)),
        )
        c.legs.append(leg)
        self.cycle = c
        self.rearm_until_ms = int(opp.get('signal_close_ms') or now_ms) + 5 * 60_000
        self.opportunity = None
        self.stats['first_limit_submitted'] += 1
        return True

    def first_path_a_open(self):
        if not self.cycle or self.cycle.side != '做多' or self.cycle.first_path != 'middle_rsi':
            return False
        first = self.first_leg()
        return bool(first and first.state == 'filled')

    def active_dict_for_path_c(self):
        c = self.cycle
        first = self.first_leg()
        return {
            'side': '做多', 'boll_signal_path': 'middle_rsi', 'filled': True, 'protected': True,
            'signal_close_ms': c.first_signal_close_ms,
            'path_c_last_signal_bar_t': c.path_c_last_signal_bar_t,
            'legs': [{'state': 'filled', 'path_c': False}],
        }

    def evaluate_path_c(self, slices, now_ms):
        if not self.first_path_a_open() or self.path_c_filled() or self.pending_leg('path_c_second'):
            return None
        c = self.cycle
        five_t = int(slices['5m'][-1]['t'])
        if c.path_c_opportunity is None and five_t > c.path_c_last_checked_bar_t:
            c.path_c_last_checked_bar_t = five_t
            active = self.active_dict_for_path_c()
            opp = build1543._new_path_c_opportunity(active, {
                '1H': slices['1H'], '15m': slices['15m'], '5m': slices['5m'], '1m': slices['1m'], '4H': slices['4H']
            })
            c.path_c_last_signal_bar_t = int(active.get('path_c_last_signal_bar_t') or c.path_c_last_signal_bar_t)
            if isinstance(opp, dict):
                c.path_c_opportunity = dict(opp)
                c.path_c_last_signal_bar_t = int(opp.get('signal_bar_t') or five_t)
                self.stats['path_c_opportunities'] += 1
        opp = c.path_c_opportunity
        if not isinstance(opp, dict):
            return None
        result, new_opp, transition = model.evaluate(
            slices['1H'], slices['15m'], slices['5m'], slices['1m'], slices['4H'],
            opportunity=opp, stop_atr=STOP_ATR, maker_bps=MAKER_BPS,
            taker_bps=TAKER_BPS, slippage_bps=SLIPPAGE_BPS,
            now_ms=int(now_ms), allow_new=False,
        )
        if transition:
            self.transitions['path_c_' + str(transition[0])] += 1
        if not isinstance(new_opp, dict):
            c.path_c_opportunity = None
            self.stats['path_c_invalidated'] += 1
            return None
        new_opp = dict(new_opp)
        new_opp['path_c'] = True
        new_opp['signal_path'] = build1543.PATH_C_LABEL
        c.path_c_opportunity = new_opp
        score = dict((result.get('scores') or {}).get('做多') or {})
        score['opportunity'] = new_opp
        confirmations = dict(score.get('confirmations') or {})
        confirmations['path_c'] = True
        confirmations['path_c_rsi_required'] = False
        confirmations['first_signal_path'] = 'middle_rsi'
        score['confirmations'] = confirmations
        if not score.get('eligible') or not score.get('gate') or float(score.get('total') or 0.0) < float(model.THRESHOLD):
            self.stats['path_c_score_blocks'] += 1
            return None
        return result, score, new_opp

    def submit_path_c(self, evaluated, now_ms, mark):
        if not evaluated or not self.cycle or self.pending_leg('path_c_second') or self.path_c_filled():
            return False
        if self.daily_block_day == local_day(now_ms):
            self.stats['path_c_daily_drawdown_blocks'] += 1
            return False
        _result, score, opp = evaluated
        filled_risk = sum(x.estimated_loss for x in self.filled_legs())
        remaining = self.daily_remaining(now_ms, mark) - filled_risk
        if remaining <= 0:
            self.stats['path_c_risk_blocks'] += 1
            return False
        plan, blockers = self.build_plan('做多', float(mark), opp, score, now_ms, mark, 2.0, SECOND_SIGNAL_NOTIONAL, remaining)
        if not plan:
            self.stats['path_c_execution_blocks'] += 1
            for text in blockers:
                self.blockers['path_c: ' + text] += 1
            return False
        trig = dict((score.get('confirmations') or {}).get('trigger') or {})
        trig['boll_path'] = build1543.PATH_C_LABEL
        trig['path_c_rsi_required'] = False
        leg = Leg(
            cycle_id=self.cycle.cycle_id, signal_type='path_c_second', boll_path=build1543.PATH_C_LABEL,
            opportunity_id=str(opp.get('id') or ''), side='做多', limit=float(mark), stop=float(plan['sl']),
            target=float(plan['tp']), quantity_btc=float(plan['btc']), score=float(score['total']),
            submitted_ms=int(now_ms), expires_ms=int(now_ms) + ORDER_TTL_MS,
            estimated_loss=float(plan['estimated_loss']), signal_close_ms=int(opp.get('signal_close_ms') or 0),
            score_components=dict(score.get('layers') or {}), trigger_combination=trig,
            front_r=float(plan.get('front_r', math.inf)), front_space_status=str(plan.get('front_space_status', '空间未知')),
            cost_r=float(plan.get('cost_r', math.inf)),
        )
        self.cycle.legs.append(leg)
        self.stats['path_c_limit_submitted'] += 1
        return True

    def finish(self, last_bar):
        if not self.cycle:
            return
        for leg in self.cycle.legs:
            if leg.state == 'pending':
                leg.state = 'canceled'
                self.stats[f'{leg.signal_type}_limit_unfilled'] += 1
        for leg in list(self.filled_legs()):
            slip = (SLIPPAGE_BPS + self.stress_extra_bps) / 10000.0
            exit_px = float(last_bar['c']) * (1.0 - slip if leg.side == '做多' else 1.0 + slip)
            direction = side_dir(leg.side)
            gross = (exit_px - leg.limit) * leg.quantity_btc * direction
            exit_fee = exit_px * leg.quantity_btc * TAKER_BPS / 10000.0
            net = gross - leg.entry_fee - exit_fee + leg.funding_pnl
            exit_ms = int(last_bar['t']) + 60_000
            leg.state = 'closed'; leg.exit_time = exit_ms; leg.net_pnl = net
            self.cash += net; self.cycle.realized_pnl += net
            denom = leg.stop_distance * leg.quantity_btc
            self.trades.append({
                'cycle_id': leg.cycle_id, 'signal_type': leg.signal_type, 'boll_path': leg.boll_path,
                'side': leg.side, 'score': leg.score, 'entry': leg.limit, 'stop': leg.stop, 'target': leg.target,
                'exit': exit_px, 'reason': 'EOD', 'quantity_btc': leg.quantity_btc, 'net_pnl': net,
                'gross_pnl': gross, 'entry_fee': leg.entry_fee, 'exit_fee': exit_fee,
                'funding_pnl': leg.funding_pnl, 'realized_r': net / denom if denom else 0.0,
                'mfe_r': leg.mfe / leg.stop_distance if leg.stop_distance else 0.0,
                'mae_r': leg.mae / leg.stop_distance if leg.stop_distance else 0.0,
                'front_r': leg.front_r, 'front_space_status': leg.front_space_status, 'cost_r': leg.cost_r,
                'entry_time': leg.fill_time, 'exit_time': exit_ms,
                'hold_min': (exit_ms - leg.fill_time) / 60_000.0, 'opportunity_id': leg.opportunity_id,
                'score_components': dict(leg.score_components), 'trigger_combination': dict(leg.trigger_combination),
            })
        self.maybe_finish_cycle(int(last_bar['t']) + 60_000)


def run(data, ts, meta, funding, stress_extra_bps=0.0):
    sim = Simulator(meta, funding, stress_extra_bps=stress_extra_bps)
    d1 = data['1m']
    start_i = bisect.bisect_left(ts['1m'], START_MS)
    if start_i < 1:
        raise RuntimeError('not enough 1m warmup')
    last_bar = None
    for i in range(start_i, len(d1)):
        bar = d1[i]
        if int(bar['t']) >= END_MS:
            break
        last_bar = bar
        bar_close = int(bar['t']) + 60_000
        sim.apply_funding_until(int(bar['t']), float(bar['o']))
        sim.process_pending(bar)
        sim.process_exits(bar)
        sim.apply_funding_until(bar_close, float(bar['c']))
        sim.mark_risk(bar_close, float(bar['c']))
        sim.maybe_finish_cycle(bar_close)

        if sim.rearm_until_ms and bar_close >= sim.rearm_until_ms:
            sim.rearm_until_ms = 0
            sim.stats['rearmed'] += 1

        slices = {}
        valid = True
        for tf in ('1m', '5m', '15m', '1H', '4H'):
            rows = closed_slice(data, ts, tf, bar_close)
            if rows is None:
                valid = False
                break
            slices[tf] = rows
        if not valid:
            continue

        if sim.cycle is None:
            old_opp = dict(sim.opportunity) if isinstance(sim.opportunity, dict) else None
            result, new_opp, transition = model.evaluate(
                slices['1H'], slices['15m'], slices['5m'], slices['1m'], slices['4H'],
                opportunity=sim.opportunity, stop_atr=STOP_ATR, maker_bps=MAKER_BPS,
                taker_bps=TAKER_BPS, slippage_bps=SLIPPAGE_BPS, now_ms=bar_close,
                allow_new=(sim.rearm_until_ms == 0),
            )
            sim.opportunity = new_opp
            if transition:
                sim.transitions[str(transition[0])] += 1
                if transition[0] == 'invalidated' and old_opp:
                    sim.rearm_until_ms = int(old_opp.get('signal_close_ms') or bar_close) + 5 * 60_000
                    sim.opportunity = None
            if result.get('status') == '允许开仓':
                sim.stats['first_allow_entry_states'] += 1
            sim.submit_first(result, bar_close, float(bar['c']))
        else:
            evaluated = sim.evaluate_path_c(slices, bar_close)
            sim.submit_path_c(evaluated, bar_close, float(bar['c']))

        if (i - start_i) % 10000 == 0:
            print(
                f"progress {i-start_i:,}, equity={sim.equity(bar['c']):.2f}, cycles={len(sim.cycles)}, "
                f"legs={len(sim.trades)}, active={bool(sim.cycle)}, pathC={sim.stats.get('path_c_limit_filled',0)}",
                flush=True,
            )

    if last_bar is None:
        raise RuntimeError('no test bars')
    sim.finish(last_bar)
    return sim, last_bar


def group_stats(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[str(row.get(key, 'unknown'))].append(float(row['net_pnl']))
    out = {}
    for name, vals in sorted(groups.items()):
        gp = sum(x for x in vals if x > 0); gl = -sum(x for x in vals if x < 0)
        out[name] = {
            'count': len(vals), 'wins': sum(x > 0 for x in vals),
            'win_rate_pct': 100 * sum(x > 0 for x in vals) / len(vals),
            'net_pnl': sum(vals), 'profit_factor': gp / gl if gl > 0 else math.inf,
        }
    return out


def metrics(sim, first_px, last_px):
    cycles = sim.cycles
    nets = [float(x['net_pnl']) for x in cycles]
    wins = [x for x in nets if x > 0]; losses = [x for x in nets if x < 0]
    peak = -math.inf; max_dd = 0.0; max_dd_pct = 0.0
    for _, eq in sim.equity_curve:
        peak = max(peak, eq)
        dd = max(0.0, peak - eq)
        max_dd = max(max_dd, dd)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd / peak * 100.0)
    gp = sum(wins); gl = -sum(losses)
    leg_nets = [float(t['net_pnl']) for t in sim.trades]
    path_c = [t for t in sim.trades if t['signal_type'] == 'path_c_second']
    first_legs = [t for t in sim.trades if t['signal_type'] == 'first_signal']
    by_side = group_stats(cycles, 'side')
    by_first_path = group_stats(cycles, 'first_path')
    by_signal_type = group_stats(sim.trades, 'signal_type')
    by_leg_path = group_stats(sim.trades, 'boll_path')
    return {
        'strategy_version': VERSION, 'build': BUILD, 'days': 90,
        'start_utc': START.isoformat(), 'end_utc': END.isoformat(), 'capital': CAPITAL,
        'ending_equity': sim.cash, 'net_pnl': sim.cash - CAPITAL,
        'net_return_pct': (sim.cash / CAPITAL - 1.0) * 100.0,
        'cycles': len(cycles), 'cycle_wins': len(wins), 'cycle_losses': len(losses),
        'cycle_win_rate_pct': 100 * len(wins) / len(cycles) if cycles else 0.0,
        'profit_factor': gp / gl if gl > 0 else math.inf,
        'expectancy_per_cycle': sum(nets) / len(nets) if nets else 0.0,
        'avg_cycle_win': statistics.mean(wins) if wins else 0.0,
        'avg_cycle_loss': statistics.mean(losses) if losses else 0.0,
        'payoff_ratio': statistics.mean(wins) / abs(statistics.mean(losses)) if wins and losses else math.inf,
        'legs_closed': len(sim.trades),
        'leg_win_rate_pct': 100 * sum(x > 0 for x in leg_nets) / len(leg_nets) if leg_nets else 0.0,
        'first_legs_closed': len(first_legs), 'path_c_legs_closed': len(path_c),
        'path_c_leg_net_pnl': sum(float(t['net_pnl']) for t in path_c),
        'path_c_leg_win_rate_pct': 100 * sum(float(t['net_pnl']) > 0 for t in path_c) / len(path_c) if path_c else 0.0,
        'cycles_with_path_c': sum(bool(x.get('path_c_used')) for x in cycles),
        'max_drawdown_usdt': max_dd, 'max_drawdown_pct': max_dd_pct,
        'fees': sum(float(t['entry_fee']) + float(t['exit_fee']) for t in sim.trades),
        'funding_pnl': sum(float(t['funding_pnl']) for t in sim.trades),
        'avg_cycle_hold_min': statistics.mean([x['hold_min'] for x in cycles]) if cycles else 0.0,
        'median_cycle_hold_min': statistics.median([x['hold_min'] for x in cycles]) if cycles else 0.0,
        'btc_buy_hold_pct': (last_px / first_px - 1.0) * 100.0,
        'by_side': by_side, 'by_first_path': by_first_path,
        'by_signal_type': by_signal_type, 'by_leg_boll_path': by_leg_path,
        'funnel': dict(sim.stats), 'transition_counts': dict(sim.transitions),
        'execution_blockers': dict(sim.blockers),
        'entry_threshold': model.THRESHOLD, 'front_space_min_r': model.FRONT_MIN_R,
        'cost_max_r': model.COST_MAX_R, 'stop_atr_timeframe': '1H',
        'stop_atr_multiplier': STOP_ATR, 'reward_r': REWARD_R,
        'first_signal_notional': FIRST_SIGNAL_NOTIONAL,
        'second_signal_notional': SECOND_SIGNAL_NOTIONAL,
        'path_c_enabled': True, 'path_c_side': '做多', 'path_c_rsi_required': False,
        'loss_pause_enabled': False, 'cooldown_minutes': 30,
        'entry_order_type': 'LIMIT', 'pending_order_ttl_seconds': 60,
        'boll_wall_clock_expiry_enabled': False,
        'historical_limit_reference': 'closed 1m close proxy; not historical orderbook bid/ask',
        'exit_model': 'TP/SL trigger -> market exit with 5bps slippage; same-bar dual hit uses SL first',
        'model_window_bars': MODEL_WINDOW,
    }


def write_outputs(sim, m, stress_m):
    m['stress_return_pct'] = stress_m['net_return_pct']
    m['stress_net_pnl'] = stress_m['net_pnl']
    (OUTDIR / 'metrics.json').write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')

    trade_fields = [
        'cycle_id','signal_type','boll_path','entry_time','exit_time','side','score','entry','stop','target','exit','reason',
        'quantity_btc','net_pnl','gross_pnl','entry_fee','exit_fee','funding_pnl','realized_r','mfe_r','mae_r',
        'front_r','front_space_status','cost_r','hold_min','opportunity_id','score_components','trigger_combination'
    ]
    with (OUTDIR / 'V1543_BTC_SWAP_90D_Legs.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=trade_fields); w.writeheader()
        for t in sim.trades:
            row = {k: t.get(k, '') for k in trade_fields}
            row['entry_time'] = fmt(int(t['entry_time'])); row['exit_time'] = fmt(int(t['exit_time']))
            row['score_components'] = json.dumps(row['score_components'], ensure_ascii=False, sort_keys=True)
            row['trigger_combination'] = json.dumps(row['trigger_combination'], ensure_ascii=False, sort_keys=True)
            w.writerow(row)

    cycle_fields = ['cycle_id','entry_time','exit_time','side','first_path','path_c_used','legs','net_pnl','hold_min']
    with (OUTDIR / 'V1543_BTC_SWAP_90D_Cycles.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cycle_fields); w.writeheader()
        for c in sim.cycles:
            row = {k: c.get(k, '') for k in cycle_fields}
            row['entry_time'] = fmt(int(c['entry_time'])); row['exit_time'] = fmt(int(c['exit_time']))
            w.writerow(row)

    with (OUTDIR / 'V1543_BTC_SWAP_90D_Equity.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['time_utc','equity_usdt'])
        for t, e in sim.equity_curve:
            w.writerow([fmt(t), f'{e:.8f}'])

    lines = [
        '# KAYTRADE V1.5.4 Build 1543 — BTC-USDT-SWAP 90天历史回测','',
        f'**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（固定90天，与上一轮可比）  ',
        '**模型：** Build 1543；BOLL机会不设墙钟过期；首仓LIMIT；做多路径A首仓成交后，后续5m触下轨可触发路径C第二信号；路径C不要求RSI；第二信号资金固定为第一信号2倍；取消连续亏损停开；1H ATR×1止损；整仓2R。','',
        '## 核心结果','', '| 指标 | 结果 |','|---|---:|',
        f"| 期末权益 | {m['ending_equity']:.2f} U |",
        f"| 净收益 | {m['net_pnl']:+.2f} U |",
        f"| 收益率 | {m['net_return_pct']:+.3f}% |",
        f"| 压力情景收益率（退出额外5bps） | {m['stress_return_pct']:+.3f}% |",
        f"| 完整交易轮次 | {m['cycles']} |",
        f"| 轮次胜率 | {m['cycle_win_rate_pct']:.2f}% |",
        f"| Profit Factor | {'∞' if math.isinf(m['profit_factor']) else format(m['profit_factor'], '.3f')} |",
        f"| 每轮期望 | {m['expectancy_per_cycle']:+.3f} U |",
        f"| 单腿平仓数 | {m['legs_closed']} |",
        f"| 路径C平仓腿 | {m['path_c_legs_closed']} |",
        f"| 路径C腿净PnL | {m['path_c_leg_net_pnl']:+.2f} U |",
        f"| 最大回撤 | {m['max_drawdown_usdt']:.2f} U / {m['max_drawdown_pct']:.3f}% |",
        f"| 手续费 | {m['fees']:.2f} U |",
        f"| Funding | {m['funding_pnl']:+.3f} U |",'',
        '## 回测口径','',
        f'- 起始资金 {CAPITAL:.0f}U；第一信号名义仓位上限 {FIRST_SIGNAL_NOTIONAL:.0f}U；第二信号自动 {SECOND_SIGNAL_NOTIONAL:.0f}U。',
        '- 取消连续亏损停开；保留日内300U权益回撤限制与整轮结束后30分钟冷却。',
        '- 第一信号与路径C均按LIMIT模拟；历史OKX接口没有历史盘口，因此限价参考价使用当时已收盘1m close代理，下一根1m高低区间触及才视为成交。',
        '- LIMIT单每次最多保留60秒；BOLL机会本身不设1分钟/4分钟墙钟截止。',
        '- 路径C只做多；仅当第一信号来自middle_rsi路径且已成交时，后续新的已收盘5m触及BOLL下轨才创建；RSI不参与路径C。',
        '- 每条腿使用各自入场时的1H ATR×1止损、2R整仓止盈；TP/SL触发后按市价退出，计Taker 5bps+5bps滑点。',
        '- 同一1m同时触发TP与SL时保守按SL优先；成交所在1m不用于成交后的TP/SL判断。',
        '- 本结果是历史模拟，不代表未来收益。',
    ]
    (OUTDIR / 'V1543_BTC_SWAP_90D_Backtest_Report.md').write_text('\n'.join(lines), encoding='utf-8')


def make_summary(sim, m):
    groups = {'month': defaultdict(list), 'weekday': defaultdict(list), 'path_c': defaultdict(list)}
    for c in sim.cycles:
        dt = datetime.fromtimestamp(int(c['entry_time']) / 1000, tz=timezone.utc).astimezone(SH_TZ)
        groups['month'][dt.strftime('%Y-%m')].append(float(c['net_pnl']))
        groups['weekday'][dt.strftime('%a')].append(float(c['net_pnl']))
        groups['path_c']['with_path_c' if c.get('path_c_used') else 'without_path_c'].append(float(c['net_pnl']))
    def pack(d):
        out = {}
        for k, vals in sorted(d.items()):
            gp = sum(x for x in vals if x > 0); gl = -sum(x for x in vals if x < 0)
            out[k] = {'cycles': len(vals), 'wins': sum(x > 0 for x in vals),
                      'win_rate_pct': 100 * sum(x > 0 for x in vals) / len(vals),
                      'net_pnl': sum(vals), 'profit_factor': gp / gl if gl > 0 else math.inf}
        return out
    by_score = defaultdict(list)
    for t in sim.trades:
        by_score[f"{float(t['score']):.1f}|{t['signal_type']}"] .append(float(t['net_pnl']))
    summary = {
        'metrics': m,
        'by_month_cn': pack(groups['month']),
        'by_weekday_cn': pack(groups['weekday']),
        'cycles_by_path_c_usage': pack(groups['path_c']),
        'legs_by_score_and_type': pack(by_score),
    }
    (OUTDIR / 'summary_90d.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def main():
    print('V1.5.4 Build1543 fixed 90D backtest', START, END, flush=True)
    assert model.VERSION == '1.5.4' and model.ENTRY_WINDOW_MS == 0 and model.TIME_WINDOW_ENABLED is False
    assert build1543.BUILD == '154.3' and build1543.PATH_C_LABEL == 'path_c_lower_band'
    meta = base.fetch_instrument()
    data = {tf: base.fetch_candles(tf) for tf in ('1m','5m','15m','1H','4H')}
    funding = base.fetch_funding()
    cache_patch.prime_one_minute(data['1m'], MODEL_WINDOW)
    ts = {tf: [r['t'] for r in rows] for tf, rows in data.items()}
    sim, last_bar = run(data, ts, meta, funding, 0.0)
    # Stress replay reuses the already fetched market data; only exit slippage changes.
    stress, _ = run(data, ts, meta, funding, 5.0)
    first = next(r for r in data['1m'] if int(r['t']) >= START_MS)
    m = metrics(sim, float(first['c']), float(last_bar['c']))
    stress_m = metrics(stress, float(first['c']), float(last_bar['c']))
    write_outputs(sim, m, stress_m)
    summary = make_summary(sim, m)
    print('V1543_90D_METRICS')
    print(json.dumps(m, ensure_ascii=False, indent=2), flush=True)
    print('V1543_90D_SUMMARY')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print('CACHE_STATS', cache_patch.stats(), flush=True)
    print('OUTPUT_DIR', OUTDIR.resolve(), flush=True)


if __name__ == '__main__':
    main()

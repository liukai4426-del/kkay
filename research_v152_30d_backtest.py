#!/usr/bin/env python3
from __future__ import annotations

import bisect
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

import research_v146_backtest as base
import v152_model as model
import v152_backtest_core as bt

END = datetime.now(timezone.utc).replace(second=0, microsecond=0)
START = END - timedelta(days=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)

CAPITAL = 10_000.0
LEVERAGE = 5
RISK_USDT = 100.0
RISK_PCT = 1.0
DAILY_LOSS = 300.0
POSITION_CAP = 1_000.0
STOP_ATR = 1.0
REWARD_R = 2.0
MAKER_BPS = 2.0
TAKER_BPS = 5.0
SLIPPAGE_BPS = 5.0
ENTRY_VALID_MS = 5 * 60_000
MODEL_WINDOW = 1000
SH_TZ = ZoneInfo('Asia/Shanghai')
OUTDIR = Path('backtest_output_v152_btc_30d')
OUTDIR.mkdir(parents=True, exist_ok=True)

for name, value in {
    'START': START, 'END': END, 'START_MS': START_MS, 'END_MS': END_MS,
    'CAPITAL': CAPITAL, 'LEVERAGE': LEVERAGE, 'RISK_USDT': RISK_USDT,
    'RISK_PCT': RISK_PCT, 'DAILY_LOSS': DAILY_LOSS,
    'COOLDOWN_MINUTES': 30, 'STOP_ATR': STOP_ATR, 'REWARD_R': REWARD_R,
}.items():
    setattr(base, name, value)


def fmt(ms):
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).isoformat().replace('+00:00','Z')


def closed_slice(data, ts, tf, close_ms):
    cutoff = int(close_ms) - base.BAR_MS[tf]
    idx = bisect.bisect_right(ts[tf], cutoff) - 1
    if idx < 200:
        return None
    start = max(0, idx - MODEL_WINDOW + 1)
    return data[tf][start:idx+1]


def round_contract_btc(raw_btc, meta):
    unit = float(meta['ctVal']) * float(meta.get('ctMult') or 1.0)
    lot = Decimal(str(meta['lotSz']))
    min_sz = Decimal(str(meta['minSz']))
    contracts = Decimal(str(raw_btc / unit))
    contracts = (contracts / lot).to_integral_value(rounding=ROUND_DOWN) * lot
    if contracts < min_sz:
        return 0.0
    return float(contracts) * unit


def side_dir(side):
    return 1.0 if side == '做多' else -1.0


def rearm_ready(rearm, close):
    if not rearm:
        return True
    return float(close) < float(rearm['zone_low']) or float(close) > float(rearm['zone_high'])


class Simulator:
    def __init__(self, meta, funding, stress_extra_bps=0.0):
        self.meta = meta
        self.funding = list(funding)
        self.funding_i = 0
        self.stress_extra_bps = float(stress_extra_bps)
        self.cash = CAPITAL
        self.risk = bt.RiskState()
        self.pending = None
        self.position = None
        self.position_open_ms = 0
        self.opportunity = None
        self.rearm = None
        self.daily_day = ''
        self.daily_peak = CAPITAL
        self.daily_block_day = ''
        self.trades = []
        self.equity_curve = []
        self.stats = Counter()
        self.blockers = Counter()
        self.score_counts = Counter()
        self.transitions = Counter()

    def equity(self, mark):
        if not self.position:
            return self.cash
        p = self.position
        d = side_dir(p.side)
        unreal = (float(mark) - p.entry) * p.quantity_btc * d
        return self.cash + unreal - p.entry_fee + p.funding_pnl

    def _day(self, ms):
        return datetime.fromtimestamp(ms/1000, tz=timezone.utc).astimezone(SH_TZ).strftime('%Y-%m-%d')

    def mark_risk(self, ms, mark):
        eq = self.equity(mark)
        day = self._day(ms)
        if day != self.daily_day:
            self.daily_day = day
            self.daily_peak = eq
            if self.daily_block_day != day:
                self.daily_block_day = ''
        self.daily_peak = max(self.daily_peak, eq)
        if self.daily_peak - eq >= DAILY_LOSS:
            self.daily_block_day = day
        self.equity_curve.append((int(ms), float(eq)))
        return eq

    def apply_funding_until(self, ms, mark):
        while self.funding_i < len(self.funding) and int(self.funding[self.funding_i][0]) <= int(ms):
            _, rate = self.funding[self.funding_i]
            if self.position:
                bt.apply_funding(self.position, rate, mark)
            self.funding_i += 1

    def process_pending(self, bar):
        if not self.pending:
            return
        if int(bar['t']) >= int(self.pending.expires_ms):
            self.stats['limit_unfilled'] += 1
            self.pending = None
            return
        pos = bt.fill_entry(self.pending, bar, maker_bps=MAKER_BPS)
        if pos is not None:
            self.position = pos
            self.position_open_ms = int(bar['t'])
            self.pending = None
            self.stats['limit_filled'] += 1

    def process_exit(self, bar):
        if not self.position:
            return
        result = bt.exit_on_bar(self.position, bar, taker_bps=TAKER_BPS,
                                slippage_bps=SLIPPAGE_BPS, stress_extra_bps=self.stress_extra_bps)
        if result is None:
            return
        exit_ms = int(bar['t']) + 60_000
        p = self.position
        self.cash += float(result['net_pnl'])
        self.risk.close_trade(exit_ms, result['net_pnl'])
        result.update({
            'side': p.side, 'entry': p.entry, 'stop': p.stop, 'target': p.target,
            'quantity_btc': p.quantity_btc, 'entry_time': self.position_open_ms,
            'exit_time': exit_ms, 'hold_min': (exit_ms-self.position_open_ms)/60_000,
        })
        self.trades.append(result)
        self.position = None
        self.position_open_ms = 0
        self.stats['closed'] += 1
        if result['net_pnl'] < 0 and self.risk.loss_pause_until_ms:
            self.stats['loss_pause_triggers'] += 1

    def can_submit(self, now_ms, mark):
        if self.position or self.pending:
            self.stats['active_or_pending_blocks'] += 1
            return False
        day = self._day(now_ms)
        if self.daily_block_day == day:
            self.stats['daily_risk_blocks'] += 1
            return False
        reason = self.risk.entry_block_reason(now_ms)
        if reason:
            if '暂停1小时' in reason:
                self.stats['loss_pause_blocks'] += 1
            else:
                self.stats['cooldown_blocks'] += 1
            return False
        return True

    def submit(self, result, now_ms, mark):
        side = result.get('side')
        if side not in ('做多','做空'):
            return
        score = (result.get('scores') or {}).get(side) or {}
        if not score.get('eligible') or float(score.get('total') or 0) < model.THRESHOLD:
            return
        self.stats['eligible_signals'] += 1
        self.score_counts[f"{float(score['total']):.1f}"] += 1
        if not self.can_submit(now_ms, mark):
            return
        opp = result.get('opportunity') or score.get('opportunity')
        if not isinstance(opp, dict):
            self.stats['missing_opportunity'] += 1
            return
        entry = float(mark)
        stop_distance = float(opp['atr1h']) * STOP_ATR
        d = side_dir(side)
        stop = entry - d * stop_distance
        target = entry + d * stop_distance * REWARD_R
        eq = self.equity(mark)
        daily_remaining = max(0.0, DAILY_LOSS - max(0.0, self.daily_peak - eq))
        risk_capital = min(CAPITAL, eq)
        base_risk = min(RISK_USDT, risk_capital * RISK_PCT / 100.0)
        risk_budget = min(base_risk, daily_remaining)
        if risk_budget <= 0:
            self.stats['sizing_or_cost_skips'] += 1
            return
        worst_per_btc_loss = stop_distance + entry * (TAKER_BPS/10000.0) + stop * ((TAKER_BPS+SLIPPAGE_BPS)/10000.0)
        raw_btc = min(risk_budget / worst_per_btc_loss, POSITION_CAP / entry)
        btc = round_contract_btc(raw_btc, self.meta)
        if btc <= 0:
            self.stats['sizing_or_cost_skips'] += 1
            return
        worst_roundtrip_cost = btc * (entry * TAKER_BPS/10000.0 + target * (TAKER_BPS+SLIPPAGE_BPS)/10000.0)
        plan = {'px': str(entry), 'stop_distance': stop_distance, 'btc': btc,
                'worst_roundtrip_cost': worst_roundtrip_cost}
        ok, diag, blockers = model.execution_checks(plan, opp, score)
        if not ok:
            self.stats['execution_gate_blocks'] += 1
            for reason in blockers:
                self.blockers[reason] += 1
            return
        self.pending = bt.PendingEntry(
            opportunity_id=str(opp['id']), side=side, limit=entry, stop=stop, target=target,
            quantity_btc=btc, score=float(score['total']), submitted_bar_t=int(now_ms-60_000),
            expires_ms=int(now_ms + ENTRY_VALID_MS), score_components=dict(score.get('layers') or {}),
            trigger_combination=dict((score.get('confirmations') or {}).get('trigger') or {}),
            front_r=float(diag.get('front_r', math.inf)), front_space_status=str(diag.get('front_space_status','空间未知')),
            cost_r=float(diag.get('cost_r', math.inf)),
        )
        self.stats['limit_submitted'] += 1
        self.rearm = {'zone_low': float(opp['zone_low']), 'zone_high': float(opp['zone_high']), 'side': side, 'id': opp['id']}
        self.opportunity = None

    def finish(self, last_bar):
        if self.pending:
            self.stats['limit_unfilled'] += 1
            self.pending = None
        if self.position:
            p = self.position
            d = side_dir(p.side)
            slip = (SLIPPAGE_BPS + self.stress_extra_bps) / 10000.0
            exit_px = float(last_bar['c']) * (1.0-slip if p.side=='做多' else 1.0+slip)
            gross = (exit_px-p.entry) * p.quantity_btc * d
            exit_fee = exit_px * p.quantity_btc * TAKER_BPS/10000.0
            net = gross - p.entry_fee - exit_fee + p.funding_pnl
            exit_ms = int(last_bar['t']) + 60_000
            self.cash += net
            self.risk.close_trade(exit_ms, net)
            self.trades.append({
                'reason':'EOD','exit':exit_px,'gross_pnl':gross,'entry_fee':p.entry_fee,'exit_fee':exit_fee,
                'funding_pnl':p.funding_pnl,'net_pnl':net,'mfe_r':p.mfe/p.stop_distance if p.stop_distance else 0.0,
                'mae_r':p.mae/p.stop_distance if p.stop_distance else 0.0,'realized_r':net/(p.stop_distance*p.quantity_btc) if p.stop_distance*p.quantity_btc else 0.0,
                'opportunity_id':p.opportunity_id,'score':p.score,'score_components':dict(p.score_components),
                'trigger_combination':dict(p.trigger_combination),'front_r':p.front_r,'front_space_status':p.front_space_status,
                'cost_r':p.cost_r,'side':p.side,'entry':p.entry,'stop':p.stop,'target':p.target,
                'quantity_btc':p.quantity_btc,'entry_time':self.position_open_ms,'exit_time':exit_ms,
                'hold_min':(exit_ms-self.position_open_ms)/60_000,
            })
            self.position = None


def metrics(sim, first_px, last_px):
    trades = sim.trades
    nets = [float(t['net_pnl']) for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    peak = -math.inf; max_dd = 0.0; max_dd_pct = 0.0
    for _, eq in sim.equity_curve:
        peak = max(peak, eq)
        dd = max(0.0, peak-eq)
        max_dd = max(max_dd, dd)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd/peak*100)
    by_side = {}
    for side in ('做多','做空'):
        rows = [t for t in trades if t['side']==side]
        by_side[side] = {
            'trades':len(rows), 'net_pnl':sum(t['net_pnl'] for t in rows),
            'win_rate_pct':100*sum(1 for t in rows if t['net_pnl']>0)/len(rows) if rows else 0.0,
        }
    by_reason = Counter(t['reason'] for t in trades)
    by_score = {}
    for key in sorted({f"{float(t['score']):.1f}" for t in trades}):
        rows = [t for t in trades if f"{float(t['score']):.1f}"==key]
        pos = sum(t['net_pnl'] for t in rows if t['net_pnl']>0)
        neg = -sum(t['net_pnl'] for t in rows if t['net_pnl']<0)
        by_score[key] = {'trades':len(rows),'net_pnl':sum(t['net_pnl'] for t in rows),
                         'win_rate_pct':100*sum(1 for t in rows if t['net_pnl']>0)/len(rows) if rows else 0.0,
                         'profit_factor':pos/neg if neg>0 else math.inf}
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    return {
        'strategy_version':'1.5.2','days':30,'start_utc':START.isoformat(),'end_utc':END.isoformat(),
        'capital':CAPITAL,'ending_equity':sim.cash,'net_pnl':sim.cash-CAPITAL,
        'net_return_pct':(sim.cash/CAPITAL-1)*100,'trades':len(trades),
        'wins':len(wins),'losses':len(losses),'win_rate_pct':100*len(wins)/len(trades) if trades else 0.0,
        'profit_factor':gross_profit/gross_loss if gross_loss>0 else math.inf,
        'expectancy':sum(nets)/len(nets) if nets else 0.0,
        'avg_win':statistics.mean(wins) if wins else 0.0,'avg_loss':statistics.mean(losses) if losses else 0.0,
        'payoff_ratio':statistics.mean(wins)/abs(statistics.mean(losses)) if wins and losses else math.inf,
        'max_drawdown_usdt':max_dd,'max_drawdown_pct':max_dd_pct,
        'fees':sum(float(t['entry_fee'])+float(t['exit_fee']) for t in trades),
        'funding_pnl':sum(float(t['funding_pnl']) for t in trades),
        'avg_hold_min':statistics.mean([t['hold_min'] for t in trades]) if trades else 0.0,
        'median_hold_min':statistics.median([t['hold_min'] for t in trades]) if trades else 0.0,
        'btc_buy_hold_pct':(last_px/first_px-1)*100,
        'by_side':by_side,'by_reason':dict(by_reason),'by_score':by_score,
        'funnel':dict(sim.stats),'transition_counts':dict(sim.transitions),
        'execution_blockers':dict(sim.blockers),
        'entry_threshold':model.THRESHOLD,'front_space_min_r':model.FRONT_MIN_R,
        'cost_max_r':model.COST_MAX_R,'stop_atr_timeframe':'1H','stop_atr_multiplier':STOP_ATR,
        'reward_r':REWARD_R,'single_signal_only':True,'add_on_enabled':False,
        'loss_pause_hours':1,'cooldown_minutes':30,'entry_order_type':'LIMIT',
        'exit_model':'TP/SL trigger -> market exit with declared 5bps slippage',
        'model_window_bars':MODEL_WINDOW,
    }


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
        sim.process_exit(bar)
        sim.apply_funding_until(bar_close, float(bar['c']))
        sim.mark_risk(bar_close, float(bar['c']))

        if sim.rearm and rearm_ready(sim.rearm, bar['c']):
            sim.rearm = None
            sim.stats['rearmed'] += 1

        slices = {}
        valid = True
        for tf in ('1m','5m','15m','1H','4H'):
            rows = closed_slice(data, ts, tf, bar_close)
            if rows is None:
                valid = False; break
            slices[tf] = rows
        if not valid:
            continue

        old_opp = dict(sim.opportunity) if isinstance(sim.opportunity, dict) else None
        result, new_opp, transition = model.evaluate(
            slices['1H'], slices['15m'], slices['5m'], slices['1m'], slices['4H'],
            opportunity=sim.opportunity, stop_atr=STOP_ATR,
            maker_bps=MAKER_BPS, taker_bps=TAKER_BPS, slippage_bps=SLIPPAGE_BPS,
            now_ms=bar_close, allow_new=(sim.rearm is None and sim.position is None and sim.pending is None),
        )
        sim.opportunity = new_opp
        if transition:
            sim.transitions[transition[0]] += 1
            if transition[0] == 'invalidated' and old_opp:
                sim.rearm = {'zone_low':old_opp['zone_low'],'zone_high':old_opp['zone_high'],'side':old_opp['side'],'id':old_opp['id']}
                sim.opportunity = None
        if result.get('status') == '等待5m确认': sim.stats['waiting_5m_bars'] += 1
        if result.get('status') == '等待1m触发': sim.stats['waiting_1m_bars'] += 1
        if result.get('status') == '允许开仓': sim.stats['allow_entry_states'] += 1
        sim.submit(result, bar_close, float(bar['c']))
        if (i-start_i) % 10000 == 0:
            print(f"progress {i-start_i:,}, equity={sim.equity(bar['c']):.2f}, trades={len(sim.trades)}, opp={bool(sim.opportunity)}", flush=True)

    if last_bar is None:
        raise RuntimeError('no test bars')
    sim.finish(last_bar)
    first = next(r for r in d1 if r['t'] >= START_MS)
    m = metrics(sim, float(first['c']), float(last_bar['c']))
    return sim, m


def write_outputs(sim, m, stress_m):
    m['stress_return_pct'] = stress_m['net_return_pct']
    m['stress_net_pnl'] = stress_m['net_pnl']
    (OUTDIR/'metrics.json').write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
    fields = ['entry_time','exit_time','side','score','entry','stop','target','exit','reason','quantity_btc','net_pnl','gross_pnl','entry_fee','exit_fee','funding_pnl','realized_r','mfe_r','mae_r','front_r','front_space_status','cost_r','hold_min','opportunity_id','score_components','trigger_combination']
    with (OUTDIR/'V152_BTC_SWAP_30D_Trades.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for t in sim.trades:
            row = {k:t.get(k,'') for k in fields}
            row['entry_time']=fmt(int(t['entry_time'])); row['exit_time']=fmt(int(t['exit_time']))
            row['score_components']=json.dumps(row['score_components'],ensure_ascii=False,sort_keys=True)
            row['trigger_combination']=json.dumps(row['trigger_combination'],ensure_ascii=False,sort_keys=True)
            w.writerow(row)
    with (OUTDIR/'V152_BTC_SWAP_30D_Equity.csv').open('w', newline='', encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['time_utc','equity_usdt'])
        for t,e in sim.equity_curve: w.writerow([fmt(t),f'{e:.8f}'])
    lines = [
        '# KAYTRADE V1.5.2 — BTC-USDT-SWAP 30天历史回测','',
        f'**区间：** {START:%Y-%m-%d %H:%M} UTC 至 {END:%Y-%m-%d %H:%M} UTC（30天）  ',
        '**模型：** 当前正式V1.5.2；1H方向→15m固定回调→5m确认→1m触发→6分/Hard Gate→限价开仓；1H ATR×1止损；整仓2R；当前正式版TP/SL触发后市价退出。','',
        '## 核心结果','', '| 指标 | 结果 |','|---|---:|',
        f"| 期末权益 | {m['ending_equity']:.2f} U |", f"| 净收益 | {m['net_pnl']:+.2f} U |",
        f"| 收益率 | {m['net_return_pct']:+.3f}% |", f"| 压力情景收益率（退出额外5bps） | {m['stress_return_pct']:+.3f}% |",
        f"| 交易数 | {m['trades']} |", f"| 胜率 | {m['win_rate_pct']:.2f}% |",
        f"| Profit Factor | {m['profit_factor'] if math.isinf(m['profit_factor']) else f'{m['profit_factor']:.3f}'} |",
        f"| 每笔期望 | {m['expectancy']:+.3f} U |", f"| 平均盈利 / 平均亏损 | {m['avg_win']:+.3f} / {m['avg_loss']:+.3f} U |",
        f"| 最大回撤 | {m['max_drawdown_usdt']:.2f} U / {m['max_drawdown_pct']:.3f}% |",
        f"| 手续费 | {m['fees']:.2f} U |", f"| Funding | {m['funding_pnl']:+.3f} U |",'',
        '## 入场漏斗','',
    ]
    for k,v in sorted(m['transition_counts'].items()): lines.append(f'- {k}: {v}')
    for k in ('allow_entry_states','eligible_signals','limit_submitted','limit_filled','limit_unfilled','closed','cooldown_blocks','loss_pause_blocks','daily_risk_blocks','execution_gate_blocks'):
        lines.append(f"- {k}: {m['funnel'].get(k,0)}")
    lines += ['', '## 方向表现','']
    for side,row in m['by_side'].items(): lines.append(f"- {side}: {row['trades']}笔，净PnL {row['net_pnl']:+.2f}U，胜率 {row['win_rate_pct']:.2f}%")
    lines += ['', '## 回测说明','',
              '- 交易决策直接调用正式版 `v152_model.py`；方向、回调机会、评分及Hard Gate与实盘共享。',
              '- 每次只使用当时已收盘K线；模型指标窗口保留最近1000根已收盘K线。',
              '- 限价开仓提交后最多等待5分钟；成交所在1m K线不得用于成交后的TP/SL判断，最早从下一根1m开始。',
              '- 后续1m若同时触发TP和SL，保守按SL优先。',
              '- Maker开仓2bps；TP/SL市价退出Taker 5bps并计5bps声明滑点；Funding按历史费率计入。',
              '- 本结果是历史模拟，不代表未来收益。']
    (OUTDIR/'V152_BTC_SWAP_30D_Backtest_Report.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    print('V1.5.2 30D backtest', START, END)
    meta = base.fetch_instrument()
    data = {tf:base.fetch_candles(tf) for tf in ('1m','5m','15m','1H','4H')}
    funding = base.fetch_funding()
    for tf, rows in data.items():
        bt.validate_candle_continuity(rows, base.BAR_MS[tf], tf)
    bt.validate_funding_coverage([{'t':t} for t,_ in funding], START_MS, END_MS)
    ts = {tf:[r['t'] for r in rows] for tf,rows in data.items()}
    sim, m = run(data, ts, meta, funding, 0.0)
    _, stress_m = run(data, ts, meta, funding, 5.0)
    write_outputs(sim, m, stress_m)
    print('V152_30D_METRICS')
    print(json.dumps(m, ensure_ascii=False, indent=2))
    print('OUTPUT_DIR', OUTDIR.resolve())

if __name__ == '__main__':
    main()

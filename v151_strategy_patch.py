"""KAYTRADE V1.5.1 update on top of V1.5.0.

Requested changes only:
- Connection Settings gets the same dark rounded vertical scrolling surface used by
  the Risk / Execution pages.
- Entry TP/SL distance changes from 15m ATR to 1H ATR. Because R is defined by
  stop distance, the forward-space 1.3R hard gate is recalculated with 1H ATR too.
- Longs are hard-blocked near adverse 4H resistance; shorts are hard-blocked near
  adverse 4H support, using the existing V1.4.7 0.25x-4H-ATR proximity detector.
- Three consecutive losing completed cycles pause new entries for exactly 1 hour
  (persistent across restart), then clear the streak and resume automatically.

V1.5.0 threshold 6.0, one 1x opening signal, no add-ons, 1m Trigger, mandatory
5m MACD, 1H countertrend block, 2R full-position TP and execution safety remain.
"""
from v150_strategy_patch import apply as apply_v150
apply_v150()

import math
import time
import tkinter as tk

import app
import engine
import v136_runtime
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v143_score_arbitration_patch as v143
import v146_ui_fix_patch as v146fix
import v150_strategy_patch as v150

V151_VERSION = '1.5.1'
LOSS_PAUSE_SECONDS = 60 * 60
FRONT_MIN_R = 1.3
CONNECTION_SCROLL_HEIGHT = 650


def _front_r_1h(row, result, quarter, stop_atr):
    structure = dict(row.get('structure') or {})
    front = structure.get('front')
    if not isinstance(front, dict) or front.get('price') in (None, ''):
        return math.inf
    try:
        price = float(quarter[-1]['c'])
        atr1h = float((result.get('h') or {}).get('atr'))
        risk = atr1h * float(stop_atr)
        distance = abs(float(front['price']) - price)
    except (TypeError, ValueError, KeyError, IndexError):
        return math.inf
    if not all(math.isfinite(x) for x in (price, atr1h, risk, distance)) or risk <= 0:
        return math.inf
    return distance / risk


def _rewrite_row_v151(row, result, quarter, stop_atr):
    layers = dict(row.get('layers') or {})
    confirmations = dict(row.get('confirmations') or {})
    structure = dict(row.get('structure') or {})

    trigger_ok = bool(confirmations.get('valid_1m_trigger')) or float(layers.get('trigger') or 0.0) > 0.0
    macd_ok = bool(confirmations.get('5m_macd_required'))
    hstate = str(confirmations.get('1H_trend_state') or 'neutral')
    adverse4 = float(layers.get('structure_penalty_4h') or 0.0)
    adverse4_block = adverse4 < 0.0

    front_r = _front_r_1h(row, result, quarter, stop_atr)
    front_block = math.isfinite(front_r) and front_r < FRONT_MIN_R
    structure['front_r'] = front_r
    structure['blocked'] = front_block
    structure['risk_atr_source'] = '1H'
    structure['adverse_4h_hard_block'] = adverse4_block
    row['structure'] = structure

    total = float(row.get('total') or 0.0)
    tier, multiplier, level = v150._tier(total)
    gate = trigger_ok and macd_ok and hstate != 'opposite' and not front_block and not adverse4_block
    eligible = bool(gate and tier == 1)

    row['gate'] = gate
    row['eligible'] = eligible
    row['required'] = v150.V150_THRESHOLD
    row['signal_tier'] = tier
    row['position_multiplier'] = multiplier if eligible else 0.0
    row['level'] = f'{level} · 1×仓位' if tier else level

    confirmations['4H_adverse_structure_block'] = adverse4_block
    confirmations['front_space_uses_1H_ATR'] = True
    confirmations['structure'] = structure
    row['confirmations'] = confirmations

    items = []
    for item in list(row.get('items') or []):
        if not isinstance(item, (tuple, list)) or not item:
            items.append(item)
            continue
        name = str(item[0])
        if name == '4H 不利支撑/阻力结构':
            item = ('4H 不利支撑/阻力结构（命中即禁止开仓）', *item[1:])
        elif name.startswith('前方结构空间：'):
            item = ('前方结构空间：<1.3R硬过滤（R=1H ATR×止损倍数）', *item[1:])
        items.append(item)
    row['items'] = items

    if hstate == 'opposite':
        row['reason'] = '1H逆趋势，V1.5.1禁止该方向开仓'
    elif adverse4_block:
        row['reason'] = '做多接近4H压力 / 做空接近4H支撑，V1.5.1禁止该方向开仓'
    elif front_block:
        row['reason'] = f'前方强结构有效空间 {front_r:.2f}R < 1.3R（按1H ATR止损计算），禁止开仓'
    elif not trigger_ok:
        row['reason'] = '1m有效Trigger缺失：EMA20回收 / KDJ交叉 / 反转K线均未触发，禁止开仓'
    elif not macd_ok:
        row['reason'] = '5m MACD未与开仓方向确认，V1.5.1禁止开仓'
    elif eligible:
        row['reason'] = f'开仓信号 · {total:g}/10 达标；V1.5.1全部Hard Gate通过'
    else:
        row['reason'] = f'{total:g}/10，未达固定开仓门槛6.0'
    return row


def _market_with_1h_atr(market):
    try:
        atr1h = float((market.get('h') or {}).get('atr'))
    except (TypeError, ValueError):
        atr1h = math.nan
    if not math.isfinite(atr1h) or atr1h <= 0:
        raise engine.Halt('1H ATR无效，V1.5.1禁止开仓')
    proxy = dict(market)
    proxy['m'] = dict(market.get('m') or {})
    proxy['m']['atr'] = atr1h
    return proxy, atr1h


def _diag_text_v151(plan, prefix='开仓成本诊断'):
    if not isinstance(plan, dict):
        return ''
    atr = float(plan.get('atr_1h') or plan.get('atr_15m') or 0.0)
    return (
        f"{prefix}：BTC {float(plan.get('px',0)):,.2f}；1H ATR {atr:,.2f}，"
        f"1R {float(plan.get('stop_distance',0)):,.2f}；仓位 {float(plan.get('notional',0)):,.2f} USDT；"
        f"预计手续费 {float(plan.get('expected_roundtrip_cost',0)):.2f} USDT，"
        f"最坏执行成本(含滑点预算) {float(plan.get('worst_roundtrip_cost',0)):.2f} USDT；"
        f"整仓2R毛/净 {float(plan.get('tp_full_gross',0)):.2f}/{float(plan.get('tp_full_net',0)):.2f} USDT；"
        f"2R TP/预计手续费 {float(plan.get('expected_fee_multiple',0)):.2f}×，"
        f"2R TP/最坏成本 {float(plan.get('worst_cost_multiple',0)):.2f}×"
    )


def _pause_remaining(state, now=None):
    now = time.time() if now is None else float(now)
    try:
        until = float((state or {}).get('streak_pause_until') or 0.0)
    except (TypeError, ValueError):
        until = 0.0
    return max(0.0, until - now)


def _rebuild_connection_page(owner):
    if not getattr(owner.book, 'pages', None) or len(owner.book.pages) < 1:
        return False
    page = owner.book.pages[0]
    scroll, surface, body = v146fix._scroll_surface(page, CONNECTION_SCROLL_HEIGHT)
    body.grid_columnconfigure(0, weight=0)
    body.grid_columnconfigure(1, weight=1)

    tk.Label(body, text='账户连接', bg=app.PANEL, fg='#eef5f7', font=('Helvetica',20,'bold'),
             anchor='w', bd=0).grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0,12))
    rows = [
        ('账户官方域名', owner.host, app.HOSTS),
        ('环境', owner.mode, ('OKX模拟盘','真实账户')),
        ('API Key', owner.key, None),
        ('Secret Key', owner.secret, None),
        ('Passphrase', owner.phrase, None),
    ]
    owner.connection_widgets = []
    for i, (label, var, values) in enumerate(rows, 1):
        tk.Label(body, text=label, bg=app.PANEL, fg='#dbe6eb', font=('Helvetica',12),
                 anchor='w', bd=0).grid(row=i, column=0, sticky='w', padx=(6,24), pady=11)
        widget = app.RoundedCombobox(body, textvariable=var, values=values, width=48) if values else \
                 app.RoundedEntry(body, textvariable=var, show='•', width=50)
        widget.grid(row=i, column=1, sticky='ew', padx=8, pady=11)
        owner.connection_widgets.append(widget)

    note = ('密钥仅保存在本次运行内存，退出后需重新填写；不会发送给GPT/Gemini。\n'
            '使用专用交易子账户，不与手动交易或其他机器人共用BTC仓位。\n'
            '测试连接仅只读；自动交易需要读取+交易权限，禁止提币权限。\n'
            '模拟盘与真实账户密钥不可混用；地区或产品不支持时停止。\n'
            '程序仅连接OKX官方接口，不接入原Sites网页。')
    tk.Label(body, text=note, wraplength=800, justify='left', bg=app.PANEL, fg=app.MUTED,
             font=('Helvetica',10), anchor='w', bd=0).grid(row=6, column=0, columnspan=2, sticky='w', padx=6, pady=(14,18))
    app.RoundedButton(body, text='网络自检（不下单）', command=owner.diagnose, variant='neutral', width=170).grid(
        row=7, column=0, sticky='w', padx=6, pady=(4,18))
    app.RoundedButton(body, text='测试连接（只读）', command=owner.connect, variant='accent', width=170).grid(
        row=7, column=1, sticky='w', padx=8, pady=(4,18))

    # Keep the callback target alive but hidden, preserving the V1.4.6 compact UI.
    owner.account_view = tk.Text(body, height=1, wrap='word', bg=app.PANEL_ALT, fg='#c8e5f5',
                                 font=('Menlo',12), bd=0, highlightthickness=0, padx=12, pady=10)
    owner._v151_connection_scroll = scroll
    owner._v151_connection_surface = surface
    owner._v151_connection_scrollbar = scroll.scroll
    owner._v151_connection_scroll_ready = True
    return True


def _translate_runtime_text(text, state=None, settings=None):
    if not isinstance(text, str):
        return text
    if '中国时间本日已连续亏损' in text and '次日自动恢复' in text:
        limit = int(getattr(settings, 'consecutive_losses', 3) or 3)
        remaining = _pause_remaining(state or {})
        minutes = max(1, int(math.ceil(remaining / 60.0))) if remaining > 0 else 60
        return f'连续净亏损已达 {limit} 次：停止新开仓1小时；约剩余 {minutes} 分钟，已有仓位/TP/SL继续管理'
    if '中国时间本日连续净亏损已达' in text and '次日自动恢复' in text:
        limit = int(getattr(settings, 'consecutive_losses', 3) or 3)
        remaining = _pause_remaining(state or {})
        minutes = max(1, int(math.ceil(remaining / 60.0))) if remaining > 0 else 60
        return f'连续净亏损已达 {limit} 次：停止新开仓1小时；约剩余 {minutes} 分钟，已有仓位/TP/SL继续管理'
    text = text.replace('15m ATR', '1H ATR').replace('15分钟 ATR', '1小时 ATR')
    return text


def apply():
    if getattr(engine.Engine, '_kaytrade_v151_applied', False):
        return

    previous_signal = v138.v138_signal
    previous_prepare = v138._prepare_order
    previous_refresh = engine.Engine.refresh_market
    previous_cycle = engine.Engine.cycle
    previous_arm_engine = engine.Engine.arm
    previous_app_init = app.App.__init__
    previous_app_arm = app.App.arm
    previous_finalize = v137._finalize_cycle

    def v151_signal(hour, quarter, five, one, stop_atr=1.0, four=None, day=None):
        result = previous_signal(hour, quarter, five, one, stop_atr, four=four, day=day)
        scores = result.get('scores') or {}
        for side in ('做多','做空'):
            if side in scores:
                _rewrite_row_v151(scores[side], result, quarter, stop_atr)
        result['scores'] = scores
        result['threshold'] = v150.V150_THRESHOLD
        result['score_max'] = 10.0
        result['strategy_version'] = V151_VERSION
        return v143._apply_arbitration(result)

    def prepare_order_1h(self, side, score, market, equity, available, remaining):
        proxy, atr1h = _market_with_1h_atr(market)
        original15 = float((market.get('m') or {}).get('atr') or 0.0)
        prepared = previous_prepare(self, side, score, proxy, equity, available, remaining)
        if not prepared:
            return prepared
        plan, tier, multiplier, level = prepared
        plan['atr_1h'] = atr1h
        plan['atr_source'] = '1H'
        plan['atr_15m_reference'] = original15
        # Restore the compatibility field to its true 15m value; stop_distance was
        # already calculated from the injected 1H ATR above.
        plan['atr_15m'] = original15
        return plan, tier, multiplier, level

    def reset_streak_pause(self):
        if not self.store:
            return ''
        state = self.store.data
        now = time.time()
        limit = int(getattr(self.settings, 'consecutive_losses', 3) or 3)
        remaining = _pause_remaining(state, now)
        until = float(state.get('streak_pause_until') or 0.0)
        changed = False
        if until > 0 and remaining <= 0:
            state.update(streak=0, streak_pause_until=0.0, streak_notice_day='', streak_day='')
            changed = True
            if not getattr(self, '_v151_pause_expiry_logged', False):
                self.emit('log', '连续亏损1小时暂停已结束；亏损计数已清零，自动开仓条件恢复')
                self._v151_pause_expiry_logged = True
        elif int(state.get('streak') or 0) >= limit and until <= 0:
            until = now + LOSS_PAUSE_SECONDS
            state['streak_pause_until'] = until
            state['streak_day'] = f'pause-{int(until)}'
            state['streak_notice_day'] = ''
            changed = True
            self._v151_pause_expiry_logged = False
        elif remaining > 0:
            self._v151_pause_expiry_logged = False
        if changed:
            self.store.save()
        return state.get('streak_day', '')

    def finalize_cycle(self, p):
        previous_finalize(self, p)
        if not self.store:
            return
        state = self.store.data
        limit = int(getattr(self.settings, 'consecutive_losses', 3) or 3)
        if int(state.get('streak') or 0) >= limit:
            until = time.time() + LOSS_PAUSE_SECONDS
            state['streak_pause_until'] = until
            state['streak_day'] = f'pause-{int(until)}'
            state['streak_notice_day'] = ''
            self.store.save()
            self._v151_pause_expiry_logged = False
            self.emit('log', f'连续净亏损已达 {limit} 次：从本次平仓起停止新开仓1小时；已有仓位/TP/SL继续管理')
        elif int(state.get('streak') or 0) == 0 and state.get('streak_pause_until'):
            state['streak_pause_until'] = 0.0
            state['streak_notice_day'] = ''
            self.store.save()

    def refresh_market(self):
        result = previous_refresh(self)
        # Signal output is rewritten by v151_signal via v138.v138_signal below.
        return result

    def cycle(self):
        original_emit = self.emit
        def emit(kind, data):
            return original_emit(kind, _translate_runtime_text(data,
                self.store.data if self.store else {}, self.settings))
        self.emit = emit
        try:
            result = previous_cycle(self)
        finally:
            self.emit = original_emit
        p = self.store.data.get('active') if self.store else None
        if isinstance(p, dict):
            changed = p.get('version') != V151_VERSION or not p.get('v151')
            p['v151'] = True
            p['version'] = V151_VERSION
            if changed:
                self.store.save()
        return result

    def engine_arm(self, settings):
        original_emit = self.emit
        def emit(kind, data):
            if kind == 'log' and isinstance(data, str) and data.startswith('自动交易启动；'):
                data = ('自动交易启动；V1.5.1固定6分单一开仓信号；1H ATR止损；整仓TP=2R；'
                        '4H不利支撑/压力为Hard Gate；连续3亏暂停新开仓1小时；每根已收盘1m最多一次')
            else:
                data = _translate_runtime_text(data, self.store.data if self.store else {}, settings)
            return original_emit(kind, data)
        self.emit = emit
        try:
            return previous_arm_engine(self, settings)
        finally:
            self.emit = original_emit

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        try:
            self.root.title('KAYTRADE 1.5.1 · BTC 策略控制台')
        except Exception:
            pass
        _rebuild_connection_page(self)
        for widget in v138._widgets(self.root):
            try:
                text = str(widget.cget('text') or '')
            except Exception:
                text = ''
            try:
                if '1.5.0' in text:
                    widget.configure(text=text.replace('1.5.0','1.5.1'))
                elif text == '15分钟 ATR 止损倍数 0.6—3':
                    widget.configure(text='1小时 ATR 止损倍数 0.6—3')
                elif '15m ATR 止损' in text:
                    widget.configure(text=text.replace('15m ATR 止损','1H ATR 止损'))
                elif '按账户风险与15m ATR动态计算' in text:
                    widget.configure(text=text.replace('15m ATR','1H ATR'))
            except Exception:
                pass
        self._v151_ui_ready = True

    def app_arm(self):
        original = app.simpledialog.askstring
        def askstring(title, prompt, *args, **kwargs):
            text = str(prompt).replace('15m ATR','1H ATR').replace('15分钟 ATR','1小时 ATR')
            text = text.replace('次日自动恢复','1小时后自动恢复')
            text += ('\n\nV1.5.1：TP/SL改用1H ATR；做多接近4H压力、做空接近4H支撑时禁止开仓；'
                     '连续3次亏损后暂停新开仓1小时；连接设置页增加统一滑动栏。')
            return original(title, text, *args, **kwargs)
        app.simpledialog.askstring = askstring
        try:
            return previous_app_arm(self)
        finally:
            app.simpledialog.askstring = original

    v138.v138_signal = v151_signal
    v138._prepare_order = prepare_order_1h
    v137._finalize_cycle = finalize_cycle
    v137._diag_text = _diag_text_v151
    v136_runtime._diag_text = _diag_text_v151
    engine.Engine._reset_streak_day = reset_streak_pause
    engine.Engine.refresh_market = refresh_market
    engine.Engine.cycle = cycle
    engine.Engine.arm = engine_arm
    app.App.__init__ = app_init
    app.App.arm = app_arm
    engine.Engine._kaytrade_v151_applied = True


apply()

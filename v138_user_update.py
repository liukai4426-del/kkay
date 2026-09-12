"""KAYTRADE V1.3.8 user-confirmed two-signal / six-hour risk update.

Applied after v138_strategy_patch:
- scoring has only two entry levels: 6.0-7.5 opening signal, 8.0-10 strong signal
- strong signal uses 2x the first signal position setting
- the second signal position is derived/read-only in the UI
- three consecutive losing cycles pause new entries for 6 hours (not until next day)
- runtime log timestamps remain visible in the App event log
"""
import time
import tkinter as tk
from dataclasses import replace
from datetime import datetime

from v138_strategy_patch import apply as apply_v138
apply_v138()

import app
import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138

V138_ENTRY_THRESHOLD = 6.0
V138_STRONG_THRESHOLD = 8.0
LOSS_PAUSE_SECONDS = 6 * 60 * 60


def _two_tier(total):
    value = float(total or 0.0)
    if value >= V138_STRONG_THRESHOLD:
        return 2, 2.0, '强信号'
    if value >= V138_ENTRY_THRESHOLD:
        return 1, 1.0, '开仓信号'
    return 0, 0.0, '未达开仓线'


def _tier_limit(tier):
    return {1: 1, 2: 1}.get(int(tier or 0), 0)


def _entry_notional_cap(first_position, tier, addon=False):
    first = float(first_position)
    tier = int(tier or 0)
    if tier == 1:
        return first
    if tier == 2:
        # A strong signal opened from flat is 2x the first signal position.
        # If it upgrades an existing tier-1 position, the total cap becomes
        # first + second = 3x so the second leg can still be 2x first.
        return first * (3.0 if addon else 2.0)
    return first


def _remaining_text(seconds):
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    return f'{hours}小时{minutes:02d}分'


def _loss_pause_until(state):
    try:
        return float(state.get('loss_pause_until') or 0.0)
    except Exception:
        return 0.0


def _set_loss_pause(self, reason='连续亏损3次'):
    if not self.store:
        return 0.0
    state = self.store.data
    now = time.time()
    until = _loss_pause_until(state)
    if until <= now:
        until = now + LOSS_PAUSE_SECONDS
        state['loss_pause_until'] = until
        state['streak'] = max(3, int(state.get('streak') or 0))
        self.store.save()
        resume = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(until))
        self.emit('log', f'{reason}：停止新开仓6小时；预计 {resume} 自动恢复。已有仓位/TP/SL继续管理')
    return until


def _reset_streak_six_hour(self):
    """Replace the old calendar-day reset with a true six-hour pause."""
    if not self.store:
        return ''
    state = self.store.data
    now = time.time()
    day = datetime.fromtimestamp(now).strftime('%Y-%m-%d')
    state['streak_day'] = day  # retained only for compatibility/display

    until = _loss_pause_until(state)
    if until > now:
        return day

    if until:
        state['loss_pause_until'] = 0.0
        state['streak'] = 0
        state['streak_notice_day'] = ''
        self.store.save()
        self.emit('log', '连续亏损风控的6小时暂停已结束；连续亏损计数已清零，允许重新开仓')
        return day

    if int(state.get('streak') or 0) >= 3:
        # Migrate legacy "stop until next day" state. Anchor to the most recent
        # close when possible so an already-old stop does not restart for 6h.
        base = float(state.get('last_close') or now)
        candidate = base + LOSS_PAUSE_SECONDS
        if candidate > now:
            state['loss_pause_until'] = candidate
            self.store.save()
        else:
            state['streak'] = 0
            state['streak_notice_day'] = ''
            self.store.save()
    return day


def _position_widgets(root):
    return v138._widgets(root)


def apply():
    if getattr(engine.Engine, '_kaytrade_v138_user_update_applied', False):
        return

    # Scoring: only two levels, fixed 6.0 auto-entry floor.
    v138.V138_THRESHOLD = V138_ENTRY_THRESHOLD
    v137._tier = _two_tier
    v137._tier_limit = _tier_limit

    previous_validate = engine.Settings.validate

    def validate(self):
        result = previous_validate(self)
        if abs(float(self.score_threshold) - V138_ENTRY_THRESHOLD) > 1e-9:
            raise engine.Halt('V1.3.8自动开仓评分门槛固定为6.0，不可修改')
        # Tier-1 plus a later Tier-2 upgrade can total 3x the first position.
        if float(self.max_notional) * 3.0 > float(self.capital) * float(self.leverage):
            raise engine.Halt('第一信号仓位设置过大：两档信号完整持仓最高可达第一仓位的3倍，不能超过资金×杠杆上限')
        return result

    engine.Settings.validate = validate

    # Consecutive-loss control: no daily reset; pause exactly 6 hours.
    engine.Engine._reset_streak_day = _reset_streak_six_hour

    previous_finalize = v137._finalize_cycle

    def finalize_cycle(self, p):
        previous_finalize(self, p)
        state = self.store.data if self.store else {}
        if int(state.get('streak') or 0) >= 3:
            _set_loss_pause(self)

    v137._finalize_cycle = finalize_cycle

    previous_ack = engine.Engine.acknowledge

    def acknowledge(self):
        result = previous_ack(self)
        state = self.store.data if self.store else {}
        if int(state.get('streak') or 0) >= 3:
            _set_loss_pause(self, '人工核对后连续亏损达到3次')
        return result

    engine.Engine.acknowledge = acknowledge

    previous_arm_engine = engine.Engine.arm

    def engine_arm(self, settings):
        original_emit = self.emit

        def emit(kind, data):
            if isinstance(data, str):
                data = data.replace('中国时间本日已连续亏损', '当前连续亏损')
                data = data.replace('次：仅停止新开仓，次日自动恢复', '次：仅停止新开仓，6小时后自动恢复')
            return original_emit(kind, data)

        self.emit = emit
        try:
            result = previous_arm_engine(self, settings)
        finally:
            self.emit = original_emit
        if self.store:
            until = _loss_pause_until(self.store.data)
            now = time.time()
            if until > now:
                self.emit('log', f'连续亏损风控仍在6小时暂停期内，剩余约 {_remaining_text(until-now)}；期间不提交新开仓')
        return result

    engine.Engine.arm = engine_arm

    # Apply per-signal position caps while preserving all existing risk caps.
    previous_initial = v138._submit_initial
    previous_addon = v138._submit_addon

    def submit_initial(self, market, score, equity, available, remaining):
        tier = _two_tier(float((score or {}).get('total') or 0.0))[0]
        original = self.settings
        if not original:
            return previous_initial(self, market, score, equity, available, remaining)
        first = float(original.max_notional)
        self.settings = replace(original, max_notional=_entry_notional_cap(first, tier, addon=False))
        try:
            return previous_initial(self, market, score, equity, available, remaining)
        finally:
            self.settings = original

    def submit_addon(self, p, market, score, equity, available, remaining):
        tier = _two_tier(float((score or {}).get('total') or 0.0))[0]
        original = self.settings
        if not original:
            return previous_addon(self, p, market, score, equity, available, remaining)
        first = float(original.max_notional)
        self.settings = replace(original, max_notional=_entry_notional_cap(first, tier, addon=True))
        try:
            return previous_addon(self, p, market, score, equity, available, remaining)
        finally:
            self.settings = original

    v138._submit_initial = submit_initial
    v138._submit_addon = submit_addon

    previous_reconcile = engine.Engine.reconcile

    def reconcile(self):
        original_emit = self.emit

        def emit(kind, data):
            if isinstance(data, str):
                data = data.replace('三级开仓计数已重置', '两档信号仓位计数已重置')
                data = data.replace('次日自动恢复', '6小时后自动恢复')
            return original_emit(kind, data)

        self.emit = emit
        try:
            return previous_reconcile(self)
        finally:
            self.emit = original_emit

    engine.Engine.reconcile = reconcile

    previous_app_init = app.App.__init__
    previous_app_arm = app.App.arm

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        try:
            self.root.title('KAYTRADE 1.3.8 · BTC 策略控制台')
        except Exception:
            pass

        if 'score_threshold' in self.fields:
            self.fields['score_threshold'].set('6.0')

        first_var = self.fields.get('max_notional')
        first_entry = None
        first_label = None
        threshold_label = None

        for widget in _position_widgets(self.root):
            if isinstance(widget, app.RoundedEntry) and getattr(widget, 'variable', None) is first_var:
                first_entry = widget
            try:
                text = str(widget.cget('text') or '')
            except Exception:
                text = ''
            if text.startswith('最大名义仓位 USDT'):
                first_label = widget
            elif text.startswith('自动开仓评分阈值'):
                threshold_label = widget
            try:
                if 'V1.3.8 1m触发版' in text:
                    widget.configure(text=text.replace('V1.3.8 1m触发版', 'V1.3.8 两档信号版'))
            except Exception:
                pass

        if first_label is not None:
            try:
                first_label.configure(text='第一信号仓位 USDT（6.0–7.5）')
            except Exception:
                pass
        if threshold_label is not None:
            try:
                threshold_label.configure(text='自动开仓评分阈值（固定 6.0）')
            except Exception:
                pass

        if first_entry is not None and first_var is not None:
            parent = first_entry.master
            try:
                row = int(first_entry.grid_info().get('row', 2))
            except Exception:
                row = 2
            insert_row = row + 1
            for child in list(parent.winfo_children()):
                if child in (first_entry, first_label):
                    continue
                try:
                    info = child.grid_info()
                    if info and int(info.get('row', -1)) >= insert_row:
                        child.grid_configure(row=int(info['row']) + 1)
                except Exception:
                    pass

            second_var = tk.StringVar(value='—')

            def sync_second(*_):
                try:
                    second_var.set(f'{float(first_var.get()) * 2.0:g}')
                except Exception:
                    second_var.set('—')

            tk.Label(
                parent,
                text='第二信号仓位 USDT（8.0–10，自动=第一仓位×2）',
                wraplength=340,
                bg=app.PANEL,
                fg='#dbe6eb',
                font=('Helvetica', 12),
                anchor='w',
                bd=0,
            ).grid(row=insert_row, column=0, sticky='w', padx=6, pady=11)
            second_entry = app.RoundedEntry(
                parent, textvariable=second_var, width=170, height=40, font=('Helvetica', 14)
            )
            second_entry.grid(row=insert_row, column=1, sticky='e', padx=8, pady=11)
            try:
                second_entry.entry.configure(
                    state='disabled',
                    disabledbackground=app.FIELD,
                    disabledforeground=app.MUTED,
                )
            except Exception:
                pass
            first_var.trace_add('write', sync_second)
            sync_second()
            self.second_signal_position_var = second_var
            self.second_signal_position_entry = second_entry

    def app_settings(self):
        if 'score_threshold' in self.fields:
            self.fields['score_threshold'].set('6.0')
        values = {k: float(v.get()) for k, v in self.fields.items()}
        for key in ('leverage', 'consecutive_losses', 'cooldown_minutes'):
            if int(values[key]) != values[key]:
                raise engine.Halt(key + '必须是整数')
            values[key] = int(values[key])
        return engine.Settings(**values).validate()

    def app_arm(self):
        original = app.simpledialog.askstring

        def askstring(title, prompt, *args, **kwargs):
            text = str(prompt)
            text = text.replace(
                '3.5–4.5=1×仓位 / 5.0–6.5=1.5× / 7.0–10=2×。',
                '6.0–7.5=开仓信号（第一仓位） / 8.0–10=强信号（第二仓位=第一仓位2倍）。',
            )
            text = text.replace('连亏3次停止新开仓', '连亏3次停止新开仓6小时')
            text += '\nV1.3.8两档信号：6.0–7.5开第一仓位；8.0–10开强信号仓位，固定为第一仓位2倍且不可单独编辑。'
            return original(title, text, *args, **kwargs)

        app.simpledialog.askstring = askstring
        try:
            return previous_app_arm(self)
        finally:
            app.simpledialog.askstring = original

    app.App.__init__ = app_init
    app.App.settings = app_settings
    app.App.arm = app_arm

    engine.Engine._kaytrade_v138_user_update_applied = True


apply()

"""KAYTRADE V1.5.0 strategy/log update on top of V1.4.7.

Intentional behavior changes:
- fixed opening threshold is 6.0/10 and every eligible signal is one unified
  "开仓信号" at 1x size; Tier 2 / Tier 3 and upgrade add-ons are disabled.
- only one opening-position input remains in the UI/settings.
- the redesigned 运行记录 card shows the original event time for every row.

All V1.4.7 hard gates, score components, direction arbitration, 15m ATR stop,
2R take profit, order execution and safety controls remain unchanged.
"""
from v147_strategy_patch import apply as apply_v147
apply_v147()

import math
from dataclasses import asdict, dataclass

import app
import engine
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v139_position_patch as v139
import v140_score_dialog_patch as v140
import v141_dialog_signal_positions_patch as v141
import v143_score_arbitration_patch as v143
import v146_ui_log_polish_patch as v146
import v147_strategy_patch as v147

V150_VERSION='1.5.0'
V150_THRESHOLD=6.0


@dataclass(frozen=True)
class SettingsV150(v141.SettingsV141):
    score_threshold:float=6.0
    # Kept only for backward-compatible loading of old V1.4.7 settings files.
    # They are hidden and not used for V1.5 sizing.
    second_signal_notional:float=1.0
    third_signal_notional:float=1.0

    def validate(self):
        if abs(float(self.score_threshold)-V150_THRESHOLD)>1e-9:
            raise engine.Halt('V1.5自动开仓评分门槛固定为6.0，不可修改')
        for name,value in asdict(self).items():
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise engine.Halt(name+' 必须是有限正数')
        if int(self.leverage)!=self.leverage or not 1<=self.leverage<=50:
            raise engine.Halt('杠杆范围1—50倍（整数）')
        if int(self.consecutive_losses)!=self.consecutive_losses or self.consecutive_losses>20:
            raise engine.Halt('连续亏损上限必须是1—20的整数')
        if self.daily_loss>self.capital or float(self.first_signal_notional)>self.capital*self.leverage:
            raise engine.Halt('日亏损/开仓信号仓位超出资金与杠杆范围')
        if not .6<=self.stop_atr<=3:
            raise engine.Halt('ATR止损倍数范围0.6—3')
        if abs(self.reward_r-2.0)>1e-9:
            raise engine.Halt('V1.5止盈固定为2R')
        if abs(self.fee_bps-2.0)>1e-9 or abs(self.taker_fee_bps-5.0)>1e-9 or abs(self.slippage_bps-5.0)>1e-9:
            raise engine.Halt('V1.5成本参数固定：Maker 2bps / Taker 5bps / 滑点预算5bps')
        return self


def _tier(total):
    """V1.5 has exactly one signal class and one 1x opening position."""
    value=float(total or 0.0)
    if value>=V150_THRESHOLD:
        return 1,1.0,'开仓信号'
    return 0,0.0,'未达开仓线'


def _tier_limit(tier):
    """No upgrade/add-on tiers in V1.5."""
    return 1 if int(tier or 0)==1 else 0


def _timestamp_parts(line):
    text=str(line or '')
    if len(text)>=19 and text[4]=='-' and text[7]=='-' and text[10]==' ' and text[13]==':' and text[16]==':':
        return text[11:19],text[20:].lstrip()
    return '',text


def _render_logs(self):
    """Keep V1.4.6 log-card visuals, but restore the event time visibly."""
    rows=getattr(self,'_v146_log_rows',None)
    if rows is None:
        return _PREVIOUS_RENDER_LOGS(self)
    for child in rows.winfo_children():
        try:child.destroy()
        except Exception:pass
    visible=list(getattr(self,'log_lines',[]) or [])[-v146.LOG_VISIBLE_ROWS:]
    if not visible:
        v146._render_log_row(rows,'等待中',v146.WAIT_COLOR,'等待运行记录','连接账户并开始运行后，这里会显示策略循环与安全拦截。')
        return
    for index,(kind,line) in enumerate(reversed(visible)):
        status,color=v146._log_state(kind,line)
        clock,clean=_timestamp_parts(line)
        if clean.startswith('警报：'):clean=clean[3:].lstrip()
        title='安全警报' if status=='警报' else '等待下一步' if status=='等待中' else '系统运行'
        if clock:
            title=f'{clock} · {title}'
        v146._render_log_row(rows,status,color,title,clean,separator=index>0)


_PREVIOUS_RENDER_LOGS=app.App.render_logs


def _hide_extra_position_rows(owner):
    """Leave only the first/opening signal notional row visible."""
    for widget in v138._widgets(owner.root):
        try:text=str(widget.cget('text') or '')
        except Exception:text=''
        if text.startswith('第一信号仓位 USDT'):
            try:widget.configure(text='开仓信号仓位 USDT（评分 ≥6.0）')
            except Exception:pass
        if text.startswith('第二信号仓位 USDT') or text.startswith('第三信号仓位 USDT'):
            try:
                info=widget.grid_info()
                row=int(info.get('row',-1)) if info else -1
                parent=widget.master
                widget.grid_remove()
                if row>=0:
                    for sibling in parent.winfo_children():
                        try:
                            sinfo=sibling.grid_info()
                            if sinfo and int(sinfo.get('row',-2))==row:
                                sibling.grid_remove()
                        except Exception:pass
            except Exception:pass
    owner.fields.pop('second_signal_notional',None)
    owner.fields.pop('third_signal_notional',None)


def apply():
    if getattr(engine.Engine,'_kaytrade_v150_applied',False):
        return

    # One fixed 6.0 threshold across settings, scorer and legacy helpers.
    v137.V137_THRESHOLD=V150_THRESHOLD
    v138.V138_THRESHOLD=V150_THRESHOLD
    v139.V139_THRESHOLD=V150_THRESHOLD
    v140.V140_THRESHOLD=V150_THRESHOLD
    v147.V147_THRESHOLD=V150_THRESHOLD

    engine.Settings=SettingsV150
    app.Settings=SettingsV150

    # A single 1x signal tier. The entry guard rejects any existing-position add-on.
    v137._tier=_tier
    v137._tier_limit=_tier_limit
    v139._tier=_tier
    v140._tier=_tier

    previous_signal=v138.v138_signal
    previous_app_init=app.App.__init__
    previous_cycle=engine.Engine.cycle

    def v150_signal(*args,**kwargs):
        result=previous_signal(*args,**kwargs)
        scores=result.get('scores') or {}
        for row in scores.values():
            total=float(row.get('total') or 0.0)
            tier,multiplier,level=_tier(total)
            gate=bool(row.get('gate'))
            eligible=bool(gate and tier==1)
            row['required']=V150_THRESHOLD
            row['eligible']=eligible
            row['signal_tier']=tier
            row['position_multiplier']=multiplier if eligible else 0.0
            row['level']=f'{level} · 1×仓位' if tier else level
            if gate and not tier:
                row['reason']=f'{total:g}/10，未达固定开仓门槛6.0'
            elif eligible:
                row['reason']=f'开仓信号 · {total:g}/10 达标；V1.4.7 Hard Gate 全部通过'
        result['scores']=scores
        result['threshold']=V150_THRESHOLD
        result['score_max']=10.0
        result['strategy_version']=V150_VERSION
        return v143._apply_arbitration(result)

    def app_settings(self):
        if 'score_threshold' in self.fields:
            self.fields['score_threshold'].set('6.0')
        values={k:float(v.get()) for k,v in self.fields.items()}
        for k in ('leverage','consecutive_losses','cooldown_minutes'):
            if int(values[k])!=values[k]:
                raise engine.Halt(k+'必须是整数')
            values[k]=int(values[k])
        first=float(values['first_signal_notional'])
        values['max_initial_notional']=first
        values['max_notional']=first
        return SettingsV150(**values).validate()

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:self.root.title('KAYTRADE 1.5.0 · BTC 策略控制台')
        except Exception:pass
        try:
            if 'score_threshold' in self.fields:
                self.fields['score_threshold'].set('6.0')
        except Exception:pass
        _hide_extra_position_rows(self)
        for widget in v138._widgets(self.root):
            try:
                text=str(widget.cget('text') or '')
                if '1.4.7' in text:
                    widget.configure(text=text.replace('1.4.7','1.5.0'))
                elif '自动开仓评分阈值 4.0' in text:
                    widget.configure(text=text.replace('4.0','6.0'))
            except Exception:pass
        self.render_logs()

    def cycle(self):
        result=previous_cycle(self)
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict):
            changed=p.get('version')!=V150_VERSION or not p.get('v150')
            p['v150']=True
            p['version']=V150_VERSION
            if changed:self.store.save()
        return result

    v138.v138_signal=v150_signal
    app.App.settings=app_settings
    app.App.render_logs=_render_logs
    app.App.__init__=app_init
    engine.Engine.cycle=cycle
    engine.Engine._kaytrade_v150_applied=True


apply()

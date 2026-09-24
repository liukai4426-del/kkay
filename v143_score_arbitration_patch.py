"""KAYTRADE V1.4.3 long/short score arbitration.

Only signals that already satisfy their own entry conditions take part:
- one eligible side: keep that side
- both eligible: select the strictly higher final score
- equal final scores: submit neither side and wait for a later closed 1m refresh

The patch deliberately does not change scoring, thresholds, sizing, TP/SL, order
submission, or risk controls inherited from V1.4.2.
"""
from v142_visual_patch import apply as apply_v142
apply_v142()

import app
import engine
import v138_strategy_patch as v138

V143_VERSION='1.4.3'
SIDES=('做多','做空')


def _score(row):
    return float((row or {}).get('total') or 0.0)


def _eligible(row):
    """Respect the complete eligibility decision made by the scoring engine."""
    return bool((row or {}).get('eligible'))


def _arbitrate(scores):
    """Return (side, state) without allowing an unqualified score to win."""
    qualified=[side for side in SIDES if _eligible((scores or {}).get(side))]
    if len(qualified)==1:
        return qualified[0],'single_eligible'
    if len(qualified)!=2:
        return '观望','no_eligible'
    long_score=_score(scores.get('做多'))
    short_score=_score(scores.get('做空'))
    if abs(long_score-short_score)<=1e-9:
        return '观望','equal_score_wait'
    return ('做多' if long_score>short_score else '做空'),'higher_score'


def _apply_arbitration(result):
    scores=result.get('scores') or {}
    side,state=_arbitrate(scores)
    long_score=_score(scores.get('做多'))
    short_score=_score(scores.get('做空'))
    result['side']=side
    result['direction_wait']=state=='equal_score_wait'
    result['direction_arbitration']={
        'state':state,
        'long_score':long_score,
        'short_score':short_score,
        'selected':side,
    }
    if state=='equal_score_wait':
        result['why']=(f'多空均满足开仓条件且同为 {long_score:g}/10；本轮不发单，进入方向等待。'
                       '下一根已收盘1m重新评分，分数更高且仍满足条件的一侧才允许开仓')
    elif state=='higher_score':
        chosen=str((scores.get(side) or {}).get('reason') or '')
        result['why']=(f'多空均满足开仓条件：做多 {long_score:g}/10，做空 {short_score:g}/10；'
                       f'仅选择高分方向{side}。{chosen}')
    elif state=='single_eligible':
        result['why']=str((scores.get(side) or {}).get('reason') or result.get('why') or '')
    return result


def apply():
    if getattr(engine.Engine,'_kaytrade_v143_score_arbitration_applied',False):
        return

    previous_signal=v138.v138_signal
    previous_cycle=engine.Engine.cycle
    previous_app_init=app.App.__init__
    previous_app_arm=app.App.arm

    def v143_signal(*args,**kwargs):
        return _apply_arbitration(previous_signal(*args,**kwargs))

    def cycle(self):
        result=previous_cycle(self)
        market=getattr(self,'market',None) or {}
        arbitration=market.get('direction_arbitration') or {}
        if arbitration.get('state')=='equal_score_wait':
            key=(market.get('bar'),arbitration.get('long_score'),arbitration.get('short_score'))
            if key!=getattr(self,'_v143_wait_log_key',None):
                self._v143_wait_log_key=key
                self.emit('log',market.get('why') or '多空合格信号同分，方向等待')
        else:
            self._v143_wait_log_key=None
        p=self.store.data.get('active') if self.store else None
        if isinstance(p,dict) and p.get('v142'):
            changed=p.get('version')!=V143_VERSION or not p.get('v143')
            p['v143']=True; p['version']=V143_VERSION
            if changed:self.store.save()
        return result

    def app_init(self,*args,**kwargs):
        previous_app_init(self,*args,**kwargs)
        try:self.root.title('KAYTRADE 1.4.3 · BTC 策略控制台')
        except Exception:pass
        for widget in v138._widgets(self.root):
            try:
                text=str(widget.cget('text') or '')
                if 'V1.4.2 视觉优化版' in text:
                    widget.configure(text=text.replace('V1.4.2 视觉优化版','V1.4.3 评分择优版'))
            except Exception:pass

    def app_arm(self):
        original=app.simpledialog.askstring
        def askstring(title,prompt,*args,**kwargs):
            prompt=str(prompt)+(\
                '\n\nV1.4.3方向仲裁：多空同时满足开仓条件时只开最终评分更高的一侧；同分不发单，等待后续已收盘1m重新评分。')
            return original(title,prompt,*args,**kwargs)
        app.simpledialog.askstring=askstring
        try:return previous_app_arm(self)
        finally:app.simpledialog.askstring=original

    v138.v138_signal=v143_signal
    engine.Engine.cycle=cycle
    app.App.__init__=app_init
    app.App.arm=app_arm
    engine.Engine._kaytrade_v143_score_arbitration_applied=True


apply()

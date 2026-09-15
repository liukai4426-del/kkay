"""KAYTRADE V1.6.2 production overlay.

1) Outer-band initial order returns to normal 1x sizing (no 2x single LIMIT).
2) +1R break-even amendment is disabled; original 1H ATR SL stays until SL/2R TP.
3) All 5m BOLL entry paths require 4H aligned.
4) Opening logs explicitly include LONG/SHORT direction and key order fields.
5) OKX 51290 bot-engine upgrade is treated as temporary: pause new entries,
   do not permanently stop the engine, and auto-recover after backoff/read checks.
"""
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path

from v161_update_patch import apply as apply_previous
apply_previous()

import app
import engine
import exchange
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v152_strategy_patch as v152_runtime
import v153_runtime_patch as v153_runtime
import v153_backtest_core as v153_bt
import v154_runtime_patch as v154_runtime
import v154_limit_log_fix as v154_limit
import v154_record_card_boll_fix as v154_record
import v160_update_patch as v160
import v161_update_patch as v161
import v162_model as model

VERSION="1.6.2"
BUILD="1620"
BOT_UPGRADE_CODE="51290"
BOT_RETRY_SECONDS=(5.0,10.0,15.0,30.0)

# Route every strategy facade through V1.6.2.
v160.model=model
v161.model=model
v152_runtime.model=model
v152_runtime.V152_VERSION=VERSION
v153_runtime.model=model
v153_runtime.VERSION=VERSION
v153_bt.model=model
v154_runtime.model=model
v154_limit.model=model
if hasattr(v154_record,"model"): v154_record.model=model

# V1.6.2 explicitly disables the V1.6.1 BE state machine for all new cycles.
v161._maybe_trigger_be=lambda owner: None
v161._write_probe=lambda: None

_PREVIOUS_APP_INIT=app.App.__init__
_PREVIOUS_ENGINE_ARM=engine.Engine.arm
_PREVIOUS_CYCLE=engine.Engine.cycle
_PREVIOUS_SUBMIT=v154_limit._submit_initial_limit


def _probe_target(): return os.environ.get("KAYTRADE_STARTUP_PROBE","").strip()
def _write_probe():
    target=_probe_target()
    if not target: return
    try:
        Path(target).write_text(
            f"PASS KAYTRADE {VERSION} Build {BUILD} boll_4h=aligned_required be_1r=off "
            "outer_band=1x limit_entry=on stop=1h_atr_1x tp=2r clock_sync=3sample_auto_recover "
            "okx_51290=soft_pause_auto_recover\n",encoding="utf-8")
    except Exception: pass

def _write_probe_error():
    target=_probe_target()
    if target:
        try: Path(target+".error").write_text(traceback.format_exc(),encoding="utf-8")
        except Exception: pass


def _prepare_order_v162(self,side,score,market,equity,available,remaining):
    """Use the inherited normal 1x preparation for BOTH middle and outer paths."""
    path=v160._path_from(market,score)
    if path == model.MIDDLE_PATH and not model._macd_improving(score or {}):
        self.emit("log","V1.6.2实际开仓前复核：中轨5m MACD连续改善未通过，禁止提交订单；机会保留")
        return None
    prepared=v160._PREVIOUS_PREPARE(self,side,score,market,equity,available,remaining)
    if not prepared: return prepared
    plan,tier,_multiplier,_level=prepared
    # Force the production entry label/metadata to the base 1x rule. No outer doubling.
    plan["version"]=VERSION; plan["build"]=BUILD
    plan["boll_signal_path"]=path or plan.get("boll_signal_path")
    plan["base_position_multiplier"]=1.0
    plan["boll_position_multiplier"]=1.0
    plan["position_multiplier"]=1.0
    plan["position_rule"]="all_boll_1x"
    return plan,tier,1.0,"开仓信号 · 1×基础仓位"

v138._prepare_order=_prepare_order_v162


class _BotUpgradeTemporary(exchange.NetworkError):
    pass


def _submit_v162(self,market,score,equity,available,remaining):
    """Add 51290 soft-pause semantics around the audited LIMIT submitter."""
    original_post=self.x.post
    active_before=self.store.data.get("active") if self.store else None
    last_bar_before=self.store.data.get("last_bar") if self.store else None

    def post(path,body):
        try:
            return original_post(path,body)
        except exchange.APIError as exc:
            if str(getattr(exc,"code","") or "") == BOT_UPGRADE_CODE:
                raise _BotUpgradeTemporary(
                    "OKX 51290：Trading bot engine currently upgrading. Try again later.",
                    BOT_UPGRADE_CODE,getattr(exc,"http_status",None),"POST",path) from None
            raise

    self.x.post=post
    try:
        return _PREVIOUS_SUBMIT(self,market,score,equity,available,remaining)
    except _BotUpgradeTemporary as exc:
        # 51290 is an explicit service-unavailable rejection. Release the local
        # pre-submit placeholder, but consume this signal so it is never blindly
        # replayed. Existing positions/protection are untouched.
        if self.store:
            state=self.store.data
            candidate=state.get("active")
            if isinstance(candidate,dict) and not candidate.get("order_id") and not candidate.get("filled"):
                state["active"]=active_before
                # Do not restore the just-consumed signal bar: retry only on a new
                # strategy opportunity after service recovery.
                if active_before is not None:
                    state["last_bar"]=last_bar_before
                self.store.save()
        streak=int(getattr(self,"_v162_51290_streak",0) or 0)+1
        self._v162_51290_streak=streak
        delay=BOT_RETRY_SECONDS[min(streak-1,len(BOT_RETRY_SECONDS)-1)]
        self._v162_51290_retry_at=time.monotonic()+delay
        self._v162_51290_resume=bool(self.enabled and not self.stopped)
        self.enabled=False
        self.emit("alarm",f"OKX错误码 51290｜策略交易引擎正在升级；仅暂停新开仓 {delay:.0f}s，不进入永久故障锁；已有仓位/TP/SL继续管理")
        return None
    finally:
        self.x.post=original_post

v138._submit_initial=_submit_v162


def _normalize_log(kind,data):
    if not isinstance(data,str): return data
    text=data.replace("V1.6.1","V1.6.2")
    if text.startswith("自动交易启动；"):
        return ("自动交易启动；V1.6.2：5m BOLL中轨与外轨均要求4H同向；中轨继续要求5m MACD连续改善；"
                "全部BOLL首次开仓统一1×基础仓位LIMIT；关闭+1R保本，保持原始1×1H ATR止损直到SL或2R整仓止盈；"
                "OKX 51290仅软暂停新开仓并自动恢复")
    return text


def apply():
    if getattr(engine.Engine,"_kaytrade_v162_applied",False): return

    def app_init(self,*args,**kwargs):
        try:
            _PREVIOUS_APP_INIT(self,*args,**kwargs)
            self.root.title("KAYTRADE 1.6.2 · BTC 策略控制台 · Build 1620")
            self.signal.set("V1.6.2 · 全BOLL 4H同向 / 1×开仓 / No-BE")
            self._v162_ready=True
            _write_probe()
        except Exception:
            _write_probe_error(); raise

    def engine_arm(self,settings):
        original_emit=self.emit
        def emit(kind,data): return original_emit(kind,_normalize_log(kind,data))
        self.emit=emit
        try: return _PREVIOUS_ENGINE_ARM(self,settings)
        finally: self.emit=original_emit

    def cycle(self):
        # Auto-recover the temporary 51290 new-entry pause. This does not bypass
        # user stop, store halt, or the inherited clock-sync safety state.
        retry_at=float(getattr(self,"_v162_51290_retry_at",0.0) or 0.0)
        if retry_at and time.monotonic() >= retry_at:
            self._v162_51290_retry_at=0.0
            if getattr(self,"_v162_51290_resume",False) and not self.stopped and self.store and not self.store.data.get("halt"):
                if not bool(getattr(self.x,"clock_sync_degraded",False)):
                    self.enabled=True
                    self._v162_51290_resume=False
                    self.emit("log","OKX 51290退避期结束：恢复允许新开仓；后续写入仍按新信号执行，不重复提交上一笔")

        result=_PREVIOUS_CYCLE(self)
        if self.store:
            active=self.store.data.get("active")
            if isinstance(active,dict):
                changed=active.get("version")!=VERSION or active.get("build")!=BUILD
                active["version"]=VERSION; active["build"]=BUILD
                active["boll_4h_aligned_required"]=True
                active["outer_band_position_multiplier"]=1.0
                active["be_enabled"]=False
                active.pop("be_trigger_r",None)
                if changed: self.store.save()
        return result

    app.App.__init__=app_init
    engine.Engine.arm=engine_arm
    engine.Engine.cycle=cycle
    engine.Engine._kaytrade_v162_applied=True
    app.App._kaytrade_v162_applied=True

apply()

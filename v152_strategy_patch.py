"""KAYTRADE V1.5.2 runtime wiring.

Entry sequence:
1H direction -> frozen 15m pullback opportunity -> mandatory closed-5m
confirmation -> mandatory closed-1m trigger -> score/hard-gate checks -> LIMIT entry.

V1.5.2 keeps V1.5.1's fixed 6.0 threshold, one 1x position, 1H ATR stop,
full-position 2R target, and 3-loss/1-hour pause. TP/SL trigger execution is
returned to market execution for this version.
"""
from __future__ import annotations

import copy
import math
import time

from v151_strategy_patch import apply as apply_v151
apply_v151()

import app
import engine
import exchange
import v137_strategy_patch as v137
import v138_strategy_patch as v138
import v151_strategy_patch as v151
import v152_model as model

V152_VERSION = "1.5.2"


def _zone_for_rearm(opportunity):
    if not isinstance(opportunity, dict):
        return None
    return {
        "id": opportunity.get("id", ""),
        "side": opportunity.get("side", ""),
        "zone_low": float(opportunity.get("zone_low") or 0.0),
        "zone_high": float(opportunity.get("zone_high") or 0.0),
    }


def _rearm_ready(rearm, one):
    if not isinstance(rearm, dict) or not one:
        return True
    close = float(one[-1]["c"])
    if rearm.get("side") == "做多":
        return close > float(rearm.get("zone_high") or 0.0)
    if rearm.get("side") == "做空":
        return close < float(rearm.get("zone_low") or 0.0)
    return True


def _update_mfe_mae(active, bar):
    if not isinstance(active, dict) or not active.get("v152") or not active.get("filled"):
        return False
    try:
        entry = float(active.get("avg_px") or active.get("fill_px") or active.get("px"))
        high = float(bar["h"]); low = float(bar["l"])
    except (TypeError, ValueError, KeyError):
        return False
    if entry <= 0:
        return False
    if active.get("side") == "做多":
        mfe = max(0.0, high - entry); mae = max(0.0, entry - low)
    else:
        mfe = max(0.0, entry - low); mae = max(0.0, high - entry)
    changed = False
    if mfe > float(active.get("mfe_price") or 0.0):
        active["mfe_price"] = mfe; changed = True
    if mae > float(active.get("mae_price") or 0.0):
        active["mae_price"] = mae; changed = True
    stop_distance = float(active.get("stop_distance") or 0.0)
    if stop_distance > 0:
        active["mfe_r"] = float(active.get("mfe_price") or 0.0) / stop_distance
        active["mae_r"] = float(active.get("mae_price") or 0.0) / stop_distance
        changed = True
    return changed


def _market_exit_payload(path, body):
    """V1.5.2 TP/SL trigger -> market exit, full-position one-time 2R TP."""
    if path != "/api/v5/trade/order" or not isinstance(body, dict) or not body.get("attachAlgoOrds"):
        return body
    payload = copy.deepcopy(body)
    items = [x for x in list(payload.get("attachAlgoOrds") or []) if isinstance(x, dict)]
    tps = [x for x in items if x.get("tpTriggerPx") not in (None, "")]
    sls = [x for x in items if x.get("slTriggerPx") not in (None, "")]
    if not tps or not sls:
        return payload
    # In inherited payloads the final/highest reward target is the last TP item.
    tp = tps[-1]
    sl = sls[-1]
    bracket_id = str(tp.get("attachAlgoClOrdId") or sl.get("attachAlgoClOrdId") or "")
    payload["attachAlgoOrds"] = [{
        "attachAlgoClOrdId": bracket_id,
        "tpOrdKind": "condition",
        "tpTriggerPx": str(tp["tpTriggerPx"]),
        "tpOrdPx": "-1",
        "tpTriggerPxType": str(tp.get("tpTriggerPxType") or "last"),
        "slTriggerPx": str(sl["slTriggerPx"]),
        "slOrdPx": "-1",
        "slTriggerPxType": str(sl.get("slTriggerPxType") or "last"),
    }]
    return payload


def _pause_remaining_text(engine_obj):
    if not engine_obj.store:
        return ""
    seconds = v151._pause_remaining(engine_obj.store.data)
    if seconds <= 0:
        return ""
    return f"｜连亏暂停剩余 {int(math.ceil(seconds / 60.0))} 分钟"


def apply():
    if getattr(engine.Engine, "_kaytrade_v152_applied", False):
        return

    previous_prepare = v138._prepare_order
    previous_cycle = engine.Engine.cycle
    previous_finalize = v137._finalize_cycle
    previous_post = exchange.Exchange.post
    previous_arm_engine = engine.Engine.arm
    previous_app_init = app.App.__init__
    previous_app_arm = app.App.arm

    def refresh_market(self):
        q = self.x.candles("4H")
        h = self.x.candles("1H")
        m = self.x.candles("15m")
        f = self.x.candles("5m")
        o = self.x.candles("1m")
        self._verify_latest("4H", q[-1]["t"], 14400000)
        self._verify_latest("1H", h[-1]["t"], 3600000)
        self._verify_latest("15m", m[-1]["t"], 900000)
        self._verify_latest("5m", f[-1]["t"], 300000)
        self._verify_latest("1m", o[-1]["t"], v138.ONE_MINUTE_STEP)

        state = self.store.data if self.store else {}
        old_opp = state.get("v152_opportunity") if isinstance(state.get("v152_opportunity"), dict) else None
        rearm = state.get("v152_rearm_zone") if isinstance(state.get("v152_rearm_zone"), dict) else None
        if rearm and _rearm_ready(rearm, o):
            old_rearm = dict(rearm)
            rearm = None
            state["v152_rearm_zone"] = None
            if self.store:
                self.store.save()
                self.store.record("V1.5.2回调机会重新武装", old_rearm)
                self.emit("log", "价格已离开上一固定回调区；允许等待下一次重新回调")

        allow_new = rearm is None and not state.get("active")
        result, new_opp, transition = model.evaluate(
            h, m, f, o, q, opportunity=old_opp,
            stop_atr=self.settings.stop_atr if self.settings else 1.0,
            maker_bps=self.settings.fee_bps if self.settings else 2.0,
            taker_bps=self.settings.taker_fee_bps if self.settings else 5.0,
            slippage_bps=self.settings.slippage_bps if self.settings else 5.0,
            allow_new=allow_new,
        )

        changed = old_opp != new_opp
        if transition and transition[0] == "invalidated" and old_opp:
            state["v152_rearm_zone"] = _zone_for_rearm(old_opp)
        if changed:
            state["v152_opportunity"] = new_opp
        if self.store and (changed or transition):
            self.store.save()
        if self.store and transition:
            key = f"{transition[0]}:{result.get('opportunity_id') or (old_opp or {}).get('id') or ''}:{o[-1]['t']}"
            if state.get("v152_last_transition_key") != key:
                state["v152_last_transition_key"] = key
                self.store.save()
                self.store.record("V1.5.2机会状态", {
                    "transition": transition[0], "detail": transition[1],
                    "opportunity_id": result.get("opportunity_id") or (old_opp or {}).get("id", ""),
                    "status": result.get("status"),
                })
                messages = {
                    "created": "已创建固定15m回调机会",
                    "five_confirmed": "5m回调结束确认已通过",
                    "one_triggered": "1m恢复Trigger已通过",
                    "invalidated": "回调机会失效",
                }
                self.emit("log", f"{messages.get(transition[0], transition[0])}：{transition[1]}")

        result["why"] = str(result.get("why") or "") + _pause_remaining_text(self)
        self.market = dict(result, bar=o[-1]["t"], bar1m=o[-1]["t"], bar5m=f[-1]["t"],
                           bar15=m[-1]["t"], bar1h=h[-1]["t"], bar4h=q[-1]["t"], close=o[-1]["c"])
        self.market_at = time.time()
        self.market_monotonic = time.monotonic()
        self._candle_recovered()
        self.emit("market", self.market)

    def prepare_order_v152(self, side, score, market, equity, available, remaining):
        prepared = previous_prepare(self, side, score, market, equity, available, remaining)
        if not prepared:
            return prepared
        plan, tier, multiplier, level = prepared
        opportunity = market.get("opportunity") or score.get("opportunity")
        ok, diag, blockers = model.execution_checks(plan, opportunity, score)
        if not ok:
            if self.store:
                self.store.data["last_bar"] = market.get("bar", self.store.data.get("last_bar", 0))
                self.store.save()
                self.store.record("V1.5.2执行边界拦截", {"opportunity_id": diag.get("opportunity_id"), "reasons": blockers, "diag": diag})
            self.emit("log", "V1.5.2实际限价二次检查禁止开仓：" + "；".join(blockers))
            return None
        confirmations = score.get("confirmations") or {}
        plan.update({
            "version": V152_VERSION, "v152": True,
            "opportunity_id": (opportunity or {}).get("id", ""),
            "opportunity_zone_low": (opportunity or {}).get("zone_low"),
            "opportunity_zone_high": (opportunity or {}).get("zone_high"),
            "pullback_extreme": (opportunity or {}).get("extreme"),
            "front_r": diag.get("front_r"), "front_space_status": diag.get("front_space_status"),
            "cost_r": diag.get("cost_r"), "stop_buffer_ok": diag.get("stop_buffer_ok"),
            "score_components": dict(score.get("layers") or {}),
            "required_checks": dict(confirmations.get("required") or {}),
            "trigger_combination": dict(confirmations.get("trigger") or {}),
            "market_exit_on_trigger": True,
            "mfe_price": 0.0, "mae_price": 0.0, "mfe_r": 0.0, "mae_r": 0.0,
        })
        return plan, tier, multiplier, level

    def cycle(self):
        before_active = copy.deepcopy(self.store.data.get("active")) if self.store else None
        result = previous_cycle(self)
        if not self.store:
            return result
        state = self.store.data
        active = state.get("active")
        changed = False
        if isinstance(active, dict) and (active.get("v152") or active.get("version") == V152_VERSION):
            active["v152"] = True
            active["version"] = V152_VERSION
            if self.market and self.market.get("o"):
                changed = _update_mfe_mae(active, self.market["o"][-1]) or changed
        if isinstance(active, dict) and not isinstance(before_active, dict):
            opp = state.get("v152_opportunity")
            if isinstance(opp, dict):
                active["v152"] = True
                active["version"] = V152_VERSION
                active["opportunity_id"] = active.get("opportunity_id") or opp.get("id", "")
                state["v152_rearm_zone"] = _zone_for_rearm(opp)
                state["v152_opportunity"] = None
                changed = True
                self.store.record("V1.5.2机会已提交开仓", {
                    "opportunity_id": opp.get("id"), "score": active.get("score"),
                    "score_components": active.get("score_components", {}),
                    "trigger_combination": active.get("trigger_combination", {}),
                    "front_r": active.get("front_r"), "cost_r": active.get("cost_r"),
                })
        if changed:
            self.store.save()
        return result

    def finalize_cycle(self, p):
        if self.store and isinstance(p, dict) and p.get("v152"):
            self.store.record("V1.5.2逐笔诊断", {
                "client_id": p.get("client_id"), "opportunity_id": p.get("opportunity_id"),
                "side": p.get("side"), "score": p.get("score"),
                "score_components": p.get("score_components", {}),
                "required_checks": p.get("required_checks", {}),
                "trigger_combination": p.get("trigger_combination", {}),
                "front_r": p.get("front_r"), "front_space_status": p.get("front_space_status"),
                "cost_r": p.get("cost_r"), "mfe_r": p.get("mfe_r", 0.0), "mae_r": p.get("mae_r", 0.0),
            })
        return previous_finalize(self, p)

    def post(self, path, body):
        if path == "/api/v5/trade/order" and isinstance(body, dict) and body.get("attachAlgoOrds"):
            payload = _market_exit_payload(path, body)
            return self.request("POST", path, payload, True)
        return previous_post(self, path, body)

    def engine_arm(self, settings):
        original_emit = self.emit
        def emit(kind, data):
            if kind == "log" and isinstance(data, str) and data.startswith("自动交易启动；"):
                data = ("自动交易启动；V1.5.2：1H趋势→15m固定回调机会→5m确认→1m Trigger→评分/限制→限价开仓；"
                        "固定6分且必须项不可被高分补偿；前方强结构至少1.5R；1H ATR止损/整仓2R；"
                        "TP/SL触发后市价退出；连续3次净亏损暂停新开仓1小时")
            return original_emit(kind, data)
        self.emit = emit
        try:
            return previous_arm_engine(self, settings)
        finally:
            self.emit = original_emit

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        try:
            self.root.title("KAYTRADE 1.5.2 · BTC 策略控制台")
            self.signal.set("V1.5.2 · 等待趋势")
        except Exception:
            pass
        for widget in v138._widgets(self.root):
            try:
                text = str(widget.cget("text") or "")
            except Exception:
                text = ""
            try:
                text2 = text.replace("1.5.1", "1.5.2")
                text2 = text2.replace("<1.3R", "<1.5R")
                text2 = text2.replace("TP1=1R平50% · TP2=2R平50% · TP1后SL自动移到成交均价", "整仓TP=2R · TP/SL触发后市价退出")
                if text2 != text:
                    widget.configure(text=text2)
            except Exception:
                pass
        self._v152_ui_ready = True

    def app_arm(self):
        original = app.simpledialog.askstring
        def askstring(title, prompt, *args, **kwargs):
            text = str(prompt)
            text += ("\n\nV1.5.2：必须按 1H方向→15m回调→5m确认→1m触发 的顺序；固定6分开仓且必须项缺失不能补分；"
                     "前方强结构<1.5R、结构止损缓冲不足、成本>0.30R等均禁止开仓；"
                     "整仓2R，TP/SL触发后市价退出；连续3亏暂停1小时保持不变。")
            return original(title, text, *args, **kwargs)
        app.simpledialog.askstring = askstring
        try:
            return previous_app_arm(self)
        finally:
            app.simpledialog.askstring = original

    engine.Engine.refresh_market = refresh_market
    v138._prepare_order = prepare_order_v152
    engine.Engine.cycle = cycle
    v137._finalize_cycle = finalize_cycle
    exchange.Exchange.post = post
    engine.Engine.arm = engine_arm
    app.App.__init__ = app_init
    app.App.arm = app_arm
    engine.Engine._kaytrade_v152_applied = True


apply()

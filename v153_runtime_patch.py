"""KAYTRADE V1.5.3 runtime wiring layered on the verified V1.5.2 safety stack."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from v152_ui_busy_fix import apply as apply_v152_ui
apply_v152_ui()

import app
import engine
import v138_strategy_patch as v138
import v152_backtest_core as v152_bt
import v152_strategy_patch as v152_runtime
import visual
import v153_model as model

VERSION = "1.5.3"


def _zone_for_rearm(opportunity):
    if not isinstance(opportunity, dict):
        return None
    signal_close = int(opportunity.get("signal_close_ms") or opportunity.get("created_ms") or 0)
    return {
        "id": opportunity.get("id", ""),
        "side": opportunity.get("side", ""),
        "signal_bar_t": int(opportunity.get("signal_bar_t") or 0),
        "rearm_after_ms": signal_close + 5 * 60 * 1000,
        "zone_low": float(opportunity.get("zone_low") or 0.0),
        "zone_high": float(opportunity.get("zone_high") or 0.0),
    }


def _rearm_ready(rearm, one):
    """Never reuse the same closed 5m BOLL signal after an entry."""
    if not isinstance(rearm, dict) or not one:
        return True
    latest_close_ms = int(one[-1]["t"]) + 60_000
    return latest_close_ms >= int(rearm.get("rearm_after_ms") or 0)


def _rewrite_text(text):
    if not isinstance(text, str):
        return text
    if text.startswith("自动交易启动；V1.5.2") or text.startswith("自动交易启动；V1.5.3"):
        return (
            "自动交易启动；V1.5.3：1H/15m趋势定方向→5m BOLL回调触发→4根1m直接执行窗口→评分/限制→限价开仓；"
            "做多=上方回调至中轨且RSI≥30，未触发则下轨触碰；做空=下方反弹至中轨且RSI≤70，未触发则上轨触碰；"
            "BOLL模块固定+2且同一方向不叠加；1m不再做EMA/突破/KDJ等技术确认；单一1×仓位、不加仓；"
            "固定6分；前方强结构至少1.5R；1.0×1H ATR止损/整仓2R；连续3次净亏损暂停新开仓1小时"
        )
    replacements = (
        ("V1.5.2", "V1.5.3"),
        ("1.5.2", "1.5.3"),
        ("已创建固定15m回调机会", "已创建5m BOLL入场信号，4分钟直接执行窗口已开启"),
        ("价格已离开上一固定回调区；允许等待下一次重新回调", "上一5m BOLL信号周期已结束；允许等待新的5m BOLL信号"),
        ("5m回调结束确认已通过", "5m BOLL入场信号已确认"),
        ("1m恢复Trigger已通过", "1m仅作为4分钟执行窗口，不再做技术确认"),
        ("等待5m确认", "等待5m BOLL信号"),
        ("等待1m触发", "等待4根1m执行窗口"),
        ("固定15m回调", "5m BOLL回调"),
    )
    out = text
    for old, new in replacements:
        out = out.replace(old, new)
    return out


def _with_record_rewrite(store):
    if store is None:
        return None
    original = store.record

    def record(event, data):
        return original(_rewrite_text(str(event)), data)

    store.record = record
    return original


def _restore_record(store, original):
    if store is not None and original is not None:
        store.record = original


def _wheel_units(event):
    delta = getattr(event, "delta", 0)
    if not delta:
        return 0
    return (-1 if delta > 0 else 1) if abs(delta) < 120 else int(-delta / 120)


def _bind_hidden_scroll(widget):
    if isinstance(widget, (ttk.Treeview, tk.Text, tk.Listbox)):
        def wheel(event, target=widget):
            units = _wheel_units(event)
            if units:
                try:
                    target.yview_scroll(units, "units")
                    return "break"
                except Exception:
                    return None
        widget.bind("<MouseWheel>", wheel, add="+")
        widget.bind("<Button-4>", lambda event, target=widget: (target.yview_scroll(-1, "units"), "break")[1], add="+")
        widget.bind("<Button-5>", lambda event, target=widget: (target.yview_scroll(1, "units"), "break")[1], add="+")
        try:
            widget.bind("<Shift-MouseWheel>", lambda event, target=widget: (target.xview_scroll(_wheel_units(event), "units"), "break")[1], add="+")
        except Exception:
            pass


def _hide_scrollbars(root):
    hidden = []
    for widget in v138._widgets(root):
        if isinstance(widget, (ttk.Scrollbar, visual.WideScrollbar)):
            try:
                manager = widget.winfo_manager()
                if manager == "pack":
                    widget.pack_forget()
                elif manager == "grid":
                    widget.grid_remove()
                elif manager == "place":
                    widget.place_forget()
                hidden.append(widget)
            except Exception:
                pass
        else:
            _bind_hidden_scroll(widget)
    return hidden


def apply():
    if getattr(engine.Engine, "_kaytrade_v153_applied", False):
        return

    v152_runtime.model = model
    v152_runtime.V152_VERSION = VERSION
    v152_runtime._zone_for_rearm = _zone_for_rearm
    v152_runtime._rearm_ready = _rearm_ready
    v152_bt.model = model

    previous_prepare = v138._prepare_order
    previous_refresh = engine.Engine.refresh_market
    previous_cycle = engine.Engine.cycle
    previous_arm = engine.Engine.arm
    previous_settings = app.App.settings
    previous_app_init = app.App.__init__
    previous_app_arm = app.App.arm

    def prepare_order_v153(self, side, score, market, equity, available, remaining):
        prepared = previous_prepare(self, side, score, market, equity, available, remaining)
        if not prepared:
            return prepared
        plan, tier, multiplier, level = prepared
        opportunity = market.get("opportunity") or score.get("opportunity") or {}
        plan["version"] = VERSION
        plan["v153"] = True
        plan["boll_signal_path"] = opportunity.get("signal_path")
        plan["entry_window_minute"] = ((score.get("confirmations") or {}).get("entry_window_minute") or 0)
        plan["one_minute_technical_confirmation"] = False
        return plan, tier, multiplier, level

    def refresh_market(self):
        original_emit = self.emit
        original_record = _with_record_rewrite(self.store)

        def emit(kind, data):
            return original_emit(kind, _rewrite_text(data) if isinstance(data, str) else data)

        self.emit = emit
        try:
            return previous_refresh(self)
        finally:
            self.emit = original_emit
            _restore_record(self.store, original_record)

    def cycle(self):
        original_emit = self.emit
        original_record = _with_record_rewrite(self.store)

        def emit(kind, data):
            return original_emit(kind, _rewrite_text(data) if isinstance(data, str) else data)

        self.emit = emit
        try:
            result = previous_cycle(self)
            if self.store:
                active = self.store.data.get("active")
                if isinstance(active, dict) and (active.get("version") == VERSION or active.get("v153")):
                    active["version"] = VERSION
                    active["v153"] = True
                    active["one_minute_technical_confirmation"] = False
                    self.store.save()
            return result
        finally:
            self.emit = original_emit
            _restore_record(self.store, original_record)

    def engine_arm(self, settings):
        original_emit = self.emit

        def emit(kind, data):
            return original_emit(kind, _rewrite_text(data) if isinstance(data, str) else data)

        self.emit = emit
        try:
            return previous_arm(self, settings)
        except engine.Halt as exc:
            raise engine.Halt(_rewrite_text(str(exc))) from None
        finally:
            self.emit = original_emit

    def app_settings(self):
        try:
            return previous_settings(self)
        except engine.Halt as exc:
            raise engine.Halt(_rewrite_text(str(exc))) from None

    def app_init(self, *args, **kwargs):
        previous_app_init(self, *args, **kwargs)
        try:
            self.root.title("KAYTRADE 1.5.3 · BTC 策略控制台")
            self.signal.set("V1.5.3 · 等待趋势 / 5m BOLL信号")
        except Exception:
            pass
        for widget in v138._widgets(self.root):
            try:
                text = str(widget.cget("text") or "")
            except Exception:
                text = ""
            if not text:
                continue
            try:
                rewritten = _rewrite_text(text)
                rewritten = rewritten.replace("1H方向→15m回调→5m确认→1m触发", "1H/15m趋势→5m BOLL触发→4分钟直接执行")
                if rewritten != text:
                    widget.configure(text=rewritten)
            except Exception:
                pass
        self._v153_hidden_scrollbars = _hide_scrollbars(self.root)
        self._v153_ui_ready = True

    def app_arm(self):
        original = app.simpledialog.askstring

        def askstring(title, prompt, *args, **kwargs):
            text = _rewrite_text(str(prompt))
            text = text.replace("必须按 1H方向→15m回调→5m确认→1m触发 的顺序", "按1H/15m趋势方向等待5m BOLL回调信号；信号后4分钟内1m不做技术确认")
            if "V1.5.3 BOLL规则" not in text:
                text += (
                    "\n\nV1.5.3 BOLL规则：做多=从中轨上方向下回调至中轨且RSI≥30，未触发则下轨触碰；"
                    "做空=从中轨下方向上反弹至中轨且RSI≤70，未触发则上轨触碰。"
                    "两条路径二选一固定+2，不叠加；5m信号后仅4分钟有效，1m无EMA/突破/KDJ等确认；"
                    "单一1×仓位，不进行第二次加仓。"
                )
            return original(title, text, *args, **kwargs)

        app.simpledialog.askstring = askstring
        try:
            return previous_app_arm(self)
        finally:
            app.simpledialog.askstring = original

    v138._prepare_order = prepare_order_v153
    engine.Engine.refresh_market = refresh_market
    engine.Engine.cycle = cycle
    engine.Engine.arm = engine_arm
    app.App.settings = app_settings
    app.App.__init__ = app_init
    app.App.arm = app_arm
    engine.Engine._kaytrade_v153_applied = True
    app.App._kaytrade_v153_hidden_scrollbars_applied = True


apply()

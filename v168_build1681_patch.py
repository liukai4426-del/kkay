"""KAYTRADE V1.6.8 Build1681 runtime/log cleanup.

Presentation/runtime-state repair only. Trading strategy semantics are unchanged.
"""
from __future__ import annotations

import re

from v168_update_patch import apply as apply_previous
apply_previous()

import app
import v165_model as model
import v165_update_patch as runtime
import v168_update_patch as v168

VERSION = "1.6.8"
BUILD = "1681"

_PREVIOUS_APP_EMIT = app.App.emit
_PREVIOUS_APP_INIT = app.App.__init__
_PREVIOUS_RUNTIME_TEXT = runtime._rewrite_runtime_text

_ALGO_UNTRUSTED = "Algo实时状态暂未完成可信同步"
_ALGO_READ_PATH = "/ws/v5/business:orders-algo"


def _sanitize_runtime_text(data):
    if not isinstance(data, str):
        return data

    text = _PREVIOUS_RUNTIME_TEXT(data)
    if not isinstance(text, str):
        return text

    if _ALGO_UNTRUSTED in text and (
        "只读查询暂时失败" in text or _ALGO_READ_PATH in text
    ):
        return (
            "Algo状态同步中：策略单实时缓存尚未完成可信校准；仅跳过本周期新开仓，"
            "自动交易保持开启；后台继续REST+WebSocket重同步，完成后下一周期重新通过"
            "Final Entry Guard。"
        )

    # Chinese characters are Unicode word characters, so \b cannot be used at
    # the end of a version token such as "V1.5.4未开仓". Use numeric guards.
    text = re.sub(
        r"(?<![A-Za-z0-9])V?1\.(?:[0-5]\.\d+|6\.[0-8])(?![\d.])",
        VERSION,
        text,
    )

    # Deep legacy rewrite layers can repeatedly prepend '1' to 5m BOLL text,
    # producing 115m/1115m BOLL. Canonicalize any such artifact once at the end.
    text = re.sub(r"(?<!\d)(?:1+)?15m\s+BOLL", "15m BOLL", text)
    text = re.sub(r"(?<!\d)5m\s+BOLL", "15m BOLL", text)
    text = re.sub(r"Build\s*1[3-7]\d{2}", "Build1681", text)

    replacements = (
        ("15m BOLL中轨/外轨", "15m BOLL外轨"),
        ("15m BOLL中轨／外轨", "15m BOLL外轨"),
        ("15m BOLL中轨", "15m BOLL外轨"),
        ("最新已收盘5m BOLL", "最新已收盘15m BOLL"),
        ("等待新的5m BOLL", "等待新的15m BOLL"),
        ("新的5m BOLL信号", "新的15m BOLL信号"),
        ("计划市价参考", "计划限价"),
        ("实际市价参考", "实际限价"),
        ("市价开仓", "限价开仓"),
    )
    for old, new in replacements:
        text = text.replace(old, new)

    text = re.sub(
        r"BOLL信号执行窗口[·・\s]*第\s*\d+\s*个1m",
        "15m BOLL信号有效期内",
        text,
    )
    text = text.replace("BOLL信号执行窗口", "15m BOLL信号有效期")

    text = re.sub(
        r"V1\.6\.8要求4H同向[：:]\s*15m BOLL(?:上下)?外轨在4H中性或逆向时禁止开仓[；;]?",
        "V1.6.8 4H Hard Gate：4H中性/逆向禁止开仓；",
        text,
    )

    text = text.replace("Build1680", "Build1681").replace("Build 1680", "Build 1681")
    return text


def _emit_v1681(self, kind, data):
    if isinstance(data, str):
        data = _sanitize_runtime_text(data)
    return _PREVIOUS_APP_EMIT(self, kind, data)


def _app_init_v1681(self, *args, **kwargs):
    _PREVIOUS_APP_INIT(self, *args, **kwargs)
    try:
        self.root.title("KAYTRADE 1.6.8 · BTC 策略控制台 · Build 1681")
    except Exception:
        pass
    try:
        label = getattr(self, "_v168_version_label", None)
        if label is not None:
            current = str(label.cget("text") or "")
            label.configure(text=current.replace("1680", "1681"))
    except Exception:
        pass


def apply():
    if getattr(app.App, "_kaytrade_v168_build1681_applied", False):
        return

    v168.BUILD = BUILD
    model.BUILD = BUILD
    runtime.BUILD = BUILD

    runtime._rewrite_runtime_text = _sanitize_runtime_text
    v168._rewrite_runtime_text_v168 = _sanitize_runtime_text
    app.App.emit = _emit_v1681
    app.App.__init__ = _app_init_v1681

    app.App._kaytrade_v168_build1681_applied = True
    model._kaytrade_v168_build1681_applied = True


apply()

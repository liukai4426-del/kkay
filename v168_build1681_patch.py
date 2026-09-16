"""KAYTRADE V1.6.8 Build1681 runtime/log cleanup.

This overlay intentionally does NOT change trading strategy semantics. It only:
- normalizes visible runtime text to the active V1.6.8 strategy;
- removes stale 5m BOLL middle/outer wording from legacy inherited messages;
- keeps legitimate 5m RSI/MACD/Volume references unchanged;
- rewrites legacy MARKET-entry wording to the current LIMIT-entry wording;
- turns the expected Algo cache re-synchronization state into a clear sync notice
  instead of the misleading generic "read-only query failed" presentation;
- updates visible/package build identity to Build1681.

All V1.6.8 Build1680 strategy locks remain unchanged: closed 15m outer-only BOLL,
15-minute lifecycle, BOLL +2, 4H aligned +1 mandatory Hard Gate, 1H EMA9/26 +1,
5m RSI/MACD/Volume confirmations, score >= 6, LIMIT entry, 1H ATR stop and 2R TP.
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

    # Expected fail-closed state while the read-only Algo cache is being
    # re-calibrated. This is not a failed order write and must not look like one.
    if _ALGO_UNTRUSTED in text and (
        "只读查询暂时失败" in text or _ALGO_READ_PATH in text
    ):
        return (
            "Algo状态同步中：策略单实时缓存尚未完成可信校准；仅跳过本周期新开仓，"
            "自动交易保持开启；后台继续REST+WebSocket重同步，完成后下一周期重新通过"
            "Final Entry Guard。"
        )

    # Normalize stale version/build labels that can leak from deep inherited
    # runtime layers. Do not rewrite current V1.6.8 itself.
    text = re.sub(r"V1\.(?:[0-5]\.\d+|6\.[0-7])", VERSION, text)
    text = re.sub(r"Build\s*1[3-7]\d{2}", "Build1681", text)

    # The active BOLL trigger is strictly the latest CLOSED 15m outer band.
    # Only BOLL wording is rewritten; legitimate 5m RSI/MACD/Volume semantics
    # remain untouched.
    replacements = (
        ("5m BOLL中轨/外轨", "15m BOLL外轨"),
        ("5m BOLL中轨／外轨", "15m BOLL外轨"),
        ("5m BOLL中轨", "15m BOLL外轨"),
        ("5m BOLL上下外轨", "15m BOLL上下外轨"),
        ("5m BOLL外轨", "15m BOLL外轨"),
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

    # Collapse the obsolete V1.6.2 rule explanation into the current 4H gate.
    text = re.sub(
        r"V1\.6\.8要求4H同向[：:]\s*15m BOLL(?:上下)?外轨在4H中性或逆向时禁止开仓[；;]?",
        "V1.6.8 4H Hard Gate：4H中性/逆向禁止开仓；",
        text,
    )

    # Current build identity wins after all inherited rewriting.
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

    # Identity only; strategy constants and function routes are untouched.
    v168.BUILD = BUILD
    model.BUILD = BUILD
    runtime.BUILD = BUILD

    # Make every user-visible string pass through the final V1.6.8 sanitizer,
    # including messages emitted by deep legacy layers that bypassed the older
    # runtime-specific rewrite wrapper.
    runtime._rewrite_runtime_text = _sanitize_runtime_text
    v168._rewrite_runtime_text_v168 = _sanitize_runtime_text
    app.App.emit = _emit_v1681
    app.App.__init__ = _app_init_v1681

    app.App._kaytrade_v168_build1681_applied = True
    model._kaytrade_v168_build1681_applied = True


apply()

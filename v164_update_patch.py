"""KAYTRADE V1.6.4 production runtime overlay.

V1.6.4 keeps the verified V1.6.3 order lifecycle and changes only strategy
wiring/presentation semantics:
- 5m signal volume >=1.20x prior-20 average becomes a hard opening gate;
- volume contributes no score;
- the transferred +0.5 is assigned to the 5m BOLL outer-band trigger;
- V1.6.3 outer-only RSI/MACD/4H rules remain in force.
"""
from __future__ import annotations

from v163_update_patch import apply as apply_previous
apply_previous()

import app
import engine
import v138_strategy_patch as v138
import v152_strategy_patch as v152_runtime
import v153_runtime_patch as v153_runtime
import v153_backtest_core as v153_bt
import v154_runtime_patch as v154_runtime
import v154_limit_log_fix as v154_limit
import v154_record_card_boll_fix as v154_record
import v160_update_patch as v160
import v161_update_patch as v161
import v162_update_patch as v162
import v163_update_patch as v163
import v164_model as model

VERSION = "1.6.4"
BUILD = "1640"

# Route every live/backtest facade that the production order lifecycle calls
# through the exact same V1.6.4 model.
v160.model = model
v161.model = model
v162.model = model
v162.VERSION = VERSION
v162.BUILD = BUILD
v163.model = model
v152_runtime.model = model
v152_runtime.V152_VERSION = VERSION
v153_runtime.model = model
v153_runtime.VERSION = VERSION
v153_bt.model = model
v154_runtime.model = model
v154_limit.model = model
if hasattr(v154_record, "model"):
    v154_record.model = model

# Preserve V1.6.2/V1.6.3 execution semantics: 1x LIMIT entry, 1H ATR stop,
# 2R full take-profit and BE disabled.
v138._prepare_order = v162._prepare_order_v162
v161._maybe_trigger_be = lambda owner: None

_PREVIOUS_ARM = engine.Engine.arm
_PREVIOUS_SUBMIT = v138._submit_initial


def _rewrite_runtime_text(data):
    if not isinstance(data, str):
        return data
    text = data.replace("V1.6.3", "V1.6.4").replace("V1.6.2", "V1.6.4")
    if text.startswith("自动交易启动；"):
        return (
            "自动交易启动；V1.6.4：仅5m BOLL外轨；外轨触发评分2.5（原Volume +0.5已转移）；"
            "5m信号K量能≥前20根5m均量1.20×时Hard Gate禁止开仓；Volume不再计分；"
            "做多下轨/做空上轨均要求5m RSI 30-70、5m MACD连续向交易方向改善、4H同向；"
            "全部首次开仓1× LIMIT；1×1H ATR止损、2R整仓止盈、No-BE；"
            "OKX 51290沿用V1.6.3软暂停与安全迁移"
        )
    return text


def _arm_v164(self, settings):
    original_emit = self.emit

    def emit(kind, data):
        return original_emit(kind, _rewrite_runtime_text(data))

    self.emit = emit
    try:
        return _PREVIOUS_ARM(self, settings)
    finally:
        self.emit = original_emit


def _submit_v164(self, market, score, equity, available, remaining):
    original_emit = self.emit

    def emit(kind, data):
        return original_emit(kind, _rewrite_runtime_text(data))

    self.emit = emit
    try:
        return _PREVIOUS_SUBMIT(self, market, score, equity, available, remaining)
    finally:
        self.emit = original_emit


def apply():
    if getattr(engine.Engine, "_kaytrade_v164_applied", False):
        return
    engine.Engine.arm = _arm_v164
    v138._submit_initial = _submit_v164
    engine.Engine._kaytrade_v164_applied = True
    app.App._kaytrade_v164_runtime_applied = True


apply()

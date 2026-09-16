"""V1.6.5 sleep/long-pause auto-resume safety patch.

A Mac sleep or scheduler pause is not itself an exchange/account integrity fault.
When the existing runtime reports the known sleep/long-pause condition, keep the
user's auto-trading authorization active, discard stale market state, force a
fresh OKX clock/candle read on the next cycle and apply a short safety buffer.

Real network/read failures are NOT bypassed: they continue through the existing
NetworkError/network-pause path and stop new entries while connectivity is bad.
Trading writes are untouched.
"""
from __future__ import annotations

import time

from v165_algo_fallback_patch import apply as apply_previous
apply_previous()

import engine

_SLEEP_TOKEN = "检测到睡眠或长时间停顿"
_RESYNC_BUFFER_SECONDS = 5.0


def apply():
    if getattr(engine.Engine, "_kaytrade_v165_sleep_resume_applied", False):
        return

    previous_halt = engine.Engine.halt

    def halt(self, reason):
        message = str(reason or "")
        if _SLEEP_TOKEN not in message:
            return previous_halt(self, reason)

        # Preserve the user's existing auto-trading authorization. Never use any
        # pre-sleep signal/market snapshot after a scheduler gap.
        was_enabled = bool(getattr(self, "enabled", False))
        self.market = None
        self.market_at = 0
        self.poll_at = 0
        self.candle_lag_count = 0
        self.candle_paused = False
        if hasattr(self, "startup_buffer_until"):
            self.startup_buffer_until = time.monotonic() + _RESYNC_BUFFER_SECONDS

        # Force the exchange adapter to synchronize OKX time again before fresh
        # candles are accepted. The next normal cycle performs the actual reads.
        x = getattr(self, "x", None)
        if x is not None:
            try:
                x.clock_anchor = None
            except Exception:
                pass

        # Do not manufacture a permanent or soft halt merely because macOS slept.
        # If the next read really has no connectivity, the existing network path
        # will pause entries exactly as before.
        self.enabled = was_enabled
        emit = getattr(self, "emit", None)
        if callable(emit):
            emit(
                "log",
                "检测到睡眠或长时间停顿：已废弃旧行情/旧信号并强制重新同步；"
                "保持自动交易授权，网络与最新K线恢复后自动继续开仓；断网时仍暂停新开仓",
            )
        return False

    engine.Engine.halt = halt
    engine.Engine._kaytrade_v165_sleep_resume_applied = True


apply()

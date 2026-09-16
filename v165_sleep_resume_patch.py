"""V1.6.5 sleep/long-pause soft-stop safety patch.

A Mac sleep or scheduler pause is not itself an exchange/account integrity fault.
When the existing runtime reports the known sleep/long-pause condition, stop new
automatic entries immediately WITHOUT writing a persistent fault lock. Discard
all stale pre-sleep market/signal state and force a fresh OKX clock/candle read.

After wake the application may continue refreshing/observing, but automatic entry
stays disabled until the user explicitly authorizes it again. Real network/read
failures are NOT bypassed and keep their existing safety behavior.
Trading writes are untouched.
"""
from __future__ import annotations

from v165_algo_fallback_patch import apply as apply_previous
apply_previous()

import engine

_SLEEP_TOKEN = "检测到睡眠或长时间停顿"


def apply():
    if getattr(engine.Engine, "_kaytrade_v165_sleep_resume_applied", False):
        return

    previous_halt = engine.Engine.halt

    def halt(self, reason):
        message = str(reason or "")
        if _SLEEP_TOKEN not in message:
            return previous_halt(self, reason)

        # Sleep / scheduler pause is a soft stop, not an integrity fault.
        # Never preserve a pre-sleep authorization and never use stale signals.
        self.enabled = False
        self.stopped = True
        self.market = None
        self.market_at = 0
        self.poll_at = 0
        self.candle_lag_count = 0
        self.candle_paused = False
        if hasattr(self, "startup_buffer_until"):
            self.startup_buffer_until = 0.0

        # Force the exchange adapter to synchronize OKX time again before fresh
        # candles are accepted. No persistent fault-lock field is written here.
        x = getattr(self, "x", None)
        if x is not None:
            try:
                x.clock_anchor = None
            except Exception:
                pass

        emit = getattr(self, "emit", None)
        if callable(emit):
            emit(
                "log",
                "检测到睡眠或长时间停顿：已停止自动交易，未设置故障锁；"
                "旧行情/旧信号已作废，行情恢复后请重新启动自动交易",
            )
        return False

    engine.Engine.halt = halt
    engine.Engine._kaytrade_v165_sleep_resume_applied = True


apply()

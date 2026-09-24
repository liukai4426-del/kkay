"""V1.6.5 sleep/long-pause soft-stop safety patch.

A Mac sleep or genuine scheduler pause is not itself an exchange/account integrity
fault. Stop new automatic entries WITHOUT writing a persistent fault lock.

Important runtime fix: do not infer sleep from the duration between cycle STARTS.
A slow OKX read can make one cycle take >60s and used to trigger a false sleep on
the next cycle. We now measure the idle gap since the previous cycle COMPLETED.
Network-call duration therefore cannot masquerade as Mac sleep.
"""
from __future__ import annotations

import time

from v165_runtime_network_patch import apply as apply_previous
apply_previous()

import engine

_SLEEP_TOKEN = "检测到睡眠或长时间停顿"
_SLEEP_IDLE_SECONDS = 60.0


def apply():
    if getattr(engine.Engine, "_kaytrade_v165_sleep_resume_applied", False):
        return

    previous_halt = engine.Engine.halt
    previous_cycle = engine.Engine.cycle
    previous_arm = engine.Engine.arm

    def halt(self, reason):
        message = str(reason or "")
        if _SLEEP_TOKEN not in message:
            return previous_halt(self, reason)

        # Sleep / scheduler pause is a soft stop, not an integrity fault.
        self.enabled = False
        self.stopped = True
        self.market = None
        self.market_at = 0
        self.poll_at = 0
        self.candle_lag_count = 0
        self.candle_paused = False
        if hasattr(self, "startup_buffer_until"):
            self.startup_buffer_until = 0.0

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

    def arm(self, settings):
        result = previous_arm(self, settings)
        # A fresh user authorization defines a new heartbeat baseline.
        self._v165_last_cycle_end_wall = time.time()
        self._v165_last_cycle_end_mono = time.monotonic()
        return result

    def cycle(self):
        now_wall = time.time()
        now_mono = time.monotonic()
        last_end_wall = float(getattr(self, "_v165_last_cycle_end_wall", 0.0) or 0.0)

        # Only a period where NO cycle has completed for >60s is considered a
        # sleep/long pause. A single slow cycle does not count because its end
        # timestamp is written in finally below immediately before the next cycle.
        if self.enabled and last_end_wall and now_wall - last_end_wall > _SLEEP_IDLE_SECONDS:
            self.halt("检测到睡眠或长时间停顿，必须人工重新检查后启动")

        # Neutralize the legacy start-to-start 60s detector underneath. Its
        # semantics are unsafe with synchronous HTTP because request time was
        # incorrectly counted as sleep time.
        self.poll_at = now_mono
        try:
            return previous_cycle(self)
        finally:
            self._v165_last_cycle_end_wall = time.time()
            self._v165_last_cycle_end_mono = time.monotonic()

    engine.Engine.halt = halt
    engine.Engine.arm = arm
    engine.Engine.cycle = cycle
    engine.Engine._kaytrade_v165_sleep_resume_applied = True


apply()

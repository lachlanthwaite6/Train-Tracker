from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

BRISBANE = ZoneInfo("Australia/Brisbane")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def __init__(self, timezone: ZoneInfo = BRISBANE) -> None:
        self.timezone = timezone

    def now(self) -> datetime:
        return datetime.now(self.timezone)


class SimulatedClock:
    def __init__(
        self,
        start: datetime,
        *,
        speed: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if start.tzinfo is None:
            raise ValueError("SimulatedClock start must be timezone-aware")
        if speed <= 0:
            raise ValueError("Clock speed must be positive")
        self._initial = start
        self._current = start
        self._speed = speed
        self._monotonic = monotonic
        self._last_tick = float(monotonic())
        self._running = False

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def running(self) -> bool:
        return self._running

    def _capture(self) -> None:
        tick = float(self._monotonic())
        if self._running:
            elapsed = max(0.0, tick - self._last_tick)
            self._current += timedelta(seconds=elapsed * self._speed)
        self._last_tick = tick

    def now(self) -> datetime:
        self._capture()
        return self._current

    def start(self) -> None:
        self._last_tick = float(self._monotonic())
        self._running = True

    def pause(self) -> None:
        self._capture()
        self._running = False

    def resume(self) -> None:
        self.start()

    def restart(self) -> None:
        self._current = self._initial
        self._last_tick = float(self._monotonic())
        self._running = True

    def step(self, seconds: float = 30.0) -> None:
        self._capture()
        self._current += timedelta(seconds=seconds)

    def seek(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("seek target must be timezone-aware")
        self._current = value
        self._last_tick = float(self._monotonic())

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("Clock speed must be positive")
        self._capture()
        self._speed = speed

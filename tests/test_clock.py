from __future__ import annotations

from datetime import datetime

from train_tracker.clock import BRISBANE, SimulatedClock


class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_simulated_clock_pause_speed_step_and_restart() -> None:
    monotonic = FakeMonotonic()
    start = datetime(2026, 8, 18, 23, 59, 30, tzinfo=BRISBANE)
    clock = SimulatedClock(start, monotonic=monotonic)
    clock.start()
    monotonic.value = 40
    assert clock.now().day == 19
    assert clock.now().minute == 0
    clock.pause()
    paused = clock.now()
    monotonic.value = 100
    assert clock.now() == paused
    clock.set_speed(60)
    clock.resume()
    monotonic.value = 101
    assert (clock.now() - paused).total_seconds() == 60
    clock.step(30)
    assert (clock.now() - paused).total_seconds() == 90
    clock.restart()
    assert clock.now() == start

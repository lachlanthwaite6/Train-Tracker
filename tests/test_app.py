from __future__ import annotations

from datetime import datetime

from train_tracker.app import build_clock
from train_tracker.clock import BRISBANE
from train_tracker.config import AppConfig


def test_live_clock_starts_at_current_brisbane_time() -> None:
    configured = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    current = datetime(2026, 8, 18, 22, 19, tzinfo=BRISBANE)
    config = AppConfig(start_time=configured, speed=60)

    clock = build_clock(config, mode="live", current_time=current)

    assert clock.now() == current
    assert clock.speed == 1.0


def test_simulation_clock_keeps_repeatable_configured_time() -> None:
    configured = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    current = datetime(2026, 8, 18, 22, 19, tzinfo=BRISBANE)
    config = AppConfig(start_time=configured, speed=10)

    clock = build_clock(config, mode="simulated", current_time=current)

    assert clock.now() == configured
    assert clock.speed == 10

from __future__ import annotations

from datetime import datetime, timedelta

from train_tracker.models import FeedHealth
from train_tracker.providers.simulated import Scenario, SimulatedProvider


def test_simulated_provider_is_deterministic(now: datetime) -> None:
    provider = SimulatedProvider()
    first = provider.refresh("SIM-CEN", now)
    second = provider.refresh("SIM-CEN", now)
    assert first == second
    assert len(first.departures) >= 4
    assert all(item.scheduled_time.tzinfo is not None for item in first.departures)


def test_simulated_countdown_does_not_reset_on_refresh(now: datetime) -> None:
    provider = SimulatedProvider()
    first = provider.refresh("SIM-CEN", now)
    later = provider.refresh("SIM-CEN", now + timedelta(seconds=45))
    assert later.departures[0].scheduled_time == first.departures[0].scheduled_time


def test_simulated_failure_and_alert_scenarios(now: datetime) -> None:
    provider = SimulatedProvider(Scenario.DISCONNECTED)
    offline = provider.refresh("SIM-CEN", now)
    assert offline.status.health == FeedHealth.OFFLINE
    assert offline.status.using_fallback
    assert all(item.predicted_time is None for item in offline.departures)

    provider.set_scenario(Scenario.ALERT)
    alert = provider.refresh("SIM-CEN", now)
    assert alert.alerts

    provider.set_scenario(Scenario.CANCELLATIONS)
    cancelled = provider.refresh("SIM-CEN", now)
    assert cancelled.departures[0].cancelled


def test_empty_scenario(now: datetime) -> None:
    snapshot = SimulatedProvider(Scenario.EMPTY).refresh("SIM-CEN", now)
    assert snapshot.departures == ()

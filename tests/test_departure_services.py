from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from train_tracker.models import RealtimeState
from train_tracker.providers.simulated import SimulatedProvider
from train_tracker.services.departures import merge_realtime, upcoming_departures


def test_departures_are_ordered_and_filtered(now: datetime) -> None:
    departures = list(SimulatedProvider().refresh("SIM-CEN", now).departures)
    late = replace(
        departures[0], id="late", scheduled_time=now + timedelta(hours=5), predicted_time=None
    )
    before = replace(
        departures[0], id="before", scheduled_time=now - timedelta(hours=1), predicted_time=None
    )
    ordered = upcoming_departures(reversed([*departures, late, before]), now, limit=3)
    assert len(ordered) == 3
    assert list(ordered) == sorted(ordered, key=lambda item: item.effective_time)


def test_realtime_merge_delay_and_scheduled_fallback(now: datetime) -> None:
    scheduled = tuple(
        replace(item, predicted_time=None, realtime_state=RealtimeState.SCHEDULED)
        for item in SimulatedProvider().refresh("SIM-CEN", now).departures[:2]
    )
    prediction = scheduled[0].scheduled_time + timedelta(minutes=4)
    merged = merge_realtime(scheduled, {scheduled[0].trip_id: (prediction, False)})
    assert merged[0].delay_seconds == 240
    assert merged[0].realtime_state == RealtimeState.REALTIME
    assert merged[1].realtime_state == RealtimeState.SCHEDULED

    fallback = merge_realtime(scheduled, {scheduled[0].trip_id: (prediction, False)}, stale=True)
    assert all(item.predicted_time is None for item in fallback)
    assert all(item.realtime_state == RealtimeState.STALE for item in fallback)

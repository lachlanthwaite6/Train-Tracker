from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta

from train_tracker.models import Departure, RealtimeState


def upcoming_departures(
    departures: Iterable[Departure],
    now: datetime,
    *,
    horizon: timedelta = timedelta(hours=3),
    limit: int = 8,
) -> tuple[Departure, ...]:
    lower = now - timedelta(minutes=1)
    upper = now + horizon
    eligible = [item for item in departures if lower <= item.effective_time <= upper]
    eligible.sort(key=lambda item: (item.effective_time, item.route_name, item.id))
    return tuple(eligible[:limit])


def merge_realtime(
    scheduled: Iterable[Departure],
    updates: dict[str, tuple[datetime | None, bool]],
    *,
    stale: bool = False,
) -> tuple[Departure, ...]:
    merged: list[Departure] = []
    for item in scheduled:
        update = updates.get(item.trip_id)
        if stale or update is None:
            merged.append(
                replace(
                    item,
                    predicted_time=None,
                    realtime_state=RealtimeState.STALE if stale else RealtimeState.SCHEDULED,
                )
            )
            continue
        prediction, cancelled = update
        merged.append(
            replace(
                item,
                predicted_time=prediction,
                cancelled=cancelled,
                realtime_state=RealtimeState.REALTIME,
            )
        )
    return tuple(merged)


def minutes_until(departure: Departure, now: datetime) -> int:
    seconds = (departure.effective_time - now).total_seconds()
    return max(0, int((seconds + 59) // 60))

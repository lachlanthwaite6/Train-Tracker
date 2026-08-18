from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from train_tracker.models import (
    Departure,
    FeedHealth,
    FeedStatus,
    ProviderSnapshot,
    RealtimeState,
    ServiceAlert,
    Stop,
)


@dataclass(frozen=True, slots=True)
class ReplayFrame:
    offset_seconds: float
    departures: tuple[dict[str, Any], ...]
    alerts: tuple[dict[str, Any], ...] = ()
    health: FeedHealth = FeedHealth.HEALTHY
    message: str = "Replay"


class ReplayProvider:
    def __init__(self, path: str | Path, *, loop: bool = False) -> None:
        self.path = Path(path)
        self.loop = loop
        self.station, self.frames = self._load(self.path)
        self._anchor: datetime | None = None
        self._seek_seconds = 0.0

    @property
    def mode(self) -> str:
        return "replay"

    @staticmethod
    def _load(path: Path) -> tuple[Stop, tuple[ReplayFrame, ...]]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        station_raw = raw["station"]
        station = Stop(id=str(station_raw["id"]), name=str(station_raw["name"]))
        frames = tuple(
            ReplayFrame(
                offset_seconds=float(frame["offset_seconds"]),
                departures=tuple(frame.get("departures", ())),
                alerts=tuple(frame.get("alerts", ())),
                health=FeedHealth(frame.get("health", "healthy")),
                message=str(frame.get("message", "Replay")),
            )
            for frame in raw["frames"]
        )
        if not frames:
            raise ValueError("Replay must contain at least one frame")
        if any(
            b.offset_seconds < a.offset_seconds for a, b in zip(frames, frames[1:], strict=False)
        ):
            raise ValueError("Replay frames must be sorted by offset_seconds")
        return station, frames

    def list_stops(self) -> tuple[Stop, ...]:
        return (self.station,)

    def restart(self, now: datetime) -> None:
        self._anchor = now
        self._seek_seconds = 0.0

    def seek(self, seconds: float, now: datetime) -> None:
        self._anchor = now
        self._seek_seconds = max(0.0, seconds)

    def _position(self, now: datetime) -> float:
        if self._anchor is None:
            self._anchor = now
        position = self._seek_seconds + (now - self._anchor).total_seconds()
        duration = self.frames[-1].offset_seconds
        if self.loop and duration > 0:
            return position % duration
        return max(0.0, min(position, duration))

    def refresh(self, station_id: str, now: datetime) -> ProviderSnapshot:
        position = self._position(now)
        frame = self.frames[0]
        for candidate in self.frames:
            if candidate.offset_seconds <= position:
                frame = candidate
            else:
                break
        # A departure offset is relative to the instant its frame becomes
        # active, not to every refresh, so countdowns decrease continuously.
        replay_start = now - timedelta(seconds=position)
        frame_time = replay_start + timedelta(seconds=frame.offset_seconds)
        departures = tuple(
            self._departure(item, frame_time, index) for index, item in enumerate(frame.departures)
        )
        alerts = tuple(
            ServiceAlert(
                id=str(item.get("id", f"replay-alert-{index}")),
                header=str(item["header"]),
                description=str(item.get("description", "")),
            )
            for index, item in enumerate(frame.alerts)
        )
        return ProviderSnapshot(
            station=self.station,
            departures=departures,
            alerts=alerts,
            status=FeedStatus(
                provider=self.mode,
                health=frame.health,
                fetched_at=now,
                data_timestamp=now
                if frame.health == FeedHealth.HEALTHY
                else now - timedelta(minutes=5),
                message=frame.message,
                using_fallback=frame.health != FeedHealth.HEALTHY,
            ),
        )

    def _departure(self, item: dict[str, Any], frame_time: datetime, index: int) -> Departure:
        scheduled = frame_time + timedelta(seconds=float(item["scheduled_in_seconds"]))
        delay = int(item.get("delay_seconds", 0))
        state = RealtimeState(item.get("realtime_state", "realtime"))
        predicted = (
            None if state != RealtimeState.REALTIME else scheduled + timedelta(seconds=delay)
        )
        return Departure(
            id=str(item.get("id", f"replay-{index}")),
            trip_id=str(item.get("trip_id", f"replay-trip-{index}")),
            stop_id=self.station.id,
            route_id=str(item["route"]),
            route_name=str(item["route"]),
            destination=str(item["destination"]),
            scheduled_time=scheduled,
            predicted_time=predicted,
            platform=str(item["platform"]) if item.get("platform") is not None else None,
            cancelled=bool(item.get("cancelled", False)),
            realtime_state=state,
            provider=self.mode,
        )

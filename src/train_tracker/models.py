from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RealtimeState(StrEnum):
    REALTIME = "realtime"
    SCHEDULED = "scheduled"
    STALE = "stale"


class FeedHealth(StrEnum):
    HEALTHY = "healthy"
    STALE = "stale"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Stop:
    id: str
    name: str
    latitude: float | None = None
    longitude: float | None = None
    parent_station_id: str | None = None
    wheelchair_boarding: bool | None = None


@dataclass(frozen=True, slots=True)
class Route:
    id: str
    short_name: str
    long_name: str = ""
    route_type: int | None = None
    color: str | None = None
    text_color: str | None = None


@dataclass(frozen=True, slots=True)
class Trip:
    id: str
    route_id: str
    service_id: str
    headsign: str
    direction_id: int | None = None
    wheelchair_accessible: bool | None = None


@dataclass(frozen=True, slots=True)
class Departure:
    id: str
    trip_id: str
    stop_id: str
    route_id: str
    route_name: str
    destination: str
    scheduled_time: datetime
    predicted_time: datetime | None = None
    platform: str | None = None
    cancelled: bool = False
    realtime_state: RealtimeState = RealtimeState.SCHEDULED
    provider: str = "unknown"
    wheelchair_accessible: bool | None = None
    stop_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.scheduled_time.tzinfo is None:
            raise ValueError("scheduled_time must be timezone-aware")
        if self.predicted_time is not None and self.predicted_time.tzinfo is None:
            raise ValueError("predicted_time must be timezone-aware")

    @property
    def effective_time(self) -> datetime:
        return self.predicted_time or self.scheduled_time

    @property
    def delay_seconds(self) -> int:
        if self.predicted_time is None:
            return 0
        return round((self.predicted_time - self.scheduled_time).total_seconds())


@dataclass(frozen=True, slots=True)
class VehiclePosition:
    id: str
    trip_id: str | None
    latitude: float
    longitude: float
    timestamp: datetime | None = None
    bearing: float | None = None
    speed_mps: float | None = None


@dataclass(frozen=True, slots=True)
class TripDelay:
    trip_id: str
    delay_seconds: int = 0
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ServiceAlert:
    id: str
    header: str
    description: str = ""
    severity: str = "warning"
    route_ids: tuple[str, ...] = ()
    stop_ids: tuple[str, ...] = ()
    starts_at: datetime | None = None
    ends_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FeedStatus:
    provider: str
    health: FeedHealth
    fetched_at: datetime | None
    data_timestamp: datetime | None
    message: str = ""
    using_fallback: bool = False

    def age_seconds(self, now: datetime) -> float | None:
        if self.data_timestamp is None:
            return None
        return max(0.0, (now - self.data_timestamp).total_seconds())


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    station: Stop
    departures: tuple[Departure, ...] = ()
    vehicles: tuple[VehiclePosition, ...] = ()
    alerts: tuple[ServiceAlert, ...] = ()
    status: FeedStatus = field(
        default_factory=lambda: FeedStatus(
            provider="unknown",
            health=FeedHealth.ERROR,
            fetched_at=None,
            data_timestamp=None,
            message="No data",
        )
    )
    trip_delays: tuple[TripDelay, ...] = ()


def datetime_to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def departure_to_dict(value: Departure) -> dict[str, Any]:
    return {
        "id": value.id,
        "trip_id": value.trip_id,
        "stop_id": value.stop_id,
        "route_id": value.route_id,
        "route_name": value.route_name,
        "destination": value.destination,
        "scheduled_time": datetime_to_iso(value.scheduled_time),
        "predicted_time": datetime_to_iso(value.predicted_time),
        "platform": value.platform,
        "cancelled": value.cancelled,
        "realtime_state": value.realtime_state.value,
        "provider": value.provider,
        "wheelchair_accessible": value.wheelchair_accessible,
        "stop_sequence": value.stop_sequence,
    }

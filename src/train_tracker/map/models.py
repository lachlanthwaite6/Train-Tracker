from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class GeoPoint:
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class RailStation:
    id: str
    name: str
    position: Point
    latitude: float
    longitude: float
    route_ids: tuple[str, ...] = ()
    interchange: bool = False


@dataclass(frozen=True, slots=True)
class RailShape:
    id: str
    points: tuple[Point, ...]
    distances: tuple[float | None, ...] = ()


@dataclass(frozen=True, slots=True)
class RailRoute:
    id: str
    name: str
    color: str
    text_color: str
    route_type: int
    shape_ids: tuple[str, ...]
    station_ids: tuple[str, ...]
    source_route_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StopTimeEvent:
    station_id: str
    station_name: str
    arrival_seconds: int
    departure_seconds: int
    sequence: int
    shape_distance: float | None = None


@dataclass(frozen=True, slots=True)
class RailTrip:
    id: str
    route_id: str
    service_id: str
    service_date: date
    destination: str
    direction_id: int | None
    shape_id: str | None
    stops: tuple[StopTimeEvent, ...]
    source_route_id: str | None = None


@dataclass(frozen=True, slots=True)
class RailNetwork:
    routes: Mapping[str, RailRoute]
    stations: Mapping[str, RailStation]
    shapes: Mapping[str, RailShape]
    bounds: tuple[float, float, float, float]
    projection_origin: GeoPoint
    label_positions: Mapping[str, Point] = field(default_factory=lambda: MappingProxyType({}))
    reserved_info_bounds: tuple[int, int, int, int] | None = None

    @staticmethod
    def frozen_mapping(values: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType(dict(values))


class TrainPositionSource(StrEnum):
    LIVE_GPS = "live_gps"
    SCHEDULED_ESTIMATE = "scheduled_estimate"


class MapScope(StrEnum):
    SYSTEM = "system"
    FOCUSED = "focused"
    CBD = "cbd"
    FULL = "full"


def normalize_map_scope(value: str | MapScope) -> MapScope:
    """Accept the public scope names while preserving legacy configuration values."""
    if isinstance(value, MapScope):
        return value
    aliases = {
        "route": MapScope.FOCUSED,
        "route-focus": MapScope.FOCUSED,
        "network": MapScope.SYSTEM,
    }
    return aliases[value] if value in aliases else MapScope(value)


@dataclass(frozen=True, slots=True)
class TrainMarker:
    id: str
    trip_id: str
    vehicle_id: str | None
    route_id: str
    route_name: str
    destination: str
    position: Point
    source: TrainPositionSource
    previous_station: str | None
    next_station: str | None
    scheduled_arrival: datetime | None
    predicted_arrival: datetime | None = None
    delay_seconds: int = 0
    age_seconds: float | None = None
    stale: bool = False
    raw_latitude: float | None = None
    raw_longitude: float | None = None
    cluster_count: int = 1


@dataclass(frozen=True, slots=True)
class UpcomingCall:
    route_name: str
    destination: str
    departure: datetime


@dataclass(frozen=True, slots=True)
class MapScene:
    network: RailNetwork
    markers: tuple[TrainMarker, ...]
    now: datetime
    status: str
    freshness: str
    upcoming: Mapping[str, tuple[UpcomingCall, ...]] = field(default_factory=dict)
    trips: tuple[RailTrip, ...] = ()
    delays: Mapping[str, int] = field(default_factory=dict)
    scope: MapScope = MapScope.FULL


@dataclass(frozen=True, slots=True)
class HitTarget:
    kind: str
    id: str
    distance: float

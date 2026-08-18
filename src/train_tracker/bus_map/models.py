from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from train_tracker.map.models import RailNetwork, RailTrip, TrainMarker, UpcomingCall


class BusDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class BusRouteChoice:
    id: str
    short_name: str
    long_name: str
    color: str

    @property
    def label(self) -> str:
        return f"{self.short_name} — {self.long_name}" if self.long_name else self.short_name


@dataclass(frozen=True, slots=True)
class BusMarker:
    marker: TrainMarker
    direction_id: int
    dwelling: bool = False

    @property
    def id(self) -> str:
        return self.marker.id


@dataclass(frozen=True, slots=True)
class BusMapScene:
    network: RailNetwork
    route: BusRouteChoice
    markers: tuple[BusMarker, ...]
    trips: tuple[RailTrip, ...]
    now: datetime
    status: str
    freshness: str
    direction: BusDirection
    major_stop_ids: frozenset[str] = frozenset()
    upcoming: Mapping[str, tuple[UpcomingCall, ...]] = field(default_factory=dict)
    delays: Mapping[str, int] = field(default_factory=dict)

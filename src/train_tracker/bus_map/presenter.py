from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime
from types import MappingProxyType

from train_tracker.bus_map.layout import build_bus_schematic, demo_bus_data
from train_tracker.bus_map.models import (
    BusDirection,
    BusMapScene,
    BusMarker,
    BusRouteChoice,
)
from train_tracker.bus_map.repository import BusNetworkRepository
from train_tracker.map.cbd import project_live_marker_to_cbd, resolve_marker_overlaps
from train_tracker.map.geometry import distance
from train_tracker.map.interpolation import scheduled_marker, scheduled_markers, trip_datetime
from train_tracker.map.models import RailNetwork, RailTrip, TrainMarker, UpcomingCall
from train_tracker.map.vehicle_tracker import VehicleTracker
from train_tracker.models import FeedHealth, ProviderSnapshot


class BusMapPresenter:
    """Prepares one selected bus route without coupling map logic to Tkinter."""

    def __init__(
        self,
        repository: BusNetworkRepository | None,
        *,
        route: str = "M1",
        direction: str | BusDirection = BusDirection.BOTH,
        map_width: int = 90,
        route_coverage: float = 0.70,
        lane_spacing: float = 4.0,
        station_spacing: float = 12.0,
        layout_file: str = "",
        stale_seconds: float = 90,
        expiry_seconds: float = 300,
    ) -> None:
        self.repository = repository
        self.requested_route = route
        self.direction = BusDirection(direction)
        self.map_width = map_width
        self.route_coverage = route_coverage
        self.lane_spacing = lane_spacing
        self.station_spacing = station_spacing
        self.layout_file = layout_file
        self.tracker = VehicleTracker(stale_seconds=stale_seconds, expiry_seconds=expiry_seconds)
        self._cache_key: tuple[object, ...] | None = None
        self._schematic: tuple[RailNetwork, tuple[RailTrip, ...], frozenset[str]] | None = None
        self._demo_date: date | None = None
        self._demo: tuple[RailNetwork, tuple[RailTrip, ...], BusRouteChoice] | None = None

    def route_choices(self) -> tuple[BusRouteChoice, ...]:
        if self.repository is None:
            return (BusRouteChoice("DEMO-M1", "M1", "Demo Metro", "#E463A4"),)
        return self.repository.list_routes()

    def set_route(self, route: str) -> None:
        if route != self.requested_route:
            self.requested_route = route
            self._cache_key = None

    def set_direction(self, direction: str | BusDirection) -> None:
        value = BusDirection(direction)
        if value != self.direction:
            self.direction = value
            self._cache_key = None

    def _data(self, now: datetime) -> tuple[RailNetwork, tuple[RailTrip, ...], BusRouteChoice]:
        if self.repository is None:
            if self._demo is None or self._demo_date != now.date():
                self._demo = demo_bus_data(now.date())
                self._demo_date = now.date()
            return self._demo
        route = self.repository.resolve_route(self.requested_route)
        transit = self.repository.transit_repository(route.id)
        return transit.build_network(), transit.active_trips(now), route

    def prepare(self, now: datetime, snapshot: ProviderSnapshot | None = None) -> BusMapScene:
        geographic, all_trips, route = self._data(now)
        trip_delays = snapshot.trip_delays if snapshot is not None else ()
        cancelled = {item.trip_id for item in trip_delays if item.cancelled}
        delays = MappingProxyType(
            {item.trip_id: item.delay_seconds for item in trip_delays if not item.cancelled}
        )
        all_trips = tuple(trip for trip in all_trips if trip.id not in cancelled)
        cache_key = (
            route.id,
            now.date(),
            self.direction,
            self.map_width,
            self.route_coverage,
            self.lane_spacing,
            self.station_spacing,
            self.layout_file,
            tuple(sorted(cancelled)),
        )
        if self._schematic is None or cache_key != self._cache_key:
            self._schematic = build_bus_schematic(
                geographic,
                all_trips,
                route,
                direction=self.direction,
                map_width=self.map_width,
                route_coverage=self.route_coverage,
                lane_spacing=self.lane_spacing,
                station_spacing=self.station_spacing,
                layout_file=self.layout_file,
            )
            self._cache_key = cache_key
        network, trips, major_stop_ids = self._schematic
        full_by_trip = {trip.id: trip for trip in all_trips}
        focused_by_trip = {trip.id: trip for trip in trips}
        vehicles = snapshot.vehicles if snapshot is not None else ()
        full_live = self.tracker.markers(geographic, all_trips, vehicles, now)
        live_values: list[TrainMarker] = []
        for marker in full_live:
            full_trip = full_by_trip.get(marker.trip_id)
            focused_trip = focused_by_trip.get(marker.trip_id)
            if full_trip is None or focused_trip is None:
                continue
            position = project_live_marker_to_cbd(
                marker, geographic, full_trip, network, focused_trip
            )
            proxy = scheduled_marker(network, focused_trip, now, delays.get(marker.trip_id, 0))
            if position is None and proxy is None:
                continue
            if position is not None:
                schematic_position = position
            elif proxy is not None:
                schematic_position = proxy.position
            else:
                continue
            live_values.append(
                replace(
                    marker,
                    position=schematic_position,
                    previous_station=proxy.previous_station if proxy else marker.previous_station,
                    next_station=proxy.next_station if proxy else marker.next_station,
                    scheduled_arrival=(
                        proxy.scheduled_arrival if proxy else marker.scheduled_arrival
                    ),
                    predicted_arrival=(
                        proxy.predicted_arrival if proxy else marker.predicted_arrival
                    ),
                    delay_seconds=delays.get(marker.trip_id, 0),
                )
            )
        live_trip_ids = {marker.trip_id for marker in live_values}
        estimates = tuple(
            marker
            for marker in scheduled_markers(network, trips, now, delays)
            if marker.trip_id not in live_trip_ids
        )
        resolved = resolve_marker_overlaps(
            tuple((*live_values, *estimates)), self.lane_spacing * 0.75
        )
        trips_by_id = {trip.id: trip for trip in trips}
        bus_markers = tuple(
            BusMarker(
                marker,
                int(trips_by_id[marker.trip_id].direction_id or 0),
                self._is_dwelling(marker, network),
            )
            for marker in resolved
            if marker.trip_id in trips_by_id
        )
        if live_values and estimates:
            status = "Mixed"
        elif live_values:
            status = "Live"
        elif estimates:
            status = "Estimated"
        else:
            status = "No service"
        if snapshot is not None and snapshot.status.health in {
            FeedHealth.STALE,
            FeedHealth.OFFLINE,
        }:
            status = snapshot.status.health.value.title()
        upcoming_values: dict[str, list[UpcomingCall]] = defaultdict(list)
        for trip in trips:
            for event in trip.stops:
                departure = trip_datetime(trip, event.departure_seconds, now.tzinfo)
                if departure >= now and len(upcoming_values[event.station_id]) < 3:
                    upcoming_values[event.station_id].append(
                        UpcomingCall(route.short_name, trip.destination, departure)
                    )
        upcoming = MappingProxyType(
            {
                station_id: tuple(sorted(values, key=lambda item: item.departure)[:3])
                for station_id, values in upcoming_values.items()
            }
        )
        freshness = snapshot.status.message if snapshot else "Offline scheduled simulation"
        return BusMapScene(
            network,
            route,
            bus_markers,
            trips,
            now,
            status,
            freshness,
            self.direction,
            major_stop_ids,
            upcoming,
            delays,
        )

    @staticmethod
    def _is_dwelling(marker: TrainMarker, network: RailNetwork) -> bool:
        return any(
            distance(marker.position, station.position) <= 1.5
            for station in network.stations.values()
        )

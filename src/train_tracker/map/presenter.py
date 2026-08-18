from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import MappingProxyType

from train_tracker.map.cbd import (
    CbdFocus,
    build_cbd_focus,
    project_live_marker_to_cbd,
    resolve_marker_overlaps,
)
from train_tracker.map.focused import build_route_focus
from train_tracker.map.interpolation import scheduled_marker, scheduled_markers, trip_datetime
from train_tracker.map.layout import apply_layout_override, demo_network_and_trips
from train_tracker.map.models import (
    MapScene,
    MapScope,
    RailNetwork,
    RailRoute,
    RailTrip,
    UpcomingCall,
    normalize_map_scope,
)
from train_tracker.map.network_repository import RailNetworkRepository
from train_tracker.map.system import build_system_view, resolve_system_marker_overlaps
from train_tracker.map.vehicle_tracker import VehicleTracker
from train_tracker.models import FeedHealth, ProviderSnapshot


class MapPresenter:
    """Prepares immutable map state away from Tkinter's event thread."""

    def __init__(
        self,
        repository: RailNetworkRepository | None,
        *,
        mode: str,
        layout_file: str = "",
        stale_seconds: float = 90,
        expiry_seconds: float = 300,
        scope: str | MapScope = MapScope.SYSTEM,
        cbd_station_names: tuple[str, ...] = (),
        cbd_station_spacing: float = 70.0,
        cbd_track_spacing: float = 8.0,
        focused_routes: tuple[str, ...] = (),
        rail_direction: str = "both",
        rail_map_width: int = 90,
        focused_route_coverage: float = 0.70,
        focused_track_spacing: float = 4.0,
        focused_station_spacing: float = 8.0,
        system_screen_coverage: float = 0.82,
        route_lane_spacing: float = 2.0,
        train_collision_spacing: float = 2.0,
        default_station_id: str = "place_twgsta",
    ) -> None:
        self.repository = repository
        self.mode = mode
        self.layout_file = layout_file
        self.tracker = VehicleTracker(stale_seconds=stale_seconds, expiry_seconds=expiry_seconds)
        self.scope = normalize_map_scope(scope)
        self.cbd_station_names = cbd_station_names
        self.cbd_station_spacing = cbd_station_spacing
        self.cbd_track_spacing = cbd_track_spacing
        self.focused_routes = focused_routes
        self.rail_direction = rail_direction
        self.rail_map_width = rail_map_width
        self.focused_route_coverage = focused_route_coverage
        self.focused_track_spacing = focused_track_spacing
        self.focused_station_spacing = focused_station_spacing
        self.system_screen_coverage = system_screen_coverage
        self.route_lane_spacing = route_lane_spacing
        self.train_collision_spacing = train_collision_spacing
        self.default_station_id = default_station_id
        self._network: RailNetwork | None = None
        self._demo_date: date | None = None
        self._demo_trips: tuple[RailTrip, ...] = ()
        self._cbd_cache_key: tuple[object, ...] | None = None
        self._cbd_focus: CbdFocus | None = None

    def set_scope(self, scope: str | MapScope) -> None:
        normalized = normalize_map_scope(scope)
        if normalized != self.scope:
            self.scope = normalized
            self._cbd_cache_key = None

    def set_focused_routes(self, routes: tuple[str, ...]) -> None:
        cleaned = tuple(item for item in routes if item)[:2]
        if cleaned != self.focused_routes:
            self.focused_routes = cleaned
            self._cbd_cache_key = None

    def set_rail_direction(self, direction: str) -> None:
        if direction not in {"inbound", "outbound", "both"}:
            raise ValueError("rail direction must be inbound, outbound or both")
        if direction != self.rail_direction:
            self.rail_direction = direction
            self._cbd_cache_key = None

    def route_choices(self, now: datetime) -> tuple[RailRoute, ...]:
        network, _trips = self._data(now)
        return tuple(sorted(network.routes.values(), key=lambda item: (item.name, item.id)))

    def _data(self, now: datetime) -> tuple[RailNetwork, tuple[RailTrip, ...]]:
        if self.repository is not None:
            if self._network is None:
                self._network = apply_layout_override(
                    self.repository.build_network(), self.layout_file
                )
            return self._network, self.repository.active_trips(now)
        if self._network is None or self._demo_date != now.date():
            network, trips = demo_network_and_trips(now.date())
            self._network = apply_layout_override(network, self.layout_file)
            self._demo_trips = trips
            self._demo_date = now.date()
        return self._network, self._demo_trips

    def prepare(self, now: datetime, snapshot: ProviderSnapshot | None = None) -> MapScene:
        full_network, full_trips = self._data(now)
        vehicles = snapshot.vehicles if snapshot is not None else ()
        trip_delays = snapshot.trip_delays if snapshot is not None else ()
        cancelled = {item.trip_id for item in trip_delays if item.cancelled}
        delays = MappingProxyType(
            {item.trip_id: item.delay_seconds for item in trip_delays if not item.cancelled}
        )
        full_trips = tuple(trip for trip in full_trips if trip.id not in cancelled)
        full_live = self.tracker.markers(full_network, full_trips, vehicles, now)
        full_live = tuple(
            replace(
                marker,
                delay_seconds=delays.get(marker.trip_id, 0),
                predicted_arrival=(
                    marker.scheduled_arrival + timedelta(seconds=delays.get(marker.trip_id, 0))
                    if marker.scheduled_arrival is not None
                    else None
                ),
            )
            for marker in full_live
        )
        if self.scope in {MapScope.SYSTEM, MapScope.FOCUSED, MapScope.CBD}:
            cache_key = (
                id(full_network),
                now.date(),
                self.scope,
                self.cbd_station_names,
                self.cbd_station_spacing,
                self.cbd_track_spacing,
                self.focused_routes,
                self.rail_direction,
                self.rail_map_width,
                self.focused_route_coverage,
                self.focused_track_spacing,
                self.focused_station_spacing,
                self.system_screen_coverage,
                self.route_lane_spacing,
                self.default_station_id,
                self.layout_file,
                tuple(sorted(cancelled)),
            )
            if self._cbd_focus is None or cache_key != self._cbd_cache_key:
                if self.scope == MapScope.SYSTEM:
                    self._cbd_focus = build_system_view(
                        full_network,
                        full_trips,
                        screen_coverage=self.system_screen_coverage,
                        route_lane_spacing=self.route_lane_spacing,
                        default_station_id=self.default_station_id,
                        layout_file=self.layout_file,
                    )
                elif self.scope == MapScope.FOCUSED:
                    self._cbd_focus = build_route_focus(
                        full_network,
                        full_trips,
                        self.focused_routes,
                        direction=self.rail_direction,
                        map_width=self.rail_map_width,
                        route_coverage=self.focused_route_coverage,
                        track_spacing=self.focused_track_spacing,
                        station_spacing=self.focused_station_spacing,
                        layout_file=self.layout_file,
                    )
                else:
                    self._cbd_focus = build_cbd_focus(
                        full_network,
                        full_trips,
                        self.cbd_station_names,
                        station_spacing=self.cbd_station_spacing,
                        track_spacing=self.cbd_track_spacing,
                        layout_file=self.layout_file,
                    )
                self._cbd_cache_key = cache_key
            network, trips = self._cbd_focus.network, self._cbd_focus.trips
            focused_by_trip = {trip.id: trip for trip in trips}
            full_by_trip = {trip.id: trip for trip in full_trips}
            live_values = []
            for marker in full_live:
                focused_trip = focused_by_trip.get(marker.trip_id)
                full_trip = full_by_trip.get(marker.trip_id)
                if focused_trip is None or full_trip is None:
                    continue
                live_position = project_live_marker_to_cbd(
                    marker, full_network, full_trip, network, focused_trip
                )
                proxy = scheduled_marker(network, focused_trip, now, delays.get(marker.trip_id, 0))
                if live_position is not None:
                    schematic_position = live_position
                elif proxy is not None:
                    schematic_position = proxy.position
                else:
                    continue
                live_values.append(
                    replace(
                        marker,
                        position=schematic_position,
                        previous_station=(
                            proxy.previous_station if proxy else marker.previous_station
                        ),
                        next_station=proxy.next_station if proxy else marker.next_station,
                        scheduled_arrival=(
                            proxy.scheduled_arrival if proxy else marker.scheduled_arrival
                        ),
                        predicted_arrival=(
                            proxy.predicted_arrival if proxy else marker.predicted_arrival
                        ),
                    )
                )
            live = tuple(live_values)
        else:
            network, trips = full_network, full_trips
            live = full_live
        live_trip_ids = {marker.trip_id for marker in live}
        estimates = tuple(
            marker
            for marker in scheduled_markers(network, trips, now, delays)
            if marker.trip_id not in live_trip_ids
        )
        markers = tuple((*live, *estimates))
        if self.scope == MapScope.SYSTEM:
            markers = resolve_system_marker_overlaps(
                markers,
                network,
                trips,
                self.train_collision_spacing,
            )
        elif self.scope in {MapScope.FOCUSED, MapScope.CBD}:
            spacing = (
                self.focused_track_spacing
                if self.scope == MapScope.FOCUSED
                else self.cbd_track_spacing
            )
            markers = resolve_marker_overlaps(markers, spacing * 0.7)
        if live and estimates:
            status = "Mixed"
        elif live:
            status = "Live"
        elif estimates:
            status = "Simulated"
        else:
            status = "No service"
        if snapshot is not None and snapshot.status.health in (
            FeedHealth.STALE,
            FeedHealth.OFFLINE,
        ):
            status = snapshot.status.health.value.title()
        freshness = (
            snapshot.status.message if snapshot is not None else "Offline scheduled simulation"
        )
        upcoming_values: dict[str, list[UpcomingCall]] = defaultdict(list)
        for trip in trips:
            route = network.routes[trip.route_id]
            for event in trip.stops:
                departure = trip_datetime(trip, event.departure_seconds, now.tzinfo)
                if departure >= now and len(upcoming_values[event.station_id]) < 5:
                    upcoming_values[event.station_id].append(
                        UpcomingCall(route.name, trip.destination, departure)
                    )
        upcoming = MappingProxyType(
            {
                station_id: tuple(sorted(values, key=lambda item: item.departure)[:5])
                for station_id, values in upcoming_values.items()
            }
        )
        return MapScene(
            network,
            tuple(markers),
            now,
            status,
            freshness,
            upcoming,
            trips,
            delays,
            self.scope,
        )

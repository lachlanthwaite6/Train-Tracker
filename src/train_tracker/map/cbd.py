from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from train_tracker.map.geometry import (
    cumulative_lengths,
    distance,
    offset_polyline,
    point_at_distance,
    snap_to_polyline,
)
from train_tracker.map.models import (
    Point,
    RailNetwork,
    RailRoute,
    RailShape,
    RailStation,
    RailTrip,
    TrainMarker,
)


@dataclass(frozen=True, slots=True)
class CbdFocus:
    network: RailNetwork
    trips: tuple[RailTrip, ...]
    missing_station_names: tuple[str, ...] = ()


@dataclass(slots=True)
class TrainSelection:
    hovered_id: str | None = None
    pinned_id: str | None = None
    automatic_id: str | None = None

    @property
    def active_id(self) -> str | None:
        return self.pinned_id or self.hovered_id or self.automatic_id

    def hover(self, marker_id: str | None) -> None:
        self.hovered_id = marker_id

    def pin(self, marker_id: str) -> None:
        self.pinned_id = marker_id

    def clear_pin(self) -> None:
        self.pinned_id = None

    def select_automatically(self, marker_id: str | None) -> None:
        self.automatic_id = marker_id


_DEFAULT_GRID: dict[str, tuple[float, float]] = {
    "bowen hills": (0.0, 0.0),
    "fortitude valley": (0.0, 1.0),
    "central": (0.0, 2.0),
    "roma street": (0.0, 3.0),
    "milton": (-1.0, 4.0),
    "toowong": (-2.0, 5.0),
    "south brisbane": (1.0, 4.0),
    "south bank": (1.0, 5.0),
}


def normalized_station_name(value: str) -> str:
    name = " ".join(value.casefold().replace("-", " ").split())
    return name.removesuffix(" station").strip()


def _layout_data(path: str | Path | None) -> dict[str, object]:
    if not path:
        return {}
    source = Path(path)
    if not source.exists():
        return {}
    raw = json.loads(source.read_text(encoding="utf-8"))
    value = raw.get("cbd", {})
    return value if isinstance(value, dict) else {}


def _coordinate_override(values: object, station: RailStation) -> tuple[float, float] | None:
    if not isinstance(values, dict):
        return None
    raw = values.get(station.id, values.get(normalized_station_name(station.name)))
    if not isinstance(raw, list) or len(raw) != 2:
        return None
    return float(raw[0]), float(raw[1])


def _resolve_stations(
    network: RailNetwork, names: tuple[str, ...]
) -> tuple[tuple[RailStation, ...], tuple[str, ...]]:
    by_name = {normalized_station_name(item.name): item for item in network.stations.values()}
    selected: list[RailStation] = []
    missing: list[str] = []
    for name in names:
        station = by_name.get(normalized_station_name(name))
        if station is None:
            missing.append(name)
        elif station not in selected:
            selected.append(station)
    return tuple(selected), tuple(missing)


def build_cbd_focus(
    network: RailNetwork,
    trips: tuple[RailTrip, ...],
    station_names: tuple[str, ...],
    *,
    station_spacing: float = 70.0,
    track_spacing: float = 8.0,
    layout_file: str | Path | None = None,
) -> CbdFocus:
    selected, missing = _resolve_stations(network, station_names)
    if len(selected) < 2:
        # Small offline/demo feeds may not carry every configured CBD station.
        # Preserve a useful focused view by selecting stations closest to the
        # configured Toowong/Central pair, then falling back to the first two.
        preferred = {"toowong", "central"}
        fallback = [
            station
            for station in network.stations.values()
            if normalized_station_name(station.name) in preferred
        ]
        if len(fallback) < 2:
            fallback = list(network.stations.values())[: min(8, len(network.stations))]
        selected = tuple(fallback)
    selected_ids = {station.id for station in selected}
    layout = _layout_data(layout_file)
    station_overrides = layout.get("stations")
    label_overrides = layout.get("label_positions")
    interchange_overrides = layout.get("interchanges")
    forced_interchanges = (
        {str(item).casefold() for item in interchange_overrides}
        if isinstance(interchange_overrides, list)
        else set()
    )

    positioned: dict[str, RailStation] = {}
    for index, station in enumerate(selected):
        override = _coordinate_override(station_overrides, station)
        grid = _DEFAULT_GRID.get(normalized_station_name(station.name), (0.0, float(index)))
        x, y = override or (grid[0] * station_spacing, grid[1] * station_spacing)
        positioned[station.id] = replace(station, position=Point(x, y))

    relevant_trips: list[RailTrip] = []
    for trip in trips:
        focused_stops = tuple(event for event in trip.stops if event.station_id in selected_ids)
        if len(focused_stops) >= 2:
            relevant_trips.append(replace(trip, stops=focused_stops))

    route_ids = sorted({trip.route_id for trip in relevant_trips})
    route_offsets = {
        route_id: (index - (len(route_ids) - 1) / 2) * track_spacing
        for index, route_id in enumerate(route_ids)
    }
    override_offsets = layout.get("track_offsets")
    if isinstance(override_offsets, dict):
        for route_id, value in override_offsets.items():
            if route_id in route_offsets:
                route_offsets[route_id] = float(value)

    override_geometry = layout.get("route_geometry")
    shapes: dict[str, RailShape] = {}
    shape_by_signature: dict[tuple[str, int, tuple[str, ...]], str] = {}
    focused_trips: list[RailTrip] = []
    route_shapes: dict[str, list[str]] = defaultdict(list)
    route_stations: dict[str, list[str]] = defaultdict(list)
    for trip in relevant_trips:
        direction = int(trip.direction_id or 0)
        station_ids = tuple(event.station_id for event in trip.stops)
        signature = (trip.route_id, direction, station_ids)
        shape_id = shape_by_signature.get(signature)
        if shape_id is None:
            custom = (
                override_geometry.get(trip.route_id)
                if isinstance(override_geometry, dict)
                else None
            )
            if isinstance(custom, list) and len(custom) >= 2:
                base_points = tuple(Point(float(item[0]), float(item[1])) for item in custom)
            else:
                base_points = tuple(positioned[item].position for item in station_ids)
            direction_offset = (-0.24 if direction == 0 else 0.24) * track_spacing
            shape_points = offset_polyline(
                base_points, route_offsets[trip.route_id] + direction_offset
            )
            shape_id = f"cbd:{trip.route_id}:{direction}:{len(route_shapes[trip.route_id])}"
            shapes[shape_id] = RailShape(shape_id, shape_points)
            shape_by_signature[signature] = shape_id
            route_shapes[trip.route_id].append(shape_id)
        focused_trips.append(replace(trip, shape_id=shape_id))
        for station_id in station_ids:
            if station_id not in route_stations[trip.route_id]:
                route_stations[trip.route_id].append(station_id)

    routes: dict[str, RailRoute] = {}
    for route_id in route_ids:
        source = network.routes[route_id]
        routes[route_id] = replace(
            source,
            shape_ids=tuple(route_shapes[route_id]),
            station_ids=tuple(route_stations[route_id]),
        )

    station_routes: dict[str, list[str]] = defaultdict(list)
    for route_id, route_station_ids in route_stations.items():
        for station_id in route_station_ids:
            station_routes[station_id].append(route_id)
    stations = {
        station_id: replace(
            station,
            route_ids=tuple(sorted(station_routes[station_id])),
            interchange=(
                len(station_routes[station_id]) > 1
                or station_id.casefold() in forced_interchanges
                or normalized_station_name(station.name) in forced_interchanges
            ),
        )
        for station_id, station in positioned.items()
        if station_routes[station_id]
    }
    bounds_points = [station.position for station in stations.values()]
    bounds_points.extend(point for shape in shapes.values() for point in shape.points)
    if not bounds_points:
        raise RuntimeError("CBD focus contains no rail trips for the selected stations and time.")
    bounds = (
        min(point.x for point in bounds_points),
        min(point.y for point in bounds_points),
        max(point.x for point in bounds_points),
        max(point.y for point in bounds_points),
    )
    label_positions: dict[str, Point] = {}
    for station_id, station in stations.items():
        override = _coordinate_override(label_overrides, station)
        if override is not None:
            label_positions[station_id] = Point(*override)
    reserved_raw = layout.get("reserved_info_panel_bounds")
    reserved_bounds = None
    if isinstance(reserved_raw, list) and len(reserved_raw) == 4:
        reserved_bounds = (
            int(reserved_raw[0]),
            int(reserved_raw[1]),
            int(reserved_raw[2]),
            int(reserved_raw[3]),
        )
    focused_network = RailNetwork(
        MappingProxyType(routes),
        MappingProxyType(stations),
        MappingProxyType(shapes),
        bounds,
        network.projection_origin,
        MappingProxyType(label_positions),
        reserved_bounds,
    )
    return CbdFocus(focused_network, tuple(focused_trips), missing)


def resolve_marker_overlaps(
    markers: tuple[TrainMarker, ...], minimum_distance: float
) -> tuple[TrainMarker, ...]:
    """Apply a small stable radial displacement to nearly coincident trains."""
    resolved: list[TrainMarker] = []
    for marker in sorted(markers, key=lambda item: item.id):
        position = marker.position
        collisions = sum(distance(position, item.position) < minimum_distance for item in resolved)
        if collisions:
            angle = (sum(ord(char) for char in marker.id) % 8) * math.pi / 4
            radius = minimum_distance * (0.55 + collisions * 0.25)
            position = Point(
                position.x + math.cos(angle) * radius,
                position.y + math.sin(angle) * radius,
            )
        resolved.append(replace(marker, position=position))
    return tuple(resolved)


def project_live_marker_to_cbd(
    marker: TrainMarker,
    full_network: RailNetwork,
    full_trip: RailTrip,
    focus_network: RailNetwork,
    focus_trip: RailTrip,
) -> Point | None:
    """Translate a snapped GPS position onto its corresponding CBD schematic lane."""
    full_shape = full_network.shapes.get(full_trip.shape_id or "")
    focus_shape = focus_network.shapes.get(focus_trip.shape_id or "")
    if full_shape is None or focus_shape is None or len(focus_trip.stops) < 2:
        return None
    _snapped, _offset, marker_along = snap_to_polyline(marker.position, full_shape.points)
    stop_along = [
        snap_to_polyline(full_network.stations[event.station_id].position, full_shape.points)[2]
        for event in focus_trip.stops
    ]
    for index, (start, end) in enumerate(zip(stop_along, stop_along[1:], strict=False)):
        low, high = sorted((start, end))
        if low - 1e-6 <= marker_along <= high + 1e-6:
            ratio = 0.0 if abs(end - start) < 1e-9 else (marker_along - start) / (end - start)
            ratio = min(1.0, max(0.0, ratio))
            if len(focus_shape.points) == len(focus_trip.stops):
                first, second = focus_shape.points[index], focus_shape.points[index + 1]
                return Point(
                    first.x + (second.x - first.x) * ratio,
                    first.y + (second.y - first.y) * ratio,
                )
            full_span = max(1e-9, abs(stop_along[-1] - stop_along[0]))
            overall = abs(marker_along - stop_along[0]) / full_span
            lengths = cumulative_lengths(focus_shape.points)
            return point_at_distance(focus_shape.points, lengths[-1] * overall)
    return None

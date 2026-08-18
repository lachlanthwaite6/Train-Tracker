from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from train_tracker.bus_map.layout import simplify_polyline
from train_tracker.map.cbd import CbdFocus
from train_tracker.map.geometry import cumulative_lengths, offset_polyline, point_at_distance
from train_tracker.map.models import (
    Point,
    RailNetwork,
    RailRoute,
    RailShape,
    RailStation,
    RailTrip,
)


def resolve_focused_routes(
    network: RailNetwork,
    trips: tuple[RailTrip, ...],
    requested: tuple[str, ...],
    *,
    maximum: int = 2,
) -> tuple[str, ...]:
    """Resolve human-friendly route fragments against GTFS-derived route groups."""
    resolved: list[str] = []
    for raw in requested:
        key = raw.casefold().strip()
        if not key:
            continue
        match = next(
            (
                route.id
                for route in network.routes.values()
                if route.id.casefold() == key
                or key == route.name.casefold()
                or key in route.name.casefold()
            ),
            None,
        )
        if match is not None and match not in resolved:
            resolved.append(match)
    if resolved:
        return tuple(resolved[:maximum])
    active_counts = Counter(trip.route_id for trip in trips)
    ranked = sorted(
        network.routes,
        key=lambda route_id: (-active_counts[route_id], network.routes[route_id].name),
    )
    return tuple(ranked[: min(maximum, len(ranked))])


def _direction_allowed(direction_id: int | None, direction: str) -> bool:
    if direction == "both":
        return True
    return int(direction_id or 0) == (0 if direction == "inbound" else 1)


def _layout_overrides(path: str | Path | None) -> dict[str, object]:
    if not path:
        return {}
    source = Path(path)
    if not source.exists():
        return {}
    raw = json.loads(source.read_text(encoding="utf-8"))
    value = raw.get("rail_routes", {})
    return value if isinstance(value, dict) else {}


def _override_points(values: dict[str, object], route: RailRoute) -> tuple[Point, ...] | None:
    raw = values.get(route.id, values.get(route.name))
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    return tuple(Point(float(item[0]), float(item[1])) for item in raw)


def _schematic_path(
    source: tuple[Point, ...],
    *,
    map_width: int,
    coverage: float,
) -> tuple[Point, ...]:
    simplified = simplify_polyline(source, max(1.0, _extent(source) / 24))
    min_x = min(point.x for point in simplified)
    max_x = max(point.x for point in simplified)
    min_y = min(point.y for point in simplified)
    max_y = max(point.y for point in simplified)
    if max_y - min_y > max_x - min_x:
        simplified = tuple(Point(-point.y, point.x) for point in simplified)
        min_x = min(point.x for point in simplified)
        max_x = max(point.x for point in simplified)
        min_y = min(point.y for point in simplified)
        max_y = max(point.y for point in simplified)
    target_width = min(map_width - 4.0, 128.0 * coverage)
    left = (map_width - target_width) / 2
    x_span = max(1e-9, max_x - min_x)
    y_span = max(1e-9, max_y - min_y)
    points = tuple(
        Point(
            left + (point.x - min_x) / x_span * target_width,
            12.0 + (point.y - min_y) / y_span * 36.0,
        )
        for point in simplified
    )
    if len(points) == 2:
        first, last = points
        return (first, Point((first.x + last.x) / 2, 32), last)
    return points


def _extent(points: tuple[Point, ...]) -> float:
    return max(
        max(point.x for point in points) - min(point.x for point in points),
        max(point.y for point in points) - min(point.y for point in points),
        1.0,
    )


def _displayed_stop_ids(
    representative: RailTrip,
    network: RailNetwork,
    *,
    path_length: float,
    station_spacing: float,
) -> set[str]:
    events = representative.stops
    maximum = max(3, min(10, math.floor(path_length / station_spacing) + 1))
    if len(events) <= maximum:
        return {event.station_id for event in events}
    selected = {
        events[round(index * (len(events) - 1) / (maximum - 1))].station_id
        for index in range(maximum)
    }
    selected.update(
        event.station_id for event in events if network.stations[event.station_id].interchange
    )
    return selected


def build_route_focus(
    network: RailNetwork,
    trips: tuple[RailTrip, ...],
    requested_routes: tuple[str, ...],
    *,
    direction: str = "both",
    map_width: int = 90,
    route_coverage: float = 0.70,
    track_spacing: float = 4.0,
    station_spacing: float = 8.0,
    layout_file: str | Path | None = None,
) -> CbdFocus:
    route_ids = resolve_focused_routes(network, trips, requested_routes)
    selected_trips = tuple(
        trip
        for trip in trips
        if trip.route_id in route_ids and _direction_allowed(trip.direction_id, direction)
    )
    if not selected_trips:
        raise RuntimeError("Focused rail map has no active trips for the selected routes.")
    overrides = _layout_overrides(layout_file)
    route_offsets = {
        route_id: (index - (len(route_ids) - 1) / 2) * track_spacing * 2.0
        for index, route_id in enumerate(route_ids)
    }
    shapes: dict[str, RailShape] = {}
    routes: dict[str, RailRoute] = {}
    focused_trips: list[RailTrip] = []
    station_positions: dict[str, list[Point]] = defaultdict(list)
    station_routes: dict[str, set[str]] = defaultdict(set)
    displayed_ids: set[str] = set()
    major_ids: set[str] = set()

    for route_id in route_ids:
        route_trips = tuple(trip for trip in selected_trips if trip.route_id == route_id)
        if not route_trips:
            continue
        representative = max(route_trips, key=lambda item: len(item.stops))
        source_shape = network.shapes.get(representative.shape_id or "")
        source_points = (
            source_shape.points
            if source_shape is not None
            else tuple(
                network.stations[event.station_id].position for event in representative.stops
            )
        )
        base = _override_points(overrides, network.routes[route_id]) or _schematic_path(
            source_points, map_width=map_width, coverage=route_coverage
        )
        route_center = offset_polyline(base, route_offsets[route_id])
        path_lengths = cumulative_lengths(route_center)
        path_length = path_lengths[-1]
        route_displayed = _displayed_stop_ids(
            representative,
            network,
            path_length=path_length,
            station_spacing=station_spacing,
        )
        displayed_ids.update(route_displayed)
        ordered = [event.station_id for event in representative.stops]
        for index, station_id in enumerate(ordered):
            if station_id not in route_displayed:
                continue
            progress = index / max(1, len(ordered) - 1)
            station_positions[station_id].append(
                point_at_distance(route_center, path_length * progress)
            )
            station_routes[station_id].add(route_id)
        visible_order = [item for item in ordered if item in route_displayed]
        major_ids.update(
            (
                visible_order[0],
                visible_order[len(visible_order) // 2],
                visible_order[-1],
            )
        )

        route_shape_ids: list[str] = []
        for direction_id in sorted({int(trip.direction_id or 0) for trip in route_trips}):
            lane = (-0.5 if direction_id == 0 else 0.5) * track_spacing
            shape_id = f"focused:{route_id}:{direction_id}"
            shapes[shape_id] = RailShape(shape_id, offset_polyline(route_center, lane))
            route_shape_ids.append(shape_id)
        for trip in route_trips:
            events = tuple(event for event in trip.stops if event.station_id in route_displayed)
            if len(events) < 2:
                continue
            shape_id = f"focused:{route_id}:{int(trip.direction_id or 0)}"
            focused_trips.append(replace(trip, shape_id=shape_id, stops=events))
        routes[route_id] = replace(
            network.routes[route_id],
            shape_ids=tuple(route_shape_ids),
            station_ids=tuple(visible_order),
        )

    stations: dict[str, RailStation] = {}
    for station_id in displayed_ids:
        points = station_positions[station_id]
        if not points:
            continue
        source = network.stations[station_id]
        position = Point(
            sum(point.x for point in points) / len(points),
            sum(point.y for point in points) / len(points),
        )
        serving = tuple(sorted(station_routes[station_id]))
        stations[station_id] = replace(
            source,
            position=position,
            route_ids=serving,
            interchange=station_id in major_ids,
        )
    if not shapes or not stations:
        raise RuntimeError("Focused rail map could not build route geometry.")
    points = [point for shape in shapes.values() for point in shape.points]
    bounds = (
        min(point.x for point in points),
        min(point.y for point in points),
        max(point.x for point in points),
        max(point.y for point in points),
    )
    focused_network = RailNetwork(
        MappingProxyType(routes),
        MappingProxyType(stations),
        MappingProxyType(shapes),
        bounds,
        network.projection_origin,
        MappingProxyType({}),
        (map_width, 0, 128, 64),
    )
    return CbdFocus(focused_network, tuple(focused_trips))

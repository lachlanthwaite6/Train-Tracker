from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from train_tracker.map.cbd import CbdFocus
from train_tracker.map.geometry import offset_polyline, point_at_distance, snap_to_polyline
from train_tracker.map.models import (
    Point,
    RailNetwork,
    RailRoute,
    RailShape,
    RailStation,
    RailTrip,
    TrainMarker,
)
from train_tracker.map.projection import MapViewport


@dataclass(frozen=True, slots=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    def intersects(self, other: Rect, gap: float = 0.0) -> bool:
        return not (
            self.right + gap <= other.left
            or other.right + gap <= self.left
            or self.bottom + gap <= other.top
            or other.bottom + gap <= self.top
        )

    def contains(self, point: Point) -> bool:
        return self.left <= point.x <= self.right and self.top <= point.y <= self.bottom


@dataclass(frozen=True, slots=True)
class LabelRequest:
    id: str
    text: str
    anchor: Point
    priority: int = 0
    interchange: bool = False


def place_system_labels(
    requests: tuple[LabelRequest, ...],
    *,
    width: int,
    height: int,
    max_labels: int,
    blockers: tuple[Rect, ...] = (),
    status_bounds: Rect | None = None,
    compact: bool = True,
) -> dict[str, Point]:
    """Place labels deterministically using bounded candidate positions."""
    occupied = list(blockers)
    if status_bounds is not None:
        occupied.append(status_bounds)
    placed: dict[str, Point] = {}
    for request in sorted(requests, key=lambda item: (-item.priority, item.id)):
        if len(placed) >= max_labels:
            break
        text_width = len(request.text) * (4 if compact else 7)
        text_height = 5 if compact else 11
        node_gap = 4 if request.interchange else 3
        x, y = request.anchor.x, request.anchor.y
        candidates = (
            Point(x - text_width / 2, y - text_height - node_gap),
            Point(x - text_width / 2, y + node_gap),
            Point(x - text_width - node_gap, y - text_height / 2),
            Point(x + node_gap, y - text_height / 2),
            Point(x + node_gap, y - text_height - node_gap),
        )
        for candidate in candidates:
            bounds = Rect(
                round(candidate.x),
                round(candidate.y),
                round(candidate.x) + text_width,
                round(candidate.y) + text_height,
            )
            if bounds.left < 0 or bounds.top < 0:
                continue
            if bounds.right >= width or bounds.bottom >= height:
                continue
            if any(bounds.intersects(item, 1) for item in occupied):
                continue
            placed[request.id] = Point(bounds.left, bounds.top)
            occupied.append(bounds)
            break
    return placed


def choose_information_overlay(
    width: int,
    height: int,
    *,
    blocked_points: tuple[Point, ...],
    avoid_points: tuple[Point, ...] = (),
    overlay_size: tuple[int, int] = (36, 18),
    status_height: int = 6,
) -> Rect:
    """Choose the least obstructive compact information corner."""
    box_width, box_height = overlay_size
    candidates = (
        Rect(1, status_height + 1, 1 + box_width, status_height + 1 + box_height),
        Rect(width - box_width - 1, status_height + 1, width - 1, status_height + 1 + box_height),
        Rect(1, height - box_height - 1, 1 + box_width, height - 1),
        Rect(width - box_width - 1, height - box_height - 1, width - 1, height - 1),
    )

    def score(bounds: Rect) -> tuple[float, float, float]:
        covered = sum(1 for point in blocked_points if bounds.contains(point))
        avoided = sum(8 for point in avoid_points if bounds.contains(point))
        cx = (bounds.left + bounds.right) / 2
        cy = (bounds.top + bounds.bottom) / 2
        distance_score = min(
            (math.hypot(cx - point.x, cy - point.y) for point in avoid_points),
            default=0.0,
        )
        return covered + avoided, -distance_score, bounds.left + bounds.top / 1000

    return min(candidates, key=score)


def _canonical_pattern(station_ids: tuple[str, ...]) -> tuple[str, ...]:
    reverse = tuple(reversed(station_ids))
    return min(station_ids, reverse)


def route_patterns(
    network: RailNetwork, trips: tuple[RailTrip, ...]
) -> dict[str, tuple[tuple[str, ...], ...]]:
    """Return distinct GTFS-derived route branches, ignoring reverse duplicates."""
    values: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for trip in trips:
        sequence = tuple(event.station_id for event in trip.stops)
        if len(sequence) < 2:
            continue
        canonical = _canonical_pattern(sequence)
        if canonical not in values[trip.route_id]:
            values[trip.route_id].append(canonical)
    for route in network.routes.values():
        if route.id not in values and len(route.station_ids) >= 2:
            values[route.id].append(_canonical_pattern(route.station_ids))
    selected: dict[str, tuple[tuple[str, ...], ...]] = {}
    for route_id, patterns in values.items():
        ordered = sorted(patterns, key=lambda item: (-len(item), item))
        kept: list[tuple[str, ...]] = []
        covered_stations: set[str] = set()
        covered_termini: set[str] = set()
        for pattern in ordered:
            adds_branch = (
                not kept
                or len(set(pattern) - covered_stations) >= 2
                or pattern[0] not in covered_termini
                or pattern[-1] not in covered_termini
            )
            if not adds_branch:
                continue
            kept.append(pattern)
            covered_stations.update(pattern)
            covered_termini.update((pattern[0], pattern[-1]))
            if len(kept) == 4:
                break
        selected[route_id] = tuple(kept)
    return selected


def _load_curated_positions(path: str | Path | None) -> dict[str, Point]:
    if not path:
        return {}
    source = Path(path)
    if not source.exists():
        return {}
    raw = json.loads(source.read_text(encoding="utf-8"))
    values = raw.get("system_stations", {})
    if not isinstance(values, dict):
        return {}
    return {
        station_id: Point(float(value[0]), float(value[1]))
        for station_id, value in values.items()
        if isinstance(value, list) and len(value) == 2
    }


def _fit_positions(
    stations: dict[str, RailStation],
    *,
    screen_coverage: float,
    curated: dict[str, Point],
) -> dict[str, Point]:
    source = {station_id: station.position for station_id, station in stations.items()}
    source.update(
        {station_id: point for station_id, point in curated.items() if station_id in source}
    )
    min_x = min(point.x for point in source.values())
    max_x = max(point.x for point in source.values())
    min_y = min(point.y for point in source.values())
    max_y = max(point.y for point in source.values())
    x_coverage = min(0.97, screen_coverage + 0.15)
    y_coverage = min(0.875, screen_coverage / x_coverage)
    target_width = 128 * x_coverage
    target_height = 64 * y_coverage
    left = (128 - target_width) / 2
    top = 6 + max(0.0, (58 - target_height) / 2)
    x_span = max(1e-9, max_x - min_x)
    y_span = max(1e-9, max_y - min_y)
    positions = {
        station_id: Point(
            round(left + (point.x - min_x) / x_span * target_width),
            round(top + (point.y - min_y) / y_span * target_height),
        )
        for station_id, point in source.items()
    }
    # Quantisation can place nearby physical stations on the same pixel. Move
    # only true collisions, in stable ID order, without changing topology.
    occupied: list[Point] = []
    result: dict[str, Point] = {}
    nudges = (
        Point(0, 0),
        Point(2, 0),
        Point(-2, 0),
        Point(0, 2),
        Point(0, -2),
        Point(2, 2),
        Point(-2, -2),
    )
    for station_id in sorted(positions):
        origin = positions[station_id]
        selected = origin
        for nudge in nudges:
            candidate = Point(
                min(126, max(2, origin.x + nudge.x)),
                min(62, max(7, origin.y + nudge.y)),
            )
            if all(
                math.hypot(candidate.x - item.x, candidate.y - item.y) >= 1.5 for item in occupied
            ):
                selected = candidate
                break
        result[station_id] = selected
        occupied.append(selected)
    return result


def _smooth_corridors(
    positions: dict[str, Point],
    stations: dict[str, RailStation],
    patterns: dict[str, tuple[tuple[str, ...], ...]],
) -> dict[str, Point]:
    """Straighten ordinary corridor stations while keeping topology anchors fixed."""
    neighbours: dict[str, set[str]] = defaultdict(set)
    termini: set[str] = set()
    for route_patterns_value in patterns.values():
        for pattern in route_patterns_value:
            termini.update((pattern[0], pattern[-1]))
            for first, second in zip(pattern, pattern[1:], strict=False):
                if first in positions and second in positions:
                    neighbours[first].add(second)
                    neighbours[second].add(first)
    fixed = {
        station_id
        for station_id, station in stations.items()
        if station.interchange or len(neighbours[station_id]) != 2 or station_id in termini
    }
    result = dict(positions)
    for _iteration in range(18):
        updated = dict(result)
        for station_id in sorted(result):
            if station_id in fixed or len(neighbours[station_id]) != 2:
                continue
            first, second = sorted(neighbours[station_id])
            average = Point(
                (result[first].x + result[second].x) / 2,
                (result[first].y + result[second].y) / 2,
            )
            current = result[station_id]
            updated[station_id] = Point(
                current.x * 0.2 + average.x * 0.8,
                current.y * 0.2 + average.y * 0.8,
            )
        result = updated
    return {
        station_id: Point(round(point.x), round(point.y)) for station_id, point in result.items()
    }


def system_viewport(width: int, height: int) -> MapViewport:
    """Scale the logical 128x64 schematic without losing its designed margins."""
    return MapViewport(width, height, Point(64, 32), min(width / 128, height / 64), 0)


def _octilinear_segment(start: Point, end: Point, _token: str) -> tuple[Point, ...]:
    dx, dy = end.x - start.x, end.y - start.y
    if dx == 0 or dy == 0 or abs(abs(dx) - abs(dy)) <= 1:
        return (start, end)
    diagonal = min(abs(dx), abs(dy))
    sx = 1 if dx >= 0 else -1
    sy = 1 if dy >= 0 else -1
    if abs(dx) > abs(dy):
        diagonal_point = Point(start.x + sx * diagonal, end.y)
        horizontal_point = Point(end.x, end.y)
        points: tuple[Point, ...] = (start, diagonal_point, horizontal_point)
    else:
        diagonal_point = Point(end.x, start.y + sy * diagonal)
        vertical_point = Point(end.x, end.y)
        points = (start, diagonal_point, vertical_point)
    return tuple(
        point for index, point in enumerate(points) if index == 0 or point != points[index - 1]
    )


def _edge_key(first: str, second: str) -> tuple[str, str]:
    return min(first, second), max(first, second)


def _edge_polyline(
    first: str,
    second: str,
    positions: dict[str, Point],
    *,
    offset: float,
) -> tuple[Point, ...]:
    key = _edge_key(first, second)
    canonical = _octilinear_segment(positions[key[0]], positions[key[1]], ":".join(key))
    shifted = offset_polyline(canonical, offset)
    return shifted if (first, second) == key else tuple(reversed(shifted))


def _major_station_ids(
    stations: dict[str, RailStation],
    patterns: dict[str, tuple[tuple[str, ...], ...]],
    default_station_id: str,
) -> set[str]:
    degree: dict[str, set[str]] = defaultdict(set)
    termini: set[str] = set()
    for route_patterns_value in patterns.values():
        for pattern in route_patterns_value:
            termini.update((pattern[0], pattern[-1]))
            for first, second in zip(pattern, pattern[1:], strict=False):
                degree[first].add(second)
                degree[second].add(first)
    values = {station_id for station_id in stations if len(degree[station_id]) != 2}
    values.update(termini)
    if default_station_id in stations:
        values.add(default_station_id)
    return values


def _principal_interchanges(
    stations: dict[str, RailStation],
    positions: dict[str, Point],
    patterns: dict[str, tuple[tuple[str, ...], ...]],
) -> set[str]:
    degree: dict[str, set[str]] = defaultdict(set)
    for route_patterns_value in patterns.values():
        for pattern in route_patterns_value:
            for first, second in zip(pattern, pattern[1:], strict=False):
                degree[first].add(second)
                degree[second].add(first)
    ranked = sorted(
        (
            station
            for station in stations.values()
            if len(station.route_ids) > 1 and station.id in positions
        ),
        key=lambda station: (-len(station.route_ids), -len(degree[station.id]), station.id),
    )
    selected: list[str] = []
    for station in ranked:
        point = positions[station.id]
        if all(
            math.hypot(point.x - positions[item].x, point.y - positions[item].y) >= 7
            for item in selected
        ):
            selected.append(station.id)
        if len(selected) == 8:
            break
    selected.extend(
        station_id
        for station_id, values in degree.items()
        if len(values) > 2 and station_id not in selected
    )
    return set(selected)


def build_system_view(
    network: RailNetwork,
    trips: tuple[RailTrip, ...],
    *,
    screen_coverage: float = 0.82,
    route_lane_spacing: float = 2.0,
    default_station_id: str = "place_twgsta",
    layout_file: str | Path | None = None,
) -> CbdFocus:
    """Build a fitted, connected, GTFS-derived 128x64 railway schematic."""
    patterns = route_patterns(network, trips)
    if not patterns:
        raise RuntimeError("Rail System View has no usable route patterns.")
    used_station_ids = {
        station_id
        for route_patterns_value in patterns.values()
        for pattern in route_patterns_value
        for station_id in pattern
    }
    source_stations = {
        station_id: station
        for station_id, station in network.stations.items()
        if station_id in used_station_ids
    }
    positions = _fit_positions(
        source_stations,
        screen_coverage=screen_coverage,
        curated=_load_curated_positions(layout_file),
    )
    positions = _smooth_corridors(positions, source_stations, patterns)
    edge_routes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for route_id, route_patterns_value in patterns.items():
        for pattern in route_patterns_value:
            for first, second in zip(pattern, pattern[1:], strict=False):
                if first in positions and second in positions:
                    edge_routes[_edge_key(first, second)].add(route_id)

    shapes: dict[str, RailShape] = {}
    routes: dict[str, RailRoute] = {}
    shape_by_pattern: dict[tuple[str, tuple[str, ...]], str] = {}
    for route_id, route_patterns_value in patterns.items():
        route = network.routes.get(route_id)
        if route is None:
            continue
        shape_ids: list[str] = []
        station_order: list[str] = []
        for pattern_index, pattern in enumerate(route_patterns_value):
            usable = tuple(item for item in pattern if item in positions)
            if len(usable) < 2:
                continue
            points: list[Point] = []
            for first, second in zip(usable, usable[1:], strict=False):
                sharing = sorted(edge_routes[_edge_key(first, second)])
                lane_index = sharing.index(route_id)
                offset = (lane_index - (len(sharing) - 1) / 2) * route_lane_spacing
                segment = _edge_polyline(first, second, positions, offset=offset)
                points.extend(segment if not points else segment[1:])
            shape_id = f"system:{route_id}:{pattern_index}"
            shapes[shape_id] = RailShape(shape_id, tuple(points))
            shape_ids.append(shape_id)
            shape_by_pattern[(route_id, _canonical_pattern(usable))] = shape_id
            for station_id in usable:
                if station_id not in station_order:
                    station_order.append(station_id)
        routes[route_id] = replace(
            route,
            shape_ids=tuple(shape_ids),
            station_ids=tuple(station_order),
        )

    system_trips: list[RailTrip] = []
    for trip in trips:
        visible_stops = tuple(event for event in trip.stops if event.station_id in positions)
        sequence = tuple(event.station_id for event in visible_stops)
        if len(sequence) < 2 or trip.route_id not in routes:
            continue
        key = (trip.route_id, _canonical_pattern(sequence))
        trip_shape_id: str | None = shape_by_pattern.get(key)
        if trip_shape_id is None:
            candidates = routes[trip.route_id].shape_ids
            trip_shape_id = candidates[0] if candidates else None
        system_trips.append(replace(trip, shape_id=trip_shape_id, stops=visible_stops))

    major_ids = _major_station_ids(source_stations, patterns, default_station_id)
    principal_interchanges = _principal_interchanges(source_stations, positions, patterns)
    major_ids.update(principal_interchanges)
    stations = {
        station_id: replace(
            station,
            position=positions[station_id],
            interchange=station_id in principal_interchanges,
        )
        for station_id, station in source_stations.items()
        if station_id in positions
    }
    label_positions = MappingProxyType(
        {station_id: positions[station_id] for station_id in major_ids if station_id in positions}
    )
    all_points = [point for shape in shapes.values() for point in shape.points]
    if not all_points:
        raise RuntimeError("Rail System View could not build schematic geometry.")
    bounds = (
        min(point.x for point in all_points),
        min(point.y for point in all_points),
        max(point.x for point in all_points),
        max(point.y for point in all_points),
    )
    system_network = RailNetwork(
        MappingProxyType(routes),
        MappingProxyType(stations),
        MappingProxyType(shapes),
        bounds,
        network.projection_origin,
        label_positions,
    )
    return CbdFocus(system_network, tuple(system_trips))


def _marker_normal(
    marker: TrainMarker,
    network: RailNetwork,
    trip_by_id: dict[str, RailTrip],
) -> Point:
    trip = trip_by_id.get(marker.trip_id)
    shape = network.shapes.get(trip.shape_id or "") if trip is not None else None
    if shape is None or len(shape.points) < 2:
        return Point(0, 1)
    _snapped, _distance, along = snap_to_polyline(marker.position, shape.points)
    before = point_at_distance(shape.points, max(0, along - 1))
    after = point_at_distance(shape.points, along + 1)
    dx, dy = after.x - before.x, after.y - before.y
    length = max(1e-9, math.hypot(dx, dy))
    return Point(-dy / length, dx / length)


def resolve_system_marker_overlaps(
    markers: tuple[TrainMarker, ...],
    network: RailNetwork,
    trips: tuple[RailTrip, ...],
    spacing: float,
) -> tuple[TrainMarker, ...]:
    """Separate directions and close trains with stable perpendicular formations."""
    if not markers:
        return markers
    trip_by_id = {trip.id: trip for trip in trips}

    def direction_for(marker: TrainMarker) -> int:
        trip = trip_by_id.get(marker.trip_id)
        return int(trip.direction_id or 0) if trip is not None else 0

    ordered = sorted(
        markers,
        key=lambda marker: (
            marker.route_id,
            direction_for(marker),
            marker.trip_id,
            marker.id,
        ),
    )
    groups: list[list[TrainMarker]] = []
    for marker in ordered:
        group = next(
            (
                item
                for item in groups
                if any(
                    math.hypot(
                        marker.position.x - other.position.x,
                        marker.position.y - other.position.y,
                    )
                    < spacing * 1.4
                    for other in item
                )
            ),
            None,
        )
        if group is None:
            groups.append([marker])
        else:
            group.append(marker)
    resolved: dict[str, TrainMarker] = {}
    for group in groups:
        if len(group) > 4:
            representative = group[0]
            resolved[representative.id] = replace(
                representative,
                cluster_count=len(group),
            )
            continue
        for index, marker in enumerate(group):
            trip = trip_by_id.get(marker.trip_id)
            direction = int(trip.direction_id or 0) if trip is not None else 0
            normal = _marker_normal(marker, network, trip_by_id)
            direction_side = -0.5 if direction == 0 else 0.5
            stack = (index - (len(group) - 1) / 2) * 0.55
            offset = (direction_side + stack) * spacing
            resolved[marker.id] = replace(
                marker,
                position=Point(
                    min(126, max(2, marker.position.x + normal.x * offset)),
                    min(62, max(7, marker.position.y + normal.y * offset)),
                ),
            )
    return tuple(resolved[marker.id] for marker in markers if marker.id in resolved)

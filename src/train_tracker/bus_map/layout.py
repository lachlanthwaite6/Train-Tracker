from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import MappingProxyType

from train_tracker.bus_map.models import BusDirection, BusRouteChoice
from train_tracker.map.geometry import (
    distance,
    offset_polyline,
    point_at_distance,
    snap_to_polyline,
)
from train_tracker.map.models import (
    GeoPoint,
    Point,
    RailNetwork,
    RailRoute,
    RailShape,
    RailStation,
    RailTrip,
    StopTimeEvent,
)


def simplify_polyline(points: tuple[Point, ...], tolerance: float) -> tuple[Point, ...]:
    """Ramer-Douglas-Peucker simplification with stable endpoint retention."""
    if len(points) <= 2:
        return points
    start, end = points[0], points[-1]
    dx, dy = end.x - start.x, end.y - start.y
    denominator = max(1e-9, math.hypot(dx, dy))
    distances = [
        abs(dy * point.x - dx * point.y + end.x * start.y - end.y * start.x) / denominator
        for point in points[1:-1]
    ]
    maximum = max(distances, default=0.0)
    if maximum <= tolerance:
        return (start, end)
    split = distances.index(maximum) + 1
    left = simplify_polyline(points[: split + 1], tolerance)
    right = simplify_polyline(points[split:], tolerance)
    return (*left[:-1], *right)


def _normalized_path(
    source: tuple[Point, ...], map_width: int, coverage: float
) -> tuple[Point, ...]:
    if len(source) < 2:
        source = (Point(0, 0), Point(1, 0))
    min_x = min(point.x for point in source)
    max_x = max(point.x for point in source)
    min_y = min(point.y for point in source)
    max_y = max(point.y for point in source)
    if max_y - min_y > max_x - min_x:
        source = tuple(Point(-point.y, point.x) for point in source)
        min_x = min(point.x for point in source)
        max_x = max(point.x for point in source)
        min_y = min(point.y for point in source)
        max_y = max(point.y for point in source)
    target_width = min(map_width - 1.0, 128.0 * coverage)
    left = (map_width - target_width) / 2
    x_span = max(1e-9, max_x - min_x)
    y_span = max(1e-9, max_y - min_y)
    target_height = 45.0
    result = tuple(
        Point(
            left + (point.x - min_x) / x_span * target_width,
            9.0 + (point.y - min_y) / y_span * target_height,
        )
        for point in source
    )
    # A perfectly straight geographic shape gains one restrained 45-degree
    # bend so direction and motion remain readable on the tiny canvas.
    if len(result) == 2:
        start, end = result
        middle_x = (start.x + end.x) / 2
        middle_y = min(52.0, max(12.0, (start.y + end.y) / 2 + 10.0))
        return (start, Point(middle_x, middle_y), end)
    return result


def _layout_override(path: str | Path | None, route: BusRouteChoice) -> tuple[Point, ...] | None:
    if not path:
        return None
    source = Path(path)
    if not source.exists():
        return None
    raw = json.loads(source.read_text(encoding="utf-8"))
    routes = raw.get("bus_routes", {})
    if not isinstance(routes, dict):
        return None
    values = routes.get(route.id, routes.get(route.short_name))
    if not isinstance(values, list) or len(values) < 2:
        return None
    return tuple(Point(float(item[0]), float(item[1])) for item in values)


def _direction_allowed(direction_id: int | None, direction: BusDirection) -> bool:
    if direction == BusDirection.BOTH:
        return True
    return (direction_id or 0) == (0 if direction == BusDirection.INBOUND else 1)


def build_bus_schematic(
    geographic: RailNetwork,
    trips: tuple[RailTrip, ...],
    route: BusRouteChoice,
    *,
    direction: BusDirection,
    map_width: int,
    route_coverage: float,
    lane_spacing: float,
    station_spacing: float = 12.0,
    layout_file: str | Path | None = None,
) -> tuple[RailNetwork, tuple[RailTrip, ...], frozenset[str]]:
    selected_trips = tuple(
        trip for trip in trips if _direction_allowed(trip.direction_id, direction)
    )
    if not selected_trips:
        return geographic, (), frozenset()
    representative = max(selected_trips, key=lambda item: len(item.stops))
    source_shape = geographic.shapes.get(representative.shape_id or "")
    source_points = (
        source_shape.points
        if source_shape is not None
        else tuple(geographic.stations[event.station_id].position for event in representative.stops)
    )
    extent = max(
        max(point.x for point in source_points) - min(point.x for point in source_points),
        max(point.y for point in source_points) - min(point.y for point in source_points),
        1.0,
    )
    simplified = simplify_polyline(source_points, extent / 28)
    centerline = _layout_override(layout_file, route) or _normalized_path(
        simplified, map_width, route_coverage
    )

    center_length = sum(
        distance(first, second) for first, second in zip(centerline, centerline[1:], strict=False)
    )

    station_progress: dict[str, float] = {
        event.station_id: index / max(1, len(representative.stops) - 1)
        for index, event in enumerate(representative.stops)
    }
    for trip in selected_trips:
        full_shape = geographic.shapes.get(trip.shape_id or "")
        for event in trip.stops:
            if event.station_id in station_progress:
                continue
            if full_shape is not None:
                along = snap_to_polyline(
                    geographic.stations[event.station_id].position, full_shape.points
                )[2]
                full_length = max(
                    1e-9,
                    sum(
                        distance(first, second)
                        for first, second in zip(
                            full_shape.points, full_shape.points[1:], strict=False
                        )
                    ),
                )
                station_progress[event.station_id] = min(1.0, max(0.0, along / full_length))
            else:
                station_progress[event.station_id] = 0.0

    stations: dict[str, RailStation] = {}
    ordered_ids = sorted(station_progress, key=lambda item: station_progress[item])
    maximum_major = min(6, max(2, math.floor(center_length / station_spacing) + 1))
    major_step = max(1, math.ceil(len(ordered_ids) / maximum_major))
    major_ids = {
        station_id
        for index, station_id in enumerate(ordered_ids)
        if index in {0, len(ordered_ids) - 1} or index % major_step == 0
    }
    for station_id, progress in station_progress.items():
        source_station = geographic.stations[station_id]
        major = station_id in major_ids or any(
            word in source_station.name.casefold()
            for word in ("cultural centre", "roma street", "uq lakes", "university")
        )
        if major:
            major_ids.add(station_id)
        stations[station_id] = replace(
            source_station,
            position=point_at_distance(centerline, center_length * progress),
            route_ids=(route.id,),
            interchange=major,
        )

    lane_shapes: dict[int, RailShape] = {}
    directions = {int(trip.direction_id or 0) for trip in selected_trips}
    for direction_id in directions:
        offset = (-0.5 if direction_id == 0 else 0.5) * lane_spacing
        shape_id = f"bus:{route.id}:{direction_id}"
        lane_shapes[direction_id] = RailShape(shape_id, offset_polyline(centerline, offset))

    focused_trips: list[RailTrip] = []
    for trip in selected_trips:
        direction_id = int(trip.direction_id or 0)
        events = tuple(event for event in trip.stops if event.station_id in stations)
        if len(events) >= 2:
            focused_trips.append(replace(trip, shape_id=lane_shapes[direction_id].id, stops=events))
    shapes = {shape.id: shape for shape in lane_shapes.values()}
    map_route = RailRoute(
        route.id,
        route.short_name,
        route.color,
        "#ffffff",
        3,
        tuple(shapes),
        tuple(ordered_ids),
    )
    network = RailNetwork(
        MappingProxyType({route.id: map_route}),
        MappingProxyType(stations),
        MappingProxyType(shapes),
        (0.0, 4.0, float(map_width - 1), 59.0),
        geographic.projection_origin,
    )
    return network, tuple(focused_trips), frozenset(major_ids)


def demo_bus_data(service_date: date) -> tuple[RailNetwork, tuple[RailTrip, ...], BusRouteChoice]:
    route = BusRouteChoice("DEMO-M1", "M1", "Demo Metro", "#E463A4")
    stop_names = (
        "Eight Mile Plains",
        "Upper Mt Gravatt",
        "Griffith University",
        "Holland Park",
        "Greenslopes",
        "Buranda",
        "Cultural Centre",
        "Roma Street",
        "King George Square",
    )
    stations = {
        f"demo-bus-{index}": RailStation(
            f"demo-bus-{index}",
            name,
            Point(index * 100.0, math.sin(index / 2) * 80.0),
            -27.58 + index * 0.015,
            153.05 - index * 0.002,
            (route.id,),
            index in {0, 2, 6, 7, 8},
        )
        for index, name in enumerate(stop_names)
    }
    shapes = {
        "demo-m1-0": RailShape("demo-m1-0", tuple(item.position for item in stations.values())),
        "demo-m1-1": RailShape(
            "demo-m1-1", tuple(reversed(tuple(item.position for item in stations.values())))
        ),
    }
    network = RailNetwork(
        MappingProxyType(
            {
                route.id: RailRoute(
                    route.id,
                    route.short_name,
                    route.color,
                    "#ffffff",
                    3,
                    tuple(shapes),
                    tuple(stations),
                )
            }
        ),
        MappingProxyType(stations),
        MappingProxyType(shapes),
        (0, -80, 800, 80),
        GeoPoint(-27.48, 153.03),
    )
    trips: list[RailTrip] = []
    for cycle in range(4):
        for direction_id in (0, 1):
            ordered = tuple(stations) if direction_id == 0 else tuple(reversed(tuple(stations)))
            start = 7 * 3600 + cycle * 360 + direction_id * 180
            events = tuple(
                StopTimeEvent(
                    station_id,
                    stations[station_id].name,
                    start + index * 150,
                    start + index * 150 + 25,
                    index + 1,
                )
                for index, station_id in enumerate(ordered)
            )
            trips.append(
                RailTrip(
                    f"demo-m1-{cycle}-{direction_id}",
                    route.id,
                    "DEMO",
                    service_date,
                    stations[ordered[-1]].name,
                    direction_id,
                    f"demo-m1-{direction_id}",
                    events,
                )
            )
    return network, tuple(trips), route

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import MappingProxyType

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


def apply_layout_override(network: RailNetwork, path: str | Path | None) -> RailNetwork:
    if not path:
        return network
    source = Path(path)
    if not source.exists():
        return network
    raw = json.loads(source.read_text(encoding="utf-8"))
    stations = dict(network.stations)
    for station_id, coordinates in raw.get("stations", {}).items():
        if station_id not in stations or len(coordinates) != 2:
            continue
        old = stations[station_id]
        stations[station_id] = RailStation(
            old.id,
            old.name,
            Point(float(coordinates[0]), float(coordinates[1])),
            old.latitude,
            old.longitude,
            old.route_ids,
            old.interchange,
        )
    shapes = dict(network.shapes)
    for shape_id, points in raw.get("shapes", {}).items():
        geometry = tuple(Point(float(item[0]), float(item[1])) for item in points if len(item) == 2)
        if geometry:
            shapes[shape_id] = RailShape(shape_id, geometry)
    all_points = [station.position for station in stations.values()]
    all_points.extend(point for shape in shapes.values() for point in shape.points)
    bounds = (
        min(point.x for point in all_points),
        min(point.y for point in all_points),
        max(point.x for point in all_points),
        max(point.y for point in all_points),
    )
    return RailNetwork(
        MappingProxyType(dict(network.routes)),
        MappingProxyType(stations),
        MappingProxyType(shapes),
        bounds,
        network.projection_origin,
        network.label_positions,
        network.reserved_info_bounds,
    )


def demo_network_and_trips(service_date: date) -> tuple[RailNetwork, tuple[RailTrip, ...]]:
    """Small built-in, clearly-labelled map for first-run offline simulation."""

    station_data = (
        ("demo_ips", "Demo Ipswich", 0.0, 120.0),
        ("place_twgsta", "Toowong", 240.0, 80.0),
        ("demo_central", "Demo Central", 390.0, 55.0),
        ("demo_air", "Demo Airport", 520.0, -90.0),
        ("demo_clev", "Demo Cleveland", 630.0, 150.0),
        ("demo_coast", "Demo Gold Coast", 470.0, 390.0),
    )
    memberships = {
        "demo_ips": ("DEMO-W",),
        "place_twgsta": ("DEMO-W", "DEMO-S"),
        "demo_central": ("DEMO-W", "DEMO-S", "DEMO-A"),
        "demo_air": ("DEMO-A",),
        "demo_clev": ("DEMO-W",),
        "demo_coast": ("DEMO-S",),
    }
    stations = {
        station_id: RailStation(
            station_id,
            name,
            Point(x, y),
            -27.5 + y / 10000,
            153.0 + x / 10000,
            memberships[station_id],
            len(memberships[station_id]) > 1,
        )
        for station_id, name, x, y in station_data
    }
    sequences = {
        "DEMO-W": ("demo_ips", "place_twgsta", "demo_central", "demo_clev"),
        "DEMO-S": ("place_twgsta", "demo_central", "demo_coast"),
        "DEMO-A": ("demo_central", "demo_air"),
    }
    colors = {"DEMO-W": "#f4b942", "DEMO-S": "#5bc0eb", "DEMO-A": "#e85d75"}
    shapes: dict[str, RailShape] = {}
    routes: dict[str, RailRoute] = {}
    for route_id, sequence in sequences.items():
        shape_id = f"shape:{route_id}"
        shapes[shape_id] = RailShape(shape_id, tuple(stations[item].position for item in sequence))
        routes[route_id] = RailRoute(
            route_id,
            route_id.removeprefix("DEMO-") + " demo",
            colors[route_id],
            "#ffffff",
            2,
            (shape_id,),
            sequence,
        )
    network = RailNetwork(
        MappingProxyType(routes),
        MappingProxyType(stations),
        MappingProxyType(shapes),
        (0.0, -90.0, 630.0, 390.0),
        GeoPoint(-27.47, 153.03),
    )
    trips: list[RailTrip] = []
    for route_index, (route_id, sequence) in enumerate(sequences.items()):
        for direction in (0, 1):
            ordered = sequence if direction == 0 else tuple(reversed(sequence))
            start = 7 * 3600 + route_index * 240 + direction * 600
            events = tuple(
                StopTimeEvent(
                    station_id,
                    stations[station_id].name,
                    start + index * 480,
                    start + index * 480 + 45,
                    index + 1,
                )
                for index, station_id in enumerate(ordered)
            )
            trips.append(
                RailTrip(
                    f"demo-{route_id}-{direction}",
                    route_id,
                    "DEMO",
                    service_date,
                    stations[ordered[-1]].name,
                    direction,
                    f"shape:{route_id}",
                    events,
                )
            )
    return network, tuple(trips)

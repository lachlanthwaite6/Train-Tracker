from __future__ import annotations

import hashlib
from dataclasses import replace

from PIL import ImageColor

from train_tracker.config import AppConfig, MapConfig
from train_tracker.map.geometry import distance, snap_to_polyline
from train_tracker.map.models import MapScope, Point
from train_tracker.map.presenter import MapPresenter
from train_tracker.map.renderer import MapRenderOptions, NetworkMapRenderer
from train_tracker.map.system import (
    LabelRequest,
    Rect,
    choose_information_overlay,
    place_system_labels,
    resolve_system_marker_overlaps,
    system_viewport,
)


def _system_scene():  # type: ignore[no-untyped-def]
    config = AppConfig()
    presenter = MapPresenter(
        None,
        mode="simulated",
        scope=MapScope.SYSTEM,
        system_screen_coverage=config.map.system_screen_coverage,
        route_lane_spacing=config.map.route_lane_spacing,
        train_collision_spacing=config.map.train_collision_spacing,
        default_station_id=config.map.default_station_id,
    )
    return presenter.prepare(config.start_time)


def test_system_view_is_default_and_covers_at_least_eighty_percent() -> None:
    assert MapConfig().default_scope == "system"
    scene = _system_scene()
    assert scene.scope == MapScope.SYSTEM
    left, top, right, bottom = scene.network.bounds
    assert (right - left) * (bottom - top) / (128 * 64) >= 0.80


def test_system_preserves_branches_interchanges_and_parent_station_identity() -> None:
    scene = _system_scene()
    assert len(scene.network.routes) >= 3
    central = scene.network.stations["demo_central"]
    assert len(central.route_ids) == 3
    assert central.interchange
    assert "place_twgsta" in scene.network.stations


def test_shared_corridor_routes_receive_distinct_lane_offsets() -> None:
    scene = _system_scene()
    station = scene.network.stations["place_twgsta"]
    route_points = []
    for route_id in ("DEMO-W", "DEMO-S"):
        shape = scene.network.shapes[scene.network.routes[route_id].shape_ids[0]]
        route_points.append(snap_to_polyline(station.position, shape.points)[0])
    assert distance(route_points[0], route_points[1]) >= 1.5


def test_opposite_directions_and_multiple_trains_form_stable_separation() -> None:
    scene = _system_scene()
    route_id = next(iter(scene.network.routes))
    route_trips = [trip for trip in scene.trips if trip.route_id == route_id]
    outbound = next(trip for trip in route_trips if int(trip.direction_id or 0) == 0)
    inbound = next(trip for trip in route_trips if int(trip.direction_id or 0) == 1)
    marker = scene.markers[0]
    shared = Point(64, 32)
    markers = (
        replace(marker, id="a", trip_id=outbound.id, position=shared),
        replace(marker, id="b", trip_id=inbound.id, position=shared),
        replace(marker, id="c", trip_id=outbound.id, position=shared),
    )
    first = resolve_system_marker_overlaps(markers, scene.network, scene.trips, 2)
    second = resolve_system_marker_overlaps(markers, scene.network, scene.trips, 2)
    assert first == second
    assert len({item.position for item in first}) == len(first)
    assert first[0].position != first[1].position


def test_dense_train_group_uses_compact_count_marker() -> None:
    scene = _system_scene()
    marker = scene.markers[0]
    markers = tuple(
        replace(marker, id=f"cluster-{index}", position=Point(64, 32)) for index in range(6)
    )
    clustered = resolve_system_marker_overlaps(markers, scene.network, scene.trips, 2)
    assert len(clustered) == 1
    assert clustered[0].cluster_count == 6


def test_label_engine_avoids_collisions_status_and_boundaries() -> None:
    requests = (
        LabelRequest("a", "AAA", Point(20, 20), priority=2),
        LabelRequest("b", "BBB", Point(20, 20), priority=1),
        LabelRequest("edge", "EDGE", Point(1, 7), priority=3),
    )
    placed = place_system_labels(
        requests,
        width=128,
        height=64,
        max_labels=2,
        blockers=(Rect(17, 10, 23, 16),),
        status_bounds=Rect(0, 0, 128, 6),
    )
    assert len(placed) <= 2
    boxes = [
        Rect(
            point.x,
            point.y,
            point.x + len(next(r.text for r in requests if r.id == key)) * 4,
            point.y + 5,
        )
        for key, point in placed.items()
    ]
    assert all(box.left >= 0 and box.top >= 6 for box in boxes)
    assert all(box.right < 128 and box.bottom < 64 for box in boxes)
    assert not (len(boxes) == 2 and boxes[0].intersects(boxes[1], 1))


def test_information_overlay_chooses_an_unoccupied_corner() -> None:
    selected = Point(110, 15)
    bounds = choose_information_overlay(
        128,
        64,
        blocked_points=(Point(18, 16), Point(110, 15), Point(110, 52)),
        avoid_points=(selected,),
    )
    assert not bounds.contains(selected)
    assert bounds.top >= 6


def test_toowong_highlight_native_render_and_determinism() -> None:
    scene = _system_scene()
    viewport = system_viewport(128, 64)
    options = MapRenderOptions(
        scope=MapScope.SYSTEM,
        highlighted_station_id="place_twgsta",
        animation_frame=4,
        max_led_labels=10,
    )
    renderer = NetworkMapRenderer()
    first = renderer.render(scene, (128, 64), viewport=viewport, options=options)
    second = renderer.render(
        scene,
        (128, 64),
        viewport=system_viewport(128, 64),
        options=options,
    )
    assert first.size == (128, 64)
    assert hashlib.sha256(first.tobytes()).digest() == hashlib.sha256(second.tobytes()).digest()
    point = viewport.world_to_screen(scene.network.stations["place_twgsta"].position)
    amber = ImageColor.getrgb("#ffd866")
    crop = first.crop(
        (
            max(0, round(point.x) - 5),
            max(0, round(point.y) - 5),
            min(128, round(point.x) + 6),
            min(64, round(point.y) + 6),
        )
    )
    assert amber in set(crop.get_flattened_data())


def test_system_and_route_focus_switch_without_restart() -> None:
    config = AppConfig()
    presenter = MapPresenter(None, mode="simulated", scope="system")
    system = presenter.prepare(config.start_time)
    presenter.set_scope("route")
    focused = presenter.prepare(config.start_time)
    presenter.set_scope("system")
    restored = presenter.prepare(config.start_time)
    assert (system.scope, focused.scope, restored.scope) == (
        MapScope.SYSTEM,
        MapScope.FOCUSED,
        MapScope.SYSTEM,
    )
    assert len(system.network.routes) > len(focused.network.routes)

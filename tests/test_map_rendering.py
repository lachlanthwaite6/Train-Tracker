from __future__ import annotations

import hashlib
from datetime import datetime

from train_tracker.clock import BRISBANE
from train_tracker.map.layout import demo_network_and_trips
from train_tracker.map.models import MapScene, Point
from train_tracker.map.presenter import MapPresenter
from train_tracker.map.projection import MapViewport
from train_tracker.map.renderer import MapRenderOptions, NetworkMapRenderer, hit_test


def test_demo_no_database_has_toowong_and_simulated_service() -> None:
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    scene = MapPresenter(None, mode="simulated").prepare(now)
    assert scene.network.stations["place_twgsta"].name == "Toowong"
    assert scene.status == "Simulated"
    assert scene.markers


def test_headless_dimensions_route_filter_and_hit_testing() -> None:
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    scene = MapPresenter(None, mode="simulated").prepare(now)
    viewport = MapViewport.fit(scene.network.bounds, 900, 600, 40)
    renderer = NetworkMapRenderer()
    full = renderer.render(scene, (900, 600), viewport=viewport)
    filtered = renderer.render(
        scene,
        (900, 600),
        viewport=viewport,
        options=MapRenderOptions(visible_routes=frozenset({"DEMO-A"})),
    )
    assert full.size == (900, 600)
    assert hashlib.sha256(full.tobytes()).digest() != hashlib.sha256(filtered.tobytes()).digest()
    station_point = viewport.world_to_screen(scene.network.stations["place_twgsta"].position)
    station_hit = hit_test(scene, viewport, station_point)
    assert station_hit is not None and station_hit.kind == "station"
    marker_point = viewport.world_to_screen(scene.markers[0].position)
    marker_hit = hit_test(scene, viewport, marker_point, marker_radius=15)
    assert marker_hit is not None and marker_hit.kind == "train"
    assert hit_test(scene, viewport, Point(-100, -100)) is None


def test_estimate_marker_style_changes_pixels() -> None:
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    network, trips = demo_network_and_trips(now.date())
    base = MapScene(network, (), now, "No service", "offline", trips=trips)
    with_markers = MapPresenter(None, mode="simulated").prepare(now)
    renderer = NetworkMapRenderer()
    viewport = MapViewport.fit(network.bounds, 800, 500, 40)
    empty = renderer.render(base, (800, 500), viewport=viewport)
    shown = renderer.render(with_markers, (800, 500), viewport=viewport)
    assert empty.tobytes() != shown.tobytes()

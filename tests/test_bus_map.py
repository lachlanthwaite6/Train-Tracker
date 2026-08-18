from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageColor

from train_tracker.bus_map.layout import build_bus_schematic, simplify_polyline
from train_tracker.bus_map.models import BusDirection
from train_tracker.bus_map.presenter import BusMapPresenter
from train_tracker.bus_map.renderer import BusMapRenderer, BusRenderOptions, draw_bus_sprite
from train_tracker.bus_map.repository import BusNetworkRepository
from train_tracker.clock import BRISBANE
from train_tracker.config import BusMapConfig, load_config
from train_tracker.gtfs.importer import import_gtfs
from train_tracker.map.cbd import TrainSelection, resolve_marker_overlaps
from train_tracker.map.geometry import distance
from train_tracker.map.models import Point, TrainPositionSource
from train_tracker.map.vehicle_tracker import VehicleTracker
from train_tracker.models import (
    FeedHealth,
    FeedStatus,
    ProviderSnapshot,
    Stop,
    TripDelay,
    VehiclePosition,
)


@pytest.fixture
def bus_repository(bus_gtfs_zip: Path, tmp_path: Path) -> BusNetworkRepository:
    database = tmp_path / "bus.sqlite3"
    import_gtfs(bus_gtfs_zip, database)
    return BusNetworkRepository(database)


def _presenter(
    repository: BusNetworkRepository,
    *,
    direction: BusDirection = BusDirection.BOTH,
) -> BusMapPresenter:
    return BusMapPresenter(
        repository,
        route="M1",
        direction=direction,
        map_width=90,
        route_coverage=0.70,
        lane_spacing=4,
        station_spacing=12,
    )


def _snapshot(
    now: datetime,
    *,
    vehicle_age: float | None = None,
    delays: tuple[TripDelay, ...] = (),
    health: FeedHealth = FeedHealth.HEALTHY,
) -> ProviderSnapshot:
    vehicles = ()
    if vehicle_age is not None:
        vehicles = (
            VehiclePosition(
                "bus-101",
                "M1_IN",
                -27.558,
                153.083,
                now - timedelta(seconds=vehicle_age),
            ),
        )
    return ProviderSnapshot(
        Stop("place_cul", "Cultural Centre"),
        vehicles=vehicles,
        status=FeedStatus("fixture", health, now, now, "Fixture realtime"),
        trip_delays=delays,
    )


def test_bus_config_defaults_validation_and_backward_compatibility(tmp_path: Path) -> None:
    config = BusMapConfig()
    assert config.map_width + config.info_panel_width == 128
    assert config.bus_sprite_size == "6x4"
    with pytest.raises(ValueError, match="total 128"):
        BusMapConfig(map_width=88)
    with pytest.raises(ValueError, match="inbound"):
        BusMapConfig(direction="sideways")

    legacy = tmp_path / "legacy.toml"
    legacy.write_text('[app]\nmode = "simulated"\n', encoding="utf-8")
    loaded = load_config(legacy)
    assert loaded.bus_map.default_route == "M1"
    assert loaded.bus_map.direction == "both"


def test_bus_repository_filters_routes_and_prefers_metro(
    bus_repository: BusNetworkRepository,
) -> None:
    routes = bus_repository.list_routes()
    assert [route.short_name for route in routes] == ["M1", "M2"]
    assert all(route.short_name != "Rail" for route in routes)
    assert bus_repository.resolve_route("m1").id == "M1_TEST"
    assert bus_repository.resolve_route("unknown").short_name == "M1"


def test_schematic_direction_filter_coverage_stops_and_parallel_lanes(
    bus_repository: BusNetworkRepository,
) -> None:
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    route = bus_repository.resolve_route("M1")
    repository = bus_repository.transit_repository(route.id)
    geographic = repository.build_network()
    active = repository.active_trips(now)
    network, trips, major = build_bus_schematic(
        geographic,
        active,
        route,
        direction=BusDirection.BOTH,
        map_width=90,
        route_coverage=0.70,
        lane_spacing=4,
        station_spacing=12,
    )
    assert {trip.direction_id for trip in trips} == {0, 1}
    assert len(network.shapes) == 2
    shapes = tuple(network.shapes.values())
    assert distance(shapes[0].points[0], shapes[1].points[-1]) >= 3
    all_x = [point.x for shape in shapes for point in shape.points]
    assert max(all_x) - min(all_x) >= 80
    assert 2 <= len(major) <= 6
    assert all(network.stations[station_id].interchange for station_id in major)

    inbound = _presenter(bus_repository, direction=BusDirection.INBOUND).prepare(now)
    outbound = _presenter(bus_repository, direction=BusDirection.OUTBOUND).prepare(now)
    assert {trip.direction_id for trip in inbound.trips} == {0}
    assert {trip.direction_id for trip in outbound.trips} == {1}


def test_polyline_simplification_keeps_endpoints() -> None:
    points = (Point(0, 0), Point(2, 0.05), Point(4, 0), Point(6, 3))
    simplified = simplify_polyline(points, 0.1)
    assert simplified[0] == points[0]
    assert simplified[-1] == points[-1]
    assert len(simplified) < len(points)


def test_scheduled_motion_dwell_delay_and_midnight(
    bus_repository: BusNetworkRepository,
) -> None:
    presenter = _presenter(bus_repository)
    moving_time = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    moving = presenter.prepare(moving_time)
    assert moving.status == "Estimated"
    assert {bus.marker.trip_id for bus in moving.markers}.issuperset({"M1_IN", "M1_OUT"})
    first = next(bus for bus in moving.markers if bus.marker.trip_id == "M1_IN")
    later = presenter.prepare(moving_time + timedelta(seconds=30))
    repeated = next(bus for bus in later.markers if bus.marker.trip_id == "M1_IN")
    assert repeated.id == first.id
    assert repeated.marker.position != first.marker.position

    dwell = presenter.prepare(datetime(2026, 8, 18, 7, 23, 10, tzinfo=BRISBANE))
    assert next(bus for bus in dwell.markers if bus.marker.trip_id == "M1_IN").dwelling

    delayed = presenter.prepare(
        moving_time,
        _snapshot(moving_time, delays=(TripDelay("M1_IN", 120),)),
    )
    delayed_bus = next(bus for bus in delayed.markers if bus.marker.trip_id == "M1_IN")
    assert delayed_bus.marker.delay_seconds == 120
    assert delayed_bus.marker.predicted_arrival == (
        delayed_bus.marker.scheduled_arrival + timedelta(seconds=120)
    )

    after_midnight = datetime(2026, 8, 19, 1, 2, tzinfo=BRISBANE)
    night = presenter.prepare(after_midnight)
    assert any(bus.marker.trip_id == "M1_NIGHT" for bus in night.markers)


def test_live_gps_mixed_stale_expiry_and_implausible_rejection(
    bus_repository: BusNetworkRepository,
) -> None:
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    presenter = _presenter(bus_repository)
    mixed = presenter.prepare(now, _snapshot(now, vehicle_age=5))
    live = next(bus for bus in mixed.markers if bus.marker.trip_id == "M1_IN")
    assert mixed.status == "Mixed"
    assert live.marker.source == TrainPositionSource.LIVE_GPS
    assert not live.marker.stale

    stale = presenter.prepare(now, _snapshot(now, vehicle_age=100))
    assert next(bus for bus in stale.markers if bus.marker.trip_id == "M1_IN").marker.stale
    expired = presenter.prepare(now, _snapshot(now, vehicle_age=301))
    assert all(
        bus.marker.source == TrainPositionSource.SCHEDULED_ESTIMATE for bus in expired.markers
    )

    route = bus_repository.resolve_route("M1")
    repository = bus_repository.transit_repository(route.id)
    network = repository.build_network()
    trips = repository.active_trips(now)
    tracker = VehicleTracker(stale_seconds=90, expiry_seconds=300)
    distant = VehiclePosition("lost", "M1_IN", -28.5, 154.0, now)
    assert tracker.markers(network, trips, (distant,), now) == ()

    forward = VehiclePosition("progress", "M1_IN", -27.5, 153.045, now)
    backward = VehiclePosition("progress", "M1_IN", -27.575, 153.1, now + timedelta(seconds=1))
    first_position = tracker.markers(network, trips, (forward,), now)[0].position
    clamped_position = tracker.markers(network, trips, (backward,), now + timedelta(seconds=1))[
        0
    ].position
    assert distance(clamped_position, first_position) == pytest.approx(0, abs=1e-8)


def test_bus_collision_offsets_and_hover_pin_clear_behavior(
    bus_repository: BusNetworkRepository,
) -> None:
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    marker = _presenter(bus_repository).prepare(now).markers[0].marker
    duplicate = marker.__class__(
        "duplicate",
        marker.trip_id,
        marker.vehicle_id,
        marker.route_id,
        marker.route_name,
        marker.destination,
        marker.position,
        marker.source,
        marker.previous_station,
        marker.next_station,
        marker.scheduled_arrival,
    )
    resolved = resolve_marker_overlaps((marker, duplicate), 3)
    assert resolved == resolve_marker_overlaps((duplicate, marker), 3)
    assert resolved[0].position != resolved[1].position

    selection = TrainSelection()
    selection.select_automatically(marker.id)
    selection.hover(duplicate.id)
    assert selection.active_id == duplicate.id
    selection.pin(marker.id)
    selection.hover(None)
    assert selection.active_id == marker.id
    selection.clear_pin()
    assert selection.active_id == marker.id


@pytest.mark.parametrize("size", ["5x3", "6x4", "7x5"])
def test_bus_sprite_sizes_sources_direction_and_state(size: str) -> None:
    image = Image.new("RGB", (24, 16), "black")
    bounds = draw_bus_sprite(
        image,
        Point(12, 8),
        (228, 99, 164),
        size=size,
        direction_id=0,
        source=TrainPositionSource.LIVE_GPS,
        selected=True,
        dwelling=True,
        animation_frame=0,
    )
    width, height = (int(value) for value in size.split("x"))
    assert bounds[2] - bounds[0] + 1 == width
    assert bounds[3] - bounds[1] + 1 == height
    assert image.getpixel((bounds[2], bounds[1] + 1)) == (255, 255, 210)

    estimated = Image.new("RGB", (24, 16), "black")
    reverse = draw_bus_sprite(
        estimated,
        Point(12, 8),
        (228, 99, 164),
        size=size,
        direction_id=1,
        source=TrainPositionSource.SCHEDULED_ESTIMATE,
        stale=True,
    )
    assert estimated.getpixel((reverse[0], reverse[1] + 1)) == (255, 255, 210)
    assert estimated.tobytes() != image.tobytes()


def test_native_renderer_empty_then_selected_panel_is_deterministic(
    bus_repository: BusNetworkRepository,
) -> None:
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    scene = _presenter(bus_repository).prepare(now)
    renderer = BusMapRenderer()
    options = BusRenderOptions(animation_frame=3)
    empty = renderer.render(scene, options=options)
    assert empty.size == (128, 64)
    background = ImageColor.getrgb(options.background)
    assert set(empty.crop((90, 0, 128, 64)).get_flattened_data()) == {background}

    selected = renderer.render(
        scene,
        options=BusRenderOptions(
            selected_bus_id=scene.markers[0].id,
            animation_frame=3,
        ),
    )
    assert set(selected.crop((90, 0, 128, 64)).get_flattened_data()) != {background}
    assert empty.tobytes() != selected.tobytes()
    assert (
        hashlib.sha256(empty.tobytes()).digest()
        == hashlib.sha256(renderer.render(scene, options=options).tobytes()).digest()
    )

    preview = renderer.render(scene, (1280, 640), options=options)
    assert preview.size == (1280, 640)
    assert preview.resize((128, 64), Image.Resampling.NEAREST).tobytes() == empty.tobytes()


def test_no_service_scene_keeps_information_panel_empty(
    bus_repository: BusNetworkRepository,
) -> None:
    scene = _presenter(bus_repository).prepare(datetime(2026, 8, 18, 12, 0, tzinfo=BRISBANE))
    assert scene.status == "No service"
    assert scene.markers == ()
    image = BusMapRenderer().render(scene)
    background = ImageColor.getrgb("#05080d")
    assert set(image.crop((90, 0, 128, 64)).get_flattened_data()) == {background}

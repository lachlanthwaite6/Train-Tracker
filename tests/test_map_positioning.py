from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from train_tracker.clock import BRISBANE
from train_tracker.gtfs.importer import import_gtfs
from train_tracker.map.geometry import distance
from train_tracker.map.interpolation import scheduled_marker, scheduled_markers
from train_tracker.map.models import GeoPoint, Point, TrainPositionSource
from train_tracker.map.network_repository import RailNetworkRepository
from train_tracker.map.projection import LocalProjection, MapViewport
from train_tracker.map.vehicle_tracker import VehicleTracker
from train_tracker.models import VehiclePosition


@pytest.fixture
def rail_data(rail_gtfs_zip: Path, tmp_path: Path):  # type: ignore[no-untyped-def]
    database = tmp_path / "rail.sqlite3"
    import_gtfs(rail_gtfs_zip, database)
    repository = RailNetworkRepository(database)
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    return repository.build_network(), repository.active_trips(now)


def test_local_projection_round_trip() -> None:
    projection = LocalProjection(GeoPoint(-27.47, 153.03))
    projected = projection.project(-27.485, 152.992)
    restored = projection.unproject(projected)
    assert restored.latitude == pytest.approx(-27.485)
    assert restored.longitude == pytest.approx(152.992)


def test_viewport_fit_transform_zoom_and_pan() -> None:
    viewport = MapViewport.fit((0, 0, 1000, 500), 800, 600, 40)
    point = Point(300, 200)
    assert viewport.screen_to_world(viewport.world_to_screen(point)).x == pytest.approx(point.x)
    before = viewport.screen_to_world(Point(400, 300))
    viewport.zoom_at(2, Point(400, 300))
    after = viewport.screen_to_world(Point(400, 300))
    assert before == after
    old_center = viewport.center
    viewport.pan(20, -10)
    assert viewport.center != old_center


def test_scheduled_interpolation_dwell_and_stable_identity(rail_data) -> None:  # type: ignore[no-untyped-def]
    network, trips = rail_data
    trip = next(
        item
        for item in trips
        if item.id == "B1" and item.service_date == datetime(2026, 8, 18).date()
    )
    moving = scheduled_marker(network, trip, datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE))
    assert moving is not None
    assert moving.source == TrainPositionSource.SCHEDULED_ESTIMATE
    assert moving.previous_station == "Toowong station"
    assert moving.next_station == "Central station"
    repeated = scheduled_marker(network, trip, datetime(2026, 8, 18, 7, 26, tzinfo=BRISBANE))
    assert repeated is not None and repeated.id == moving.id
    assert repeated.position != moving.position
    delayed = scheduled_marker(
        network,
        trip,
        datetime(2026, 8, 18, 7, 27, tzinfo=BRISBANE),
        delay_seconds=120,
    )
    assert delayed is not None and delayed.position == moving.position
    assert delayed.delay_seconds == 120
    assert delayed.scheduled_arrival is not None
    assert delayed.predicted_arrival == delayed.scheduled_arrival + timedelta(seconds=120)
    dwell = scheduled_marker(network, trip, datetime(2026, 8, 18, 7, 30, 30, tzinfo=BRISBANE))
    assert dwell is not None
    assert dwell.position == network.stations["place_cen"].position


def test_multiple_trains_opposite_directions_and_midnight(
    rail_gtfs_zip: Path, tmp_path: Path
) -> None:
    database = tmp_path / "rail.sqlite3"
    import_gtfs(rail_gtfs_zip, database)
    repository = RailNetworkRepository(database)
    network = repository.build_network()
    morning = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    markers = scheduled_markers(network, repository.active_trips(morning), morning)
    assert len(markers) >= 3
    assert {marker.trip_id for marker in markers}.issuperset({"B1", "B2", "G1"})
    b1 = next(marker for marker in markers if marker.trip_id == "B1")
    b2 = next(marker for marker in markers if marker.trip_id == "B2")
    assert distance(b1.position, b2.position) > 10
    after_midnight = datetime(2026, 8, 19, 1, 10, tzinfo=BRISBANE)
    night = scheduled_markers(network, repository.active_trips(after_midnight), after_midnight)
    assert any(marker.trip_id == "GNIGHT" for marker in night)


def test_gps_snapping_stale_expiry_and_implausible_rejection(rail_data) -> None:  # type: ignore[no-untyped-def]
    network, trips = rail_data
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    tracker = VehicleTracker(stale_seconds=90, expiry_seconds=300, snap_metres=1000)
    fresh = VehiclePosition("vehicle-1", "B1", -27.475, 153.008, now)
    markers = tracker.markers(network, trips, (fresh,), now)
    assert len(markers) == 1
    assert markers[0].source == TrainPositionSource.LIVE_GPS
    assert not markers[0].stale
    stale = VehiclePosition("vehicle-1", "B1", -27.475, 153.008, now - timedelta(seconds=100))
    assert tracker.markers(network, trips, (stale,), now)[0].stale
    expired = VehiclePosition("vehicle-1", "B1", -27.475, 153.008, now - timedelta(seconds=301))
    assert tracker.markers(network, trips, (expired,), now) == ()
    distant = VehiclePosition("vehicle-2", "B1", -28.5, 154.0, now)
    assert tracker.markers(network, trips, (distant,), now) == ()

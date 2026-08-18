from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from train_tracker.clock import BRISBANE
from train_tracker.gtfs.importer import SCHEMA_VERSION, import_gtfs
from train_tracker.gtfs.repository import GTFSRepository
from train_tracker.map.network_repository import RailNetworkRepository


def test_imports_map_schema_shapes_and_trip_geometry(rail_gtfs_zip: Path, tmp_path: Path) -> None:
    database = tmp_path / "rail.sqlite3"
    counts = import_gtfs(rail_gtfs_zip, database)
    assert counts["shapes"] == 16
    repository = GTFSRepository(database)
    assert repository.schema_version() == SCHEMA_VERSION
    with sqlite3.connect(database) as connection:
        shape_id = connection.execute("SELECT shape_id FROM trips WHERE trip_id='B1'").fetchone()[0]
        shape_distance = connection.execute(
            "SELECT shape_dist_traveled FROM stop_times WHERE trip_id='B1' "
            "ORDER BY stop_sequence LIMIT 1"
        ).fetchone()[0]
    assert shape_id == "blue_out"
    assert shape_distance == 0


def test_old_database_has_actionable_reimport_error(tmp_path: Path) -> None:
    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO metadata VALUES('schema_version', '1')")
    with pytest.raises(RuntimeError, match="must be reimported"):
        GTFSRepository(database).require_map_schema()


def test_network_filters_bus_and_aggregates_platforms(rail_gtfs_zip: Path, tmp_path: Path) -> None:
    database = tmp_path / "rail.sqlite3"
    import_gtfs(rail_gtfs_zip, database)
    network = RailNetworkRepository(database).build_network()
    assert set(network.routes) == {"R_BLUE", "R_GOLD"}
    assert "R_BUS" not in network.routes
    assert "place_twgsta" in network.stations
    assert "TWG1" not in network.stations
    assert network.stations["place_cen"].interchange
    assert set(network.stations["place_cen"].route_ids) == {"R_BLUE", "R_GOLD"}
    assert network.routes["R_BLUE"].color == "#3B82F6"
    assert network.routes["R_BLUE"].source_route_ids == ("R_BLUE",)


def test_shapes_both_directions_and_active_trips(rail_gtfs_zip: Path, tmp_path: Path) -> None:
    database = tmp_path / "rail.sqlite3"
    import_gtfs(rail_gtfs_zip, database)
    repository = RailNetworkRepository(database)
    network = repository.build_network()
    assert {"blue_out", "blue_in"}.issubset(network.shapes)
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    trips = repository.active_trips(now)
    assert {"B1", "B2", "G1", "GNIGHT"}.issubset({trip.id for trip in trips})


def test_missing_shapes_creates_fallback_geometry(tiny_gtfs_zip: Path, tmp_path: Path) -> None:
    database = tmp_path / "tiny.sqlite3"
    import_gtfs(tiny_gtfs_zip, database)
    network = RailNetworkRepository(database).build_network()
    assert network.routes["R1"].shape_ids
    shape = network.shapes[network.routes["R1"].shape_ids[0]]
    assert len(shape.points) == 2

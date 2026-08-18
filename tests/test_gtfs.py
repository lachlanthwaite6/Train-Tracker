from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from train_tracker.clock import BRISBANE
from train_tracker.gtfs.importer import import_gtfs
from train_tracker.gtfs.repository import GTFSRepository, parse_gtfs_time


def test_gtfs_time_supports_values_after_midnight() -> None:
    service_date = datetime(2026, 8, 18, tzinfo=BRISBANE).date()
    parsed = parse_gtfs_time(service_date, "25:05:00")
    assert parsed.day == 19
    assert parsed.hour == 1 and parsed.minute == 5


def test_gtfs_import_and_scheduled_query(tiny_gtfs_zip: Path, tmp_path: Path) -> None:
    database = tmp_path / "tiny.sqlite3"
    counts = import_gtfs(tiny_gtfs_zip, database)
    assert counts["stops"] == 2
    assert counts["stop_times"] == 3
    repository = GTFSRepository(database)
    stops = repository.list_stops("Central")
    assert stops[0].id == "S1"
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    departures = repository.scheduled_departures("S1", now)
    assert departures[0].trip_id == "T1"
    assert departures[0].platform == "2"


def test_gtfs_previous_service_day_midnight_rollover(tiny_gtfs_zip: Path, tmp_path: Path) -> None:
    database = tmp_path / "tiny.sqlite3"
    import_gtfs(tiny_gtfs_zip, database)
    repository = GTFSRepository(database)
    now = datetime(2026, 8, 19, 1, 0, tzinfo=BRISBANE)
    departures = repository.scheduled_departures("S1", now)
    assert any(item.trip_id == "T2" and item.scheduled_time.day == 19 for item in departures)


def test_parent_station_aggregates_platform_departures(tiny_gtfs_zip: Path, tmp_path: Path) -> None:
    database = tmp_path / "tiny.sqlite3"
    import_gtfs(tiny_gtfs_zip, database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO stops "
            "(stop_id, stop_name, location_type, wheelchair_boarding) "
            "VALUES ('P1', 'Tiny parent station', 1, 1)"
        )
        connection.execute("UPDATE stops SET parent_station = 'P1' WHERE stop_id = 'S1'")

    repository = GTFSRepository(database)
    stations = repository.list_stops(stations_only=True)
    assert [station.id for station in stations] == ["P1"]

    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    departures = repository.scheduled_departures("P1", now)
    assert departures[0].trip_id == "T1"
    assert departures[0].stop_id == "S1"
    assert departures[0].platform == "2"

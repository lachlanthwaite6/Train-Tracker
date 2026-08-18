from __future__ import annotations

import csv
import io
import logging
import sqlite3
import zipfile
from collections.abc import Iterable
from pathlib import Path

LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = 2

TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "stops.txt": (
        "stops",
        (
            "stop_id",
            "stop_name",
            "stop_lat",
            "stop_lon",
            "location_type",
            "parent_station",
            "platform_code",
            "wheelchair_boarding",
        ),
    ),
    "routes.txt": (
        "routes",
        (
            "route_id",
            "route_short_name",
            "route_long_name",
            "route_type",
            "route_color",
            "route_text_color",
        ),
    ),
    "trips.txt": (
        "trips",
        (
            "route_id",
            "service_id",
            "trip_id",
            "trip_headsign",
            "direction_id",
            "shape_id",
            "wheelchair_accessible",
        ),
    ),
    "stop_times.txt": (
        "stop_times",
        (
            "trip_id",
            "arrival_time",
            "departure_time",
            "stop_id",
            "stop_sequence",
            "pickup_type",
            "drop_off_type",
            "shape_dist_traveled",
        ),
    ),
    "shapes.txt": (
        "shapes",
        (
            "shape_id",
            "shape_pt_lat",
            "shape_pt_lon",
            "shape_pt_sequence",
            "shape_dist_traveled",
        ),
    ),
    "calendar.txt": (
        "calendar",
        (
            "service_id",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
            "start_date",
            "end_date",
        ),
    ),
    "calendar_dates.txt": (
        "calendar_dates",
        ("service_id", "date", "exception_type"),
    ),
}

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=OFF;
CREATE TABLE stops (
  stop_id TEXT PRIMARY KEY, stop_name TEXT NOT NULL, stop_lat REAL, stop_lon REAL,
  location_type INTEGER, parent_station TEXT, platform_code TEXT,
  wheelchair_boarding INTEGER
);
CREATE TABLE routes (
  route_id TEXT PRIMARY KEY, route_short_name TEXT, route_long_name TEXT,
  route_type INTEGER, route_color TEXT, route_text_color TEXT
);
CREATE TABLE trips (
  route_id TEXT NOT NULL, service_id TEXT NOT NULL, trip_id TEXT PRIMARY KEY,
  trip_headsign TEXT, direction_id INTEGER, shape_id TEXT,
  wheelchair_accessible INTEGER
);
CREATE TABLE stop_times (
  trip_id TEXT NOT NULL, arrival_time TEXT, departure_time TEXT NOT NULL,
  stop_id TEXT NOT NULL, stop_sequence INTEGER NOT NULL, pickup_type INTEGER,
  drop_off_type INTEGER, shape_dist_traveled REAL
);
CREATE TABLE shapes (
  shape_id TEXT NOT NULL, shape_pt_lat REAL NOT NULL, shape_pt_lon REAL NOT NULL,
  shape_pt_sequence INTEGER NOT NULL, shape_dist_traveled REAL,
  PRIMARY KEY(shape_id, shape_pt_sequence)
);
CREATE TABLE calendar (
  service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER, wednesday INTEGER,
  thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER,
  start_date TEXT, end_date TEXT
);
CREATE TABLE calendar_dates (
  service_id TEXT NOT NULL, date TEXT NOT NULL, exception_type INTEGER NOT NULL,
  PRIMARY KEY(service_id, date)
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

INDEXES = """
CREATE INDEX idx_stop_times_stop_departure ON stop_times(stop_id, departure_time);
CREATE INDEX idx_stop_times_trip_sequence ON stop_times(trip_id, stop_sequence);
CREATE INDEX idx_trips_service ON trips(service_id);
CREATE INDEX idx_trips_route ON trips(route_id);
CREATE INDEX idx_trips_shape ON trips(shape_id);
CREATE INDEX idx_shapes_id_sequence ON shapes(shape_id, shape_pt_sequence);
CREATE INDEX idx_calendar_dates_date ON calendar_dates(date, exception_type);
CREATE INDEX idx_stops_name ON stops(stop_name COLLATE NOCASE);
ANALYZE;
"""


def _rows_from_member(archive: zipfile.ZipFile, filename: str) -> Iterable[dict[str, str]]:
    with (
        archive.open(filename) as binary,
        io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as text,
    ):
        yield from csv.DictReader(text)


def import_gtfs(zip_path: str | Path, database_path: str | Path) -> dict[str, int]:
    source = Path(zip_path)
    target = Path(database_path)
    if not source.exists():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(target.suffix + ".new")
    if staging.exists():
        staging.unlink()
    counts: dict[str, int] = {}
    connection = sqlite3.connect(staging)
    try:
        connection.executescript(SCHEMA)
        with zipfile.ZipFile(source) as archive:
            available = set(archive.namelist())
            required = {"stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}
            missing = required - available
            if missing:
                raise ValueError(f"GTFS archive is missing: {', '.join(sorted(missing))}")
            for filename, (table, columns) in TABLES.items():
                if filename not in available:
                    counts[table] = 0
                    continue
                placeholders = ",".join("?" for _ in columns)
                quoted_columns = ",".join(columns)
                statement = (
                    f"INSERT OR REPLACE INTO {table} ({quoted_columns}) VALUES ({placeholders})"
                )
                batch: list[tuple[str | None, ...]] = []
                count = 0
                for row in _rows_from_member(archive, filename):
                    batch.append(tuple(row.get(column) or None for column in columns))
                    if len(batch) >= 10_000:
                        connection.executemany(statement, batch)
                        count += len(batch)
                        batch.clear()
                if batch:
                    connection.executemany(statement, batch)
                    count += len(batch)
                counts[table] = count
                LOGGER.info("Imported GTFS table", extra={"table": table, "rows": count})
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES('source_zip', ?)", (str(source),)
        )
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.executescript(INDEXES)
        connection.commit()
    except Exception:
        connection.close()
        staging.unlink(missing_ok=True)
        raise
    else:
        connection.close()
        staging.replace(target)
    return counts

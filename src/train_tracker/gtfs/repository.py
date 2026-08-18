from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from train_tracker.clock import BRISBANE
from train_tracker.gtfs.importer import SCHEMA_VERSION
from train_tracker.models import Departure, RealtimeState, Stop


def parse_gtfs_time(service_date: date, value: str, timezone: ZoneInfo = BRISBANE) -> datetime:
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return datetime.combine(service_date, time(0), timezone) + timedelta(
        hours=hours, minutes=minutes, seconds=seconds
    )


class GTFSRepository:
    def __init__(self, database_path: str | Path, timezone: ZoneInfo = BRISBANE) -> None:
        self.database_path = Path(database_path)
        self.timezone = timezone

    def _connect(self) -> sqlite3.Connection:
        if not self.database_path.exists():
            raise FileNotFoundError(
                f"GTFS database not found: {self.database_path}. Run import-gtfs first."
            )
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def schema_version(self) -> int:
        with self._connect() as connection:
            try:
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()
            except sqlite3.OperationalError:
                return 1
        return int(row[0]) if row is not None else 1

    def require_map_schema(self) -> None:
        if self.schema_version() < SCHEMA_VERSION:
            raise RuntimeError(
                "GTFS database must be reimported to enable the network map.\n"
                "Run:\n"
                "  train-tracker import-gtfs data/SEQ_GTFS.zip "
                "--database data/translink.sqlite3"
            )

    def list_stops(
        self,
        query: str = "",
        limit: int = 100,
        *,
        stations_only: bool = False,
    ) -> tuple[Stop, ...]:
        sql = "SELECT * FROM stops"
        clauses: list[str] = []
        parameters: list[object] = []
        if query:
            clauses.append("stop_name LIKE ? COLLATE NOCASE")
            parameters.append(f"%{query}%")
        if stations_only:
            clauses.append("location_type = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY stop_name LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return tuple(
            Stop(
                id=row["stop_id"],
                name=row["stop_name"],
                latitude=row["stop_lat"],
                longitude=row["stop_lon"],
                parent_station_id=row["parent_station"],
                wheelchair_boarding=(
                    bool(row["wheelchair_boarding"])
                    if row["wheelchair_boarding"] not in (None, 0)
                    else None
                ),
            )
            for row in rows
        )

    def get_stop(self, stop_id: str) -> Stop:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM stops WHERE stop_id = ?", (stop_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown stop_id: {stop_id}")
        return Stop(
            id=row["stop_id"],
            name=row["stop_name"],
            latitude=row["stop_lat"],
            longitude=row["stop_lon"],
            parent_station_id=row["parent_station"],
        )

    def _active_services(self, connection: sqlite3.Connection, service_date: date) -> set[str]:
        day_column = service_date.strftime("%A").lower()
        value = service_date.strftime("%Y%m%d")
        services = {
            row[0]
            for row in connection.execute(
                f"SELECT service_id FROM calendar WHERE {day_column} = 1 "
                "AND start_date <= ? AND end_date >= ?",
                (value, value),
            )
        }
        for row in connection.execute(
            "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?", (value,)
        ):
            if row[1] == 1:
                services.add(row[0])
            elif row[1] == 2:
                services.discard(row[0])
        return services

    def scheduled_departures(
        self,
        stop_id: str,
        now: datetime,
        *,
        horizon: timedelta = timedelta(hours=3),
        limit: int = 100,
    ) -> tuple[Departure, ...]:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        rows_with_dates: list[tuple[sqlite3.Row, date]] = []
        with self._connect() as connection:
            child_rows = connection.execute(
                "SELECT stop_id FROM stops WHERE parent_station = ? ORDER BY stop_id",
                (stop_id,),
            ).fetchall()
            stop_ids = tuple(row[0] for row in child_rows) or (stop_id,)
            stop_placeholders = ",".join("?" for _ in stop_ids)
            for service_date in (now.date() - timedelta(days=1), now.date()):
                services = self._active_services(connection, service_date)
                if not services:
                    continue
                placeholders = ",".join("?" for _ in services)
                rows = connection.execute(
                    "SELECT st.*, t.route_id, t.trip_headsign, t.wheelchair_accessible, "
                    "r.route_short_name, r.route_long_name, s.platform_code "
                    "FROM stop_times st JOIN trips t ON t.trip_id = st.trip_id "
                    "JOIN routes r ON r.route_id = t.route_id "
                    "JOIN stops s ON s.stop_id = st.stop_id "
                    f"WHERE st.stop_id IN ({stop_placeholders}) "
                    f"AND t.service_id IN ({placeholders})",
                    (*stop_ids, *services),
                ).fetchall()
                rows_with_dates.extend((row, service_date) for row in rows)
        departures: list[Departure] = []
        upper = now + horizon
        for row, service_date in rows_with_dates:
            scheduled = parse_gtfs_time(service_date, row["departure_time"], self.timezone)
            if scheduled < now - timedelta(minutes=1) or scheduled > upper:
                continue
            route_name = row["route_short_name"] or row["route_long_name"] or row["route_id"]
            departures.append(
                Departure(
                    id=f"{row['trip_id']}:{row['stop_id']}:{row['stop_sequence']}",
                    trip_id=row["trip_id"],
                    stop_id=row["stop_id"],
                    route_id=row["route_id"],
                    route_name=route_name,
                    destination=row["trip_headsign"] or row["route_long_name"] or "Service",
                    scheduled_time=scheduled,
                    platform=row["platform_code"],
                    realtime_state=RealtimeState.SCHEDULED,
                    provider="translink",
                    wheelchair_accessible=(
                        bool(row["wheelchair_accessible"])
                        if row["wheelchair_accessible"] not in (None, 0)
                        else None
                    ),
                    stop_sequence=int(row["stop_sequence"]),
                )
            )
        departures.sort(key=lambda item: item.scheduled_time)
        return tuple(departures[:limit])

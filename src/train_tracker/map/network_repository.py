from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from train_tracker.gtfs.repository import GTFSRepository
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
from train_tracker.map.projection import LocalProjection

# GTFS route_type 2 is conventional heavy rail. The 100-series values are the
# corresponding extended rail categories; tram/light-rail values are omitted.
HEAVY_RAIL_TYPES = frozenset({2, 100, 101, 102, 103, 105, 106, 107, 109})
DEFAULT_ROUTE_COLORS = ("#e35d5b", "#5da9e9", "#56c596", "#f6c85f", "#b084e8", "#ef8a47")


def parse_time_seconds(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


class RailNetworkRepository:
    """Builds and caches map-ready rail data from a versioned GTFS database."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        route_types: frozenset[int] | None = None,
        route_ids: tuple[str, ...] = (),
    ) -> None:
        self.database_path = Path(database_path)
        self.gtfs = GTFSRepository(database_path)
        self._network: RailNetwork | None = None
        self._trip_cache: dict[date, tuple[RailTrip, ...]] = {}
        self._source_route_ids: tuple[str, ...] = ()
        self._canonical_by_source: dict[str, str] = {}
        self.route_types = route_types or HEAVY_RAIL_TYPES
        self.requested_route_ids = route_ids

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def build_network(self) -> RailNetwork:
        if self._network is not None:
            return self._network
        self.gtfs.require_map_schema()
        with self._connect() as connection:
            type_placeholders = ",".join("?" for _ in self.route_types)
            route_sql = f"SELECT * FROM routes WHERE route_type IN ({type_placeholders})"
            route_parameters: tuple[object, ...] = tuple(sorted(self.route_types))
            if self.requested_route_ids:
                route_placeholders = ",".join("?" for _ in self.requested_route_ids)
                route_sql += f" AND route_id IN ({route_placeholders})"
                route_parameters += self.requested_route_ids
            route_sql += " ORDER BY route_id"
            route_rows = connection.execute(route_sql, route_parameters).fetchall()
            if not route_rows:
                raise RuntimeError("Imported GTFS database contains no matching transit routes.")
            route_ids = tuple(str(row["route_id"]) for row in route_rows)
            self._source_route_ids = route_ids
            color_counts = Counter(str(row["route_color"] or "") for row in route_rows)
            self._canonical_by_source = {
                str(row["route_id"]): (
                    f"rail:{row['route_color']}"
                    if row["route_color"] and color_counts[str(row["route_color"])] > 1
                    else str(row["route_id"])
                )
                for row in route_rows
            }
            placeholders = ",".join("?" for _ in route_ids)
            stop_rows = connection.execute(
                "SELECT DISTINCT s.* FROM stops s "
                "JOIN stop_times st ON st.stop_id = s.stop_id "
                "JOIN trips t ON t.trip_id = st.trip_id "
                f"WHERE t.route_id IN ({placeholders})",
                route_ids,
            ).fetchall()
            parent_ids = {str(row["parent_station"]) for row in stop_rows if row["parent_station"]}
            if parent_ids:
                parent_placeholders = ",".join("?" for _ in parent_ids)
                parent_rows = connection.execute(
                    f"SELECT * FROM stops WHERE stop_id IN ({parent_placeholders})",
                    tuple(parent_ids),
                ).fetchall()
            else:
                parent_rows = []
            all_stop_rows = {str(row["stop_id"]): row for row in (*stop_rows, *parent_rows)}
            coords = [
                (float(row["stop_lat"]), float(row["stop_lon"]))
                for row in all_stop_rows.values()
                if row["stop_lat"] is not None and row["stop_lon"] is not None
            ]
            if not coords:
                raise RuntimeError("Rail stops have no coordinates; the network map cannot render.")
            origin = GeoPoint(
                sum(item[0] for item in coords) / len(coords),
                sum(item[1] for item in coords) / len(coords),
            )
            projection = LocalProjection(origin)
            child_to_station: dict[str, str] = {}
            grouped_children: dict[str, list[sqlite3.Row]] = defaultdict(list)
            for row in stop_rows:
                station_id = str(row["parent_station"] or row["stop_id"])
                child_to_station[str(row["stop_id"])] = station_id
                grouped_children[station_id].append(row)

            station_routes: dict[str, set[str]] = defaultdict(set)
            station_sequences: dict[str, list[str]] = defaultdict(list)
            trip_rows = connection.execute(
                "SELECT t.trip_id, t.route_id, t.shape_id, st.stop_id, st.stop_sequence "
                "FROM trips t JOIN stop_times st ON st.trip_id = t.trip_id "
                f"WHERE t.route_id IN ({placeholders}) "
                "ORDER BY t.trip_id, st.stop_sequence",
                route_ids,
            ).fetchall()
            trip_stations: dict[str, list[str]] = defaultdict(list)
            trip_route: dict[str, str] = {}
            trip_shape: dict[str, str | None] = {}
            for row in trip_rows:
                station_id = child_to_station.get(str(row["stop_id"]), str(row["stop_id"]))
                trip_id = str(row["trip_id"])
                route_id = self._canonical_by_source[str(row["route_id"])]
                trip_route[trip_id] = route_id
                trip_shape[trip_id] = str(row["shape_id"]) if row["shape_id"] else None
                station_routes[station_id].add(route_id)
                if not trip_stations[trip_id] or trip_stations[trip_id][-1] != station_id:
                    trip_stations[trip_id].append(station_id)
                if station_id not in station_sequences[route_id]:
                    station_sequences[route_id].append(station_id)

            stations: dict[str, RailStation] = {}
            for station_id, children in grouped_children.items():
                parent = all_stop_rows.get(station_id)
                coordinate_rows = (
                    [parent] if parent is not None and parent["stop_lat"] is not None else children
                )
                usable = [
                    row
                    for row in coordinate_rows
                    if row is not None and row["stop_lat"] is not None
                ]
                if not usable:
                    continue
                latitude = sum(float(row["stop_lat"]) for row in usable) / len(usable)
                longitude = sum(float(row["stop_lon"]) for row in usable) / len(usable)
                name = str(parent["stop_name"] if parent is not None else children[0]["stop_name"])
                serving = tuple(sorted(station_routes[station_id]))
                stations[station_id] = RailStation(
                    station_id,
                    name,
                    projection.project(latitude, longitude),
                    latitude,
                    longitude,
                    serving,
                    len(serving) > 1,
                )

            shape_rows = connection.execute(
                "SELECT sh.* FROM shapes sh WHERE sh.shape_id IN "
                "(SELECT DISTINCT shape_id FROM trips WHERE shape_id IS NOT NULL "
                f"AND route_id IN ({placeholders})) ORDER BY shape_id, shape_pt_sequence",
                route_ids,
            ).fetchall()
        shape_points: dict[str, list[Point]] = defaultdict(list)
        shape_distances: dict[str, list[float | None]] = defaultdict(list)
        for row in shape_rows:
            shape_id = str(row["shape_id"])
            shape_points[shape_id].append(
                projection.project(float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
            )
            shape_distances[shape_id].append(
                float(row["shape_dist_traveled"])
                if row["shape_dist_traveled"] is not None
                else None
            )
        shapes: dict[str, RailShape] = {
            shape_id: RailShape(shape_id, tuple(points), tuple(shape_distances[shape_id]))
            for shape_id, points in shape_points.items()
            if points
        }
        route_shape_ids: dict[str, list[str]] = defaultdict(list)
        seen_geometry: dict[str, set[tuple[tuple[int, int], ...]]] = defaultdict(set)
        for trip_id, station_ids in trip_stations.items():
            route_id = trip_route[trip_id]
            selected_shape_id = trip_shape[trip_id]
            if selected_shape_id not in shapes:
                fallback_points = tuple(
                    stations[station_id].position
                    for station_id in station_ids
                    if station_id in stations
                )
                if len(fallback_points) < 2:
                    continue
                selected_shape_id = f"fallback:{trip_id}"
                shapes[selected_shape_id] = RailShape(selected_shape_id, fallback_points)
            signature = tuple(
                (round(point.x), round(point.y)) for point in shapes[selected_shape_id].points
            )
            if signature not in seen_geometry[route_id]:
                seen_geometry[route_id].add(signature)
                route_shape_ids[route_id].append(selected_shape_id)

        grouped_route_rows: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in route_rows:
            grouped_route_rows[self._canonical_by_source[str(row["route_id"])]].append(row)
        routes: dict[str, RailRoute] = {}
        for index, (route_id, grouped_rows) in enumerate(grouped_route_rows.items()):
            row = grouped_rows[0]
            color = str(row["route_color"] or "").strip().lstrip("#")
            text_color = str(row["route_text_color"] or "FFFFFF").strip().lstrip("#")
            endpoints: Counter[str] = Counter()
            for route_row in grouped_rows:
                for endpoint in str(route_row["route_long_name"] or "").split(" - "):
                    if endpoint:
                        endpoints[endpoint] += 1
            if len(endpoints) > 1:
                endpoints.pop("Brisbane City", None)
            group_name = " / ".join(item for item, _count in endpoints.most_common(3))
            routes[route_id] = RailRoute(
                route_id,
                group_name or str(row["route_short_name"] or row["route_long_name"] or route_id),
                f"#{color}"
                if len(color) == 6
                else DEFAULT_ROUTE_COLORS[index % len(DEFAULT_ROUTE_COLORS)],
                f"#{text_color}" if len(text_color) == 6 else "#ffffff",
                int(row["route_type"]),
                tuple(route_shape_ids[route_id]),
                tuple(station_sequences[route_id]),
                tuple(str(item["route_id"]) for item in grouped_rows),
            )
        all_points = [station.position for station in stations.values()]
        all_points.extend(point for shape in shapes.values() for point in shape.points)
        bounds = (
            min(point.x for point in all_points),
            min(point.y for point in all_points),
            max(point.x for point in all_points),
            max(point.y for point in all_points),
        )
        self._network = RailNetwork(
            MappingProxyType(routes),
            MappingProxyType(stations),
            MappingProxyType(shapes),
            bounds,
            origin,
        )
        return self._network

    def _active_services(self, connection: sqlite3.Connection, service_date: date) -> set[str]:
        day_column = service_date.strftime("%A").lower()
        encoded = service_date.strftime("%Y%m%d")
        services = {
            str(row[0])
            for row in connection.execute(
                f"SELECT service_id FROM calendar WHERE {day_column} = 1 "
                "AND start_date <= ? AND end_date >= ?",
                (encoded, encoded),
            )
        }
        for service_id, exception_type in connection.execute(
            "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?", (encoded,)
        ):
            if exception_type == 1:
                services.add(str(service_id))
            elif exception_type == 2:
                services.discard(str(service_id))
        return services

    def active_trips(self, now: datetime) -> tuple[RailTrip, ...]:
        network = self.build_network()
        trips: list[RailTrip] = []
        for service_date in (now.date() - timedelta(days=1), now.date()):
            if service_date not in self._trip_cache:
                self._trip_cache[service_date] = self._load_service_trips(service_date, network)
            trips.extend(self._trip_cache[service_date])
        return tuple(trips)

    def _load_service_trips(self, service_date: date, network: RailNetwork) -> tuple[RailTrip, ...]:
        with self._connect() as connection:
            services = self._active_services(connection, service_date)
            if not services:
                return ()
            service_placeholders = ",".join("?" for _ in services)
            route_placeholders = ",".join("?" for _ in self._source_route_ids)
            rows = connection.execute(
                "SELECT t.*, st.arrival_time, st.departure_time, st.stop_id, "
                "st.stop_sequence, st.shape_dist_traveled, s.parent_station, s.stop_name "
                "FROM trips t JOIN stop_times st ON st.trip_id = t.trip_id "
                "JOIN stops s ON s.stop_id = st.stop_id "
                f"WHERE t.service_id IN ({service_placeholders}) "
                f"AND t.route_id IN ({route_placeholders}) "
                "ORDER BY t.trip_id, st.stop_sequence",
                (*services, *self._source_route_ids),
            ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            grouped[str(row["trip_id"])].append(row)
        result: list[RailTrip] = []
        for trip_id, trip_rows in grouped.items():
            first = trip_rows[0]
            stops: list[StopTimeEvent] = []
            for row in trip_rows:
                station_id = str(row["parent_station"] or row["stop_id"])
                station = network.stations.get(station_id)
                if station is None:
                    continue
                arrival = str(row["arrival_time"] or row["departure_time"])
                departure = str(row["departure_time"] or row["arrival_time"])
                stops.append(
                    StopTimeEvent(
                        station_id,
                        station.name,
                        parse_time_seconds(arrival),
                        parse_time_seconds(departure),
                        int(row["stop_sequence"]),
                        float(row["shape_dist_traveled"])
                        if row["shape_dist_traveled"] is not None
                        else None,
                    )
                )
            if len(stops) >= 2:
                result.append(
                    RailTrip(
                        trip_id,
                        self._canonical_by_source[str(first["route_id"])],
                        str(first["service_id"]),
                        service_date,
                        str(first["trip_headsign"] or stops[-1].station_name),
                        int(first["direction_id"]) if first["direction_id"] is not None else None,
                        str(first["shape_id"]) if first["shape_id"] else None,
                        tuple(stops),
                        str(first["route_id"]),
                    )
                )
        return tuple(result)

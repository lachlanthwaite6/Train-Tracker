from __future__ import annotations

import sqlite3
from pathlib import Path

from train_tracker.bus_map.models import BusRouteChoice
from train_tracker.map.network_repository import RailNetworkRepository

# GTFS route_type 3 is conventional bus. The 700-series values are extended
# bus categories used by some producers.
BUS_ROUTE_TYPES = frozenset({3, *range(700, 717)})


class BusNetworkRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._repositories: dict[str, RailNetworkRepository] = {}

    def list_routes(self) -> tuple[BusRouteChoice, ...]:
        if not self.database_path.exists():
            return ()
        placeholders = ",".join("?" for _ in BUS_ROUTE_TYPES)
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT route_id, route_short_name, route_long_name, route_color "
                f"FROM routes WHERE route_type IN ({placeholders}) "
                "ORDER BY CASE WHEN route_short_name IN ('M1','M2') THEN 0 ELSE 1 END, "
                "route_short_name, route_id",
                tuple(sorted(BUS_ROUTE_TYPES)),
            ).fetchall()
        return tuple(
            BusRouteChoice(
                str(row[0]),
                str(row[1] or row[0]),
                str(row[2] or ""),
                self._color(str(row[3] or "")),
            )
            for row in rows
        )

    def resolve_route(self, requested: str) -> BusRouteChoice:
        routes = self.list_routes()
        if not routes:
            raise RuntimeError("Imported GTFS database contains no bus routes.")
        key = requested.casefold().strip()
        return next(
            (
                route
                for route in routes
                if route.id.casefold() == key or route.short_name.casefold() == key
            ),
            routes[0],
        )

    def transit_repository(self, route_id: str) -> RailNetworkRepository:
        if route_id not in self._repositories:
            self._repositories[route_id] = RailNetworkRepository(
                self.database_path,
                route_types=BUS_ROUTE_TYPES,
                route_ids=(route_id,),
            )
        return self._repositories[route_id]

    @staticmethod
    def _color(value: str) -> str:
        cleaned = value.strip().lstrip("#")
        return f"#{cleaned}" if len(cleaned) == 6 else "#36d7ff"

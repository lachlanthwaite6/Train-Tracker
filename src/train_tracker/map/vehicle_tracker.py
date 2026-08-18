from __future__ import annotations

from datetime import datetime

from train_tracker.map.geometry import point_at_distance, snap_to_polyline
from train_tracker.map.interpolation import scheduled_marker
from train_tracker.map.models import RailNetwork, RailTrip, TrainMarker, TrainPositionSource
from train_tracker.map.projection import LocalProjection
from train_tracker.models import VehiclePosition


class VehicleTracker:
    def __init__(
        self, *, stale_seconds: float = 90, expiry_seconds: float = 300, snap_metres: float = 3000
    ) -> None:
        self.stale_seconds = stale_seconds
        self.expiry_seconds = expiry_seconds
        self.snap_metres = snap_metres
        self._progress: dict[str, float] = {}

    def markers(
        self,
        network: RailNetwork,
        trips: tuple[RailTrip, ...],
        vehicles: tuple[VehiclePosition, ...],
        now: datetime,
    ) -> tuple[TrainMarker, ...]:
        by_trip = {trip.id: trip for trip in trips}
        projection = LocalProjection(network.projection_origin)
        result: list[TrainMarker] = []
        for vehicle in vehicles:
            if vehicle.trip_id is None or vehicle.trip_id not in by_trip:
                continue
            age = max(0.0, (now - vehicle.timestamp).total_seconds()) if vehicle.timestamp else None
            if age is not None and age > self.expiry_seconds:
                continue
            trip = by_trip[vehicle.trip_id]
            shape = network.shapes.get(trip.shape_id or "")
            if shape is None:
                continue
            snapped, offset, along = snap_to_polyline(
                projection.project(vehicle.latitude, vehicle.longitude), shape.points
            )
            if offset > self.snap_metres:
                continue
            previous = self._progress.get(vehicle.id)
            if previous is not None and along + 100 < previous:
                along = previous
                snapped = point_at_distance(shape.points, along)
            self._progress[vehicle.id] = along
            estimate = scheduled_marker(network, trip, now)
            result.append(
                TrainMarker(
                    f"live:{vehicle.id}",
                    trip.id,
                    vehicle.id,
                    trip.route_id,
                    network.routes[trip.route_id].name,
                    trip.destination,
                    snapped,
                    TrainPositionSource.LIVE_GPS,
                    estimate.previous_station if estimate else None,
                    estimate.next_station if estimate else None,
                    estimate.scheduled_arrival if estimate else None,
                    age_seconds=age,
                    stale=age is not None and age > self.stale_seconds,
                    raw_latitude=vehicle.latitude,
                    raw_longitude=vehicle.longitude,
                )
            )
        return tuple(result)

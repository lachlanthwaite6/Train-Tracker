from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta, tzinfo

from train_tracker.map.geometry import cumulative_lengths, point_at_distance, snap_to_polyline
from train_tracker.map.models import Point, RailNetwork, RailTrip, TrainMarker, TrainPositionSource


def trip_datetime(trip: RailTrip, seconds: int, timezone: tzinfo | None) -> datetime:
    # ``timezone`` is passed from an aware datetime's tzinfo; datetime accepts
    # the runtime tzinfo protocol even though its concrete type is intentionally broad here.
    return datetime.combine(trip.service_date, time(0), tzinfo=timezone) + timedelta(
        seconds=seconds
    )


def _shape_position(network: RailNetwork, trip: RailTrip, index: int, progress: float) -> Point:
    previous = trip.stops[index]
    following = trip.stops[index + 1]
    shape = network.shapes.get(trip.shape_id or "")
    if shape is None or len(shape.points) < 2:
        start = network.stations[previous.station_id].position
        end = network.stations[following.station_id].position
        return Point(start.x + (end.x - start.x) * progress, start.y + (end.y - start.y) * progress)
    geometry_lengths = cumulative_lengths(shape.points)
    if (
        previous.shape_distance is not None
        and following.shape_distance is not None
        and shape.distances
        and shape.distances[0] is not None
        and shape.distances[-1] is not None
        and shape.distances[-1] != shape.distances[0]
    ):
        source_start = float(shape.distances[0])
        source_span = float(shape.distances[-1]) - source_start
        from_distance = (
            (previous.shape_distance - source_start) / source_span * geometry_lengths[-1]
        )
        to_distance = (following.shape_distance - source_start) / source_span * geometry_lengths[-1]
    else:
        from_distance = snap_to_polyline(
            network.stations[previous.station_id].position, shape.points
        )[2]
        to_distance = snap_to_polyline(
            network.stations[following.station_id].position, shape.points
        )[2]
        # Reverse-direction trips can use a shape encoded in the opposite order.
        if to_distance < from_distance:
            from_distance, to_distance = to_distance, from_distance
            progress = 1.0 - progress
    return point_at_distance(shape.points, from_distance + (to_distance - from_distance) * progress)


def scheduled_marker(
    network: RailNetwork, trip: RailTrip, now: datetime, delay_seconds: int = 0
) -> TrainMarker | None:
    if now.tzinfo is None or len(trip.stops) < 2:
        return None
    seconds = (
        int((now - datetime.combine(trip.service_date, time(0), now.tzinfo)).total_seconds())
        - delay_seconds
    )
    if seconds < trip.stops[0].arrival_seconds or seconds > trip.stops[-1].departure_seconds:
        return None
    for index, event in enumerate(trip.stops):
        if event.arrival_seconds <= seconds <= event.departure_seconds:
            station = network.stations[event.station_id]
            next_name = trip.stops[index + 1].station_name if index + 1 < len(trip.stops) else None
            return TrainMarker(
                f"scheduled:{trip.id}",
                trip.id,
                None,
                trip.route_id,
                network.routes[trip.route_id].name,
                trip.destination,
                station.position,
                TrainPositionSource.SCHEDULED_ESTIMATE,
                event.station_name,
                next_name,
                trip_datetime(trip, event.arrival_seconds, now.tzinfo),
                predicted_arrival=trip_datetime(
                    trip, event.arrival_seconds + delay_seconds, now.tzinfo
                ),
                delay_seconds=delay_seconds,
            )
        if index + 1 >= len(trip.stops):
            continue
        following = trip.stops[index + 1]
        if event.departure_seconds < seconds < following.arrival_seconds:
            duration = max(1, following.arrival_seconds - event.departure_seconds)
            progress = (seconds - event.departure_seconds) / duration
            return TrainMarker(
                f"scheduled:{trip.id}",
                trip.id,
                None,
                trip.route_id,
                network.routes[trip.route_id].name,
                trip.destination,
                _shape_position(network, trip, index, progress),
                TrainPositionSource.SCHEDULED_ESTIMATE,
                event.station_name,
                following.station_name,
                trip_datetime(trip, following.arrival_seconds, now.tzinfo),
                predicted_arrival=trip_datetime(
                    trip, following.arrival_seconds + delay_seconds, now.tzinfo
                ),
                delay_seconds=delay_seconds,
            )
    return None


def scheduled_markers(
    network: RailNetwork,
    trips: tuple[RailTrip, ...],
    now: datetime,
    delays: Mapping[str, int] | None = None,
) -> tuple[TrainMarker, ...]:
    values = delays or {}
    return tuple(
        marker
        for trip in trips
        if (marker := scheduled_marker(network, trip, now, values.get(trip.id, 0))) is not None
    )

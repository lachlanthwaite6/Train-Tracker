from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from train_tracker.clock import BRISBANE
from train_tracker.models import ServiceAlert, VehiclePosition


@dataclass(frozen=True, slots=True)
class TripRealtimeUpdate:
    trip_id: str
    stop_id: str | None
    stop_sequence: int | None
    predicted_time: datetime | None
    delay_seconds: int | None
    cancelled: bool


@dataclass(frozen=True, slots=True)
class ParsedRealtime:
    timestamp: datetime | None
    trip_updates: tuple[TripRealtimeUpdate, ...] = ()
    vehicles: tuple[VehiclePosition, ...] = ()
    alerts: tuple[ServiceAlert, ...] = ()


def _bindings() -> Any:
    try:
        from google.transit import gtfs_realtime_pb2  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "GTFS-Realtime decoding requires the gtfs-realtime-bindings package"
        ) from exc
    return gtfs_realtime_pb2


def _timestamp(value: int, timezone: ZoneInfo) -> datetime | None:
    return datetime.fromtimestamp(value, timezone) if value else None


def parse_trip_updates(data: bytes, timezone: ZoneInfo = BRISBANE) -> ParsedRealtime:
    module = _bindings()
    feed = module.FeedMessage()
    feed.ParseFromString(data)
    updates: list[TripRealtimeUpdate] = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        update = entity.trip_update
        trip_id = update.trip.trip_id
        cancelled = update.trip.schedule_relationship == module.TripDescriptor.CANCELED
        if update.stop_time_update:
            for stop_update in update.stop_time_update:
                event = (
                    stop_update.departure
                    if stop_update.HasField("departure")
                    else stop_update.arrival
                )
                predicted = _timestamp(event.time, timezone) if event.HasField("time") else None
                delay = event.delay if event.HasField("delay") else None
                updates.append(
                    TripRealtimeUpdate(
                        trip_id=trip_id,
                        stop_id=stop_update.stop_id or None,
                        stop_sequence=(
                            stop_update.stop_sequence
                            if stop_update.HasField("stop_sequence")
                            else None
                        ),
                        predicted_time=predicted,
                        delay_seconds=delay,
                        cancelled=cancelled,
                    )
                )
        else:
            updates.append(TripRealtimeUpdate(trip_id, None, None, None, None, cancelled))
    return ParsedRealtime(_timestamp(feed.header.timestamp, timezone), tuple(updates))


def parse_vehicle_positions(data: bytes, timezone: ZoneInfo = BRISBANE) -> ParsedRealtime:
    module = _bindings()
    feed = module.FeedMessage()
    feed.ParseFromString(data)
    vehicles: list[VehiclePosition] = []
    for entity in feed.entity:
        if not entity.HasField("vehicle") or not entity.vehicle.HasField("position"):
            continue
        vehicle = entity.vehicle
        position = vehicle.position
        vehicles.append(
            VehiclePosition(
                id=vehicle.vehicle.id or entity.id,
                trip_id=vehicle.trip.trip_id or None,
                latitude=position.latitude,
                longitude=position.longitude,
                timestamp=_timestamp(vehicle.timestamp, timezone),
                bearing=position.bearing if position.HasField("bearing") else None,
                speed_mps=position.speed if position.HasField("speed") else None,
            )
        )
    return ParsedRealtime(_timestamp(feed.header.timestamp, timezone), vehicles=tuple(vehicles))


def _translated(value: Any) -> str:
    if not value.translation:
        return ""
    english = next((item.text for item in value.translation if item.language == "en"), None)
    return cast(str, english or value.translation[0].text)


def parse_alerts(data: bytes, timezone: ZoneInfo = BRISBANE) -> ParsedRealtime:
    module = _bindings()
    feed = module.FeedMessage()
    feed.ParseFromString(data)
    alerts: list[ServiceAlert] = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        alerts.append(
            ServiceAlert(
                id=entity.id,
                header=_translated(alert.header_text) or "Service alert",
                description=_translated(alert.description_text),
                route_ids=tuple(item.route_id for item in alert.informed_entity if item.route_id),
                stop_ids=tuple(item.stop_id for item in alert.informed_entity if item.stop_id),
                starts_at=(
                    _timestamp(alert.active_period[0].start, timezone)
                    if alert.active_period and alert.active_period[0].HasField("start")
                    else None
                ),
                ends_at=(
                    _timestamp(alert.active_period[0].end, timezone)
                    if alert.active_period and alert.active_period[0].HasField("end")
                    else None
                ),
            )
        )
    return ParsedRealtime(_timestamp(feed.header.timestamp, timezone), alerts=tuple(alerts))

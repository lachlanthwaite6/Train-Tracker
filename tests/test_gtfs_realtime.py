from __future__ import annotations

from datetime import datetime

from google.transit import gtfs_realtime_pb2

from train_tracker.clock import BRISBANE
from train_tracker.gtfs.realtime import parse_alerts, parse_trip_updates, parse_vehicle_positions


def _feed() -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_776_116_700
    return feed


def test_parse_local_trip_update_protobuf() -> None:
    feed = _feed()
    entity = feed.entity.add()
    entity.id = "update-1"
    entity.trip_update.trip.trip_id = "T1"
    update = entity.trip_update.stop_time_update.add()
    update.stop_id = "S1"
    update.stop_sequence = 1
    update.departure.time = 1_776_117_000
    update.departure.delay = 300
    parsed = parse_trip_updates(feed.SerializeToString())
    assert parsed.trip_updates[0].trip_id == "T1"
    assert parsed.trip_updates[0].delay_seconds == 300
    assert parsed.trip_updates[0].predicted_time == datetime.fromtimestamp(1_776_117_000, BRISBANE)


def test_parse_local_vehicle_and_alert_protobuf() -> None:
    vehicle_feed = _feed()
    entity = vehicle_feed.entity.add()
    entity.id = "vehicle-1"
    entity.vehicle.vehicle.id = "EMU-001"
    entity.vehicle.trip.trip_id = "T1"
    entity.vehicle.position.latitude = -27.47
    entity.vehicle.position.longitude = 153.02
    entity.vehicle.timestamp = 1_776_116_700
    vehicles = parse_vehicle_positions(vehicle_feed.SerializeToString()).vehicles
    assert vehicles[0].id == "EMU-001"

    alert_feed = _feed()
    alert_entity = alert_feed.entity.add()
    alert_entity.id = "alert-1"
    translation = alert_entity.alert.header_text.translation.add()
    translation.text = "Track work"
    translation.language = "en"
    informed = alert_entity.alert.informed_entity.add()
    informed.stop_id = "S1"
    alerts = parse_alerts(alert_feed.SerializeToString()).alerts
    assert alerts[0].header == "Track work"
    assert alerts[0].stop_ids == ("S1",)

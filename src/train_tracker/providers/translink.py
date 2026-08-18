from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from train_tracker.gtfs.realtime import (
    ParsedRealtime,
    TripRealtimeUpdate,
    parse_alerts,
    parse_trip_updates,
    parse_vehicle_positions,
)
from train_tracker.gtfs.repository import GTFSRepository
from train_tracker.models import (
    FeedHealth,
    FeedStatus,
    ProviderSnapshot,
    ServiceAlert,
    Stop,
    TripDelay,
)
from train_tracker.services.departures import merge_realtime
from train_tracker.services.freshness import classify_freshness

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TranslinkEndpoints:
    trip_updates: str
    vehicle_positions: str
    alerts: str


class TranslinkProvider:
    def __init__(
        self,
        database_path: str | Path,
        endpoints: TranslinkEndpoints,
        *,
        poll_seconds: float = 15.0,
        stale_after_seconds: float = 90.0,
        timeout_seconds: float = 10.0,
        retries: int = 3,
    ) -> None:
        self.repository = GTFSRepository(database_path)
        self.endpoints = endpoints
        self.poll_seconds = max(5.0, poll_seconds)
        self.stale_after_seconds = stale_after_seconds
        self.retries = max(1, retries)
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": "qld-train-tracker/0.1 (+local departure board)"},
            follow_redirects=True,
        )
        self._last_poll: datetime | None = None
        self._trip_data: ParsedRealtime | None = None
        self._vehicle_data: ParsedRealtime | None = None
        self._alert_data: ParsedRealtime | None = None
        self._last_error: str | None = None

    @property
    def mode(self) -> str:
        return "live"

    def list_stops(self) -> tuple[Stop, ...]:
        # Parent stations provide one useful selector while their platform
        # children are aggregated by ``scheduled_departures``.
        return self.repository.list_stops(limit=500, stations_only=True)

    def _get(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.client.get(url)
                response.raise_for_status()
                return response.content
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(min(0.5 * (2**attempt), 2.0))
        assert last_error is not None
        raise last_error

    def _poll(self, now: datetime) -> None:
        if (
            self._last_poll is not None
            and (now - self._last_poll).total_seconds() < self.poll_seconds
        ):
            return
        self._last_poll = now
        try:
            trip_bytes = self._get(self.endpoints.trip_updates)
            vehicle_bytes = self._get(self.endpoints.vehicle_positions)
            alert_bytes = self._get(self.endpoints.alerts)
            self._trip_data = parse_trip_updates(trip_bytes)
            self._vehicle_data = parse_vehicle_positions(vehicle_bytes)
            self._alert_data = parse_alerts(alert_bytes)
            self._last_error = None
        except Exception as exc:
            self._last_error = f"Realtime refresh failed: {exc}"
            LOGGER.warning("Translink realtime refresh failed", exc_info=True)

    @staticmethod
    def _select_update(
        update: TripRealtimeUpdate,
        *,
        stop_id: str,
        stop_sequence: int | None,
    ) -> bool:
        if update.stop_id:
            return update.stop_id == stop_id
        return update.stop_sequence is None or update.stop_sequence == stop_sequence

    def refresh(self, station_id: str, now: datetime) -> ProviderSnapshot:
        scheduled = self.repository.scheduled_departures(station_id, now)
        stop = self.repository.get_stop(station_id)
        self._poll(now)
        data_timestamp = self._trip_data.timestamp if self._trip_data else None
        status = classify_freshness(
            self.mode,
            now,
            data_timestamp,
            stale_after_seconds=self.stale_after_seconds,
            error=self._last_error if self._trip_data is None else None,
        )
        stale = status.health != FeedHealth.HEALTHY
        updates: dict[str, tuple[datetime | None, bool]] = {}
        if self._trip_data is not None and not stale:
            for departure in scheduled:
                candidates = (
                    update
                    for update in self._trip_data.trip_updates
                    if update.trip_id == departure.trip_id
                    and self._select_update(
                        update,
                        stop_id=departure.stop_id,
                        stop_sequence=departure.stop_sequence,
                    )
                )
                update = next(candidates, None)
                if update is None:
                    continue
                predicted = update.predicted_time
                if predicted is None and update.delay_seconds is not None:
                    predicted = departure.scheduled_time + timedelta(seconds=update.delay_seconds)
                updates[departure.trip_id] = (predicted, update.cancelled)
        departures = merge_realtime(scheduled, updates, stale=stale)
        alerts: tuple[ServiceAlert, ...] = ()
        if self._alert_data is not None:
            alerts = tuple(
                alert
                for alert in self._alert_data.alerts
                if not alert.stop_ids or station_id in alert.stop_ids
            )
        vehicles = self._vehicle_data.vehicles if self._vehicle_data is not None else ()
        if self._last_error and self._trip_data is not None:
            status = FeedStatus(
                provider=self.mode,
                health=status.health,
                fetched_at=now,
                data_timestamp=data_timestamp,
                message=f"{status.message}; using last-known-good data",
                using_fallback=status.using_fallback,
            )
        trip_delays: dict[str, TripDelay] = {}
        if self._trip_data is not None:
            for update in self._trip_data.trip_updates:
                current = trip_delays.get(update.trip_id)
                delay = update.delay_seconds or (current.delay_seconds if current else 0)
                trip_delays[update.trip_id] = TripDelay(
                    update.trip_id,
                    delay,
                    update.cancelled or (current.cancelled if current else False),
                )
        return ProviderSnapshot(
            stop,
            departures,
            vehicles,
            alerts,
            status,
            tuple(trip_delays.values()),
        )

    def close(self) -> None:
        self.client.close()


def download_static_gtfs(
    url: str,
    destination: str | Path,
    *,
    timeout_seconds: float = 60.0,
) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(target.suffix + ".part")
    with httpx.stream(
        "GET",
        url,
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=True,
        headers={"User-Agent": "qld-train-tracker/0.1"},
    ) as response:
        response.raise_for_status()
        with staging.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    staging.replace(target)
    return target

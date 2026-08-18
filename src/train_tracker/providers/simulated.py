from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from train_tracker.models import (
    Departure,
    FeedHealth,
    FeedStatus,
    ProviderSnapshot,
    RealtimeState,
    ServiceAlert,
    Stop,
)


class Scenario(StrEnum):
    NORMAL = "normal"
    DELAYS = "delays"
    RECOVERY = "recovery"
    CANCELLATIONS = "cancellations"
    PLATFORM = "platform"
    ALERT = "alert"
    STALE = "stale"
    DISCONNECTED = "disconnected"
    EMPTY = "empty"
    MIDNIGHT = "midnight"


@dataclass(slots=True)
class SimulationOverrides:
    delay_minutes: int = 0
    cancel_first: bool = False
    platform_change: bool = False
    alert: bool = False
    stale: bool = False
    disconnected: bool = False
    empty: bool = False


class SimulatedProvider:
    """Deterministic, offline Queensland-flavoured sample data.

    Stops are fictional and explicitly prefixed with ``Demo`` to avoid presenting
    sample information as a real Translink service.
    """

    _stops = (
        Stop("SIM-CEN", "Demo Central", -27.466, 153.026, wheelchair_boarding=True),
        Stop("SIM-RIV", "Demo Riverside", -27.482, 153.031, wheelchair_boarding=True),
        Stop("SIM-COA", "Demo Coast Junction", -27.999, 153.410),
    )
    _patterns: dict[str, tuple[tuple[str, str, int], ...]] = {
        "SIM-CEN": (
            ("BDVL", "Beenleigh", 7),
            ("CLBR", "Caboolture", 12),
            ("GOLD", "Varsity Lakes", 18),
            ("FERN", "Ferny Grove", 24),
            ("IPSW", "Rosewood via Ipswich", 31),
            ("REDL", "Kippa-Ring", 39),
        ),
        "SIM-RIV": (
            ("CLEV", "Cleveland", 5),
            ("SHOR", "Shorncliffe", 14),
            ("GOLD", "Varsity Lakes", 21),
            ("AIR", "Domestic Airport", 29),
            ("DOOM", "Doomben", 36),
        ),
        "SIM-COA": (
            ("AIR", "Brisbane Airport", 8),
            ("BDVL", "Brisbane City", 16),
            ("GOLD", "Varsity Lakes", 27),
            ("TILT", "Rockhampton", 44),
        ),
    }

    def __init__(self, scenario: str | Scenario = Scenario.NORMAL) -> None:
        self.scenario = Scenario(scenario)
        self.overrides = SimulationOverrides()

    @property
    def mode(self) -> str:
        return "simulated"

    def list_stops(self) -> tuple[Stop, ...]:
        return self._stops

    def set_scenario(self, scenario: str | Scenario) -> None:
        self.scenario = Scenario(scenario)

    def _delay_for(self, now: datetime, index: int) -> int:
        if self.scenario == Scenario.DELAYS:
            return (index + 1) * 180
        if self.scenario == Scenario.RECOVERY:
            cycle = (now.minute // 5) % 6
            return max(0, (5 - cycle - index) * 120)
        return self.overrides.delay_minutes * 60 if index < 2 else 0

    def refresh(self, station_id: str, now: datetime) -> ProviderSnapshot:
        stop = next((item for item in self._stops if item.id == station_id), self._stops[0])
        patterns = self._patterns[stop.id]
        empty = self.scenario == Scenario.EMPTY or self.overrides.empty
        disconnected = self.scenario == Scenario.DISCONNECTED or self.overrides.disconnected
        stale = self.scenario == Scenario.STALE or self.overrides.stale
        platform_change = self.scenario == Scenario.PLATFORM or self.overrides.platform_change
        # Anchor services to a repeatable ten-minute timetable grid. Building
        # them directly from ``now`` would reset every countdown on each poll.
        timetable_base = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 10)

        departures: list[Departure] = []
        if not empty:
            for index, (route, destination, offset) in enumerate(patterns):
                scheduled = timetable_base + timedelta(minutes=offset)
                delay = self._delay_for(now, index)
                cancelled = (
                    self.scenario == Scenario.CANCELLATIONS or self.overrides.cancel_first
                ) and index == 0
                predicted = scheduled + timedelta(seconds=delay)
                departures.append(
                    Departure(
                        id=f"sim-{stop.id}-{scheduled:%Y%m%d%H%M}-{index}",
                        trip_id=f"sim-trip-{route}-{scheduled:%H%M}",
                        stop_id=stop.id,
                        route_id=route,
                        route_name=route,
                        destination=destination,
                        scheduled_time=scheduled,
                        predicted_time=None if stale or disconnected else predicted,
                        platform=("4" if platform_change and index == 0 else str(index % 3 + 1)),
                        cancelled=cancelled,
                        realtime_state=(
                            RealtimeState.STALE if stale or disconnected else RealtimeState.REALTIME
                        ),
                        provider=self.mode,
                        wheelchair_accessible=index % 2 == 0,
                        stop_sequence=12 + index,
                    )
                )

        alert_enabled = self.scenario == Scenario.ALERT or self.overrides.alert
        alerts = (
            (
                ServiceAlert(
                    id="sim-alert",
                    header="Track work: allow extra time",
                    description="Sample alert for the offline simulator.",
                    route_ids=(patterns[0][0],),
                ),
            )
            if alert_enabled
            else ()
        )

        if disconnected:
            health = FeedHealth.OFFLINE
            message = "Connection lost - timetable fallback"
        elif stale:
            health = FeedHealth.STALE
            message = "Realtime stale - timetable fallback"
        else:
            health = FeedHealth.HEALTHY
            message = "Offline deterministic simulation"

        data_timestamp = now - timedelta(minutes=5) if stale else now
        return ProviderSnapshot(
            station=stop,
            departures=tuple(departures),
            alerts=alerts,
            status=FeedStatus(
                provider=self.mode,
                health=health,
                fetched_at=now,
                data_timestamp=data_timestamp,
                message=message,
                using_fallback=stale or disconnected,
            ),
        )

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from train_tracker.models import ProviderSnapshot, Stop


class DepartureProvider(Protocol):
    @property
    def mode(self) -> str: ...

    def list_stops(self) -> tuple[Stop, ...]: ...

    def refresh(self, station_id: str, now: datetime) -> ProviderSnapshot: ...

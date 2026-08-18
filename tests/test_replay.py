from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from train_tracker.models import FeedHealth
from train_tracker.providers.replay import ReplayProvider


def test_replay_parsing_timing_restart_and_seek(now: datetime) -> None:
    path = Path(__file__).parents[1] / "fixtures" / "sample_replay.json"
    provider = ReplayProvider(path)
    first = provider.refresh("REPLAY-CEN", now)
    assert first.departures[0].route_name == "BDVL"
    shortly_after = provider.refresh("REPLAY-CEN", now + timedelta(seconds=30))
    assert shortly_after.departures[0].scheduled_time == first.departures[0].scheduled_time
    delayed = provider.refresh("REPLAY-CEN", now + timedelta(seconds=130))
    assert delayed.departures[0].delay_seconds == 300
    stale = provider.refresh("REPLAY-CEN", now + timedelta(seconds=250))
    assert stale.status.health == FeedHealth.STALE
    provider.seek(360, now + timedelta(seconds=250))
    recovered = provider.refresh("REPLAY-CEN", now + timedelta(seconds=250))
    assert recovered.status.health == FeedHealth.HEALTHY
    provider.restart(now)
    assert provider.refresh("REPLAY-CEN", now).status.message.endswith("normal operation")

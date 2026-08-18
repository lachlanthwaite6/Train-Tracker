from __future__ import annotations

from datetime import datetime, timedelta

from train_tracker.models import FeedHealth
from train_tracker.services.freshness import classify_freshness


def test_stale_feed_detection(now: datetime) -> None:
    healthy = classify_freshness("test", now, now - timedelta(seconds=10), stale_after_seconds=90)
    stale = classify_freshness("test", now, now - timedelta(seconds=91), stale_after_seconds=90)
    offline = classify_freshness(
        "test", now, now - timedelta(seconds=10), stale_after_seconds=90, error="lost"
    )
    assert healthy.health == FeedHealth.HEALTHY
    assert stale.health == FeedHealth.STALE and stale.using_fallback
    assert offline.health == FeedHealth.OFFLINE

from __future__ import annotations

from datetime import datetime

from train_tracker.models import FeedHealth, FeedStatus


def classify_freshness(
    provider: str,
    now: datetime,
    data_timestamp: datetime | None,
    *,
    stale_after_seconds: float,
    error: str | None = None,
) -> FeedStatus:
    if error:
        return FeedStatus(provider, FeedHealth.OFFLINE, now, data_timestamp, error, True)
    if data_timestamp is None:
        return FeedStatus(provider, FeedHealth.ERROR, now, None, "No feed timestamp", True)
    age = max(0.0, (now - data_timestamp).total_seconds())
    if age > stale_after_seconds:
        return FeedStatus(
            provider,
            FeedHealth.STALE,
            now,
            data_timestamp,
            f"Realtime data is {round(age)}s old",
            True,
        )
    return FeedStatus(provider, FeedHealth.HEALTHY, now, data_timestamp, "Realtime", False)

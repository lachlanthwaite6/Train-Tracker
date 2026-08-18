# Replay schema

Replay files are UTF-8 JSON objects:

```json
{
  "station": {"id": "REPLAY-CEN", "name": "Replay Central"},
  "frames": [
    {
      "offset_seconds": 0,
      "health": "healthy",
      "message": "Morning peak",
      "departures": [
        {
          "id": "r1",
          "trip_id": "trip-r1",
          "route": "BDVL",
          "destination": "Beenleigh",
          "scheduled_in_seconds": 240,
          "delay_seconds": 60,
          "platform": "2",
          "cancelled": false,
          "realtime_state": "realtime"
        }
      ],
      "alerts": [{"id": "a1", "header": "Sample alert"}]
    }
  ]
}
```

`offset_seconds` values must be ascending. `health` is `healthy`, `stale`, `offline` or `error`.
`realtime_state` is `realtime`, `scheduled` or `stale`. Unknown optional fields are ignored.
`scheduled_in_seconds` is relative to the time its containing frame becomes active, keeping the
service's absolute simulated time stable across provider refreshes.

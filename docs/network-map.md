# Network map architecture

The map reuses the departure application's provider refresh. `TranslinkProvider` polls each
GTFS-Realtime endpoint once per configured interval and places the latest vehicles in the shared
`ProviderSnapshot`; the departure and map tabs never issue independent requests.

```text
versioned static GTFS -> cached RailNetwork + active RailTrip schedules
shared ProviderSnapshot vehicles -> trip-specific GPS snapping + freshness
clock + schedules -> deterministic scheduled estimates
immutable MapScene -> cached Pillow renderer -> Tkinter canvas / PNG
```

Static geometry and active service-day trips are cached. Network construction, SQLite work and
provider refreshes run outside the Tk event thread. Animation frames only transform cached geometry,
recalculate schedule positions and blend from the preceding GPS marker position.

## System View

System View is the default native LED rail scope. Select it with `--map-scope system` in the GUI or
`--scope system` in `render-map`. It derives distinct passenger route groups and principal branch
patterns from imported GTFS, resolves platforms to parent stations, and retains the original trip
and source route identifiers for realtime matching.

The automatic layout fits the connected network into a logical 128×64 coordinate system, straightens
ordinary corridor stations, uses horizontal/vertical/45-degree segments, and retains termini,
branches, shared corridors and approximate geographic direction. Visual lines that share the same
station-to-station edge receive deterministic two-pixel lane offsets. The native renderer uses an
identity-style viewport so the designed status and edge margins are not lost during fitting.

At board size, principal interchanges, a stable sample of minor stations, up to ten collision-placed
labels, trains, and a two-pixel route-colour key are visible. On desktop, the same scene adds station
detail, full labels, hover/click interaction, pan and zoom. A selected train opens a temporary
three-line overlay in the corner with the fewest trains and farthest from its next station.

```bash
train-tracker gui --mode simulated --view map --map-scope system
train-tracker render-map --mode simulated --scope system --width 128 --height 64 \
  --output output/rail-system-led.png
train-tracker render-map --mode simulated --scope system --width 1280 --height 640 \
  --output output/rail-system-preview.png
```

## Route Focus

Route Focus is the optional one/two-route native LED view. Select it with `--map-scope route`
(`focused` remains a compatible alias) in the GUI or `--scope route` in `render-map`. Up to two route IDs or name fragments are resolved against the
GTFS-derived canonical rail routes; no public route list is hard-coded as authoritative.

For each selected route, the builder chooses a representative active trip, simplifies insignificant
shape bends, preserves ordered stops and important turns, and expands the corridor across roughly
70% of the complete 128-pixel display. Long routes sample up to ten display stations while retaining
their original order and times. Inbound/outbound trips receive parallel tracks, and a second route
receives an additional stable corridor offset.

The native composition assigns 90 pixels to the map and 38 untouched pixels to information. Route
geometry is rendered into a separate 90-pixel image, so glow, tracks, labels and sprites cannot leak
into the right-hand region. The region stays completely empty until a train is hovered, pinned or
automatically cycled.

Train positions use compact directional 5×3 pixel sprites by default: solid with a bright outline for GPS, hollow or
dithered for schedule estimates, dimmed when stale, and surrounded by a restrained halo when
selected. Windscreen/headlight and tail-light pixels communicate direction; dark lower pixels
suggest bogies. Dwell and direction animations use only a few frames.

```bash
train-tracker gui --mode simulated --view rail-map --map-scope route
train-tracker render-map --scope route --route Airport --route Ferny \
  --width 128 --height 64 --output output/rail-led.png
```

## CBD Focus

CBD Focus is selected with `--map-scope cbd` in the GUI or `--scope cbd` for headless rendering.
It resolves configured station names against parent GTFS stations, retains trips calling at two or
more selected stations, and constructs an original schematic. The default topology uses a central
Bowen Hills–Roma Street spine with western and southern branches. It is not traced from a published
network map.

Each route receives a stable parallel offset, and its two directions receive smaller opposing lane
offsets. Nearly coincident train markers receive a deterministic radial displacement. Station nodes
are drawn above the tracks, with larger nodes for interchanges.

Fresh GPS positions are first snapped to the trip's geographic GTFS shape. Their progress between
the selected CBD stations is then projected onto the corresponding schematic lane; the dot therefore
retains GPS-derived progress without attempting to plot latitude/longitude directly on a schematic.

The 128×64 renderer allocates the left 92 pixels to the map and reserves the right 36 pixels for
train context. The reserved area is ordinary untouched background until a train is selected. The
compact renderer uses the bundled 3×5 font and clips or abbreviates every line to the panel width.
Desktop hover temporarily selects a train; a click pins it; clicking empty map space clears it. The
same selected-train state is independent from Tkinter and can later be driven by physical buttons.
Set `auto_cycle_train_seconds` to a positive interval to cycle automatically when desired.

Examples:

```bash
train-tracker gui --mode simulated --view map --map-scope cbd
train-tracker gui --mode live --view map --map-scope cbd
train-tracker render-map --mode simulated --scope cbd --width 128 --height 64 \
  --output output/cbd-led.png
train-tracker render-map --mode simulated --scope cbd --width 1200 --height 800 \
  --output output/cbd-preview.png
```

## Optional layout override

Set `layout_file` under `[map]` to a JSON file. Coordinates are map-world coordinates and may define
only the stations or shapes that need curating:

```json
{
  "stations": {
    "place_twgsta": [120.0, 80.0]
  },
  "system_stations": {
    "place_twgsta": [35, 45],
    "place_romsta": [58, 34]
  },
  "shapes": {
    "example_shape_id": [[120.0, 80.0], [180.0, 80.0], [240.0, 130.0]]
  },
  "cbd": {
    "stations": {
      "place_twgsta": [-140.0, 350.0],
      "central": [0.0, 140.0]
    },
    "track_offsets": {
      "example_route_id": 12.0
    },
    "label_positions": {
      "place_twgsta": [-132.0, 340.0]
    },
    "interchanges": ["central", "place_romsta"],
    "reserved_info_panel_bounds": [92, 0, 128, 64],
    "route_geometry": {
      "example_route_id": [[0.0, 0.0], [0.0, 140.0], [-140.0, 350.0]]
    }
  },
  "rail_routes": {
    "example_route_id": [[2, 12], [42, 32], [88, 48]]
  }
}
```

`system_stations` contains optional logical 128×64 coordinates for System View; missing stations
continue to use the automatic layout. The top-level `stations` and `shapes` keys customize legacy Full Network. CBD station overrides accept a
station ID or normalized station name. Track offsets and route geometry use canonical map route IDs.
Unknown IDs are ignored. When the file is empty or missing, automatic geometry is used.
`rail_routes` points are logical focused-map coordinates and may be keyed by canonical route ID or
GTFS-derived route name.

## Symbols

- Station: small filled light node.
- Interchange: larger filled or ringed node.
- Live GPS: solid route-coloured train sprite with a white outline and headlight.
- Scheduled estimate: hollow/dithered route-coloured train sprite.
- Stale GPS: dimmed train sprite.
- Selected train: an additional contrasting one-pixel halo.
- Dense cluster: a stable count marker when more than four trains share a tiny area.

Scheduled train movement is an estimate derived from stop times and is not a physical observation.

## Feed-state behavior

- `Live`: one or more fresh resolved GPS positions, with no schedule-only markers.
- `Mixed`: live markers plus active trips represented by scheduled estimates.
- `Simulated`: all displayed markers come from the timetable.
- `Stale` / `Offline`: the provider's degraded state is shown; last-known GPS markers are dimmed and
  removed after `live_position_expiry_seconds`.
- `No service`: no rail trip is active at the selected time.

GPS is snapped only against the resolved trip shape with a bounded distance threshold. Raw
coordinates remain attached to the marker model for diagnostics. A schedule-derived marker is
never labelled as GPS.

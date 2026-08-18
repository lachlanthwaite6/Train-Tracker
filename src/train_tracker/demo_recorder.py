from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from train_tracker.bus_map.presenter import BusMapPresenter
from train_tracker.bus_map.renderer import BusMapRenderer, BusRenderOptions
from train_tracker.bus_map.repository import BusNetworkRepository
from train_tracker.config import AppConfig
from train_tracker.map.models import MapScope
from train_tracker.map.network_repository import RailNetworkRepository
from train_tracker.map.presenter import MapPresenter
from train_tracker.map.projection import MapViewport
from train_tracker.map.renderer import MapRenderOptions, NetworkMapRenderer
from train_tracker.models import (
    FeedHealth,
    FeedStatus,
    ProviderSnapshot,
    Stop,
    TripDelay,
    VehiclePosition,
)
from train_tracker.providers.simulated import Scenario, SimulatedProvider
from train_tracker.rendering.fonts import draw_text
from train_tracker.rendering.renderer import MatrixRenderer


def _times(start: datetime, duration: float, fps: int) -> tuple[datetime, ...]:
    count = max(2, round(duration * fps))
    # Demonstrations advance thirty simulated seconds per wall-clock second so
    # vehicle movement and countdown changes are readable in a short GIF.
    return tuple(start + timedelta(seconds=index * 30 / fps) for index in range(count))


def _departure_frames(config: AppConfig, times: tuple[datetime, ...]) -> list[Image.Image]:
    provider = SimulatedProvider(Scenario.DELAYS)
    renderer = MatrixRenderer()
    frames: list[Image.Image] = []
    for index, now in enumerate(times):
        provider.overrides.platform_change = index >= len(times) // 3
        provider.overrides.cancel_first = index >= len(times) * 2 // 3
        snapshot = provider.refresh(config.station_id, now)
        frames.append(renderer.render(snapshot, now, brightness=config.display.brightness))
    return frames


def _healthy_snapshot(now: datetime) -> ProviderSnapshot:
    return ProviderSnapshot(
        Stop("demo", "Demonstration"),
        status=FeedStatus(
            "demo-recorder",
            FeedHealth.HEALTHY,
            now,
            now,
            "Deterministic demonstration",
        ),
    )


def _vehicle_snapshot(
    now: datetime,
    *,
    trip_id: str,
    vehicle_id: str,
    first: tuple[float, float],
    second: tuple[float, float],
    progress: float,
    stale: bool,
) -> ProviderSnapshot:
    latitude = first[0] + (second[0] - first[0]) * progress
    longitude = first[1] + (second[1] - first[1]) * progress
    age = 100 if stale else 8
    return ProviderSnapshot(
        Stop("demo", "Demonstration"),
        vehicles=(
            VehiclePosition(
                vehicle_id,
                trip_id,
                latitude,
                longitude,
                now - timedelta(seconds=age),
            ),
        ),
        status=FeedStatus(
            "demo-recorder",
            FeedHealth.HEALTHY,
            now,
            now,
            "Deterministic mixed GPS/timetable demonstration",
        ),
        trip_delays=(TripDelay(trip_id, 60),),
    )


def _rail_frames(
    config: AppConfig,
    times: tuple[datetime, ...],
    routes: tuple[str, ...],
    database: Path | None,
) -> list[Image.Image]:
    repository = (
        RailNetworkRepository(database) if database is not None and database.exists() else None
    )
    presenter = MapPresenter(
        repository,
        mode="simulated",
        scope=MapScope.FOCUSED,
        layout_file=config.map.layout_file,
        focused_routes=routes or config.map.focused_routes,
        rail_direction="both",
        rail_map_width=config.map.rail_map_width,
        focused_route_coverage=config.map.focused_route_coverage,
        focused_track_spacing=config.map.focused_track_spacing,
        focused_station_spacing=config.map.focused_station_spacing,
    )
    renderer = NetworkMapRenderer()
    frames: list[Image.Image] = []
    anchor = presenter.prepare(times[0], _healthy_snapshot(times[0]))
    live_trip = anchor.trips[0] if anchor.trips else None
    if anchor.markers:
        live_trip = next(
            (trip for trip in anchor.trips if trip.id == anchor.markers[0].trip_id), live_trip
        )
    for index, now in enumerate(times):
        snapshot = _healthy_snapshot(now)
        if live_trip is not None and len(live_trip.stops) >= 2:
            span = (len(live_trip.stops) - 1) * index / max(1, len(times) - 1)
            segment = min(len(live_trip.stops) - 2, math.floor(span))
            progress = span - segment
            first = anchor.network.stations[live_trip.stops[segment].station_id]
            second = anchor.network.stations[live_trip.stops[segment + 1].station_id]
            snapshot = _vehicle_snapshot(
                now,
                trip_id=live_trip.id,
                vehicle_id="demo-train-live",
                first=(first.latitude, first.longitude),
                second=(second.latitude, second.longitude),
                progress=progress,
                stale=index >= len(times) * 4 // 5,
            )
        scene = presenter.prepare(now, snapshot)
        selected = (
            scene.markers[
                index // max(1, len(times) // max(1, len(scene.markers))) % len(scene.markers)
            ].id
            if scene.markers and index >= len(times) // 2
            else None
        )
        viewport = MapViewport.fit(scene.network.bounds, config.map.rail_map_width, 64, 1)
        frames.append(
            renderer.render(
                scene,
                (128, 64),
                viewport=viewport,
                options=MapRenderOptions(
                    scope=MapScope.FOCUSED,
                    reserve_info_panel=True,
                    info_panel_width=config.map.train_info_panel_width,
                    selected_train_id=selected,
                    train_sprite_size=config.map.rail_sprite_size,
                    show_direction_animation=config.map.show_direction_animation,
                    animation_frame=index,
                ),
            )
        )
    return frames


def _bus_frames(
    config: AppConfig,
    times: tuple[datetime, ...],
    routes: tuple[str, ...],
    database: Path | None,
) -> list[Image.Image]:
    repository = (
        BusNetworkRepository(database) if database is not None and database.exists() else None
    )
    route = routes[0] if routes else config.bus_map.default_route
    presenter = BusMapPresenter(
        repository,
        route=route,
        direction="both",
        map_width=config.bus_map.map_width,
        route_coverage=config.bus_map.route_screen_coverage,
        lane_spacing=config.bus_map.lane_spacing,
        station_spacing=config.bus_map.station_spacing,
    )
    renderer = BusMapRenderer()
    frames: list[Image.Image] = []
    anchor = presenter.prepare(times[0], _healthy_snapshot(times[0]))
    live_trip = next(
        (
            trip
            for trip in anchor.trips
            if anchor.markers and trip.id == anchor.markers[0].marker.trip_id
        ),
        anchor.trips[0] if anchor.trips else None,
    )
    for index, now in enumerate(times):
        snapshot = _healthy_snapshot(now)
        if live_trip is not None and len(live_trip.stops) >= 2:
            span = (len(live_trip.stops) - 1) * index / max(1, len(times) - 1)
            segment = min(len(live_trip.stops) - 2, math.floor(span))
            progress = span - segment
            first = anchor.network.stations[live_trip.stops[segment].station_id]
            second = anchor.network.stations[live_trip.stops[segment + 1].station_id]
            snapshot = _vehicle_snapshot(
                now,
                trip_id=live_trip.id,
                vehicle_id="demo-bus-live",
                first=(first.latitude, first.longitude),
                second=(second.latitude, second.longitude),
                progress=progress,
                stale=index >= len(times) * 4 // 5,
            )
        scene = presenter.prepare(now, snapshot)
        selected = (
            scene.markers[
                index // max(1, len(times) // max(1, len(scene.markers))) % len(scene.markers)
            ].id
            if scene.markers and index >= len(times) // 2
            else None
        )
        frames.append(
            renderer.render(
                scene,
                options=BusRenderOptions(
                    map_width=config.bus_map.map_width,
                    info_panel_width=config.bus_map.info_panel_width,
                    sprite_size=config.bus_map.bus_sprite_size,
                    show_direction_animation=config.bus_map.show_direction_animation,
                    selected_bus_id=selected,
                    animation_frame=index,
                ),
            )
        )
    return frames


def _screen_switching_frames(
    config: AppConfig,
    times: tuple[datetime, ...],
    routes: tuple[str, ...],
    database: Path | None,
) -> list[Image.Image]:
    third = max(2, len(times) // 3)
    groups: tuple[tuple[str, Callable[[], list[Image.Image]]], ...] = (
        ("DEPARTURES", lambda: _departure_frames(config, times[:third])),
        ("RAIL MAP", lambda: _rail_frames(config, times[third : third * 2], routes, database)),
        ("BUS MAP", lambda: _bus_frames(config, times[third * 2 :], ("M1",), database)),
    )
    frames: list[Image.Image] = []
    for label, build in groups:
        for frame in build():
            # This small tab label is drawn by the recorder over the genuine
            # logical application frame; the underlying display is unchanged.
            draw_text(frame, (92, 57), label, (180, 205, 220), max_width=35)
            frames.append(frame)
    return frames


def logical_demo_frames(
    view: str,
    *,
    config: AppConfig,
    duration: float,
    fps: int,
    routes: tuple[str, ...] = (),
    database: Path | None = None,
    start_time: datetime | None = None,
) -> tuple[Image.Image, ...]:
    start = start_time or config.start_time
    times = _times(start, duration, fps)
    if view == "departures":
        frames = _departure_frames(config, times)
    elif view == "rail-map":
        frames = _rail_frames(config, times, routes, database)
    elif view == "bus-map":
        frames = _bus_frames(config, times, routes, database)
    elif view == "screen-switching":
        frames = _screen_switching_frames(config, times, routes, database)
    else:
        raise ValueError(f"Unknown demonstration view: {view}")
    return tuple(frames)


def record_demo(
    view: str,
    output: str | Path,
    *,
    config: AppConfig,
    duration: float = 12,
    fps: int = 6,
    scale: int = 6,
    routes: tuple[str, ...] = (),
    database: Path | None = None,
    start_time: datetime | None = None,
) -> Path:
    if duration <= 0 or fps < 1 or scale < 1:
        raise ValueError("duration, fps and scale must be positive")
    logical = logical_demo_frames(
        view,
        config=config,
        duration=duration,
        fps=fps,
        routes=routes,
        database=database,
        start_time=start_time,
    )
    enlarged = tuple(
        frame.resize((128 * scale, 64 * scale), Image.Resampling.NEAREST) for frame in logical
    )
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    enlarged[0].save(
        target,
        format="GIF",
        save_all=True,
        append_images=enlarged[1:],
        duration=round(1000 / fps),
        loop=0,
        optimize=True,
        disposal=1,
    )
    return target

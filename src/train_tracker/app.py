from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from train_tracker.bus_map.presenter import BusMapPresenter
from train_tracker.bus_map.repository import BusNetworkRepository
from train_tracker.clock import SimulatedClock
from train_tracker.config import AppConfig
from train_tracker.map.models import MapScope
from train_tracker.map.network_repository import RailNetworkRepository
from train_tracker.map.presenter import MapPresenter
from train_tracker.outputs.desktop_gui import DesktopSimulator
from train_tracker.providers.base import DepartureProvider
from train_tracker.providers.replay import ReplayProvider
from train_tracker.providers.simulated import SimulatedProvider
from train_tracker.providers.translink import TranslinkEndpoints, TranslinkProvider


def build_providers(
    config: AppConfig,
    *,
    replay_path: str | Path | None = None,
    include_live: bool = False,
) -> dict[str, DepartureProvider]:
    providers: dict[str, DepartureProvider] = {
        "simulated": SimulatedProvider(config.scenario),
    }
    if replay_path is not None:
        providers["replay"] = ReplayProvider(replay_path)
    if include_live:
        providers["live"] = TranslinkProvider(
            config.translink.database,
            TranslinkEndpoints(
                config.translink.trip_updates_url,
                config.translink.vehicle_positions_url,
                config.translink.alerts_url,
            ),
            poll_seconds=config.translink.poll_seconds,
            stale_after_seconds=config.translink.stale_after_seconds,
        )
    return providers


def build_clock(
    config: AppConfig,
    *,
    mode: str,
    current_time: datetime | None = None,
) -> SimulatedClock:
    """Create the clock appropriate for a provider mode.

    Live data must be queried against the actual local time. Simulation and
    replay deliberately retain their configured, repeatable start time.
    ``current_time`` is injectable so this boundary stays deterministic in
    tests.
    """
    if mode == "live":
        start = current_time or datetime.now(ZoneInfo(config.timezone))
        return SimulatedClock(start, speed=1.0)
    return SimulatedClock(config.start_time, speed=config.speed)


def run_gui(
    config: AppConfig,
    *,
    mode: str | None = None,
    replay_path: str | Path | None = None,
    view: str | None = None,
    map_scope: str | None = None,
    rail_routes: tuple[str, ...] | None = None,
    rail_direction: str | None = None,
    bus_route: str | None = None,
    bus_direction: str | None = None,
) -> None:
    selected_mode = mode or config.mode
    providers = build_providers(
        config,
        replay_path=replay_path,
        include_live=selected_mode == "live",
    )
    if selected_mode not in providers:
        selected_mode = next(iter(providers))
    ordered = {selected_mode: providers.pop(selected_mode), **providers}
    clock = build_clock(config, mode=selected_mode)
    station_id = config.live_station_id if selected_mode == "live" else config.station_id
    database = Path(config.translink.database)
    map_repository: RailNetworkRepository | None = None
    if database.exists():
        map_repository = RailNetworkRepository(database)
        map_repository.gtfs.require_map_schema()
    elif selected_mode == "live":
        raise FileNotFoundError(
            f"GTFS database not found: {database}. Run train-tracker import-gtfs first."
        )
    map_presenter = MapPresenter(
        map_repository,
        mode=selected_mode,
        layout_file=config.map.layout_file,
        stale_seconds=config.map.live_position_stale_seconds,
        expiry_seconds=config.map.live_position_expiry_seconds,
        scope=map_scope or config.map.default_scope,
        cbd_station_names=config.map.cbd_station_names,
        cbd_station_spacing=config.map.cbd_station_spacing,
        cbd_track_spacing=config.map.cbd_track_spacing,
        focused_routes=rail_routes or config.map.focused_routes,
        rail_direction=rail_direction or config.map.rail_direction,
        rail_map_width=config.map.rail_map_width,
        focused_route_coverage=config.map.focused_route_coverage,
        focused_track_spacing=config.map.focused_track_spacing,
        focused_station_spacing=config.map.focused_station_spacing,
    )
    bus_repository = BusNetworkRepository(database) if database.exists() else None
    bus_map_presenter = (
        BusMapPresenter(
            bus_repository,
            route=bus_route or config.bus_map.default_route,
            direction=bus_direction or config.bus_map.direction,
            map_width=config.bus_map.map_width,
            route_coverage=config.bus_map.route_screen_coverage,
            lane_spacing=config.bus_map.lane_spacing,
            station_spacing=config.bus_map.station_spacing,
            layout_file=config.bus_map.layout_file,
            stale_seconds=config.bus_map.live_position_stale_seconds,
            expiry_seconds=config.bus_map.live_position_expiry_seconds,
        )
        if config.bus_map.enabled
        else None
    )
    simulator = DesktopSimulator(
        ordered,
        clock,
        station_id=station_id,
        scale=config.display.scale,
        fps=config.display.fps,
        refresh_seconds=config.refresh_seconds,
        brightness=config.display.brightness,
        pixel_grid=config.display.pixel_grid,
        map_presenter=map_presenter,
        map_config=config.map,
        default_view=view or config.map.default_view,
        default_map_scope=MapScope(map_scope or config.map.default_scope),
        bus_map_presenter=bus_map_presenter,
        bus_map_config=config.bus_map,
    )
    simulator.run()

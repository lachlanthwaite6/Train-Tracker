from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from train_tracker.clock import BRISBANE


@dataclass(slots=True)
class DisplayConfig:
    width: int = 128
    height: int = 64
    scale: int = 6
    fps: int = 20
    brightness: int = 100
    pixel_grid: bool = True


@dataclass(slots=True)
class TranslinkConfig:
    database: str = "data/translink.sqlite3"
    static_url: str = "https://gtfsrt.api.translink.com.au/GTFS/SEQ_GTFS.zip"
    trip_updates_url: str = "https://gtfsrt.api.translink.com.au/api/realtime/SEQ/TripUpdates"
    vehicle_positions_url: str = (
        "https://gtfsrt.api.translink.com.au/api/realtime/SEQ/VehiclePositions"
    )
    alerts_url: str = "https://gtfsrt.api.translink.com.au/api/realtime/SEQ/Alerts"
    poll_seconds: float = 15.0
    stale_after_seconds: float = 90.0


@dataclass(slots=True)
class Hub75Config:
    rows: int = 64
    cols: int = 128
    chain_length: int = 1
    parallel: int = 1
    brightness: int = 60
    gpio_slowdown: int = 2
    hardware_mapping: str = "regular"


@dataclass(slots=True)
class MapConfig:
    default_view: str = "network"
    default_scope: str = "system"
    default_station_id: str = "place_twgsta"
    rail_only: bool = True
    show_station_labels: bool = True
    show_major_station_labels: bool = True
    show_minor_station_labels: bool = False
    show_scheduled_estimates: bool = True
    live_position_stale_seconds: float = 90.0
    live_position_expiry_seconds: float = 300.0
    interpolation_fps: int = 30
    padding: int = 40
    background_color: str = "#080a0d"
    layout_file: str = ""
    show_compass: bool = False
    system_screen_coverage: float = 0.82
    route_lane_spacing: float = 2.0
    train_collision_spacing: float = 2.0
    max_led_labels: int = 10
    compact_legend: bool = True
    cbd_station_names: tuple[str, ...] = (
        "Bowen Hills",
        "Fortitude Valley",
        "Central",
        "Roma Street",
        "Milton",
        "Toowong",
        "South Brisbane",
        "South Bank",
    )
    cbd_track_spacing: float = 8.0
    cbd_station_spacing: float = 70.0
    show_symbol_legend: bool = True
    reserve_train_info_panel: bool = True
    train_info_panel_width: int = 38
    auto_cycle_train_seconds: float = 0.0
    focused_routes: tuple[str, ...] = ()
    rail_direction: str = "both"
    rail_map_width: int = 90
    focused_route_coverage: float = 0.70
    focused_track_spacing: float = 4.0
    focused_station_spacing: float = 8.0
    rail_sprite_size: str = "5x3"
    show_direction_animation: bool = True
    preview_fps: int = 30

    def __post_init__(self) -> None:
        if self.default_view not in {"network", "map", "rail-map", "departures", "bus-map"}:
            raise ValueError("map.default_view must be network, rail-map, departures or bus-map")
        if self.default_scope not in {"system", "route", "focused", "cbd", "full"}:
            raise ValueError("map.default_scope must be 'system' or 'route'")
        if self.interpolation_fps < 1 or self.interpolation_fps > 120:
            raise ValueError("map.interpolation_fps must be between 1 and 120")
        if self.live_position_stale_seconds <= 0:
            raise ValueError("map.live_position_stale_seconds must be positive")
        if self.live_position_expiry_seconds < self.live_position_stale_seconds:
            raise ValueError("map live position expiry must not be shorter than stale time")
        if self.padding < 0:
            raise ValueError("map.padding must be non-negative")
        if not 0.80 <= self.system_screen_coverage <= 0.95:
            raise ValueError("map.system_screen_coverage must be between 0.80 and 0.95")
        if self.route_lane_spacing < 1 or self.route_lane_spacing > 4:
            raise ValueError("map.route_lane_spacing must be between 1 and 4")
        if self.train_collision_spacing < 1 or self.train_collision_spacing > 6:
            raise ValueError("map.train_collision_spacing must be between 1 and 6")
        if self.max_led_labels < 1 or self.max_led_labels > 16:
            raise ValueError("map.max_led_labels must be between 1 and 16")
        if not self.cbd_station_names:
            raise ValueError("map.cbd_station_names must not be empty")
        if self.cbd_track_spacing <= 0 or self.cbd_station_spacing <= 0:
            raise ValueError("map CBD spacing values must be positive")
        if self.train_info_panel_width < 0 or self.train_info_panel_width > 64:
            raise ValueError("map.train_info_panel_width must be between 0 and 64")
        if self.auto_cycle_train_seconds < 0:
            raise ValueError("map.auto_cycle_train_seconds must not be negative")
        if self.rail_direction not in {"inbound", "outbound", "both"}:
            raise ValueError("map.rail_direction must be inbound, outbound or both")
        if self.rail_map_width < 88 or self.rail_map_width > 94:
            raise ValueError("map.rail_map_width must be between 88 and 94")
        if self.rail_map_width + self.train_info_panel_width != 128:
            raise ValueError("map rail and information widths must total 128")
        if not 0.6 <= self.focused_route_coverage <= 0.95:
            raise ValueError("map.focused_route_coverage must be between 0.60 and 0.95")
        if self.focused_track_spacing < 3 or self.focused_track_spacing > 8:
            raise ValueError("map.focused_track_spacing must be between 3 and 8")
        if self.focused_station_spacing <= 0:
            raise ValueError("map.focused_station_spacing must be positive")
        if self.rail_sprite_size not in {"5x3", "6x4", "7x5"}:
            raise ValueError("map.rail_sprite_size must be 5x3, 6x4 or 7x5")
        if self.preview_fps < 1 or self.preview_fps > 60:
            raise ValueError("map.preview_fps must be between 1 and 60")


@dataclass(slots=True)
class BusMapConfig:
    enabled: bool = True
    default_route: str = "M1"
    direction: str = "both"
    map_width: int = 90
    info_panel_width: int = 38
    route_screen_coverage: float = 0.70
    lane_spacing: float = 4.0
    station_spacing: float = 12.0
    show_major_stop_labels: bool = True
    show_direction_animation: bool = True
    bus_sprite_size: str = "6x4"
    auto_cycle_bus_seconds: float = 5.0
    live_position_stale_seconds: float = 90.0
    live_position_expiry_seconds: float = 300.0
    background_color: str = "#05080d"
    route_glow: bool = True
    layout_file: str = ""
    preview_fps: int = 30

    def __post_init__(self) -> None:
        if self.direction not in {"inbound", "outbound", "both"}:
            raise ValueError("bus_map.direction must be inbound, outbound or both")
        if self.map_width < 64 or self.map_width > 100:
            raise ValueError("bus_map.map_width must be between 64 and 100")
        if self.info_panel_width < 28 or self.info_panel_width > 48:
            raise ValueError("bus_map.info_panel_width must be between 28 and 48")
        if self.map_width + self.info_panel_width != 128:
            raise ValueError("bus_map map and information widths must total 128")
        if not 0.6 <= self.route_screen_coverage <= 0.95:
            raise ValueError("bus_map.route_screen_coverage must be between 0.60 and 0.95")
        if self.lane_spacing < 3 or self.lane_spacing > 8:
            raise ValueError("bus_map.lane_spacing must be between 3 and 8")
        if self.station_spacing <= 0:
            raise ValueError("bus_map.station_spacing must be positive")
        if self.bus_sprite_size not in {"5x3", "6x4", "7x5"}:
            raise ValueError("bus_map.bus_sprite_size must be 5x3, 6x4 or 7x5")
        if self.auto_cycle_bus_seconds < 0:
            raise ValueError("bus_map.auto_cycle_bus_seconds must not be negative")
        if self.live_position_stale_seconds <= 0:
            raise ValueError("bus_map live stale time must be positive")
        if self.live_position_expiry_seconds < self.live_position_stale_seconds:
            raise ValueError("bus_map live expiry must not be shorter than stale time")
        if self.preview_fps < 1 or self.preview_fps > 60:
            raise ValueError("bus_map.preview_fps must be between 1 and 60")


@dataclass(slots=True)
class AppConfig:
    mode: str = "simulated"
    station_id: str = "SIM-CEN"
    live_station_id: str = "place_twgsta"
    timezone: str = "Australia/Brisbane"
    refresh_seconds: float = 5.0
    scenario: str = "normal"
    speed: float = 1.0
    start_time: datetime = field(
        default_factory=lambda: datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    )
    display: DisplayConfig = field(default_factory=DisplayConfig)
    translink: TranslinkConfig = field(default_factory=TranslinkConfig)
    hub75: Hub75Config = field(default_factory=Hub75Config)
    map: MapConfig = field(default_factory=MapConfig)
    bus_map: BusMapConfig = field(default_factory=BusMapConfig)


def load_config(path: str | Path = "config.toml") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    app = raw.get("app", {})
    simulation = raw.get("simulation", {})
    display = raw.get("display", {})
    translink = raw.get("translink", {})
    hub75 = raw.get("hub75", {})
    map_config = dict(raw.get("map", {}))
    bus_map_config = dict(raw.get("bus_map", {}))
    if "train_info_panel_width" in map_config and "rail_map_width" not in map_config:
        map_config["rail_map_width"] = 128 - int(map_config["train_info_panel_width"])
    if "cbd_station_names" in map_config:
        map_config["cbd_station_names"] = tuple(map_config["cbd_station_names"])
    if "focused_routes" in map_config:
        map_config["focused_routes"] = tuple(map_config["focused_routes"])
    start = datetime.fromisoformat(simulation.get("start_time", "2026-08-18T07:25:00+10:00"))
    return AppConfig(
        mode=str(app.get("mode", "simulated")),
        station_id=str(app.get("station_id", "SIM-CEN")),
        live_station_id=str(app.get("live_station_id", "place_twgsta")),
        timezone=str(app.get("timezone", "Australia/Brisbane")),
        refresh_seconds=float(app.get("refresh_seconds", 5.0)),
        scenario=str(simulation.get("scenario", "normal")),
        speed=float(simulation.get("speed", 1.0)),
        start_time=start,
        display=DisplayConfig(**display),
        translink=TranslinkConfig(**translink),
        hub75=Hub75Config(**hub75),
        map=MapConfig(**map_config),
        bus_map=BusMapConfig(**bus_map_config),
    )

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from PIL import Image

from train_tracker.app import build_providers, run_gui
from train_tracker.bus_map.presenter import BusMapPresenter
from train_tracker.bus_map.renderer import BusMapRenderer, BusRenderOptions
from train_tracker.bus_map.repository import BusNetworkRepository
from train_tracker.clock import BRISBANE
from train_tracker.config import load_config
from train_tracker.demo_recorder import record_demo
from train_tracker.gtfs.importer import import_gtfs
from train_tracker.map.models import MapScope
from train_tracker.map.network_repository import RailNetworkRepository
from train_tracker.map.presenter import MapPresenter
from train_tracker.map.projection import MapViewport
from train_tracker.map.renderer import MapRenderOptions, NetworkMapRenderer
from train_tracker.map.system import system_viewport
from train_tracker.outputs.base import OutputContext
from train_tracker.outputs.diagnostics import DiagnosticPattern, render_diagnostic
from train_tracker.outputs.image_file import ImageFileOutput
from train_tracker.providers.simulated import Scenario, SimulatedProvider
from train_tracker.providers.translink import download_static_gtfs
from train_tracker.rendering.renderer import MatrixRenderer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="train-tracker")
    parser.add_argument("--config", default="config.toml", help="TOML configuration file")
    parser.add_argument("--log-level", default="INFO")
    subparsers = parser.add_subparsers(dest="command")

    gui = subparsers.add_parser("gui", help="Launch the interactive desktop simulator")
    gui.add_argument("--mode", choices=("simulated", "replay", "live"), default=None)
    gui.add_argument("--replay", type=Path)
    gui.add_argument("--view", choices=("departures", "map", "rail-map", "bus-map"), default=None)
    gui.add_argument(
        "--map-scope",
        choices=("system", "route", "focused", "cbd", "full"),
        default=None,
    )
    gui.add_argument("--rail-routes", default=None, help="Comma-separated GTFS rail routes")
    gui.add_argument("--rail-direction", choices=("inbound", "outbound", "both"), default=None)
    gui.add_argument("--bus-route", default=None)
    gui.add_argument("--bus-direction", choices=("inbound", "outbound", "both"), default=None)

    render = subparsers.add_parser("render", help="Render one offline frame to PNG")
    render.add_argument("--output", type=Path, default=Path("output/frame.png"))
    render.add_argument(
        "--scenario", choices=tuple(item.value for item in Scenario), default="normal"
    )
    render.add_argument("--station", default=None)
    render.add_argument("--time", default=None, help="ISO-8601 time; defaults to config")

    render_map = subparsers.add_parser("render-map", help="Render the offline rail map to PNG")
    render_map.add_argument("--mode", choices=("simulated", "live"), default="simulated")
    render_map.add_argument("--output", type=Path, default=Path("output/network.png"))
    render_map.add_argument("--database", type=Path, default=None)
    render_map.add_argument("--width", type=int, default=1200)
    render_map.add_argument("--height", type=int, default=800)
    render_map.add_argument(
        "--scope", choices=("system", "route", "focused", "cbd", "full"), default=None
    )
    render_map.add_argument("--route", action="append", default=[])
    render_map.add_argument("--routes", default=None, help="Comma-separated rail routes")
    render_map.add_argument("--direction", choices=("inbound", "outbound", "both"), default=None)
    render_map.add_argument("--select-train", default=None)
    render_map.add_argument("--time", default=None, help="ISO-8601 simulated time")

    render_bus = subparsers.add_parser(
        "render-bus-map", help="Render the native Brisbane bus/Metro pixel map"
    )
    render_bus.add_argument("--mode", choices=("simulated", "live"), default="simulated")
    render_bus.add_argument("--route", default=None)
    render_bus.add_argument("--direction", choices=("inbound", "outbound", "both"), default=None)
    render_bus.add_argument("--output", type=Path, default=Path("output/metro-led.png"))
    render_bus.add_argument("--database", type=Path, default=None)
    render_bus.add_argument("--width", type=int, default=128)
    render_bus.add_argument("--height", type=int, default=64)
    render_bus.add_argument("--time", default=None, help="ISO-8601 simulated time")
    render_bus.add_argument("--select-bus", default=None)
    render_bus.add_argument("--frame", type=int, default=0)

    recorder = subparsers.add_parser(
        "record-demo", help="Record a deterministic GIF from the real logical renderers"
    )
    recorder.add_argument(
        "--view",
        choices=(
            "departures",
            "rail-map",
            "rail-system-map",
            "rail-route-focus",
            "bus-map",
            "screen-switching",
        ),
        required=True,
    )
    recorder.add_argument("--duration", type=float, default=12)
    recorder.add_argument("--fps", type=int, default=6)
    recorder.add_argument("--scale", type=int, default=6)
    recorder.add_argument("--routes", default="")
    recorder.add_argument("--database", type=Path, default=None)
    recorder.add_argument("--time", default=None)
    recorder.add_argument("--output", type=Path, required=True)

    diagnostic = subparsers.add_parser("diagnostics", help="Render a hardware test pattern")
    diagnostic.add_argument(
        "--pattern", choices=tuple(item.value for item in DiagnosticPattern), default="orientation"
    )
    diagnostic.add_argument("--frame", type=int, default=0)
    diagnostic.add_argument("--output", type=Path, default=Path("output/diagnostic.png"))

    importer = subparsers.add_parser("import-gtfs", help="Import a static GTFS ZIP into SQLite")
    importer.add_argument("zip", type=Path)
    importer.add_argument("--database", type=Path, default=None)

    download = subparsers.add_parser(
        "download-gtfs", help="Download the configured static GTFS ZIP"
    )
    download.add_argument("--output", type=Path, default=Path("data/SEQ_GTFS.zip"))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config(args.config)
    command = args.command or "gui"
    try:
        if command == "gui":
            run_gui(
                config,
                mode=getattr(args, "mode", None),
                replay_path=getattr(args, "replay", None),
                view=getattr(args, "view", None),
                map_scope=getattr(args, "map_scope", None),
                rail_routes=(
                    tuple(item.strip() for item in args.rail_routes.split(",") if item.strip())
                    if getattr(args, "rail_routes", None)
                    else None
                ),
                rail_direction=getattr(args, "rail_direction", None),
                bus_route=getattr(args, "bus_route", None),
                bus_direction=getattr(args, "bus_direction", None),
            )
        elif command == "render":
            now = datetime.fromisoformat(args.time) if args.time else config.start_time
            if now.tzinfo is None:
                now = now.replace(tzinfo=BRISBANE)
            provider = SimulatedProvider(args.scenario)
            station_id = args.station or config.station_id
            snapshot = provider.refresh(station_id, now)
            frame = MatrixRenderer().render(snapshot, now, brightness=config.display.brightness)
            with OutputContext(ImageFileOutput(args.output)) as output:
                output.present(frame)
            print(f"Rendered {args.output} ({frame.width}x{frame.height})")
        elif command == "render-map":
            if args.width < 64 or args.height < 32:
                raise ValueError("render-map dimensions must be at least 64x32")
            now = (
                datetime.fromisoformat(args.time)
                if args.time
                else (datetime.now(BRISBANE) if args.mode == "live" else config.start_time)
            )
            if now.tzinfo is None:
                now = now.replace(tzinfo=BRISBANE)
            database = args.database or Path(config.translink.database)
            repository = RailNetworkRepository(database) if database.exists() else None
            if args.mode == "live" and repository is None:
                raise FileNotFoundError(
                    f"GTFS database not found: {database}. Run train-tracker import-gtfs first."
                )
            presenter = MapPresenter(
                repository,
                mode=args.mode,
                layout_file=config.map.layout_file,
                stale_seconds=config.map.live_position_stale_seconds,
                expiry_seconds=config.map.live_position_expiry_seconds,
                scope=args.scope or config.map.default_scope,
                cbd_station_names=config.map.cbd_station_names,
                cbd_station_spacing=config.map.cbd_station_spacing,
                cbd_track_spacing=config.map.cbd_track_spacing,
                focused_routes=tuple(
                    item.strip()
                    for item in (
                        *args.route,
                        *((args.routes or "").split(",")),
                    )
                    if item.strip()
                )
                or config.map.focused_routes,
                rail_direction=args.direction or config.map.rail_direction,
                rail_map_width=config.map.rail_map_width,
                focused_route_coverage=config.map.focused_route_coverage,
                focused_track_spacing=config.map.focused_track_spacing,
                focused_station_spacing=config.map.focused_station_spacing,
                system_screen_coverage=config.map.system_screen_coverage,
                route_lane_spacing=config.map.route_lane_spacing,
                train_collision_spacing=config.map.train_collision_spacing,
                default_station_id=config.map.default_station_id,
            )
            snapshot = None
            live_provider = None
            if args.mode == "live":
                providers = build_providers(config, include_live=True)
                live_provider = providers["live"]
                snapshot = live_provider.refresh(config.live_station_id, now)
            scene = presenter.prepare(now, snapshot)
            reserve_panel = (
                scene.scope.value in {"focused", "cbd"} and config.map.reserve_train_info_panel
            )
            render_options = MapRenderOptions(
                show_labels=config.map.show_station_labels,
                show_estimates=config.map.show_scheduled_estimates,
                background=config.map.background_color,
                highlighted_station_id=config.map.default_station_id,
                scope=scene.scope,
                show_compass=config.map.show_compass,
                show_symbol_legend=config.map.show_symbol_legend,
                reserve_info_panel=reserve_panel,
                info_panel_width=config.map.train_info_panel_width,
                selected_train_id=args.select_train,
                train_sprite_size=config.map.rail_sprite_size,
                show_direction_animation=config.map.show_direction_animation,
                show_major_labels=config.map.show_major_station_labels,
                show_minor_labels=config.map.show_minor_station_labels,
                max_led_labels=config.map.max_led_labels,
                compact_legend=config.map.compact_legend,
            )
            if scene.scope == MapScope.FOCUSED:
                if args.width < 128 or args.height < 64:
                    raise ValueError("focused render-map dimensions must be at least 128x64")
                viewport = MapViewport.fit(scene.network.bounds, config.map.rail_map_width, 64, 1)
                logical = NetworkMapRenderer().render(
                    scene, (128, 64), viewport=viewport, options=render_options
                )
                frame = (
                    logical
                    if (args.width, args.height) == (128, 64)
                    else logical.resize((args.width, args.height), Image.Resampling.NEAREST)
                )
            elif scene.scope == MapScope.SYSTEM:
                viewport = system_viewport(args.width, args.height)
                frame = NetworkMapRenderer().render(
                    scene,
                    (args.width, args.height),
                    viewport=viewport,
                    options=render_options,
                )
            else:
                compact = args.width <= 160 or args.height <= 80
                panel_width = config.map.train_info_panel_width if compact and reserve_panel else 0
                if compact and reserve_panel and scene.network.reserved_info_bounds is not None:
                    panel_width = args.width - scene.network.reserved_info_bounds[0]
                map_width = args.width - panel_width
                viewport = MapViewport.fit(
                    scene.network.bounds,
                    map_width,
                    args.height,
                    min(config.map.padding, 4 if compact else config.map.padding),
                )
                frame = NetworkMapRenderer().render(
                    scene,
                    (args.width, args.height),
                    viewport=viewport,
                    options=render_options,
                )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            frame.save(args.output, format="PNG")
            if live_provider is not None:
                close = getattr(live_provider, "close", None)
                if callable(close):
                    close()
            print(f"Rendered network map to {args.output} ({args.width}x{args.height})")
        elif command == "render-bus-map":
            if args.width < 128 or args.height < 64:
                raise ValueError("render-bus-map dimensions must be at least 128x64")
            now = (
                datetime.fromisoformat(args.time)
                if args.time
                else (datetime.now(BRISBANE) if args.mode == "live" else config.start_time)
            )
            if now.tzinfo is None:
                now = now.replace(tzinfo=BRISBANE)
            database = args.database or Path(config.translink.database)
            bus_repository = BusNetworkRepository(database) if database.exists() else None
            if args.mode == "live" and bus_repository is None:
                raise FileNotFoundError(
                    f"GTFS database not found: {database}. Run train-tracker import-gtfs first."
                )
            bus_presenter = BusMapPresenter(
                bus_repository,
                route=args.route or config.bus_map.default_route,
                direction=args.direction or config.bus_map.direction,
                map_width=config.bus_map.map_width,
                route_coverage=config.bus_map.route_screen_coverage,
                lane_spacing=config.bus_map.lane_spacing,
                station_spacing=config.bus_map.station_spacing,
                layout_file=config.bus_map.layout_file,
                stale_seconds=config.bus_map.live_position_stale_seconds,
                expiry_seconds=config.bus_map.live_position_expiry_seconds,
            )
            bus_snapshot = None
            bus_live_provider = None
            if args.mode == "live":
                providers = build_providers(config, include_live=True)
                bus_live_provider = providers["live"]
                bus_snapshot = bus_live_provider.refresh(config.live_station_id, now)
            bus_scene = bus_presenter.prepare(now, bus_snapshot)
            bus_frame = BusMapRenderer().render(
                bus_scene,
                (args.width, args.height),
                options=BusRenderOptions(
                    map_width=config.bus_map.map_width,
                    info_panel_width=config.bus_map.info_panel_width,
                    show_labels=config.bus_map.show_major_stop_labels,
                    show_direction_animation=config.bus_map.show_direction_animation,
                    sprite_size=config.bus_map.bus_sprite_size,
                    background=config.bus_map.background_color,
                    route_glow=config.bus_map.route_glow,
                    selected_bus_id=args.select_bus,
                    animation_frame=args.frame,
                ),
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            bus_frame.save(args.output, format="PNG")
            if bus_live_provider is not None:
                close = getattr(bus_live_provider, "close", None)
                if callable(close):
                    close()
            print(f"Rendered bus map to {args.output} ({args.width}x{args.height})")
        elif command == "record-demo":
            demo_time = datetime.fromisoformat(args.time) if args.time else config.start_time
            if demo_time.tzinfo is None:
                demo_time = demo_time.replace(tzinfo=BRISBANE)
            database = args.database or Path(config.translink.database)
            target = record_demo(
                args.view,
                args.output,
                config=config,
                duration=args.duration,
                fps=args.fps,
                scale=args.scale,
                routes=tuple(item.strip() for item in args.routes.split(",") if item.strip()),
                database=database if database.exists() else None,
                start_time=demo_time,
            )
            print(f"Recorded {args.view} demonstration to {target}")
        elif command == "diagnostics":
            frame = render_diagnostic(args.pattern, args.frame)
            with OutputContext(ImageFileOutput(args.output)) as output:
                output.present(frame)
            print(f"Rendered {args.pattern} diagnostic to {args.output}")
        elif command == "import-gtfs":
            database = args.database or Path(config.translink.database)
            counts = import_gtfs(args.zip, database)
            print(f"Imported {args.zip} into {database}")
            for table, count in counts.items():
                print(f"  {table}: {count}")
        elif command == "download-gtfs":
            path = download_static_gtfs(config.translink.static_url, args.output)
            print(f"Downloaded {path}")
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())

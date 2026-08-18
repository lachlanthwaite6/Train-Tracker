from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageColor, ImageSequence

from train_tracker.cli import _parser
from train_tracker.config import AppConfig, MapConfig, load_config
from train_tracker.demo_recorder import logical_demo_frames, record_demo
from train_tracker.map.geometry import distance
from train_tracker.map.models import MapScope, Point, TrainPositionSource
from train_tracker.map.presenter import MapPresenter
from train_tracker.map.projection import MapViewport
from train_tracker.map.renderer import MapRenderOptions, NetworkMapRenderer, draw_train_sprite


def _focused_scene():  # type: ignore[no-untyped-def]
    config = AppConfig()
    presenter = MapPresenter(
        None,
        mode="simulated",
        scope=MapScope.FOCUSED,
        focused_routes=("DEMO-W", "DEMO-S"),
        rail_map_width=config.map.rail_map_width,
        focused_track_spacing=config.map.focused_track_spacing,
        focused_station_spacing=config.map.focused_station_spacing,
    )
    return presenter.prepare(config.start_time)


def test_focused_rail_coverage_station_spacing_tracks_and_multiple_routes() -> None:
    scene = _focused_scene()
    assert scene.scope == MapScope.FOCUSED
    assert len(scene.network.routes) == 2
    all_x = [point.x for shape in scene.network.shapes.values() for point in shape.points]
    assert max(all_x) - min(all_x) >= 82

    for route in scene.network.routes.values():
        route_stations = [scene.network.stations[item] for item in route.station_ids]
        separations = [
            distance(first.position, second.position)
            for first, second in zip(route_stations, route_stations[1:], strict=False)
        ]
        assert min(separations) >= 6
        route_shapes = [scene.network.shapes[item] for item in route.shape_ids]
        assert len(route_shapes) == 2
        assert distance(route_shapes[0].points[0], route_shapes[1].points[0]) >= 3


def test_train_sprite_dimensions_direction_and_live_estimated_stale_styles() -> None:
    live = Image.new("RGB", (24, 16), "black")
    bounds = draw_train_sprite(
        live,
        Point(12, 8),
        (255, 196, 37),
        size="6x4",
        direction_id=0,
        source=TrainPositionSource.LIVE_GPS,
        selected=True,
        dwelling=True,
    )
    assert bounds[2] - bounds[0] + 1 == 6
    assert bounds[3] - bounds[1] + 1 == 4
    assert live.getpixel((bounds[2], bounds[1] + 1)) == (255, 255, 220)
    assert live.getpixel((bounds[0], bounds[1] + 1)) == (255, 72, 72)

    estimated = Image.new("RGB", (24, 16), "black")
    reverse = draw_train_sprite(
        estimated,
        Point(12, 8),
        (255, 196, 37),
        direction_id=1,
        source=TrainPositionSource.SCHEDULED_ESTIMATE,
    )
    stale = Image.new("RGB", (24, 16), "black")
    draw_train_sprite(
        stale,
        Point(12, 8),
        (255, 196, 37),
        source=TrainPositionSource.LIVE_GPS,
        stale=True,
    )
    assert estimated.getpixel((reverse[0], reverse[1] + 1)) == (255, 255, 220)
    assert len({live.tobytes(), estimated.tobytes(), stale.tobytes()}) == 3


def test_focused_native_panel_bounds_selection_and_determinism() -> None:
    scene = _focused_scene()
    renderer = NetworkMapRenderer()
    viewport = MapViewport.fit(scene.network.bounds, 90, 64, 1)
    options = MapRenderOptions(
        scope=MapScope.FOCUSED,
        reserve_info_panel=True,
        info_panel_width=38,
        animation_frame=4,
    )
    empty = renderer.render(scene, (128, 64), viewport=viewport, options=options)
    background = ImageColor.getrgb(options.background)
    assert empty.size == (128, 64)
    assert set(empty.crop((90, 0, 128, 64)).get_flattened_data()) == {background}

    selected = renderer.render(
        scene,
        (128, 64),
        viewport=viewport,
        options=MapRenderOptions(
            scope=MapScope.FOCUSED,
            reserve_info_panel=True,
            info_panel_width=38,
            selected_train_id=scene.markers[0].id,
            animation_frame=4,
        ),
    )
    assert set(selected.crop((90, 0, 128, 64)).get_flattened_data()) != {background}
    assert (
        hashlib.sha256(empty.tobytes()).digest()
        == hashlib.sha256(
            renderer.render(scene, (128, 64), viewport=viewport, options=options).tobytes()
        ).digest()
    )


def test_all_three_cli_view_selections_and_map_configuration(tmp_path: Path) -> None:
    parser = _parser()
    for view in ("departures", "rail-map", "bus-map"):
        assert parser.parse_args(("gui", "--view", view)).view == view
    config = MapConfig(default_view="rail-map", default_scope="focused")
    assert config.rail_map_width + config.train_info_panel_width == 128
    legacy = tmp_path / "legacy.toml"
    legacy.write_text("[map]\ntrain_info_panel_width = 36\n", encoding="utf-8")
    loaded = load_config(legacy)
    assert loaded.map.rail_map_width == 92


def test_demo_frames_and_gif_are_native_scaled_and_deterministic(tmp_path: Path) -> None:
    config = AppConfig()
    for view in ("departures", "rail-map", "bus-map"):
        frames = logical_demo_frames(
            view,
            config=config,
            duration=1,
            fps=2,
            database=None,
        )
        assert frames
        assert all(frame.size == (128, 64) for frame in frames)

    first = tmp_path / "first.gif"
    second = tmp_path / "second.gif"
    for output in (first, second):
        record_demo(
            "screen-switching",
            output,
            config=config,
            duration=3,
            fps=2,
            scale=2,
            database=None,
        )
    assert first.read_bytes() == second.read_bytes()
    with Image.open(first) as animation:
        frames = tuple(ImageSequence.Iterator(animation))
        assert len(frames) >= 6
        assert all(frame.size == (256, 128) for frame in frames)


def test_readme_showcase_media_paths_exist() -> None:
    root = Path(__file__).parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    for name in (
        "departure-board.gif",
        "rail-map.gif",
        "bus-map.gif",
        "screen-switching.gif",
    ):
        relative = f"docs/media/{name}"
        assert relative in readme
        assert (root / relative).exists()

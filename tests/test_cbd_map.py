from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PIL import ImageColor

from train_tracker.clock import BRISBANE
from train_tracker.gtfs.importer import import_gtfs
from train_tracker.map.cbd import (
    TrainSelection,
    build_cbd_focus,
    normalized_station_name,
    project_live_marker_to_cbd,
    resolve_marker_overlaps,
)
from train_tracker.map.geometry import distance
from train_tracker.map.interpolation import scheduled_marker
from train_tracker.map.models import MapScope, Point
from train_tracker.map.network_repository import RailNetworkRepository
from train_tracker.map.presenter import MapPresenter
from train_tracker.map.projection import MapViewport
from train_tracker.map.renderer import MapRenderOptions, NetworkMapRenderer


def _rail_repository(rail_gtfs_zip: Path, tmp_path: Path) -> RailNetworkRepository:
    database = tmp_path / "rail.sqlite3"
    import_gtfs(rail_gtfs_zip, database)
    return RailNetworkRepository(database)


def test_cbd_selection_missing_names_spacing_and_parallel_tracks(
    rail_gtfs_zip: Path, tmp_path: Path
) -> None:
    repository = _rail_repository(rail_gtfs_zip, tmp_path)
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    network = repository.build_network()
    focus = build_cbd_focus(
        network,
        repository.active_trips(now),
        ("Toowong", "Central", "Missing Place"),
        station_spacing=80,
        track_spacing=10,
    )
    assert {normalized_station_name(item.name) for item in focus.network.stations.values()} == {
        "toowong",
        "central",
    }
    assert focus.missing_station_names == ("Missing Place",)
    toowong = focus.network.stations["place_twgsta"]
    central = focus.network.stations["place_cen"]
    assert distance(toowong.position, central.position) >= 80

    blue_shapes = [
        focus.network.shapes[item].points for item in focus.network.routes["R_BLUE"].shape_ids
    ]
    assert len(blue_shapes) >= 2
    assert blue_shapes[0] != blue_shapes[1]

    full_trip = next(
        item
        for item in repository.active_trips(now)
        if item.id == "B1" and item.service_date == now.date()
    )
    full_marker = scheduled_marker(network, full_trip, now)
    focus_trip = next(
        item for item in focus.trips if item.id == "B1" and item.service_date == now.date()
    )
    assert full_marker is not None
    projected = project_live_marker_to_cbd(
        full_marker, network, full_trip, focus.network, focus_trip
    )
    assert projected is not None
    min_x, min_y, max_x, max_y = focus.network.bounds
    assert min_x <= projected.x <= max_x
    assert min_y <= projected.y <= max_y


def test_cbd_layout_station_override(rail_gtfs_zip: Path, tmp_path: Path) -> None:
    repository = _rail_repository(rail_gtfs_zip, tmp_path)
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    layout = tmp_path / "layout.json"
    layout.write_text(
        '{"cbd":{'
        '"stations":{"place_twgsta":[123,456]},'
        '"label_positions":{"place_twgsta":[120,440]},'
        '"interchanges":["toowong"],'
        '"reserved_info_panel_bounds":[92,0,128,64]'
        "}}",
        encoding="utf-8",
    )
    focus = build_cbd_focus(
        repository.build_network(),
        repository.active_trips(now),
        ("Toowong", "Central"),
        layout_file=layout,
    )
    assert focus.network.stations["place_twgsta"].position == Point(123, 456)
    assert focus.network.stations["place_twgsta"].interchange
    assert focus.network.label_positions["place_twgsta"] == Point(120, 440)
    assert focus.network.reserved_info_bounds == (92, 0, 128, 64)


def test_overlap_resolution_and_train_selection_are_deterministic() -> None:
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    scene = MapPresenter(None, mode="simulated").prepare(now)
    marker = scene.markers[0]
    duplicate = replace(marker, id="duplicate")
    resolved = resolve_marker_overlaps((marker, duplicate), 8)
    assert resolved == resolve_marker_overlaps((duplicate, marker), 8)
    assert resolved[0].position != resolved[1].position

    selection = TrainSelection()
    selection.hover(marker.id)
    assert selection.active_id == marker.id
    selection.select_automatically("automatic")
    assert selection.active_id == marker.id
    selection.pin("duplicate")
    selection.hover(None)
    assert selection.active_id == "duplicate"
    selection.clear_pin()
    assert selection.active_id == "automatic"


def test_cbd_scope_switch_and_exact_led_panel_bounds(rail_gtfs_zip: Path, tmp_path: Path) -> None:
    repository = _rail_repository(rail_gtfs_zip, tmp_path)
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    presenter = MapPresenter(
        repository,
        mode="simulated",
        scope=MapScope.CBD,
        cbd_station_names=("Toowong", "Central"),
    )
    cbd = presenter.prepare(now)
    assert cbd.scope == MapScope.CBD
    assert len(cbd.network.stations) == 2
    presenter.set_scope(MapScope.FULL)
    full = presenter.prepare(now)
    assert full.scope == MapScope.FULL
    assert len(full.network.stations) > len(cbd.network.stations)

    options = MapRenderOptions(
        scope=MapScope.CBD,
        reserve_info_panel=True,
        info_panel_width=36,
        show_compass=True,
    )
    viewport = MapViewport.fit(cbd.network.bounds, 92, 64, 4)
    renderer = NetworkMapRenderer()
    empty_panel = renderer.render(cbd, (128, 64), viewport=viewport, options=options)
    assert empty_panel.size == (128, 64)
    background = ImageColor.getrgb(options.background)
    assert set(empty_panel.crop((92, 0, 128, 64)).get_flattened_data()) == {background}

    selected = renderer.render(
        cbd,
        (128, 64),
        viewport=viewport,
        options=MapRenderOptions(
            scope=MapScope.CBD,
            reserve_info_panel=True,
            info_panel_width=36,
            selected_train_id=cbd.markers[0].id,
        ),
    )
    assert set(selected.crop((92, 0, 128, 64)).get_flattened_data()) != {background}


def test_cbd_compass_suppression_symbol_legend_and_deterministic_png() -> None:
    now = datetime(2026, 8, 18, 7, 25, tzinfo=BRISBANE)
    presenter = MapPresenter(
        None,
        mode="simulated",
        scope=MapScope.CBD,
        cbd_station_names=("Toowong", "Central"),
    )
    scene = presenter.prepare(now)
    viewport = MapViewport.fit(scene.network.bounds, 600, 500, 40)
    renderer = NetworkMapRenderer()
    without_compass = renderer.render(
        scene,
        (800, 500),
        viewport=viewport,
        options=MapRenderOptions(
            scope=MapScope.CBD,
            show_compass=False,
            show_symbol_legend=False,
            reserve_info_panel=True,
        ),
    )
    compass_requested = renderer.render(
        scene,
        (800, 500),
        viewport=viewport,
        options=MapRenderOptions(
            scope=MapScope.CBD,
            show_compass=True,
            show_symbol_legend=False,
            reserve_info_panel=True,
        ),
    )
    assert without_compass.tobytes() == compass_requested.tobytes()

    with_symbols = renderer.render(
        scene,
        (800, 500),
        viewport=viewport,
        options=MapRenderOptions(
            scope=MapScope.CBD,
            show_symbol_legend=True,
            reserve_info_panel=True,
        ),
    )
    assert with_symbols.tobytes() != without_compass.tobytes()
    assert (
        hashlib.sha256(with_symbols.tobytes()).digest()
        == hashlib.sha256(
            renderer.render(
                scene,
                (800, 500),
                viewport=viewport,
                options=MapRenderOptions(
                    scope=MapScope.CBD,
                    show_symbol_legend=True,
                    reserve_info_panel=True,
                ),
            ).tobytes()
        ).digest()
    )

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from train_tracker.map.cbd import normalized_station_name
from train_tracker.map.geometry import (
    cumulative_lengths,
    distance,
    point_at_distance,
    snap_to_polyline,
)
from train_tracker.map.models import (
    HitTarget,
    MapScene,
    MapScope,
    Point,
    RailTrip,
    TrainMarker,
    TrainPositionSource,
)
from train_tracker.map.projection import MapViewport
from train_tracker.map.system import (
    LabelRequest,
    Rect,
    choose_information_overlay,
    place_system_labels,
    system_viewport,
)
from train_tracker.rendering.fonts import draw_text as draw_pixel_text
from train_tracker.rendering.layout import abbreviate


@dataclass(frozen=True, slots=True)
class MapRenderOptions:
    show_labels: bool = True
    show_trains: bool = True
    show_estimates: bool = True
    visible_routes: frozenset[str] | None = None
    background: str = "#080a0d"
    highlighted_station_id: str = "place_twgsta"
    scope: MapScope = MapScope.SYSTEM
    show_compass: bool = False
    show_symbol_legend: bool = True
    reserve_info_panel: bool = False
    info_panel_width: int = 36
    selected_train_id: str | None = None
    train_sprite_size: str = "5x3"
    show_direction_animation: bool = True
    animation_frame: int = 0
    show_major_labels: bool = True
    show_minor_labels: bool = False
    show_all_stations: bool = False
    max_led_labels: int = 10
    compact_legend: bool = True


RGB = tuple[int, int, int]


def _rgb(value: str) -> RGB:
    cleaned = value.lstrip("#")
    return tuple(int(cleaned[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _dim(color: RGB, factor: float) -> RGB:
    return tuple(min(255, round(channel * factor)) for channel in color)  # type: ignore[return-value]


def draw_train_sprite(
    image: Image.Image,
    center: Point,
    color: RGB,
    *,
    size: str = "6x4",
    direction_id: int = 0,
    source: TrainPositionSource = TrainPositionSource.SCHEDULED_ESTIMATE,
    stale: bool = False,
    selected: bool = False,
    dwelling: bool = False,
    animation_frame: int = 0,
    heading: Point | None = None,
) -> tuple[int, int, int, int]:
    """Draw a directional miniature train designed for the native LED canvas."""
    width, height = (int(value) for value in size.split("x", 1))
    x0 = round(center.x - width / 2)
    y0 = round(center.y - height / 2)
    x1, y1 = x0 + width - 1, y0 + height - 1
    draw = ImageDraw.Draw(image)
    body = _dim(color, 0.42) if stale else color
    outline = _dim((238, 248, 255), 0.45) if stale else (238, 248, 255)
    if selected and animation_frame % 2 == 0:
        draw.rectangle((x0 - 1, y0 - 1, x1 + 1, y1 + 1), outline=(255, 232, 92))
    if source == TrainPositionSource.LIVE_GPS:
        draw.rectangle((x0, y0, x1, y1), fill=body, outline=outline)
    else:
        draw.rectangle((x0, y0, x1, y1), fill=(8, 10, 13), outline=body)
        for x in range(x0 + 1, x1):
            if (x + animation_frame) % 2 == 0 and y0 + 1 < y1:
                draw.point((x, y0 + 1), fill=body)
    if heading is not None and abs(heading.y) > abs(heading.x):
        forward = heading.y >= 0
        front_y = y1 if forward else y0
        tail_y = y0 if forward else y1
        draw.point((x0 + width // 2, front_y), fill=(255, 255, 220))
        draw.point((x0 + width // 2, tail_y), fill=(255, 72, 72))
    else:
        forward = heading.x >= 0 if heading is not None else direction_id == 0
        front_x = x1 if forward else x0
        tail_x = x0 if forward else x1
        marker_y = y0 + (1 if heading is None else height // 2)
        draw.point((front_x, marker_y), fill=(255, 255, 220))
        draw.point((tail_x, marker_y), fill=(255, 72, 72))
    if width >= 6 and height >= 4:
        draw.line((x0 + 2, y0, x1 - 2, y0), fill=_dim(body, 1.2))
        draw.point((x0 + 1, y1), fill=(0, 0, 0))
        draw.point((x1 - 1, y1), fill=(0, 0, 0))
    if dwelling and animation_frame % 3 == 0:
        draw.point((x0 + width // 2, y1 - 1), fill=(160, 255, 225))
    return x0, y0, x1, y1


class NetworkMapRenderer:
    def __init__(self) -> None:
        self.font = ImageFont.load_default()
        self._static_key: tuple[object, ...] | None = None
        self._static_image: Image.Image | None = None

    def render(
        self,
        scene: MapScene,
        size: tuple[int, int] = (1200, 800),
        *,
        viewport: MapViewport | None = None,
        options: MapRenderOptions | None = None,
    ) -> Image.Image:
        width, height = size
        options = options or MapRenderOptions(scope=scene.scope)
        compact = width <= 160 or height <= 80
        panel_width = self._panel_width(width, scene, options, compact)
        map_width = max(1, width - panel_width)
        padding = (
            1
            if compact and scene.scope in {MapScope.SYSTEM, MapScope.FOCUSED}
            else (4 if compact else 55)
        )
        viewport = viewport or (
            system_viewport(map_width, height)
            if scene.scope == MapScope.SYSTEM
            else MapViewport.fit(scene.network.bounds, map_width, height, padding)
        )
        viewport.width = map_width
        viewport.height = height
        route_ids = options.visible_routes or frozenset(scene.network.routes)
        key = (
            id(scene.network),
            map_width,
            height,
            round(viewport.center.x, 3),
            round(viewport.center.y, 3),
            round(viewport.scale, 8),
            route_ids,
            options.show_labels,
            options.background,
            options.highlighted_station_id,
            options.scope,
            options.show_compass,
            options.show_symbol_legend,
            options.show_all_stations,
            options.compact_legend,
            compact,
        )
        if key != self._static_key or self._static_image is None:
            self._static_image = self._render_static(scene, viewport, options, route_ids, compact)
            self._static_key = key
        map_image = self._static_image.copy()
        draw = ImageDraw.Draw(map_image, "RGBA")
        if compact and options.show_direction_animation:
            self._draw_direction_dashes(
                map_image, scene, viewport, route_ids, options.animation_frame
            )
        trips_by_id = {trip.id: trip for trip in scene.trips}
        if scene.scope == MapScope.SYSTEM and options.show_labels:
            self._draw_system_labels(
                map_image,
                scene,
                viewport,
                route_ids,
                options,
                compact,
            )
        if options.show_trains:
            for marker in scene.markers:
                if marker.route_id not in route_ids:
                    continue
                if (
                    marker.source == TrainPositionSource.SCHEDULED_ESTIMATE
                    and not options.show_estimates
                ):
                    continue
                point = viewport.world_to_screen(marker.position)
                if not (-10 <= point.x <= map_width + 10 and -10 <= point.y <= height + 10):
                    continue
                trip = trips_by_id.get(marker.trip_id)
                dwelling = any(
                    distance(marker.position, station.position) <= 1.5
                    for station in scene.network.stations.values()
                )
                self._draw_train_marker(
                    map_image,
                    draw,
                    point,
                    marker,
                    scene.network.routes[marker.route_id].color,
                    compact=compact,
                    selected=marker.id == options.selected_train_id,
                    size=options.train_sprite_size,
                    direction_id=int(trip.direction_id or 0) if trip is not None else 0,
                    dwelling=dwelling,
                    animation_frame=options.animation_frame,
                    heading=self._marker_heading(scene, marker, trip),
                )
        if compact and scene.scope == MapScope.SYSTEM:
            self._redraw_system_station_centres(map_image, scene, viewport, route_ids, options)
        if compact:
            self._draw_compact_status(map_image, scene)
            if (
                scene.scope == MapScope.SYSTEM
                and options.compact_legend
                and options.selected_train_id is None
            ):
                self._draw_compact_route_key(map_image, scene, route_ids)
        else:
            self._draw_status(draw, scene, map_width)
            if options.show_symbol_legend:
                self._draw_marker_legend(draw, height)

        image = Image.new("RGB", (width, height), options.background)
        image.paste(map_image, (0, 0))
        selected = next(
            (item for item in scene.markers if item.id == options.selected_train_id), None
        )
        if panel_width and selected is not None:
            self._draw_train_info(image, selected, map_width, panel_width, compact)
        elif compact and scene.scope == MapScope.SYSTEM and selected is not None:
            self._draw_system_train_info(image, scene, selected, viewport)
        return image

    @staticmethod
    def _panel_width(width: int, scene: MapScene, options: MapRenderOptions, compact: bool) -> int:
        if options.scope not in {MapScope.FOCUSED, MapScope.CBD} or not options.reserve_info_panel:
            return 0
        if compact:
            bounds = scene.network.reserved_info_bounds
            if bounds is not None and 0 <= bounds[0] < width:
                return width - bounds[0]
            return min(max(0, options.info_panel_width), max(0, width - 32))
        return min(max(220, round(width * 0.24)), max(0, width - 320))

    def _render_static(
        self,
        scene: MapScene,
        viewport: MapViewport,
        options: MapRenderOptions,
        route_ids: frozenset[str],
        compact: bool,
    ) -> Image.Image:
        image = Image.new("RGB", (viewport.width, viewport.height), options.background)
        draw = ImageDraw.Draw(image)
        for route_id, route in scene.network.routes.items():
            if route_id not in route_ids:
                continue
            for shape_id in route.shape_ids:
                shape = scene.network.shapes.get(shape_id)
                if shape is None or len(shape.points) < 2:
                    continue
                points = [
                    (viewport.world_to_screen(point).x, viewport.world_to_screen(point).y)
                    for point in shape.points
                ]
                draw.line(points, fill="#171c22", width=3 if compact else 9, joint="curve")
                draw.line(points, fill=route.color, width=1 if compact else 5, joint="curve")
        for station in scene.network.stations.values():
            serving_visible = [item for item in station.route_ids if item in route_ids]
            if not serving_visible:
                continue
            if (
                compact
                and options.scope == MapScope.SYSTEM
                and not self._system_station_visible(station.id, scene, options)
            ):
                continue
            point = viewport.world_to_screen(station.position)
            radius = (
                (2 if station.interchange else 1) if compact else (5 if station.interchange else 3)
            )
            highlighted = (
                station.id == options.highlighted_station_id
                or normalized_station_name(station.name) == "toowong"
            )
            if highlighted:
                ring = 4 if compact else 10
                draw.ellipse(
                    (point.x - ring, point.y - ring, point.x + ring, point.y + ring),
                    outline="#ffd866",
                    width=1 if compact else 2,
                )
            draw.ellipse(
                (point.x - radius, point.y - radius, point.x + radius, point.y + radius),
                fill="#e8edf2",
                outline="#11161c",
            )
            if (
                options.show_labels
                and options.scope != MapScope.SYSTEM
                and (not compact or options.scope != MapScope.FOCUSED or station.interchange)
            ):
                label_world = scene.network.label_positions.get(station.id)
                label_point = (
                    viewport.world_to_screen(label_world) if label_world is not None else point
                )
                if compact:
                    label = abbreviate(normalized_station_name(station.name), 3)
                    draw_pixel_text(
                        image,
                        (round(label_point.x) + 3, round(label_point.y) - 2),
                        label,
                        (219, 226, 232),
                        max_width=12,
                    )
                else:
                    draw.text(
                        (label_point.x + 7, label_point.y - 7),
                        station.name,
                        font=self.font,
                        fill="#dbe2e8",
                        stroke_width=2,
                        stroke_fill=options.background,
                    )
        if not compact:
            if options.compact_legend:
                if options.scope == MapScope.SYSTEM:
                    self._draw_system_desktop_legend(draw, scene, route_ids, viewport.width)
                else:
                    self._draw_route_legend(draw, scene, route_ids)
            if options.show_symbol_legend:
                self._draw_station_legend(draw)
            if options.show_compass and options.scope == MapScope.FULL:
                self._draw_compass(draw, viewport.width)
        return image

    @staticmethod
    def _draw_train_marker(
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        point: Point,
        marker: TrainMarker,
        route_color: str,
        *,
        compact: bool,
        selected: bool,
        size: str,
        direction_id: int,
        dwelling: bool,
        animation_frame: int,
        heading: Point | None,
    ) -> None:
        if compact:
            bounds = draw_train_sprite(
                image,
                point,
                _rgb(route_color),
                size=size,
                direction_id=direction_id,
                source=marker.source,
                stale=marker.stale,
                selected=selected,
                dwelling=dwelling,
                animation_frame=animation_frame,
                heading=heading,
            )
            if marker.cluster_count > 1:
                count = str(min(9, marker.cluster_count))
                draw.rectangle(
                    (bounds[2] - 1, bounds[1] - 2, bounds[2] + 4, bounds[1] + 3),
                    fill="#080a0d",
                )
                draw_pixel_text(
                    image,
                    (bounds[2], bounds[1] - 2),
                    count,
                    (255, 235, 122),
                    max_width=4,
                )
            return
        radius = 2 if compact else 6
        if marker.source == TrainPositionSource.LIVE_GPS:
            fill = route_color if not marker.stale else "#626872"
            draw.ellipse(
                (point.x - radius, point.y - radius, point.x + radius, point.y + radius),
                fill=fill,
                outline="#ffffff",
                width=1 if compact else 2,
            )
        else:
            draw.ellipse(
                (point.x - radius, point.y - radius, point.x + radius, point.y + radius),
                fill=(8, 10, 13, 220),
                outline=route_color,
                width=1 if compact else 2,
            )
        if selected:
            ring = radius + (2 if compact else 4)
            draw.ellipse(
                (point.x - ring, point.y - ring, point.x + ring, point.y + ring),
                outline="#ffef77",
                width=1 if compact else 2,
            )

    @staticmethod
    def _system_station_visible(
        station_id: str, scene: MapScene, options: MapRenderOptions
    ) -> bool:
        station = scene.network.stations[station_id]
        if options.show_all_stations or station.interchange:
            return True
        if station_id in scene.network.label_positions:
            return True
        # A stable sample retains station rhythm without filling every LED.
        return sum(ord(value) for value in station_id) % 4 == 0

    def _draw_system_labels(
        self,
        image: Image.Image,
        scene: MapScene,
        viewport: MapViewport,
        route_ids: frozenset[str],
        options: MapRenderOptions,
        compact: bool,
    ) -> None:
        requests: list[LabelRequest] = []
        selected = next(
            (marker for marker in scene.markers if marker.id == options.selected_train_id),
            None,
        )
        selected_names = {
            normalized_station_name(name)
            for name in (
                selected.next_station if selected else None,
                selected.previous_station if selected else None,
            )
            if name
        }
        blockers: list[Rect] = []
        if not compact and options.compact_legend:
            blockers.append(Rect(max(0, viewport.width - 500), 0, viewport.width, 72))
        for marker in scene.markers:
            if marker.route_id not in route_ids:
                continue
            point = viewport.world_to_screen(marker.position)
            radius = 3 if compact else 8
            blockers.append(
                Rect(point.x - radius, point.y - radius, point.x + radius, point.y + radius)
            )
        for station in scene.network.stations.values():
            if not any(route_id in route_ids for route_id in station.route_ids):
                continue
            point = viewport.world_to_screen(station.position)
            if station.interchange:
                radius = 2 if compact else 7
                blockers.append(
                    Rect(point.x - radius, point.y - radius, point.x + radius, point.y + radius)
                )
            is_major = station.id in scene.network.label_positions
            is_selected_station = station.id == options.highlighted_station_id
            is_selected_call = normalized_station_name(station.name) in selected_names
            if not (
                is_selected_station
                or is_selected_call
                or (options.show_major_labels and is_major)
                or options.show_minor_labels
            ):
                continue
            requests.append(
                LabelRequest(
                    station.id,
                    (
                        abbreviate(normalized_station_name(station.name), 3)
                        if compact
                        else station.name
                    ),
                    point,
                    priority=(
                        120
                        if is_selected_call
                        else 110
                        if is_selected_station
                        else 80
                        if station.interchange
                        else 40
                    ),
                    interchange=station.interchange,
                )
            )
        placements = place_system_labels(
            tuple(requests),
            width=viewport.width,
            height=viewport.height,
            max_labels=(
                options.max_led_labels
                if compact
                else max(24, options.max_led_labels * 5)
                if options.show_minor_labels
                else max(12, options.max_led_labels * 2)
            ),
            blockers=tuple(blockers),
            status_bounds=Rect(0, 0, min(300, viewport.width), 60 if not compact else 6),
            compact=compact,
        )
        for request in requests:
            label_point = placements.get(request.id)
            if label_point is None:
                continue
            color = (
                (255, 220, 112) if request.id == options.highlighted_station_id else (219, 226, 232)
            )
            if compact:
                draw_pixel_text(
                    image,
                    (round(label_point.x), round(label_point.y)),
                    request.text,
                    color,
                    max_width=12,
                )
            else:
                ImageDraw.Draw(image).text(
                    (label_point.x, label_point.y),
                    request.text,
                    font=self.font,
                    fill=color,
                    stroke_width=2,
                    stroke_fill=options.background,
                )

    @staticmethod
    def _marker_heading(
        scene: MapScene, marker: TrainMarker, trip: RailTrip | None
    ) -> Point | None:
        shape = scene.network.shapes.get(trip.shape_id or "") if trip is not None else None
        if trip is None or shape is None or len(shape.points) < 2 or len(trip.stops) < 2:
            return None
        _point, _distance, along = snap_to_polyline(marker.position, shape.points)
        first_along = snap_to_polyline(
            scene.network.stations[trip.stops[0].station_id].position, shape.points
        )[2]
        last_along = snap_to_polyline(
            scene.network.stations[trip.stops[-1].station_id].position, shape.points
        )[2]
        sign = 1 if last_along >= first_along else -1
        before = point_at_distance(shape.points, along - sign)
        after = point_at_distance(shape.points, along + sign)
        return Point(after.x - before.x, after.y - before.y)

    @staticmethod
    def _redraw_system_station_centres(
        image: Image.Image,
        scene: MapScene,
        viewport: MapViewport,
        route_ids: frozenset[str],
        options: MapRenderOptions,
    ) -> None:
        draw = ImageDraw.Draw(image)
        for station in scene.network.stations.values():
            if not any(route_id in route_ids for route_id in station.route_ids):
                continue
            if not NetworkMapRenderer._system_station_visible(station.id, scene, options):
                continue
            point = viewport.world_to_screen(station.position)
            x, y = round(point.x), round(point.y)
            if station.interchange:
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), outline="#f5f8fb")
            draw.point((x, y), fill="#ffffff")

    @staticmethod
    def _draw_compact_route_key(
        image: Image.Image, scene: MapScene, route_ids: frozenset[str]
    ) -> None:
        visible = [route for route in scene.network.routes.values() if route.id in route_ids]
        if not visible:
            return
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, image.height - 2, image.width - 1, image.height - 1), fill="#080a0d")
        sample_width = max(2, image.width // len(visible))
        for index, route in enumerate(visible):
            left = index * sample_width
            right = image.width - 1 if index == len(visible) - 1 else left + sample_width - 1
            draw.line((left, image.height - 1, right, image.height - 1), fill=route.color)

    def _draw_system_train_info(
        self,
        image: Image.Image,
        scene: MapScene,
        marker: TrainMarker,
        viewport: MapViewport,
    ) -> None:
        marker_points = tuple(viewport.world_to_screen(item.position) for item in scene.markers)
        selected_point = viewport.world_to_screen(marker.position)
        next_station = next(
            (
                station
                for station in scene.network.stations.values()
                if marker.next_station
                and normalized_station_name(station.name)
                == normalized_station_name(marker.next_station)
            ),
            None,
        )
        avoid: tuple[Point, ...] = (selected_point,)
        if next_station is not None:
            avoid += (viewport.world_to_screen(next_station.position),)
        bounds = choose_information_overlay(
            image.width,
            image.height,
            blocked_points=marker_points,
            avoid_points=avoid,
        )
        draw = ImageDraw.Draw(image)
        draw.rectangle(
            (bounds.left, bounds.top, bounds.right, bounds.bottom),
            fill="#0c1117",
            outline="#64717f",
        )
        source = "LIVE" if marker.source == TrainPositionSource.LIVE_GPS else "EST"
        delay = f"{marker.delay_seconds // 60:+d}M" if marker.delay_seconds else "ON TIME"
        lines = (
            f"{abbreviate(marker.route_name, 3)}>{abbreviate(marker.destination, 3)}",
            f"{abbreviate(marker.previous_station or '-', 3)}-"
            f"{abbreviate(marker.next_station or '-', 3)}",
            f"{source} {delay}",
        )
        for index, line in enumerate(lines):
            draw_pixel_text(
                image,
                (round(bounds.left) + 1, round(bounds.top) + 1 + index * 6),
                line,
                (236, 241, 245),
                max_width=round(bounds.right - bounds.left) - 2,
            )

    @staticmethod
    def _draw_direction_dashes(
        image: Image.Image,
        scene: MapScene,
        viewport: MapViewport,
        route_ids: frozenset[str],
        frame: int,
    ) -> None:
        draw = ImageDraw.Draw(image)
        for route_id in route_ids:
            route = scene.network.routes.get(route_id)
            if route is None:
                continue
            color = _rgb(route.color)
            for shape_id in route.shape_ids:
                shape = scene.network.shapes.get(shape_id)
                if shape is None or len(shape.points) < 2:
                    continue
                screen_points = tuple(viewport.world_to_screen(point) for point in shape.points)
                lengths = cumulative_lengths(screen_points)
                for target in range(frame % 12, round(lengths[-1]), 12):
                    point = point_at_distance(screen_points, float(target))
                    draw.point((round(point.x), round(point.y)), fill=_dim(color, 1.35))

    def _draw_status(self, draw: ImageDraw.ImageDraw, scene: MapScene, width: int) -> None:
        box_right = min(292, width - 10)
        if box_right <= 60:
            return
        draw.rounded_rectangle(
            (10, 10, box_right, 54), radius=6, fill=(12, 16, 21, 225), outline="#39414c"
        )
        draw.text(
            (20, 17),
            f"{scene.now:%a %d %b  %H:%M:%S} Brisbane",
            font=self.font,
            fill="#f2f5f7",
        )
        status_color = {
            "Live": "#54d98c",
            "Mixed": "#f4c95d",
            "Simulated": "#77bdfb",
            "Stale": "#d7a84a",
            "Offline": "#e26d6d",
        }.get(scene.status, "#aeb7c2")
        draw.text(
            (20, 34),
            f"{scene.status}  •  {scene.freshness[:34]}",
            font=self.font,
            fill=status_color,
        )

    @staticmethod
    def _draw_compact_status(image: Image.Image, scene: MapScene) -> None:
        color = {
            "Live": (84, 217, 140),
            "Mixed": (244, 201, 93),
            "Simulated": (119, 189, 251),
            "Stale": (215, 168, 74),
            "Offline": (226, 109, 109),
        }.get(scene.status, (174, 183, 194))
        draw_pixel_text(image, (1, 1), scene.now.strftime("%H:%M"), color, max_width=20)

    def _draw_route_legend(
        self, draw: ImageDraw.ImageDraw, scene: MapScene, route_ids: frozenset[str]
    ) -> None:
        y = 76
        for route_id, route in scene.network.routes.items():
            if route_id not in route_ids:
                continue
            draw.line((18, y + 5, 38, y + 5), fill=route.color, width=5)
            draw.text((45, y), route.name, font=self.font, fill="#cbd3da")
            y += 17

    def _draw_system_desktop_legend(
        self,
        draw: ImageDraw.ImageDraw,
        scene: MapScene,
        route_ids: frozenset[str],
        width: int,
    ) -> None:
        routes = [route for route in scene.network.routes.values() if route.id in route_ids]
        if not routes:
            return
        rows = min(4, len(routes))
        column_width = 240
        left = max(10, width - column_width * math.ceil(len(routes) / rows) - 12)
        right = width - 10
        draw.rounded_rectangle((left, 8, right, 68), radius=5, fill="#0c1117", outline="#303946")
        for index, route in enumerate(routes):
            column = index // rows
            row = index % rows
            x = left + 10 + column * column_width
            y = 15 + row * 13
            draw.line((x, y + 5, x + 18, y + 5), fill=route.color, width=4)
            draw.text(
                (x + 25, y),
                route.name,
                font=self.font,
                fill="#cbd3da",
            )

    def _draw_station_legend(self, draw: ImageDraw.ImageDraw) -> None:
        y = 58
        draw.ellipse((18, y, 24, y + 6), fill="#e8edf2")
        draw.text((30, y - 3), "Station", font=self.font, fill="#dbe2e8")
        draw.ellipse((86, y - 2, 96, y + 8), fill="#e8edf2", outline="#ffffff")
        draw.text((102, y - 3), "Interchange", font=self.font, fill="#dbe2e8")

    def _draw_marker_legend(self, draw: ImageDraw.ImageDraw, height: int) -> None:
        y = height - 31
        draw.ellipse((16, y, 26, y + 10), fill="#5da9e9", outline="#ffffff", width=1)
        draw.text((32, y), "Live GPS", font=self.font, fill="#dbe2e8")
        draw.ellipse((94, y, 104, y + 10), outline="#5da9e9", width=2)
        draw.text((110, y), "Estimate", font=self.font, fill="#dbe2e8")
        draw.ellipse((168, y, 178, y + 10), fill="#626872", outline="#aeb7c2", width=1)
        draw.text((184, y), "Stale", font=self.font, fill="#dbe2e8")

    def _draw_compass(self, draw: ImageDraw.ImageDraw, width: int) -> None:
        draw.line((width - 31, 54, width - 31, 25), fill="#dbe2e8", width=2)
        draw.polygon(((width - 31, 18), (width - 37, 29), (width - 25, 29)), fill="#dbe2e8")
        draw.text((width - 35, 58), "N", font=self.font, fill="#dbe2e8")

    def _draw_train_info(
        self,
        image: Image.Image,
        marker: TrainMarker,
        x: int,
        width: int,
        compact: bool,
    ) -> None:
        if compact:
            source = "LIVE" if marker.source == TrainPositionSource.LIVE_GPS else "EST"
            delay = f"{marker.delay_seconds // 60:+d}M" if marker.delay_seconds else "ON TIME"
            lines = (
                abbreviate(marker.route_name, 8),
                abbreviate(marker.destination, 8),
                abbreviate(marker.previous_station or "-", 8),
                ">" + abbreviate(marker.next_station or "-", 7),
                source,
                delay,
            )
            for index, line in enumerate(lines):
                draw_pixel_text(
                    image,
                    (x + 1, 2 + index * 9),
                    line,
                    (235, 240, 244),
                    max_width=max(0, width - 2),
                )
            return
        draw = ImageDraw.Draw(image)
        source = "LIVE GPS" if marker.source == TrainPositionSource.LIVE_GPS else "ESTIMATE"
        age = "" if marker.age_seconds is None else f"  Age {marker.age_seconds:.0f}s"
        values = (
            marker.route_name,
            f"To {marker.destination}",
            f"{marker.previous_station or '—'} →",
            marker.next_station or "—",
            f"{source}{age}",
            f"Delay {marker.delay_seconds:+d}s",
        )
        y = 18
        for index, value in enumerate(values):
            fill = "#ffef77" if index == 0 else "#dbe2e8"
            draw.text((x + 12, y), value, font=self.font, fill=fill)
            y += 19


def hit_test(
    scene: MapScene,
    viewport: MapViewport,
    screen: Point,
    *,
    station_radius: float = 12,
    marker_radius: float = 12,
) -> HitTarget | None:
    candidates: list[HitTarget] = []
    for marker in scene.markers:
        point = viewport.world_to_screen(marker.position)
        value = math.hypot(screen.x - point.x, screen.y - point.y)
        if value <= marker_radius:
            candidates.append(HitTarget("train", marker.id, value))
    for station in scene.network.stations.values():
        point = viewport.world_to_screen(station.position)
        value = math.hypot(screen.x - point.x, screen.y - point.y)
        if value <= station_radius:
            candidates.append(HitTarget("station", station.id, value))
    return min(candidates, key=lambda item: item.distance) if candidates else None

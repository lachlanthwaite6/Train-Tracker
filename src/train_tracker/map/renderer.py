from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from train_tracker.map.cbd import normalized_station_name
from train_tracker.map.geometry import cumulative_lengths, distance, point_at_distance
from train_tracker.map.models import (
    HitTarget,
    MapScene,
    MapScope,
    Point,
    TrainMarker,
    TrainPositionSource,
)
from train_tracker.map.projection import MapViewport
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
    scope: MapScope = MapScope.FULL
    show_compass: bool = False
    show_symbol_legend: bool = True
    reserve_info_panel: bool = False
    info_panel_width: int = 36
    selected_train_id: str | None = None
    train_sprite_size: str = "6x4"
    show_direction_animation: bool = True
    animation_frame: int = 0


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
    front_x = x1 if direction_id == 0 else x0
    tail_x = x0 if direction_id == 0 else x1
    draw.point((front_x, y0 + 1), fill=(255, 255, 220))
    draw.point((tail_x, y0 + 1), fill=(255, 72, 72))
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
        padding = 1 if compact and scene.scope == MapScope.FOCUSED else (4 if compact else 55)
        viewport = viewport or MapViewport.fit(scene.network.bounds, map_width, height, padding)
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
                )
        if compact:
            self._draw_compact_status(map_image, scene)
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
            if options.show_labels and (
                not compact or options.scope != MapScope.FOCUSED or station.interchange
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
    ) -> None:
        if compact:
            draw_train_sprite(
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

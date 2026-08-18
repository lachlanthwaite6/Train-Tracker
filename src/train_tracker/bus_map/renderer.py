from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from train_tracker.bus_map.models import BusMapScene, BusMarker
from train_tracker.map.geometry import cumulative_lengths, point_at_distance
from train_tracker.map.models import Point, TrainPositionSource
from train_tracker.rendering.fonts import draw_text
from train_tracker.rendering.layout import abbreviate

RGB = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class BusRenderOptions:
    map_width: int = 90
    info_panel_width: int = 38
    show_labels: bool = True
    show_live: bool = True
    show_estimates: bool = True
    show_direction_animation: bool = True
    sprite_size: str = "6x4"
    background: str = "#05080d"
    route_glow: bool = True
    selected_bus_id: str | None = None
    animation_frame: int = 0


def _rgb(value: str) -> RGB:
    cleaned = value.lstrip("#")
    return tuple(int(cleaned[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _dim(color: RGB, factor: float) -> RGB:
    return tuple(min(255, round(channel * factor)) for channel in color)  # type: ignore[return-value]


def draw_bus_sprite(
    image: Image.Image,
    center: Point,
    color: RGB,
    *,
    size: str,
    direction_id: int,
    source: TrainPositionSource,
    stale: bool = False,
    selected: bool = False,
    dwelling: bool = False,
    animation_frame: int = 0,
) -> tuple[int, int, int, int]:
    width, height = (int(value) for value in size.split("x", 1))
    x0 = round(center.x - width / 2)
    y0 = round(center.y - height / 2)
    x1, y1 = x0 + width - 1, y0 + height - 1
    draw = ImageDraw.Draw(image)
    body = _dim(color, 0.42) if stale else color
    outline = _dim((235, 247, 255), 0.45) if stale else (235, 247, 255)
    if selected and animation_frame % 2 == 0:
        draw.rectangle((x0 - 1, y0 - 1, x1 + 1, y1 + 1), outline=(255, 230, 90))
    if source == TrainPositionSource.LIVE_GPS:
        draw.rectangle((x0, y0, x1, y1), fill=body, outline=outline)
    else:
        draw.rectangle((x0, y0, x1, y1), fill=(5, 8, 13), outline=body)
        for x in range(x0 + 1, x1):
            if (x + animation_frame) % 2 == 0 and y0 + 1 < y1:
                draw.point((x, y0 + 1), fill=body)
    front_x = x1 if direction_id == 0 else x0
    draw.point((front_x, y0 + 1), fill=(255, 255, 210))
    if height >= 4:
        draw.point((x0 + 1, y1), fill=(0, 0, 0))
        draw.point((x1 - 1, y1), fill=(0, 0, 0))
    if dwelling and animation_frame % 3 == 0:
        door_x = x0 + width // 2
        draw.point((door_x, y1 - 1), fill=(180, 255, 235))
    return x0, y0, x1, y1


class BusMapRenderer:
    logical_size = (128, 64)

    def render(
        self,
        scene: BusMapScene,
        size: tuple[int, int] = logical_size,
        *,
        options: BusRenderOptions | None = None,
    ) -> Image.Image:
        options = options or BusRenderOptions()
        logical = self._render_logical(scene, options)
        if size == self.logical_size:
            return logical
        return logical.resize(size, Image.Resampling.NEAREST)

    def _render_logical(self, scene: BusMapScene, options: BusRenderOptions) -> Image.Image:
        image = Image.new("RGB", self.logical_size, options.background)
        draw = ImageDraw.Draw(image)
        route_color = _rgb(scene.route.color)
        for shape in scene.network.shapes.values():
            points = [(round(point.x), round(point.y)) for point in shape.points]
            if options.route_glow:
                draw.line(points, fill=_dim(route_color, 0.22), width=3, joint="curve")
            draw.line(points, fill=route_color, width=1, joint="curve")
            if options.show_direction_animation:
                self._draw_direction_dashes(
                    draw, shape.points, route_color, options.animation_frame
                )

        marker_positions = {
            marker.marker.next_station: marker.marker.position for marker in scene.markers
        }
        major_index = 0
        for station_id, station in scene.network.stations.items():
            point = station.position
            major = station_id in scene.major_stop_ids
            near_bus = any(
                abs(point.x - marker.x) <= 4 and abs(point.y - marker.y) <= 4
                for marker in marker_positions.values()
            )
            glow = (110, 255, 235) if near_bus else (98, 145, 165)
            if major:
                draw.rectangle(
                    (
                        round(point.x) - 2,
                        round(point.y) - 2,
                        round(point.x) + 2,
                        round(point.y) + 2,
                    ),
                    outline=glow,
                )
            draw.point((round(point.x), round(point.y)), fill=(235, 250, 255))
            if options.show_labels and major:
                label = abbreviate(station.name.replace(" station", ""), 3)
                label_y = round(point.y) - 7 if major_index % 2 == 0 else round(point.y) + 3
                draw_text(
                    image,
                    (
                        max(0, min(options.map_width - 12, round(point.x) - 4)),
                        max(0, min(58, label_y)),
                    ),
                    label,
                    (190, 220, 230),
                    max_width=12,
                )
                major_index += 1

        for bus in scene.markers:
            marker = bus.marker
            if marker.source == TrainPositionSource.LIVE_GPS and not options.show_live:
                continue
            if (
                marker.source == TrainPositionSource.SCHEDULED_ESTIMATE
                and not options.show_estimates
            ):
                continue
            draw_bus_sprite(
                image,
                marker.position,
                route_color,
                size=options.sprite_size,
                direction_id=bus.direction_id,
                source=marker.source,
                stale=marker.stale,
                selected=marker.id == options.selected_bus_id,
                dwelling=bus.dwelling,
                animation_frame=options.animation_frame,
            )

        # Route glows, lane offsets, stop halos and sprites are deliberately
        # hard-clipped at the panel boundary.  The physical display contract
        # reserves these pixels as true black space until a bus is selected.
        draw.rectangle(
            (options.map_width, 0, self.logical_size[0] - 1, self.logical_size[1] - 1),
            fill=_rgb(options.background),
        )
        selected = next((bus for bus in scene.markers if bus.id == options.selected_bus_id), None)
        if selected is None:
            self._draw_compact_legend(image, scene, options)
        else:
            self._draw_info(image, selected, options)
        return image

    @staticmethod
    def _draw_direction_dashes(
        draw: ImageDraw.ImageDraw,
        points: tuple[Point, ...],
        color: RGB,
        frame: int,
    ) -> None:
        lengths = cumulative_lengths(points)
        if not lengths:
            return
        phase = frame % 12
        for target in range(phase, round(lengths[-1]), 12):
            point = point_at_distance(points, float(target))
            draw.point((round(point.x), round(point.y)), fill=_dim(color, 1.35))

    @staticmethod
    def _draw_compact_legend(
        image: Image.Image, scene: BusMapScene, options: BusRenderOptions
    ) -> None:
        draw = ImageDraw.Draw(image)
        color = _rgb(scene.route.color)
        draw.line((1, 61, 8, 61), fill=color)
        draw_text(image, (10, 59), abbreviate(scene.route.short_name, 4), color, max_width=16)
        draw.rectangle((31, 59, 35, 62), fill=color, outline=(240, 250, 255))
        draw.rectangle((43, 59, 47, 62), outline=color)
        draw_text(image, (50, 59), abbreviate(scene.status, 8), (160, 190, 205), max_width=32)

    @staticmethod
    def _draw_info(image: Image.Image, bus: BusMarker, options: BusRenderOptions) -> None:
        marker = bus.marker
        source = "LIVE" if marker.source == TrainPositionSource.LIVE_GPS else "EST"
        age = "" if marker.age_seconds is None else f" {marker.age_seconds:.0f}S"
        delay = f"{marker.delay_seconds // 60:+d} MIN" if marker.delay_seconds else "ON TIME"
        lines = (
            marker.route_name,
            marker.destination,
            abbreviate(marker.previous_station or "-", 5)
            + ">"
            + abbreviate(marker.next_station or "-", 5),
            source + age,
            delay,
        )
        x = options.map_width + 1
        width = max(0, options.info_panel_width - 2)
        for index, value in enumerate(lines):
            draw_text(
                image,
                (x, 4 + index * 11),
                abbreviate(value, max(1, width // 4)),
                (235, 246, 250),
                max_width=width,
            )

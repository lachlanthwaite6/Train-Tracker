from __future__ import annotations

import math
from dataclasses import dataclass

from train_tracker.map.models import GeoPoint, Point

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True, slots=True)
class LocalProjection:
    """Stable local equirectangular projection in metres for SEQ."""

    origin: GeoPoint

    def project(self, latitude: float, longitude: float) -> Point:
        lat = math.radians(latitude)
        lon = math.radians(longitude)
        lat0 = math.radians(self.origin.latitude)
        lon0 = math.radians(self.origin.longitude)
        return Point(
            EARTH_RADIUS_M * (lon - lon0) * math.cos(lat0),
            -EARTH_RADIUS_M * (lat - lat0),
        )

    def unproject(self, point: Point) -> GeoPoint:
        lat0 = math.radians(self.origin.latitude)
        latitude = self.origin.latitude - math.degrees(point.y / EARTH_RADIUS_M)
        longitude = self.origin.longitude + math.degrees(
            point.x / (EARTH_RADIUS_M * math.cos(lat0))
        )
        return GeoPoint(latitude, longitude)


@dataclass(slots=True)
class MapViewport:
    width: int
    height: int
    center: Point
    scale: float
    padding: int = 40

    @classmethod
    def fit(
        cls,
        bounds: tuple[float, float, float, float],
        width: int,
        height: int,
        padding: int = 40,
    ) -> MapViewport:
        min_x, min_y, max_x, max_y = bounds
        usable_w = max(1, width - padding * 2)
        usable_h = max(1, height - padding * 2)
        world_w = max(1.0, max_x - min_x)
        world_h = max(1.0, max_y - min_y)
        return cls(
            width,
            height,
            Point((min_x + max_x) / 2, (min_y + max_y) / 2),
            min(usable_w / world_w, usable_h / world_h),
            padding,
        )

    def world_to_screen(self, point: Point) -> Point:
        return Point(
            self.width / 2 + (point.x - self.center.x) * self.scale,
            self.height / 2 + (point.y - self.center.y) * self.scale,
        )

    def screen_to_world(self, point: Point) -> Point:
        return Point(
            self.center.x + (point.x - self.width / 2) / self.scale,
            self.center.y + (point.y - self.height / 2) / self.scale,
        )

    def zoom_at(self, factor: float, screen: Point) -> None:
        before = self.screen_to_world(screen)
        self.scale = min(max(self.scale * factor, 0.0001), 100.0)
        after = self.screen_to_world(screen)
        self.center = Point(
            self.center.x + before.x - after.x,
            self.center.y + before.y - after.y,
        )

    def pan(self, dx_pixels: float, dy_pixels: float) -> None:
        self.center = Point(
            self.center.x - dx_pixels / self.scale,
            self.center.y - dy_pixels / self.scale,
        )

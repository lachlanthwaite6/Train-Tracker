from __future__ import annotations

import math

from train_tracker.map.models import Point


def distance(a: Point, b: Point) -> float:
    return math.hypot(b.x - a.x, b.y - a.y)


def cumulative_lengths(points: tuple[Point, ...]) -> tuple[float, ...]:
    if not points:
        return ()
    values = [0.0]
    for start, end in zip(points, points[1:], strict=False):
        values.append(values[-1] + distance(start, end))
    return tuple(values)


def point_at_distance(points: tuple[Point, ...], target: float) -> Point:
    if not points:
        raise ValueError("polyline has no points")
    if len(points) == 1:
        return points[0]
    lengths = cumulative_lengths(points)
    target = min(max(0.0, target), lengths[-1])
    for index in range(len(points) - 1):
        if target <= lengths[index + 1]:
            span = max(1e-9, lengths[index + 1] - lengths[index])
            ratio = (target - lengths[index]) / span
            start, end = points[index], points[index + 1]
            return Point(start.x + (end.x - start.x) * ratio, start.y + (end.y - start.y) * ratio)
    return points[-1]


def snap_to_polyline(point: Point, points: tuple[Point, ...]) -> tuple[Point, float, float]:
    """Return snapped point, perpendicular distance and distance along polyline."""

    if not points:
        raise ValueError("polyline has no points")
    if len(points) == 1:
        return points[0], distance(point, points[0]), 0.0
    best_point = points[0]
    best_distance = float("inf")
    best_along = 0.0
    travelled = 0.0
    for start, end in zip(points, points[1:], strict=False):
        dx, dy = end.x - start.x, end.y - start.y
        length_sq = dx * dx + dy * dy
        ratio = (
            0.0
            if length_sq == 0
            else ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_sq
        )
        ratio = min(1.0, max(0.0, ratio))
        candidate = Point(start.x + dx * ratio, start.y + dy * ratio)
        candidate_distance = distance(point, candidate)
        segment_length = math.sqrt(length_sq)
        if candidate_distance < best_distance:
            best_point = candidate
            best_distance = candidate_distance
            best_along = travelled + segment_length * ratio
        travelled += segment_length
    return best_point, best_distance, best_along


def offset_polyline(points: tuple[Point, ...], offset: float) -> tuple[Point, ...]:
    """Offset a polyline using averaged segment normals.

    This intentionally small geometry primitive is sufficient for schematic
    transit tracks and remains deterministic at sharp joins.
    """
    if len(points) < 2 or offset == 0:
        return points
    normals: list[Point] = []
    for start, end in zip(points, points[1:], strict=False):
        dx, dy = end.x - start.x, end.y - start.y
        length = max(1e-9, math.hypot(dx, dy))
        normals.append(Point(-dy / length, dx / length))
    result: list[Point] = []
    for index, point in enumerate(points):
        if index == 0:
            normal = normals[0]
        elif index == len(points) - 1:
            normal = normals[-1]
        else:
            nx = normals[index - 1].x + normals[index].x
            ny = normals[index - 1].y + normals[index].y
            length = max(1e-9, math.hypot(nx, ny))
            normal = Point(nx / length, ny / length)
        result.append(Point(point.x + normal.x * offset, point.y + normal.y * offset))
    return tuple(result)

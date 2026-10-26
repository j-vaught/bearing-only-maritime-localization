"""Dependency-light reference line-of-sight checks against land polygons.

Coordinates are planar ``(east, north)`` pairs in metres. Each polygon is its
outer boundary as a sequence of vertices; the first vertex may optionally be
repeated at the end. A ray is obstructed only when it passes through polygon
interior. Touching or following a shoreline boundary alone does not obstruct.

``tolerance_m`` is an interior-penetration tolerance, not a coordinate
rounding tolerance. Interior intervals no longer than this value are ignored
to avoid treating numerical slivers at a shoreline as meaningful blockage.
The open ray is checked, so a target exactly on a shoreline is visible from
water when the ray reaches the boundary without crossing land interior.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from math import hypot, isfinite
from typing import TypeAlias

Point2D: TypeAlias = tuple[float, float]
LandPolygon: TypeAlias = Sequence[Point2D]

_PARAMETER_EPSILON = 1e-12


def line_of_sight_clear(
    observer: Point2D,
    target: Point2D,
    land_polygons: Iterable[LandPolygon],
    *,
    tolerance_m: float = 1e-6,
) -> bool:
    """Return whether the open observer-to-target segment avoids land.

    Polygon boundaries are closed for intersection calculation, but boundary
    contact alone is not considered an obstruction. A positive-length
    interior crossing blocks visibility when its length exceeds
    ``tolerance_m``. The function supports concave polygons and either winding
    direction. Rings may be open or closed.

    Args:
        observer: Observer position in planar east/north metres.
        target: Target position in the same coordinate frame.
        land_polygons: Polygon rings, each represented by ``(east, north)``
            vertices. Holes are not represented by this reference interface.
        tolerance_m: Minimum interior path length treated as an obstruction.

    Raises:
        ValueError: If coordinates, polygons, or tolerance are invalid.
    """
    _validate_point(observer, "observer")
    _validate_point(target, "target")
    if not isfinite(tolerance_m) or tolerance_m < 0.0:
        raise ValueError("tolerance_m must be finite and nonnegative")

    ray_length = hypot(target[0] - observer[0], target[1] - observer[1])
    if ray_length == 0.0:
        return True

    for polygon in land_polygons:
        ring = _normalize_ring(polygon)
        if _ray_crosses_polygon_interior(observer, target, ray_length, ring, tolerance_m):
            return False
    return True


def _validate_point(point: Point2D, name: str) -> None:
    if len(point) != 2 or not all(isfinite(value) for value in point):
        raise ValueError(f"{name} must contain two finite coordinates")


def _normalize_ring(polygon: LandPolygon) -> tuple[Point2D, ...]:
    ring = tuple((float(point[0]), float(point[1])) for point in polygon)
    for point in ring:
        _validate_point(point, "polygon vertex")
    if len(ring) >= 2 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3 or len(set(ring)) < 3:
        raise ValueError("each land polygon must have at least three distinct vertices")
    return ring


def _ray_crosses_polygon_interior(
    start: Point2D,
    end: Point2D,
    ray_length: float,
    ring: tuple[Point2D, ...],
    tolerance_m: float,
) -> bool:
    cuts = [0.0, 1.0]
    for index, first in enumerate(ring):
        second = ring[(index + 1) % len(ring)]
        cuts.extend(_segment_intersection_parameters(start, end, first, second))

    cuts.sort()
    unique_cuts: list[float] = []
    for parameter in cuts:
        if not unique_cuts or parameter - unique_cuts[-1] > _PARAMETER_EPSILON:
            unique_cuts.append(parameter)

    for left, right in zip(unique_cuts, unique_cuts[1:]):
        interval_length = (right - left) * ray_length
        if interval_length <= tolerance_m:
            continue
        midpoint_parameter = (left + right) / 2.0
        midpoint = (
            start[0] + midpoint_parameter * (end[0] - start[0]),
            start[1] + midpoint_parameter * (end[1] - start[1]),
        )
        if _strictly_inside(midpoint, ring):
            return True
    return False


def _segment_intersection_parameters(
    start: Point2D,
    end: Point2D,
    edge_start: Point2D,
    edge_end: Point2D,
) -> list[float]:
    """Return ray parameters where two closed segments meet, including overlap."""
    ray = (end[0] - start[0], end[1] - start[1])
    edge = (edge_end[0] - edge_start[0], edge_end[1] - edge_start[1])
    offset = (edge_start[0] - start[0], edge_start[1] - start[1])
    denominator = _cross(ray, edge)
    numerator_t = _cross(offset, edge)
    numerator_u = _cross(offset, ray)

    scale = max(1.0, hypot(*ray) * hypot(*edge))
    epsilon = _PARAMETER_EPSILON * scale
    if abs(denominator) > epsilon:
        parameter = numerator_t / denominator
        edge_parameter = numerator_u / denominator
        if (
            -_PARAMETER_EPSILON <= parameter <= 1.0 + _PARAMETER_EPSILON
            and -_PARAMETER_EPSILON <= edge_parameter <= 1.0 + _PARAMETER_EPSILON
        ):
            return [min(1.0, max(0.0, parameter))]
        return []

    if abs(_cross(offset, ray)) > epsilon:
        return []

    ray_squared = ray[0] * ray[0] + ray[1] * ray[1]
    if ray_squared == 0.0:
        return []
    first_parameter = (
        (edge_start[0] - start[0]) * ray[0] + (edge_start[1] - start[1]) * ray[1]
    ) / ray_squared
    second_parameter = (
        (edge_end[0] - start[0]) * ray[0] + (edge_end[1] - start[1]) * ray[1]
    ) / ray_squared
    overlap_start = max(0.0, min(first_parameter, second_parameter))
    overlap_end = min(1.0, max(first_parameter, second_parameter))
    if overlap_start <= overlap_end + _PARAMETER_EPSILON:
        return [min(1.0, max(0.0, overlap_start)), min(1.0, max(0.0, overlap_end))]
    return []


def _strictly_inside(point: Point2D, ring: tuple[Point2D, ...]) -> bool:
    """Ray-cast point-in-ring test; points on the boundary are outside."""
    inside = False
    x, y = point
    for index, first in enumerate(ring):
        second = ring[(index + 1) % len(ring)]
        if _point_on_segment(point, first, second):
            return False
        x1, y1 = first
        x2, y2 = second
        if (y1 > y) != (y2 > y):
            crossing_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing_x:
                inside = not inside
    return inside


def _point_on_segment(point: Point2D, start: Point2D, end: Point2D) -> bool:
    edge = (end[0] - start[0], end[1] - start[1])
    offset = (point[0] - start[0], point[1] - start[1])
    cross = _cross(edge, offset)
    scale = max(1.0, hypot(*edge) * hypot(*offset))
    if abs(cross) > _PARAMETER_EPSILON * scale:
        return False
    dot = offset[0] * edge[0] + offset[1] * edge[1]
    return -_PARAMETER_EPSILON <= dot <= edge[0] ** 2 + edge[1] ** 2 + _PARAMETER_EPSILON


def _cross(first: Point2D, second: Point2D) -> float:
    return first[0] * second[1] - first[1] * second[0]

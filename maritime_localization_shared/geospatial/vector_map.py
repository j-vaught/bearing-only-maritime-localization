"""Indexed polygon water and line-of-sight queries with explicit unknown coverage.

All geometry coordinates use one projected coordinate reference system with
metre axes. This module never reprojects geometry; callers must supply geometry
already expressed in the declared CRS.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite
from typing import Literal, cast

from pyproj import CRS, Transformer
from shapely import from_geojson, transform
from shapely.geometry import GeometryCollection, LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree

from maritime_localization_shared.contracts.inputs_v2 import MapV2

Point2D = tuple[float, float]
WaterStatus = Literal["water", "land", "unknown"]
Visibility = Literal["clear", "blocked", "unknown"]


class VectorMap:
    """Polygon map adapter for water classification and observer-to-target LOS.

    All direct constructor geometries must already use the declared projected
    CRS with metre axes. MapV2 inputs can be explicitly transformed by passing
    target_crs to from_map_v2; no transform is guessed.
    """

    def __init__(
        self,
        land_geometry: BaseGeometry | Sequence[BaseGeometry] | str | None,
        *,
        crs: str | int | CRS,
        coverage_geometry: BaseGeometry | str | None = None,
        unknown_geometry: BaseGeometry | str | None = None,
    ) -> None:
        self._crs = _metric_projected_crs(crs)
        land = _polygonal_geometry(land_geometry, "land_geometry")
        coverage = _polygonal_geometry(coverage_geometry, "coverage_geometry")
        unknown = _polygonal_geometry(unknown_geometry, "unknown_geometry")
        self._coverage = coverage
        self._unknown = unknown
        if coverage is None:
            effective_land = GeometryCollection()
        else:
            effective_land = (
                land.intersection(coverage) if land is not None else GeometryCollection()
            )
            if unknown is not None:
                effective_land = effective_land.difference(unknown)
        self._land = effective_land
        self._land_parts = tuple(_polygon_parts(effective_land))
        self._tree = STRtree(self._land_parts) if self._land_parts else None

    @classmethod
    def from_map_v2(
        cls,
        map_v2: MapV2,
        *,
        target_crs: str | int | CRS | None = None,
    ) -> VectorMap:
        """Build from MapV2 GeoJSON, optionally transforming to a metre CRS.

        If target_crs is omitted, MapV2.crs must already be projected with
        metre axes. When target_crs is present, all geometry is transformed
        from MapV2.crs using an explicit always-XY coordinate operation.
        """
        if not isinstance(map_v2, MapV2):
            raise TypeError("map_v2 must be a MapV2 instance.")
        source_crs = CRS.from_user_input(map_v2.crs)
        coverage = _geojson_geometry(map_v2.coverage_geometry_geojson)
        unknown = _geojson_geometry(map_v2.unknown_geometry_geojson)
        land = _geojson_geometry(map_v2.land_geometry_geojson)
        if target_crs is None:
            output_crs = _metric_projected_crs(source_crs)
        else:
            output_crs = _metric_projected_crs(target_crs)
            transformer = Transformer.from_crs(source_crs, output_crs, always_xy=True)
            project = transformer.transform
            coverage = (
                transform(coverage, project, interleaved=False) if coverage is not None else None
            )
            unknown = (
                transform(unknown, project, interleaved=False) if unknown is not None else None
            )
            land = transform(land, project, interleaved=False) if land is not None else None
        return cls(
            land,
            crs=output_crs,
            coverage_geometry=coverage,
            unknown_geometry=unknown,
        )

    @property
    def crs(self) -> CRS:
        """The validated projected metre CRS used by all input geometry."""
        return self._crs

    @property
    def land_geometry(self) -> BaseGeometry:
        """Known land clipped to coverage and with explicit unknown areas removed."""
        return self._land

    def point_water_status(self, point_xy_m: Point2D) -> WaterStatus:
        """Classify a point as water, land, or unknown."""
        point = _point(point_xy_m, "point")
        if self._unknown is not None and self._unknown.covers(point):
            return "unknown"
        if self._coverage is None or not self._coverage.covers(point):
            return "unknown"
        if self._land.contains(point):
            return "land"
        return "water"

    def observer_valid(self, observer_xy_m: Point2D) -> bool:
        """Return whether the observer is in known water; shore is permitted."""
        return self.point_water_status(observer_xy_m) == "water"

    def line_visibility(
        self,
        observer_xy_m: Point2D,
        target_xy_m: Point2D,
        *,
        tolerance_m: float = 1e-6,
        indexed: bool = True,
    ) -> Visibility:
        """Classify a ray as clear, blocked, or unknown.

        A land observer is reported as blocked, while an observer in unknown or
        uncovered space yields unknown. Shoreline-only contact, including a
        target endpoint on shore, does not block. A known land interior crossing
        blocks before unknown regions are considered.
        """
        observer = _point(observer_xy_m, "observer")
        target = _point(target_xy_m, "target")
        if not isfinite(tolerance_m) or tolerance_m < 0.0:
            raise ValueError("tolerance_m must be finite and nonnegative.")
        observer_status = self.point_water_status(observer_xy_m)
        if observer_status == "land":
            return "blocked"
        if observer_status == "unknown":
            return "unknown"
        ray = LineString((observer.coords[0], target.coords[0]))
        if ray.length == 0.0:
            return "clear"

        if self._crosses_known_land(ray, tolerance_m, indexed=indexed):
            return "blocked"
        if self._coverage is None or not self._coverage.covers(ray):
            return "unknown"
        if self._unknown is not None and _interior_overlap_exceeds(ray, self._unknown, tolerance_m):
            return "unknown"
        return "clear"

    def _crosses_known_land(self, ray: LineString, tolerance_m: float, *, indexed: bool) -> bool:
        if indexed and self._tree is not None:
            candidates = [self._land_parts[int(i)] for i in self._tree.query(ray)]
        else:
            candidates = self._land_parts
        return any(_interior_overlap_exceeds(ray, land, tolerance_m) for land in candidates)


def point_water_status(vector_map: VectorMap, point_xy_m: Point2D) -> WaterStatus:
    """Functional adapter for VectorMap.point_water_status."""
    return vector_map.point_water_status(point_xy_m)


def line_visibility(
    vector_map: VectorMap,
    observer_xy_m: Point2D,
    target_xy_m: Point2D,
    *,
    tolerance_m: float = 1e-6,
    indexed: bool = True,
) -> Visibility:
    """Functional adapter for VectorMap.line_visibility."""
    return vector_map.line_visibility(
        observer_xy_m, target_xy_m, tolerance_m=tolerance_m, indexed=indexed
    )


def _metric_projected_crs(crs: str | int | CRS) -> CRS:
    try:
        parsed = CRS.from_user_input(crs)
    except (TypeError, ValueError) as exc:
        raise ValueError("crs must be a valid projected CRS with metre axes.") from exc
    axes = parsed.axis_info
    if (
        not parsed.is_projected
        or len(axes) < 2
        or any(abs(axis.unit_conversion_factor - 1.0) > 1e-12 for axis in axes[:2])
    ):
        raise ValueError("crs must be projected and use metres on both horizontal axes.")
    return parsed


def _polygonal_geometry(
    geometry: BaseGeometry | Sequence[BaseGeometry] | str | None, name: str
) -> BaseGeometry | None:
    if geometry is None:
        return None
    if isinstance(geometry, str):
        geometry = _geojson_geometry(geometry)
    if geometry is None:
        return None
    if isinstance(geometry, BaseGeometry):
        items = (geometry,)
    else:
        items = tuple(geometry)
        if not items:
            raise ValueError(f"{name} must not be empty.")
    polygon_items: list[BaseGeometry] = []
    for item in items:
        if not isinstance(item, BaseGeometry):
            raise TypeError(f"{name} must contain Polygon or MultiPolygon geometries.")
        if item.is_empty:
            raise ValueError(f"{name} must not contain empty geometries.")
        if not item.is_valid:
            raise ValueError(f"{name} must contain valid polygonal geometries.")
        if item.geom_type not in {"Polygon", "MultiPolygon"}:
            raise TypeError(f"{name} must contain Polygon or MultiPolygon geometries.")
        polygon_items.append(item)
    merged = unary_union(polygon_items)
    if not merged.is_valid:
        raise ValueError(f"{name} must remain valid after overlap normalization.")
    return merged


def _geojson_geometry(value: str | None) -> BaseGeometry | None:
    if value is None:
        return None
    try:
        return cast(BaseGeometry, from_geojson(value))
    except Exception as exc:
        raise ValueError("GeoJSON geometry could not be parsed.") from exc


def _polygon_parts(geometry: BaseGeometry):
    if geometry.geom_type == "Polygon":
        yield cast(Polygon, geometry)
    elif geometry.geom_type == "MultiPolygon":
        yield from geometry.geoms


def _point(point_xy_m: Point2D, name: str) -> Point:
    try:
        x, y = point_xy_m
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain two finite coordinates.") from exc
    if not isfinite(x) or not isfinite(y):
        raise ValueError(f"{name} must contain two finite coordinates.")
    return Point(float(x), float(y))


def _interior_overlap_exceeds(ray: LineString, polygon: BaseGeometry, tolerance_m: float) -> bool:
    intersection = ray.intersection(polygon)
    for line in _line_parts(intersection):
        coordinates = tuple(line.coords)
        for start, end in zip(coordinates, coordinates[1:]):
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            segment_length = (dx * dx + dy * dy) ** 0.5
            if segment_length <= tolerance_m:
                continue
            midpoint = Point((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            if polygon.contains(midpoint):
                return True
    return False


def _line_parts(geometry: BaseGeometry):
    if geometry.geom_type == "LineString":
        yield cast(LineString, geometry)
    elif geometry.geom_type in {"MultiLineString", "GeometryCollection"}:
        for part in geometry.geoms:
            yield from _line_parts(part)

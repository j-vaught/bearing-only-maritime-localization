"""Typed, fail-closed proofs for candidate ray coverage in a prepared map snapshot."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Literal, cast

from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely import from_geojson, to_wkb
from shapely.errors import GEOSException
from shapely.geometry import MultiPoint, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import PreparedGeometry, prep

from maritime_localization_shared.contracts.inputs_v2 import MapV2

Point2D = tuple[float, float]
TargetMapPoint = tuple[str, Point2D]
CoverageProofMethod = Literal["convex_hull_inside_known_coverage"]


@dataclass(frozen=True, slots=True, init=False)
class PreparedCoverageMap:
    """Reusable immutable geometry snapshot for per-candidate certificates."""

    map_crs: str
    map_source: str
    map_version: str
    map_coverage_status: str
    map_identity_sha256: str
    _coverage_wkb: bytes
    _unknown_wkb: bytes | None
    _land_wkb: bytes | None
    _coverage: BaseGeometry
    _unknown: BaseGeometry | None
    _land: BaseGeometry | None
    _prepared_coverage: PreparedGeometry
    _prepared_unknown: PreparedGeometry | None
    _prepared_land: PreparedGeometry | None

    @classmethod
    def _mint(
        cls,
        *,
        map_v2: MapV2,
        map_identity_sha256: str,
        coverage_wkb: bytes,
        unknown_wkb: bytes | None,
        land_wkb: bytes | None,
        coverage: BaseGeometry,
        unknown: BaseGeometry | None,
        land: BaseGeometry | None,
    ) -> PreparedCoverageMap:
        context = object.__new__(cls)
        values = {
            "map_crs": map_v2.crs,
            "map_source": map_v2.source,
            "map_version": map_v2.map_version,
            "map_coverage_status": map_v2.coverage_status,
            "map_identity_sha256": map_identity_sha256,
            "_coverage_wkb": coverage_wkb,
            "_unknown_wkb": unknown_wkb,
            "_land_wkb": land_wkb,
            "_coverage": coverage,
            "_unknown": unknown,
            "_land": land,
            "_prepared_coverage": prep(coverage),
            "_prepared_unknown": prep(unknown) if unknown is not None else None,
            "_prepared_land": prep(land) if land is not None else None,
        }
        for name, value in values.items():
            object.__setattr__(context, name, value)
        return context

    def _candidate_is_known_water(self, candidate_xy_m: Point2D) -> bool:
        point = Point(candidate_xy_m)
        if self._prepared_unknown is not None and self._prepared_unknown.covers(point):
            return False
        if not self._prepared_coverage.covers(point):
            return False
        return self._prepared_land is None or not self._prepared_land.contains(point)

    def _envelope_is_known(self, hull: BaseGeometry) -> bool:
        if not self._prepared_coverage.covers(hull):
            return False
        return self._prepared_unknown is None or not self._prepared_unknown.intersects(hull)


def prepare_candidate_coverage_context(map_v2: MapV2) -> PreparedCoverageMap | None:
    """Prepare one reusable exact MapV2 snapshot, or return None if unusable.

    Preparation validates a projected metre CRS and polygonal coverage. The
    candidate factory can then use this context for many candidates without
    reparsing GeoJSON, rebuilding the map identity, or preparing geometries.
    """
    try:
        if not isinstance(map_v2, MapV2) or map_v2.coverage_status not in {"known", "partial"}:
            return None
        crs = CRS.from_user_input(map_v2.crs)
        _require_projected_metres(crs)
        coverage = _geojson_polygon(map_v2.coverage_geometry_geojson)
        unknown = _geojson_polygon(map_v2.unknown_geometry_geojson)
        land = _geojson_polygon(map_v2.land_geometry_geojson)
        if coverage is None:
            return None
        coverage_wkb = to_wkb(coverage, output_dimension=2, byte_order=1)
        unknown_wkb = to_wkb(unknown, output_dimension=2, byte_order=1) if unknown else None
        land_wkb = to_wkb(land, output_dimension=2, byte_order=1) if land else None
        map_identity = _map_identity(
            map_v2.schema_version,
            map_v2.crs,
            map_v2.source,
            map_v2.map_version,
            map_v2.coverage_status,
            coverage_wkb,
            unknown_wkb,
            land_wkb,
        )
        return PreparedCoverageMap._mint(
            map_v2=map_v2,
            map_identity_sha256=map_identity,
            coverage_wkb=coverage_wkb,
            unknown_wkb=unknown_wkb,
            land_wkb=land_wkb,
            coverage=coverage,
            unknown=unknown,
            land=land,
        )
    except (TypeError, ValueError, CRSError, GEOSException):
        return None


@dataclass(frozen=True, slots=True, init=False)
class CandidateCoverageCertificate:
    """Immutable evidence bound to one candidate, target set, and map snapshot.

    Instances are minted only after the prepared context proves exact observer
    water and a convex hull that contains every candidate ray within known
    coverage. The certificate proves coverage, not that rays avoid land.
    """

    candidate_xy_m: Point2D
    target_map_xy: tuple[TargetMapPoint, ...]
    map_crs: str
    map_source: str
    map_version: str
    map_coverage_status: str
    map_identity_sha256: str
    envelope_bounds_m: tuple[float, float, float, float]
    proof_method: CoverageProofMethod
    candidate_water_status: Literal["water"]

    @classmethod
    def _mint(
        cls,
        *,
        candidate_xy_m: Point2D,
        target_map_xy: tuple[TargetMapPoint, ...],
        context: PreparedCoverageMap,
        envelope_bounds_m: tuple[float, float, float, float],
    ) -> CandidateCoverageCertificate:
        certificate = object.__new__(cls)
        values = {
            "candidate_xy_m": candidate_xy_m,
            "target_map_xy": target_map_xy,
            "map_crs": context.map_crs,
            "map_source": context.map_source,
            "map_version": context.map_version,
            "map_coverage_status": context.map_coverage_status,
            "map_identity_sha256": context.map_identity_sha256,
            "envelope_bounds_m": envelope_bounds_m,
            "proof_method": "convex_hull_inside_known_coverage",
            "candidate_water_status": "water",
        }
        for name, value in values.items():
            object.__setattr__(certificate, name, value)
        return certificate

    def matches_map(self, map_context: PreparedCoverageMap) -> bool:
        """Return whether this proof matches the reusable prepared map identity."""
        return (
            isinstance(map_context, PreparedCoverageMap)
            and self.map_identity_sha256 == map_context.map_identity_sha256
            and self.map_crs == map_context.map_crs
            and self.map_source == map_context.map_source
            and self.map_version == map_context.map_version
            and self.map_coverage_status == map_context.map_coverage_status
        )

    def covers(
        self,
        candidate_xy_m: Point2D,
        target_map_xy: Sequence[TargetMapPoint],
    ) -> bool:
        """Check exact candidate and complete key/coordinate tuple binding."""
        try:
            return (
                self.proof_method == "convex_hull_inside_known_coverage"
                and self.candidate_water_status == "water"
                and self.candidate_xy_m == _point_xy(candidate_xy_m)
                and self.target_map_xy == _canonical_targets(target_map_xy)
            )
        except (TypeError, ValueError):
            return False


def certify_candidate_coverage(
    map_or_context: MapV2 | PreparedCoverageMap,
    candidate_xy_m: Point2D,
    target_map_xy: Sequence[TargetMapPoint],
) -> CandidateCoverageCertificate | None:
    """Mint a candidate certificate, or return None when any proof is missing.

    A caller may pass a MapV2 for convenience or, for repeated candidates, a
    PreparedCoverageMap made once by prepare_candidate_coverage_context. The
    caller is responsible for passing every exact in-range offset-corrected
    target. The factory binds all supplied keys and map coordinates. The CRS
    must already be projected metres and shared by candidate, targets, and map.
    """
    try:
        if isinstance(map_or_context, MapV2):
            context = prepare_candidate_coverage_context(map_or_context)
        elif isinstance(map_or_context, PreparedCoverageMap):
            context = map_or_context
        else:
            return None
        if context is None:
            return None
        candidate = _point_xy(candidate_xy_m)
        targets = _canonical_targets(target_map_xy)
        if not context._candidate_is_known_water(candidate):
            return None
        hull = _candidate_target_hull(candidate, targets)
        if not context._envelope_is_known(hull):
            return None
        return CandidateCoverageCertificate._mint(
            candidate_xy_m=candidate,
            target_map_xy=targets,
            context=context,
            envelope_bounds_m=_bounds(hull),
        )
    except (TypeError, ValueError, CRSError, GEOSException):
        return None


def _point_xy(point_xy_m: Point2D) -> Point2D:
    try:
        x, y = point_xy_m
    except (TypeError, ValueError) as exc:
        raise ValueError("Coordinates must contain two finite values.") from exc
    if not isfinite(x) or not isfinite(y):
        raise ValueError("Coordinates must contain two finite values.")
    return float(x), float(y)


def _canonical_targets(target_map_xy: Sequence[TargetMapPoint]) -> tuple[TargetMapPoint, ...]:
    canonical: list[TargetMapPoint] = []
    for item in target_map_xy:
        try:
            key, point = item
        except (TypeError, ValueError) as exc:
            raise ValueError("Each target must contain a key and map coordinate.") from exc
        if not isinstance(key, str) or not key:
            raise ValueError("Target keys must be nonempty strings.")
        canonical.append((key, _point_xy(point)))
    keys = [key for key, _ in canonical]
    if len(keys) != len(set(keys)):
        raise ValueError("Target keys must be unique.")
    return tuple(sorted(canonical, key=lambda item: item[0]))


def _candidate_target_hull(
    candidate_xy_m: Point2D, targets: tuple[TargetMapPoint, ...]
) -> BaseGeometry:
    points = [candidate_xy_m, *(point for _, point in targets)]
    return MultiPoint(points).convex_hull


def _bounds(geometry: BaseGeometry) -> tuple[float, float, float, float]:
    west, south, east, north = geometry.bounds
    return float(west), float(south), float(east), float(north)


def _geojson_polygon(value: str | None) -> BaseGeometry | None:
    if value is None:
        return None
    geometry = cast(BaseGeometry, from_geojson(value))
    _validate_polygon(geometry)
    return unary_union(geometry)


def _validate_polygon(geometry: BaseGeometry) -> None:
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError("Map geometry must be nonempty and valid.")
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Map geometry must be a Polygon or MultiPolygon.")


def _require_projected_metres(crs: CRS) -> None:
    axes = crs.axis_info
    if (
        not crs.is_projected
        or len(axes) < 2
        or any(abs(axis.unit_conversion_factor - 1.0) > 1e-12 for axis in axes[:2])
    ):
        raise ValueError("Map coordinates must use a projected CRS with metre axes.")


def _map_identity(
    schema_version: int,
    crs: str,
    source: str,
    version: str,
    coverage_status: str,
    coverage_wkb: bytes,
    unknown_wkb: bytes | None,
    land_wkb: bytes | None,
) -> str:
    payload = {
        "schema_version": schema_version,
        "crs": crs,
        "source": source,
        "map_version": version,
        "coverage_status": coverage_status,
        "coverage_wkb_sha256": sha256(coverage_wkb).hexdigest(),
        "unknown_wkb_sha256": sha256(unknown_wkb).hexdigest() if unknown_wkb else None,
        "land_wkb_sha256": sha256(land_wkb).hexdigest() if land_wkb else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()

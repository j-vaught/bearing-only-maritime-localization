"""Typed, fail-closed proof that candidate rays stay in known map coverage."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Literal, cast

from pyproj import CRS
from shapely import from_geojson, from_wkb, to_wkb
from shapely.errors import GEOSException
from shapely.geometry import MultiPoint, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from maritime_localization_shared.contracts.inputs_v2 import MapV2
from maritime_localization_shared.geospatial.vector_map import VectorMap

Point2D = tuple[float, float]
TargetMapPoint = tuple[str, Point2D]
CoverageProofMethod = Literal["convex_hull_inside_known_coverage"]


@dataclass(frozen=True, slots=True, init=False)
class CandidateCoverageCertificate:
    """Immutable evidence bound to one candidate, target set, and MapV2 snapshot.

    Instances are minted only by certify_candidate_coverage. The certificate
    retains WKB witnesses and recomputes its proof in covers(), so callers do
    not rely on a caller-supplied boolean. It proves known coverage and a known
    water observer; it does not prove rays are free of land.
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
    _coverage_wkb: bytes
    _unknown_wkb: bytes | None
    _land_wkb: bytes | None
    _schema_version: int

    @classmethod
    def _mint(
        cls,
        *,
        candidate_xy_m: Point2D,
        target_map_xy: tuple[TargetMapPoint, ...],
        map_crs: str,
        map_source: str,
        map_version: str,
        map_coverage_status: str,
        map_identity_sha256: str,
        envelope_bounds_m: tuple[float, float, float, float],
        coverage_wkb: bytes,
        unknown_wkb: bytes | None,
        land_wkb: bytes | None,
        schema_version: int,
    ) -> CandidateCoverageCertificate:
        certificate = object.__new__(cls)
        object.__setattr__(certificate, "candidate_xy_m", candidate_xy_m)
        object.__setattr__(certificate, "target_map_xy", target_map_xy)
        object.__setattr__(certificate, "map_crs", map_crs)
        object.__setattr__(certificate, "map_source", map_source)
        object.__setattr__(certificate, "map_version", map_version)
        object.__setattr__(certificate, "map_coverage_status", map_coverage_status)
        object.__setattr__(certificate, "map_identity_sha256", map_identity_sha256)
        object.__setattr__(certificate, "envelope_bounds_m", envelope_bounds_m)
        object.__setattr__(certificate, "proof_method", "convex_hull_inside_known_coverage")
        object.__setattr__(certificate, "candidate_water_status", "water")
        object.__setattr__(certificate, "_coverage_wkb", coverage_wkb)
        object.__setattr__(certificate, "_unknown_wkb", unknown_wkb)
        object.__setattr__(certificate, "_land_wkb", land_wkb)
        object.__setattr__(certificate, "_schema_version", schema_version)
        return certificate

    def matches_map(self, map_v2: MapV2) -> bool:
        """Return whether this proof is bound to the exact MapV2 snapshot."""
        try:
            if not isinstance(map_v2, MapV2):
                return False
            coverage_wkb, unknown_wkb, land_wkb = _map_geometry_witnesses(map_v2)
            return self.map_identity_sha256 == _map_identity(
                map_v2.schema_version,
                map_v2.crs,
                map_v2.source,
                map_v2.map_version,
                map_v2.coverage_status,
                coverage_wkb,
                unknown_wkb,
                land_wkb,
            )
        except (TypeError, ValueError, GEOSException):
            return False

    def covers(
        self,
        candidate_xy_m: Point2D,
        target_map_xy: Sequence[TargetMapPoint],
    ) -> bool:
        """Verify exact candidate/target binding and recompute the coverage proof."""
        try:
            candidate = _point_xy(candidate_xy_m)
            targets = _canonical_targets(target_map_xy)
            if candidate != self.candidate_xy_m or targets != self.target_map_xy:
                return False
            if self.proof_method != "convex_hull_inside_known_coverage":
                return False
            if self.candidate_water_status != "water":
                return False
            if self.map_coverage_status not in {"known", "partial"}:
                return False
            if (
                _map_identity(
                    self._schema_version,
                    self.map_crs,
                    self.map_source,
                    self.map_version,
                    self.map_coverage_status,
                    self._coverage_wkb,
                    self._unknown_wkb,
                    self._land_wkb,
                )
                != self.map_identity_sha256
            ):
                return False
            crs = CRS.from_user_input(self.map_crs)
            _require_projected_metres(crs)
            coverage = cast(BaseGeometry, from_wkb(self._coverage_wkb))
            unknown = from_wkb(self._unknown_wkb) if self._unknown_wkb is not None else None
            land = from_wkb(self._land_wkb) if self._land_wkb is not None else None
            _validate_polygon(coverage)
            if unknown is not None:
                _validate_polygon(unknown)
            if land is not None:
                _validate_polygon(land)

            candidate_point = Point(candidate)
            if unknown is not None and unknown.covers(candidate_point):
                return False
            if not coverage.covers(candidate_point):
                return False
            if land is not None and land.contains(candidate_point):
                return False

            hull = _candidate_target_hull(candidate, targets)
            if not coverage.covers(hull):
                return False
            if unknown is not None and unknown.intersects(hull):
                return False
            return _bounds(hull) == self.envelope_bounds_m
        except (TypeError, ValueError, GEOSException):
            return False


def certify_candidate_coverage(
    map_v2: MapV2,
    candidate_xy_m: Point2D,
    target_map_xy: Sequence[TargetMapPoint],
) -> CandidateCoverageCertificate | None:
    """Mint a candidate coverage certificate, or return None when unproven.

    Map coordinates, candidate coordinates, and target coordinates must all
    already use MapV2.crs. The CRS must be projected with metre axes. The
    convex hull of the candidate and all in-range targets is a conservative
    superset of every candidate-to-target segment. Certification requires that
    hull to be fully covered and disjoint from explicit unknown geometry.
    The exact candidate must separately classify as known water. Land along
    target rays is intentionally not tested by this coverage certificate.
    """
    try:
        if not isinstance(map_v2, MapV2):
            return None
        candidate = _point_xy(candidate_xy_m)
        targets = _canonical_targets(target_map_xy)
        vector_map = VectorMap.from_map_v2(map_v2)
        if vector_map.point_water_status(candidate) != "water":
            return None
        coverage_wkb, unknown_wkb, land_wkb = _map_geometry_witnesses(map_v2)
        if not coverage_wkb or map_v2.coverage_status not in {"known", "partial"}:
            return None
        coverage = cast(BaseGeometry, from_wkb(coverage_wkb))
        unknown = from_wkb(unknown_wkb) if unknown_wkb is not None else None
        if unknown is not None and unknown.is_empty:
            unknown = None
        hull = _candidate_target_hull(candidate, targets)
        if not coverage.covers(hull):
            return None
        if unknown is not None and unknown.intersects(hull):
            return None
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
        return CandidateCoverageCertificate._mint(
            candidate_xy_m=candidate,
            target_map_xy=targets,
            map_crs=map_v2.crs,
            map_source=map_v2.source,
            map_version=map_v2.map_version,
            map_coverage_status=map_v2.coverage_status,
            map_identity_sha256=map_identity,
            envelope_bounds_m=_bounds(hull),
            coverage_wkb=coverage_wkb,
            unknown_wkb=unknown_wkb,
            land_wkb=land_wkb,
            schema_version=map_v2.schema_version,
        )
    except (TypeError, ValueError, GEOSException):
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


def _map_geometry_witnesses(map_v2: MapV2) -> tuple[bytes, bytes | None, bytes | None]:
    coverage = _geojson_polygon(map_v2.coverage_geometry_geojson)
    unknown = _geojson_polygon(map_v2.unknown_geometry_geojson)
    land = _geojson_polygon(map_v2.land_geometry_geojson)
    coverage_wkb = to_wkb(coverage, output_dimension=2, byte_order=1) if coverage else b""
    unknown_wkb = to_wkb(unknown, output_dimension=2, byte_order=1) if unknown else None
    land_wkb = to_wkb(land, output_dimension=2, byte_order=1) if land else None
    return coverage_wkb, unknown_wkb, land_wkb


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

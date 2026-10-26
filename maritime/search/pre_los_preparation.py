"""Trusted candidate-bound preparation of complete pre-LOS physical targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, degrees, hypot, isfinite
from threading import RLock
from typing import TYPE_CHECKING, Literal
from weakref import WeakKeyDictionary

from pyproj import Geod
from shapely import to_geojson

from maritime.geometry.coordinates import geodesic_bearing_distance
from maritime.geometry.coverage_certificate import (
    CandidateCoverageCertificate,
    PreparedCoverageMap,
    certify_candidate_coverage,
    prepare_candidate_coverage_context,
)
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import MapV2, MeasurementSetV2
from maritime.schemas.object_types import normalize_object_type

from .config import SearchConfig
from .prediction import PreparedPredictionContext
from .scoring import CandidateScorer

if TYPE_CHECKING:
    from maritime.schemas.inputs_v2 import CatalogV2

_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True, slots=True)
class PreLOSTargetRecord:
    """One complete physical target before line-of-sight filtering."""

    key: str
    bearing_deg: float
    range_m: float
    object_type: str | None
    uncertainty_m: float | None
    target_map_xy_m: tuple[float, float]
    offset_quality: Literal["calibrated", "nominal_zero_unknown"]

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("key must be a nonempty string.")
        if (
            isinstance(self.bearing_deg, bool)
            or not isinstance(self.bearing_deg, (int, float))
            or not isfinite(self.bearing_deg)
            or not 0.0 <= self.bearing_deg < 360.0
        ):
            raise ValueError("bearing_deg must be finite and in [0, 360).")
        if (
            isinstance(self.range_m, bool)
            or not isinstance(self.range_m, (int, float))
            or not isfinite(self.range_m)
            or self.range_m < 0.0
        ):
            raise ValueError("range_m must be finite and nonnegative.")
        if self.object_type is not None and not isinstance(self.object_type, str):
            raise TypeError("object_type must be a string or None.")
        if self.uncertainty_m is not None and (
            isinstance(self.uncertainty_m, bool)
            or not isinstance(self.uncertainty_m, (int, float))
            or not isfinite(self.uncertainty_m)
            or self.uncertainty_m < 0.0
        ):
            raise ValueError("uncertainty_m must be finite and nonnegative or None.")
        if (
            not isinstance(self.target_map_xy_m, tuple)
            or len(self.target_map_xy_m) != 2
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                for value in self.target_map_xy_m
            )
        ):
            raise ValueError("target_map_xy_m must contain two finite coordinates.")
        if self.offset_quality not in ("calibrated", "nominal_zero_unknown"):
            raise ValueError("Unsupported target offset quality.")


@dataclass(frozen=True, slots=True, init=False)
class PreparedPreLOSTargets:
    """Factory-minted complete pre-LOS geometry bound to one scorer candidate."""

    candidate_xy_m: tuple[float, float]
    records: tuple[PreLOSTargetRecord, ...]
    coverage_certificate: CandidateCoverageCertificate | None
    coverage_map_context: PreparedCoverageMap | None
    supported: bool
    reason: str | None
    unresolved_model_keys: tuple[str, ...]
    measurements: MeasurementSetV2 = field(repr=False, compare=False)
    config: SearchConfig = field(repr=False, compare=False)
    _scorer: CandidateScorer = field(repr=False, compare=False)
    _catalog: CatalogV2 = field(repr=False, compare=False)
    _vector_map: VectorMap = field(repr=False, compare=False)
    _prediction_context: PreparedPredictionContext = field(repr=False, compare=False)

    @classmethod
    def _mint(
        cls,
        *,
        candidate_xy_m: tuple[float, float],
        records: tuple[PreLOSTargetRecord, ...],
        coverage_certificate: CandidateCoverageCertificate | None,
        coverage_map_context: PreparedCoverageMap | None,
        supported: bool,
        reason: str | None,
        unresolved_model_keys: tuple[str, ...],
        scorer: CandidateScorer,
    ) -> PreparedPreLOSTargets:
        result = object.__new__(cls)
        values = {
            "candidate_xy_m": candidate_xy_m,
            "records": records,
            "coverage_certificate": coverage_certificate,
            "coverage_map_context": coverage_map_context,
            "supported": supported,
            "reason": reason,
            "unresolved_model_keys": unresolved_model_keys,
            "measurements": scorer.measurements,
            "config": scorer.config,
            "_scorer": scorer,
            "_catalog": scorer.catalog,
            "_vector_map": scorer.vector_map,
            "_prediction_context": scorer._prediction_context,
        }
        for name, value in values.items():
            object.__setattr__(result, name, value)
        return result

    def matches_inputs(self, measurements: MeasurementSetV2, config: SearchConfig) -> bool:
        """Check the exact immutable scorer inputs used by the preparation."""
        return self.measurements is measurements and self.config is config

    def matches_scorer(self, scorer: CandidateScorer) -> bool:
        """Check object identity bindings against the source scorer."""
        return (
            isinstance(scorer, CandidateScorer)
            and self._scorer is scorer
            and self._catalog is scorer.catalog
            and self._vector_map is scorer.vector_map
            and self._prediction_context is scorer._prediction_context
            and self.matches_inputs(scorer.measurements, scorer.config)
            and self.coverage_map_context is _coverage_context_for_scorer(scorer)
        )


_COVERAGE_CONTEXTS: WeakKeyDictionary[CandidateScorer, PreparedCoverageMap | None] = (
    WeakKeyDictionary()
)
_COVERAGE_LOCK = RLock()


def _coverage_context_for_scorer(scorer: CandidateScorer) -> PreparedCoverageMap | None:
    with _COVERAGE_LOCK:
        if scorer in _COVERAGE_CONTEXTS:
            return _COVERAGE_CONTEXTS[scorer]
        vector_map = scorer.vector_map
        coverage = vector_map._coverage
        if coverage is None or coverage.is_empty:
            context = None
        else:
            unknown = vector_map._unknown
            land = vector_map._land
            map_v2 = MapV2(
                crs=vector_map.crs.to_string(),
                source="prepared-vector-map",
                map_version="derived-geometry-v1",
                coverage_status="partial"
                if unknown is not None and not unknown.is_empty
                else "known",
                coverage_geometry_geojson=to_geojson(coverage),
                unknown_geometry_geojson=(
                    to_geojson(unknown) if unknown is not None and not unknown.is_empty else None
                ),
                land_geometry_geojson=to_geojson(land) if not land.is_empty else None,
            )
            context = prepare_candidate_coverage_context(map_v2, target_crs=vector_map.crs)
        _COVERAGE_CONTEXTS[scorer] = context
        return context


def prepare_pre_los_targets(
    scorer: CandidateScorer,
    candidate_xy_m: tuple[float, float],
) -> PreparedPreLOSTargets:
    """Mint all nearby, in-range physical target geometry without testing LOS.

    The source scorer supplies the catalog, prepared geographic index, map,
    feature metadata, range contract, and type-label setting. Every physical
    record in the same spatial query as the exact predictor is checked for an
    unsupported model before its exact target range is applied. This mirrors
    predictor coverage and model gates while omitting all ray/land tests.
    """
    if not isinstance(scorer, CandidateScorer):
        raise TypeError("scorer must be a CandidateScorer.")
    try:
        east_m, north_m = candidate_xy_m
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate_xy_m must contain two finite coordinates.") from exc
    if (
        isinstance(east_m, bool)
        or isinstance(north_m, bool)
        or not isfinite(east_m)
        or not isfinite(north_m)
    ):
        raise ValueError("candidate_xy_m must contain two finite coordinates.")
    candidate = (float(east_m), float(north_m))
    config = scorer.config
    prediction = scorer._prediction_context
    unsupported: list[str] = []
    records: list[PreLOSTargetRecord] = []
    map_context = _coverage_context_for_scorer(scorer)
    reason: str | None = None
    coverage_certificate: CandidateCoverageCertificate | None = None

    observer_status = scorer.vector_map.point_water_status(candidate)
    if observer_status != "water":
        reason = f"candidate water status is {observer_status}"
    observer_lon, observer_lat = prediction.inverse.transform(*candidate)
    if not isfinite(observer_lon) or not isfinite(observer_lat):
        reason = reason or "candidate cannot be transformed to a valid geographic position"
    else:
        nearby = prediction.spatial_index.query_radius_with_geometry(
            float(observer_lon),
            float(observer_lat),
            config.max_range_m + prediction.max_offset_m,
        )
        for record_index, base_bearing, base_range in nearby:
            prepared = prediction.records[record_index]
            if not prepared.is_physical:
                continue
            record = prepared.record
            if prepared.offset_quality == "unsupported":
                unsupported.append(record.key)
                continue
            bearing = base_bearing
            distance = base_range
            if prepared.east_offset_m != 0.0 or prepared.north_offset_m != 0.0:
                offset_bearing = (
                    degrees(atan2(prepared.east_offset_m, prepared.north_offset_m)) % 360.0
                )
                offset_range = hypot(prepared.east_offset_m, prepared.north_offset_m)
                target_lon, target_lat, _ = _GEOD.fwd(
                    record.longitude_deg,
                    record.latitude_deg,
                    offset_bearing,
                    offset_range,
                )
                geometry = geodesic_bearing_distance(
                    (float(observer_lon), float(observer_lat)),
                    (float(target_lon), float(target_lat)),
                )
                bearing, distance = geometry.true_bearing_deg, geometry.distance_m
            if config.range_inclusive:
                if distance < config.min_range_m or distance > config.max_range_m:
                    continue
            elif distance <= config.min_range_m or distance >= config.max_range_m:
                continue
            target_east, target_north = prediction.forward.transform(
                record.longitude_deg
                if prepared.east_offset_m == 0.0 and prepared.north_offset_m == 0.0
                else target_lon,
                record.latitude_deg
                if prepared.east_offset_m == 0.0 and prepared.north_offset_m == 0.0
                else target_lat,
            )
            object_type = (
                normalize_object_type(
                    record.normalized_type or record.raw_type,
                    type_labels_enabled=config.use_types,
                )
                or "unknown"
            )
            records.append(
                PreLOSTargetRecord(
                    key=record.key,
                    bearing_deg=float(bearing) % 360.0,
                    range_m=float(distance),
                    object_type=object_type if config.use_types else None,
                    uncertainty_m=prepared.uncertainty_m,
                    target_map_xy_m=(float(target_east), float(target_north)),
                    offset_quality=prepared.offset_quality,
                )
            )
    if unsupported:
        reason = "one or more nearby physical records have unsupported feature models"
    elif reason is None:
        if map_context is None:
            reason = "candidate map does not provide certifiable known coverage"
        else:
            coverage_certificate = certify_candidate_coverage(
                map_context,
                candidate,
                [(item.key, item.target_map_xy_m) for item in records],
            )
            if coverage_certificate is None:
                reason = "candidate target envelope is not certified inside known coverage"
    return PreparedPreLOSTargets._mint(
        candidate_xy_m=candidate,
        records=tuple(records),
        coverage_certificate=coverage_certificate,
        coverage_map_context=map_context,
        supported=reason is None,
        reason=reason,
        unresolved_model_keys=tuple(sorted(unsupported)),
        scorer=scorer,
    )


__all__ = ["PreLOSTargetRecord", "PreparedPreLOSTargets", "prepare_pre_los_targets"]

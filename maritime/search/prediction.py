"""Predict catalog objects visible from one projected observer position.

Search inputs use only a candidate observer pose and the geographic catalog.
Every range decision is ellipsoidal, bearings are WGS84 true-north initial
bearings, and map coverage uncertainty is carried explicitly in the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, degrees, hypot, isfinite
from typing import Literal

from pyproj import CRS, Geod, Transformer

from maritime.catalog_identity import canonicalize_chart_aids
from maritime.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime.geometry.coordinates import (
    BearingDistance,
    geodesic_bearing_distance,
)
from maritime.geometry.spatial_index import GeographicSpatialIndex
from maritime.geometry.vector_map import VectorMap
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.schemas.object_types import (
    is_visual_entity,
    normalize_object_type,
)

CoverageStatus = Literal["known", "unresolved"]
OffsetQuality = Literal["calibrated", "nominal_zero_unknown", "unsupported"]
_GEOD = Geod(ellps="WGS84")
_VISIBLE_OFFSET_KEYS = {
    "visible_feature_offset_east_m",
    "visible_feature_offset_north_m",
}


@dataclass(frozen=True, slots=True)
class PredictedVisibleGroup:
    """One angularly unresolved group of definitely visible physical objects."""

    bearing_deg: float
    object_type: str | None
    member_keys: tuple[str, ...]
    representative_key: str
    range_m: float
    extent_half_width_deg: float
    catalog_uncertainty_m: float | None
    ais_antenna_offset_supported: bool = False
    visible_feature_offset_quality: OffsetQuality = "nominal_zero_unknown"


@dataclass(frozen=True, slots=True)
class PredictedGroupSet:
    """Predicted groups plus map-coverage completeness for the query."""

    groups: tuple[PredictedVisibleGroup, ...]
    coverage_status: CoverageStatus
    unresolved_member_keys: tuple[str, ...]
    unresolved_model: bool = False
    unresolved_model_keys: tuple[str, ...] = ()
    identity_limited_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _PreparedRecord:
    record: CatalogRecordV2
    is_physical: bool
    east_offset_m: float
    north_offset_m: float
    offset_quality: OffsetQuality
    half_width_deg: float
    uncertainty_m: float | None
    identity_limited: bool


@dataclass(frozen=True, slots=True)
class PreparedPredictionContext:
    """Immutable validated preparation reused by repeated candidate predictions.

    Use prepare_prediction_context to construct this object. Its catalog, map,
    index alignment, CRS, and prediction settings are fixed for its lifetime.
    """

    catalog: CatalogV2 = field(repr=False, compare=False)
    vector_map: VectorMap = field(repr=False, compare=False)
    spatial_index: GeographicSpatialIndex = field(repr=False, compare=False)
    crs: CRS = field(repr=False, compare=False)
    records: tuple[_PreparedRecord, ...] = field(repr=False, compare=False)
    inverse: Transformer = field(repr=False, compare=False)
    forward: Transformer = field(repr=False, compare=False)
    max_offset_m: float
    signature: tuple[object, ...]
    source_catalog: CatalogV2 | None = field(default=None, repr=False, compare=False)


def prepare_prediction_context(
    catalog: CatalogV2,
    *,
    observer_crs: str | int | CRS,
    visibility_radius_m: float,
    vector_map: VectorMap,
    minimum_range_m: float = 0.0,
    range_inclusive: bool = True,
    angular_resolution_deg: float = 0.1,
    type_labels_enabled: bool = True,
    spatial_index: GeographicSpatialIndex | None = None,
) -> PreparedPredictionContext:
    """Validate and cache catalog-dependent fields and coordinate transforms."""

    if not isinstance(catalog, CatalogV2) or not isinstance(vector_map, VectorMap):
        raise TypeError("catalog and vector_map must be their V2 shared types.")
    source_catalog = catalog
    catalog = canonicalize_chart_aids(catalog)
    if not isinstance(type_labels_enabled, bool) or type(range_inclusive) is not bool:
        raise TypeError("type_labels_enabled and range_inclusive must be bool values.")
    if (
        not isfinite(visibility_radius_m)
        or visibility_radius_m < 0
        or not isfinite(minimum_range_m)
        or minimum_range_m < 0
        or minimum_range_m > visibility_radius_m
        or not isfinite(angular_resolution_deg)
        or not 0 <= angular_resolution_deg < 180
    ):
        raise ValueError("Prediction range and angular settings are invalid.")
    try:
        crs = CRS.from_user_input(observer_crs)
    except (TypeError, ValueError) as exc:
        raise ValueError("observer_crs must be a valid projected metre CRS.") from exc
    axes = crs.axis_info
    if (
        not crs.is_projected
        or len(axes) < 2
        or any(abs(axis.unit_conversion_factor - 1.0) > 1e-12 for axis in axes[:2])
    ):
        raise ValueError("observer_crs must be projected and use metres on both horizontal axes.")
    if not crs.equals(vector_map.crs):
        raise ValueError("observer_crs must match vector_map.crs.")
    index = spatial_index or GeographicSpatialIndex(
        ((r.longitude_deg, r.latitude_deg) for r in catalog.records),
        keys=(r.key for r in catalog.records),
    )
    keys = tuple(r.key for r in catalog.records)
    points = tuple((float(r.longitude_deg), float(r.latitude_deg)) for r in catalog.records)
    if index.keys != keys or index.points != points:
        raise ValueError("spatial_index must match catalog coordinates, keys, and order.")
    prepared = tuple(
        _PreparedRecord(
            record=r,
            is_physical=is_visual_entity(r.normalized_type or r.raw_type),
            east_offset_m=(offset := _visible_feature_offset(r))[0],
            north_offset_m=offset[1],
            offset_quality=offset[2],
            half_width_deg=_record_half_width(r),
            uncertainty_m=_record_uncertainty(r),
            identity_limited=(
                _record_metadata(r).get("physical_linkage_status") == "unresolved"
                or _record_metadata(r).get("identity_ambiguous") is True
            ),
        )
        for r in catalog.records
    )
    maximum_offset = max(
        (
            hypot(r.east_offset_m, r.north_offset_m)
            for r in prepared
            if r.is_physical and r.offset_quality != "unsupported"
        ),
        default=0.0,
    )
    signature = (
        crs.to_wkt(),
        visibility_radius_m,
        minimum_range_m,
        range_inclusive,
        angular_resolution_deg,
        type_labels_enabled,
    )
    return PreparedPredictionContext(
        catalog,
        vector_map,
        index,
        crs,
        prepared,
        Transformer.from_crs(crs, "EPSG:4326", always_xy=True),
        Transformer.from_crs("EPSG:4326", crs, always_xy=True),
        maximum_offset,
        signature,
        source_catalog,
    )


def predict_visible_groups(
    observer_xy_m: tuple[float, float],
    catalog: CatalogV2,
    *,
    observer_crs: str | int | CRS,
    visibility_radius_m: float,
    vector_map: VectorMap,
    minimum_range_m: float = 0.0,
    range_inclusive: bool = True,
    angular_resolution_deg: float = 0.1,
    type_labels_enabled: bool = True,
    spatial_index: GeographicSpatialIndex | None = None,
    prepared_context: PreparedPredictionContext | None = None,
) -> PredictedGroupSet:
    """Predict range-limited, line-of-sight-visible angular catalog groups.

    ``observer_xy_m`` is expressed in ``observer_crs``, which must be the
    same projected-metre CRS used by ``vector_map``. Catalog coordinates are
    transformed from their V2 longitude/latitude fields only for map LOS; range
    and true bearing always use WGS84 geodesics. The configured range interval
    is applied before LOS and angular grouping. Unknown observer or LOS
    coverage marks the result unresolved and does not silently classify the
    affected feature as visible or blocked.

    Explicit visible-feature east/north offset metadata are applied as
    true-east/north WGS84 offsets from the catalog reference point. Missing
    offsets use a nominal zero with unknown quality. Physical-linkage uncertainty
    is listed in ``identity_limited_keys`` after range and clear line-of-sight
    filtering, but does not reject valid source-record geometry. Supplied unsupported
    dimensional or antenna-offset metadata sets unresolved_model and excludes
    that record from definite groups. catalog_uncertainty_m carries recognized
    position uncertainty when supplied; otherwise it is None.
    """
    if not isinstance(catalog, CatalogV2):
        raise TypeError("catalog must be a CatalogV2 instance.")
    if not isinstance(vector_map, VectorMap):
        raise TypeError("vector_map must be a VectorMap instance with explicit coverage.")
    if not isinstance(type_labels_enabled, bool):
        raise TypeError("type_labels_enabled must be a bool.")
    if not isfinite(visibility_radius_m) or visibility_radius_m < 0.0:
        raise ValueError("visibility_radius_m must be finite and nonnegative.")
    if not isfinite(minimum_range_m) or minimum_range_m < 0.0:
        raise ValueError("minimum_range_m must be finite and nonnegative.")
    if minimum_range_m > visibility_radius_m:
        raise ValueError("minimum_range_m cannot exceed visibility_radius_m.")
    if type(range_inclusive) is not bool:
        raise TypeError("range_inclusive must be a bool.")
    if not isfinite(angular_resolution_deg) or not 0.0 <= angular_resolution_deg < 180.0:
        raise ValueError("angular_resolution_deg must be finite and in [0, 180).")

    try:
        crs = CRS.from_user_input(observer_crs)
    except (TypeError, ValueError) as exc:
        raise ValueError("observer_crs must be a valid projected metre CRS.") from exc
    axes = crs.axis_info
    if (
        not crs.is_projected
        or len(axes) < 2
        or any(abs(axis.unit_conversion_factor - 1.0) > 1e-12 for axis in axes[:2])
    ):
        raise ValueError("observer_crs must be projected and use metres on both horizontal axes.")
    if not crs.equals(vector_map.crs):
        raise ValueError("observer_crs must match vector_map.crs.")
    try:
        east_m, north_m = observer_xy_m
    except (TypeError, ValueError) as exc:
        raise ValueError("observer_xy_m must contain two finite coordinates.") from exc
    if not isfinite(east_m) or not isfinite(north_m):
        raise ValueError("observer_xy_m must contain two finite coordinates.")
    observer_xy = (float(east_m), float(north_m))

    if prepared_context is None:
        prepared_context = prepare_prediction_context(
            catalog,
            observer_crs=crs,
            visibility_radius_m=visibility_radius_m,
            vector_map=vector_map,
            minimum_range_m=minimum_range_m,
            range_inclusive=range_inclusive,
            angular_resolution_deg=angular_resolution_deg,
            type_labels_enabled=type_labels_enabled,
            spatial_index=spatial_index,
        )
    else:
        expected = (
            crs.to_wkt(),
            visibility_radius_m,
            minimum_range_m,
            range_inclusive,
            angular_resolution_deg,
            type_labels_enabled,
        )
        if (
            not isinstance(prepared_context, PreparedPredictionContext)
            or (
                prepared_context.catalog is not catalog
                and prepared_context.source_catalog is not catalog
            )
            or prepared_context.vector_map is not vector_map
            or prepared_context.crs != crs
            or prepared_context.signature != expected
            or (spatial_index is not None and prepared_context.spatial_index is not spatial_index)
        ):
            raise ValueError(
                "prepared_context does not match catalog, map, index, CRS, or settings."
            )
    observer_lon, observer_lat = prepared_context.inverse.transform(*observer_xy)
    if not isfinite(observer_lon) or not isfinite(observer_lat):
        raise ValueError("observer_xy_m cannot be transformed to a valid geographic position.")

    nearby_geometry = prepared_context.spatial_index.query_radius_with_geometry(
        observer_lon, observer_lat, visibility_radius_m + prepared_context.max_offset_m
    )
    detections: list[DetectedObject] = []
    metadata_by_key: dict[str, _PreparedRecord] = {}
    offset_applied_by_key: dict[str, OffsetQuality] = {}
    unresolved: list[str] = []
    unresolved_model_keys: list[str] = []
    identity_limited_keys: list[str] = []
    observer_status = vector_map.point_water_status(observer_xy)
    los_candidates: list[tuple[_PreparedRecord, BearingDistance, tuple[float, float]]] = []

    for record_index, indexed_bearing_deg, indexed_distance_m in nearby_geometry:
        prepared_record = prepared_context.records[record_index]
        record = prepared_record.record
        if not prepared_record.is_physical:
            continue
        east_offset_m = prepared_record.east_offset_m
        north_offset_m = prepared_record.north_offset_m
        offset_quality = prepared_record.offset_quality
        if offset_quality == "unsupported":
            unresolved_model_keys.append(record.key)
            continue
        if east_offset_m != 0.0 or north_offset_m != 0.0:
            offset_bearing_deg = degrees(atan2(east_offset_m, north_offset_m)) % 360.0
            offset_range_m = hypot(east_offset_m, north_offset_m)
            target_lon, target_lat, _ = _GEOD.fwd(
                record.longitude_deg,
                record.latitude_deg,
                offset_bearing_deg,
                offset_range_m,
            )
            geometry = geodesic_bearing_distance(
                (float(observer_lon), float(observer_lat)),
                (float(target_lon), float(target_lat)),
            )
        else:
            target_lon, target_lat = record.longitude_deg, record.latitude_deg
            geometry = BearingDistance(
                true_bearing_deg=indexed_bearing_deg,
                distance_m=indexed_distance_m,
                grid_bearing_deg=indexed_bearing_deg,
                meridian_convergence_deg=0.0,
                bearing_error_deg=0.0,
            )
        if range_inclusive:
            if geometry.distance_m < minimum_range_m or geometry.distance_m > visibility_radius_m:
                continue
        elif geometry.distance_m <= minimum_range_m or geometry.distance_m >= visibility_radius_m:
            continue
        target_east_m, target_north_m = prepared_context.forward.transform(target_lon, target_lat)
        target_xy = (float(target_east_m), float(target_north_m))
        los_candidates.append((prepared_record, geometry, target_xy))

    visibilities = vector_map.line_visibility_many(
        observer_xy,
        [target_xy for _, _, target_xy in los_candidates],
    )
    for (prepared_record, geometry, _), visibility in zip(
        los_candidates, visibilities, strict=True
    ):
        record = prepared_record.record
        if visibility == "unknown" or observer_status == "unknown":
            unresolved.append(record.key)
            continue
        if visibility == "blocked" or observer_status == "land":
            continue
        if prepared_record.identity_limited:
            identity_limited_keys.append(record.key)

        source_type = record.normalized_type or record.raw_type
        detections.append(
            DetectedObject(
                object_id=record.key,
                object_type=normalize_object_type(
                    source_type, type_labels_enabled=type_labels_enabled
                )
                or "unknown",
                bearing_deg=geometry.true_bearing_deg,
                range_m=geometry.distance_m,
            )
        )
        metadata_by_key[record.key] = prepared_record
        offset_applied_by_key[record.key] = prepared_record.offset_quality

    grouped = group_angular_detections(detections, angular_resolution_deg)
    key_to_detection = {item.object_id: item for item in detections}
    output: list[PredictedVisibleGroup] = []
    for member_keys in grouped.truth_groups:
        representative_key = min(
            member_keys,
            key=lambda key: (key_to_detection[key].range_m, key),
        )
        representative = key_to_detection[representative_key]
        extent = max(
            _circular_separation(representative.bearing_deg, key_to_detection[key].bearing_deg)
            + metadata_by_key[key].half_width_deg
            for key in member_keys
        )
        output.append(
            PredictedVisibleGroup(
                bearing_deg=representative.bearing_deg % 360.0,
                object_type=representative.object_type if type_labels_enabled else None,
                member_keys=member_keys,
                representative_key=representative_key,
                range_m=representative.range_m,
                extent_half_width_deg=extent,
                catalog_uncertainty_m=_group_uncertainty(member_keys, metadata_by_key),
                ais_antenna_offset_supported=offset_applied_by_key[representative_key]
                == "calibrated",
                visible_feature_offset_quality=offset_applied_by_key[representative_key],
            )
        )

    return PredictedGroupSet(
        groups=tuple(output),
        coverage_status="unresolved" if unresolved or observer_status == "unknown" else "known",
        unresolved_member_keys=tuple(sorted(unresolved)),
        unresolved_model=bool(unresolved_model_keys),
        unresolved_model_keys=tuple(sorted(unresolved_model_keys)),
        identity_limited_keys=tuple(sorted(identity_limited_keys)),
    )


def _visible_feature_offset(record: CatalogRecordV2) -> tuple[float, float, OffsetQuality]:
    metadata = _record_metadata(record)
    present = _VISIBLE_OFFSET_KEYS.intersection(metadata)
    if not present:
        quality: OffsetQuality = (
            "unsupported" if _has_unsupported_model_metadata(metadata) else "nominal_zero_unknown"
        )
        return 0.0, 0.0, quality
    if present != _VISIBLE_OFFSET_KEYS:
        return 0.0, 0.0, "unsupported"
    east_m = metadata["visible_feature_offset_east_m"]
    north_m = metadata["visible_feature_offset_north_m"]
    if (
        isinstance(east_m, bool)
        or not isinstance(east_m, (int, float))
        or not isfinite(east_m)
        or isinstance(north_m, bool)
        or not isinstance(north_m, (int, float))
        or not isfinite(north_m)
        or _has_unsupported_model_metadata(metadata)
    ):
        return 0.0, 0.0, "unsupported"
    return float(east_m), float(north_m), "calibrated"


def _has_unsupported_model_metadata(metadata: dict[str, object]) -> bool:
    for key in metadata:
        normalized = key.casefold()
        if "visible_feature_offset_" in normalized and key not in _VISIBLE_OFFSET_KEYS:
            return True
        if normalized == "angular_half_width_deg":
            continue
        if any(
            token in normalized
            for token in ("antenna", "dimension", "length", "beam", "width", "height")
        ):
            return True
    return False


def _circular_separation(first_deg: float, second_deg: float) -> float:
    return abs((first_deg - second_deg + 180.0) % 360.0 - 180.0)


def _record_metadata(record: CatalogRecordV2) -> dict[str, object]:
    return dict(record.metadata)


def _record_half_width(record: CatalogRecordV2) -> float:
    value = _record_metadata(record).get("angular_half_width_deg", 0.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return 0.0
    return max(0.0, float(value))


def _record_uncertainty(record: CatalogRecordV2) -> float | None:
    metadata = _record_metadata(record)
    for name in ("position_uncertainty_m", "horizontal_uncertainty_m", "uncertainty_m"):
        value = metadata.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if isfinite(value) and value >= 0.0:
                return float(value)
    return None


def _group_uncertainty(
    member_keys: tuple[str, ...], records_by_key: dict[str, _PreparedRecord]
) -> float | None:
    values = [
        uncertainty
        for key in member_keys
        if (uncertainty := records_by_key[key].uncertainty_m) is not None
    ]
    return max(values) if values else None


__all__ = [
    "PredictedGroupSet",
    "PredictedVisibleGroup",
    "PreparedPredictionContext",
    "predict_visible_groups",
    "prepare_prediction_context",
]

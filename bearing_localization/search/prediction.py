"""Predict catalog objects visible from one projected observer position.

Search inputs use only a candidate observer pose and the geographic catalog.
Every range decision is ellipsoidal, bearings are WGS84 true-north initial
bearings, and map coverage uncertainty is carried explicitly in the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot, isfinite
from typing import Literal

from pyproj import CRS, Geod, Transformer

from maritime_localization_shared.contracts.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime_localization_shared.contracts.object_types import (
    is_visual_entity,
    normalize_object_type,
)
from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime_localization_shared.geospatial.coordinates import geodesic_bearing_distance
from maritime_localization_shared.geospatial.spatial_index import GeographicSpatialIndex
from maritime_localization_shared.geospatial.vector_map import VectorMap

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


def predict_visible_groups(
    observer_xy_m: tuple[float, float],
    catalog: CatalogV2,
    *,
    observer_crs: str | int | CRS,
    visibility_radius_m: float,
    vector_map: VectorMap,
    angular_resolution_deg: float = 0.1,
    type_labels_enabled: bool = True,
    spatial_index: GeographicSpatialIndex | None = None,
) -> PredictedGroupSet:
    """Predict range-limited, line-of-sight-visible angular catalog groups.

    ``observer_xy_m`` is expressed in ``observer_crs``, which must be the
    same projected-metre CRS used by ``vector_map``. Catalog coordinates are
    transformed from their V2 longitude/latitude fields only for map LOS; range
    and true bearing always use WGS84 geodesics. Range is inclusive and applied
    before LOS. Unknown observer/LOS coverage marks the result unresolved and
    does not silently classify the affected feature as visible or blocked.

    Explicit visible-feature east/north offset metadata are applied as
    true-east/north WGS84 offsets from the catalog reference point. Missing
    offsets use a nominal zero with unknown quality. Supplied unsupported
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

    inverse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    observer_lon, observer_lat = inverse.transform(*observer_xy)
    if not isfinite(observer_lon) or not isfinite(observer_lat):
        raise ValueError("observer_xy_m cannot be transformed to a valid geographic position.")

    index = spatial_index or GeographicSpatialIndex(
        ((record.longitude_deg, record.latitude_deg) for record in catalog.records),
        keys=(record.key for record in catalog.records),
    )
    expected_keys = tuple(record.key for record in catalog.records)
    if index.keys != expected_keys:
        raise ValueError("spatial_index must match catalog keys and order.")
    forward = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    physical_records = tuple(
        record
        for record in catalog.records
        if is_visual_entity(
            record.normalized_type or record.raw_type,
            type_labels_enabled=type_labels_enabled,
        )
    )
    physical_by_key = {record.key: record for record in physical_records}
    offset_data = {record.key: _visible_feature_offset(record) for record in physical_records}
    max_offset_m = max(
        (
            hypot(east_m, north_m)
            for east_m, north_m, quality in offset_data.values()
            if quality != "unsupported"
        ),
        default=0.0,
    )
    nearby_indices = index.query_radius(
        observer_lon, observer_lat, visibility_radius_m + max_offset_m
    )
    detections: list[DetectedObject] = []
    metadata_by_key: dict[str, CatalogRecordV2] = {}
    offset_applied_by_key: dict[str, OffsetQuality] = {}
    unresolved: list[str] = []
    unresolved_model_keys = tuple(
        sorted(
            record.key for record in physical_records if offset_data[record.key][2] == "unsupported"
        )
    )
    observer_status = vector_map.point_water_status(observer_xy)

    for record_index in nearby_indices:
        record = catalog.records[record_index]
        if record.key not in physical_by_key:
            continue
        east_offset_m, north_offset_m, offset_quality = offset_data[record.key]
        if offset_quality == "unsupported":
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
        else:
            target_lon, target_lat = record.longitude_deg, record.latitude_deg
        geometry = geodesic_bearing_distance(
            (float(observer_lon), float(observer_lat)),
            (float(target_lon), float(target_lat)),
        )
        if geometry.distance_m > visibility_radius_m:
            continue

        target_east_m, target_north_m = forward.transform(target_lon, target_lat)
        target_xy = (float(target_east_m), float(target_north_m))
        visibility = vector_map.line_visibility(observer_xy, target_xy)
        if visibility == "unknown" or observer_status == "unknown":
            unresolved.append(record.key)
            continue
        if visibility == "blocked" or observer_status == "land":
            continue

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
        metadata_by_key[record.key] = record
        offset_applied_by_key[record.key] = offset_quality

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
            + _record_half_width(metadata_by_key[key])
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
        unresolved_model_keys=unresolved_model_keys,
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
    member_keys: tuple[str, ...], records_by_key: dict[str, CatalogRecordV2]
) -> float | None:
    values = [
        uncertainty
        for key in member_keys
        if (uncertainty := _record_uncertainty(records_by_key[key])) is not None
    ]
    return max(values) if values else None


__all__ = ["PredictedGroupSet", "PredictedVisibleGroup", "predict_visible_groups"]

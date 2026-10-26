"""Predict catalog objects visible from one projected observer position.

Search inputs use only a candidate observer pose and the geographic catalog.
Every range decision is ellipsoidal, bearings are WGS84 true-north initial
bearings, and map coverage uncertainty is carried explicitly in the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from pyproj import CRS, Transformer

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


@dataclass(frozen=True, slots=True)
class PredictedGroupSet:
    """Predicted groups plus map-coverage completeness for the query."""

    groups: tuple[PredictedVisibleGroup, ...]
    coverage_status: CoverageStatus
    unresolved_member_keys: tuple[str, ...]


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

    AIS feature/antenna offsets have no standardized V2 metadata key and are
    not applied. ``catalog_uncertainty_m`` carries a recognized position
    uncertainty metadata value when supplied; otherwise it is ``None``.
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
    nearby_indices = index.query_radius(observer_lon, observer_lat, visibility_radius_m)
    forward = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    detections: list[DetectedObject] = []
    metadata_by_key: dict[str, CatalogRecordV2] = {}
    unresolved: list[str] = []
    observer_status = vector_map.point_water_status(observer_xy)

    for record_index in nearby_indices:
        record = catalog.records[record_index]
        source_type = record.normalized_type or record.raw_type
        if not is_visual_entity(source_type, type_labels_enabled=type_labels_enabled):
            continue
        target_east_m, target_north_m = forward.transform(record.longitude_deg, record.latitude_deg)
        target_xy = (float(target_east_m), float(target_north_m))
        visibility = vector_map.line_visibility(observer_xy, target_xy)
        if visibility == "unknown" or observer_status == "unknown":
            unresolved.append(record.key)
            continue
        if visibility == "blocked" or observer_status == "land":
            continue

        geometry = geodesic_bearing_distance(
            (float(observer_lon), float(observer_lat)),
            (record.longitude_deg, record.latitude_deg),
        )
        # GeographicSpatialIndex has already performed the inclusive exact
        # range check; geodesic distance is retained for representative choice.
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
            )
        )

    return PredictedGroupSet(
        groups=tuple(output),
        coverage_status="unresolved" if unresolved else "known",
        unresolved_member_keys=tuple(sorted(unresolved)),
    )


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

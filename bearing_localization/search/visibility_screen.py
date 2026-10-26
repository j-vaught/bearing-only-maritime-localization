"""LOS screen certifying a lower bound on visible angular groups."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot, isfinite
from typing import Literal, cast

from pyproj import Geod

from bearing_localization.search.prediction import PreparedPredictionContext, _PreparedRecord
from maritime_localization_shared.contracts.object_types import normalize_object_type
from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime_localization_shared.geospatial.coordinates import (
    BearingDistance,
    geodesic_bearing_distance,
)

_GEOD = Geod(ellps="WGS84")
Status = Literal["visible", "blocked", "unresolved", "unexamined"]
_OFFSET_KEYS = frozenset({"visible_feature_offset_east_m", "visible_feature_offset_north_m"})
_SAFE_METADATA_KEYS = frozenset(
    {
        *_OFFSET_KEYS,
        "angular_half_width_deg",
        "position_uncertainty_m",
        "horizontal_uncertainty_m",
        "uncertainty_m",
        "physical_linkage_status",
        "identity_ambiguous",
        "physical_entity_id",
        "physical_group_id",
        "source_record_identity_status",
        "observation_association_status",
        "identity_resolution",
        "identity_resolution_reason",
        "source_feature_id",
        "source_component_count",
        "source_components",
        "source_attributes",
        "chart_id",
        "scale_band",
        "service_layer",
        "uncertainty_status",
        "identity_kind",
        "motion_status",
        "motion_valid",
        "position_interpolated",
    }
)
_MODEL_KEY_TOKENS = (
    "antenna",
    "beam",
    "bearing",
    "coordinate",
    "dimension",
    "displacement",
    "feature_center",
    "geometry",
    "height",
    "length",
    "location",
    "longitude",
    "latitude",
    "offset",
    "position",
    "range",
    "shift",
    "translation",
    "width",
)


@dataclass(frozen=True, slots=True)
class ComponentEvidence:
    component_index: int
    member_keys: tuple[str, ...]
    status: Status
    confirmed_clear_key: str | None
    examined_member_keys: tuple[str, ...]
    clear_member_keys: tuple[str, ...]
    blocked_member_keys: tuple[str, ...]
    unknown_member_keys: tuple[str, ...]
    unexamined_member_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VisibilityScreenResult:
    minimum_visible_group_count: int
    certified: bool
    reason: str | None
    complete: bool
    exact_visible_group_count: int | None
    components: tuple[ComponentEvidence, ...]
    ray_check_count: int
    clear_count: int
    blocked_count: int
    unknown_count: int
    unexamined_component_count: int
    unexamined_member_count: int
    unresolved_model_member_keys: tuple[str, ...]
    unresolved_coverage_member_keys: tuple[str, ...]
    observer_status: Literal["water", "land", "unknown"]


def screen_candidate_visibility(
    observer_xy_m: tuple[float, float],
    prepared_context: PreparedPredictionContext,
    *,
    stop_after_minimum_groups: int | None = None,
    scan_all_members: bool = False,
) -> VisibilityScreenResult:
    """Certify one surviving group per clear pre-LOS circular component."""
    return _screen_candidate_visibility(
        observer_xy_m,
        prepared_context,
        stop_after_minimum_groups=stop_after_minimum_groups,
        scan_all_members=scan_all_members,
        use_batched_los=False,
    )


def _screen_candidate_visibility_batched(
    observer_xy_m: tuple[float, float],
    prepared_context: PreparedPredictionContext,
    *,
    stop_after_minimum_groups: int | None = None,
    scan_all_members: bool = False,
) -> VisibilityScreenResult:
    """Full-scan batch implementation retained pending measured path selection."""
    return _screen_candidate_visibility(
        observer_xy_m,
        prepared_context,
        stop_after_minimum_groups=stop_after_minimum_groups,
        scan_all_members=scan_all_members,
        use_batched_los=True,
    )


def _screen_candidate_visibility_scalar(
    observer_xy_m: tuple[float, float],
    prepared_context: PreparedPredictionContext,
    *,
    stop_after_minimum_groups: int | None = None,
    scan_all_members: bool = False,
) -> VisibilityScreenResult:
    """Scalar LOS oracle retained for exact parity checks and fallback diagnosis."""
    return _screen_candidate_visibility(
        observer_xy_m,
        prepared_context,
        stop_after_minimum_groups=stop_after_minimum_groups,
        scan_all_members=scan_all_members,
        use_batched_los=False,
    )


def _screen_candidate_visibility(
    observer_xy_m: tuple[float, float],
    prepared_context: PreparedPredictionContext,
    *,
    stop_after_minimum_groups: int | None,
    scan_all_members: bool,
    use_batched_los: bool,
) -> VisibilityScreenResult:
    """Implementation shared by the batch path and scalar reference."""
    # Shared grouping separates components at gaps larger than the threshold.
    # Removing occluded members only widens retained gaps, so components cannot join.
    if not isinstance(prepared_context, PreparedPredictionContext):
        raise TypeError("prepared_context must be a PreparedPredictionContext.")
    if stop_after_minimum_groups is not None and (
        isinstance(stop_after_minimum_groups, bool)
        or not isinstance(stop_after_minimum_groups, int)
        or stop_after_minimum_groups < 0
    ):
        raise ValueError("stop_after_minimum_groups must be a nonnegative integer or None.")
    if not isinstance(scan_all_members, bool):
        raise TypeError("scan_all_members must be bool.")
    try:
        east, north = observer_xy_m
    except (TypeError, ValueError) as exc:
        raise ValueError("observer_xy_m must contain two finite coordinates.") from exc
    if not isfinite(east) or not isfinite(north):
        raise ValueError("observer_xy_m must contain two finite coordinates.")
    observer = (float(east), float(north))
    sig = prepared_context.signature
    lon0, lat0 = prepared_context.inverse.transform(*observer)
    if not isfinite(lon0) or not isfinite(lat0):
        raise ValueError("observer_xy_m cannot be transformed to a valid geographic position.")
    vector_map = prepared_context.vector_map
    observer_status = vector_map.point_water_status(observer)
    nearby = prepared_context.spatial_index.query_radius(
        lon0, lat0, cast(float, sig[1]) + prepared_context.max_offset_m
    )
    targets: dict[str, tuple[_PreparedRecord, BearingDistance, tuple[float, float]]] = {}
    model_keys: list[str] = []
    for idx in nearby:
        item = prepared_context.records[idx]
        record = item.record
        if not item.is_physical:
            continue
        if item.offset_quality == "unsupported" or _has_unsupported_metadata_model(
            dict(record.metadata)
        ):
            model_keys.append(record.key)
            continue
        lon, lat = record.longitude_deg, record.latitude_deg
        if item.east_offset_m != 0 or item.north_offset_m != 0:
            az = degrees(atan2(item.east_offset_m, item.north_offset_m)) % 360
            lon, lat, _ = _GEOD.fwd(lon, lat, az, hypot(item.east_offset_m, item.north_offset_m))
        geom = geodesic_bearing_distance((float(lon0), float(lat0)), (float(lon), float(lat)))
        radius, minimum = cast(float, sig[1]), cast(float, sig[2])
        inside = (
            minimum <= geom.distance_m <= radius if sig[3] else minimum < geom.distance_m < radius
        )
        if not inside:
            continue
        x, y = prepared_context.forward.transform(lon, lat)
        targets[record.key] = (item, geom, (float(x), float(y)))
    detections = [
        DetectedObject(
            key,
            normalize_object_type(
                item.record.normalized_type or item.record.raw_type,
                type_labels_enabled=bool(sig[5]),
            )
            or "unknown",
            geom.true_bearing_deg,
            geom.distance_m,
        )
        for key, (item, geom, _) in targets.items()
    ]
    groups = group_angular_detections(detections, cast(float, sig[4])).truth_groups
    batch_visibility_by_key: dict[str, str] | None = None
    if (
        use_batched_los
        and scan_all_members
        and stop_after_minimum_groups is None
        and observer_status == "water"
    ):
        ray_keys = tuple(
            key
            for members in groups
            for key in sorted(members, key=lambda member: (targets[member][1].distance_m, member))
        )
        ray_states = vector_map.line_visibility_many(
            observer, tuple(targets[key][2] for key in ray_keys)
        )
        batch_visibility_by_key = dict(zip(ray_keys, ray_states, strict=True))
    evidence: list[ComponentEvidence] = []
    visibility: dict[str, str] = {}
    unresolved: set[str] = set()
    rays = nclear = nblocked = nunknown = k = 0
    stopped = False
    for ci, members in enumerate(groups):
        ordered = sorted(members, key=lambda key: (targets[key][1].distance_m, key))
        checked: list[str] = []
        clear: list[str] = []
        blocked: list[str] = []
        unknown: list[str] = []
        confirmed = None
        for key in ordered:
            if observer_status == "land":
                blocked.append(key)
                visibility[key] = "blocked"
                continue
            if observer_status == "unknown":
                unknown.append(key)
                unresolved.add(key)
                visibility[key] = "unknown"
                continue
            checked.append(key)
            rays += 1
            state = (
                batch_visibility_by_key[key]
                if batch_visibility_by_key is not None
                else vector_map.line_visibility(observer, targets[key][2])
            )
            visibility[key] = state
            if state == "clear":
                clear.append(key)
                if confirmed is None:
                    confirmed = key
                    k += 1
                    if not scan_all_members:
                        if stop_after_minimum_groups is not None and k >= stop_after_minimum_groups:
                            stopped = True
                        break
            elif state == "blocked":
                blocked.append(key)
            else:
                unknown.append(key)
                unresolved.add(key)
        nclear += len(clear)
        nblocked += len(blocked)
        nunknown += len(unknown)
        left = tuple(
            key
            for key in ordered
            if key not in checked and key not in clear and key not in blocked and key not in unknown
        )
        status: Status = (
            "visible"
            if confirmed
            else "unresolved"
            if unknown
            else "unexamined"
            if left
            else "blocked"
        )
        evidence.append(
            ComponentEvidence(
                ci,
                members,
                status,
                confirmed,
                tuple(checked),
                tuple(clear),
                tuple(blocked),
                tuple(unknown),
                left,
            )
        )
        if stopped:
            break
    if stopped:
        for ci in range(len(evidence), len(groups)):
            members = groups[ci]
            evidence.append(
                ComponentEvidence(ci, members, "unexamined", None, (), (), (), (), members)
            )
    model, cov = tuple(sorted(set(model_keys))), tuple(sorted(unresolved))
    complete = not stopped and all(not c.unexamined_member_keys for c in evidence)
    reason = (
        "unsupported_feature_model"
        if model
        else "unknown_observer_coverage"
        if observer_status == "unknown"
        else "unknown_line_of_sight"
        if cov
        else None
    )
    certified = reason is None
    if observer_status == "land":
        k, certified, reason = 0, True, None
    exact = None
    if scan_all_members and complete and certified:
        visible = [d for d in detections if visibility.get(d.object_id) == "clear"]
        exact = len(group_angular_detections(visible, cast(float, sig[4])).truth_groups)
    return VisibilityScreenResult(
        k,
        certified,
        reason,
        complete,
        exact,
        tuple(evidence),
        rays,
        nclear,
        nblocked,
        nunknown,
        sum(c.status == "unexamined" for c in evidence),
        sum(len(c.unexamined_member_keys) for c in evidence),
        model,
        cov,
        observer_status,
    )


def _has_unsupported_metadata_model(metadata: dict[str, object]) -> bool:
    """Fail closed on metadata that can encode unsupported target geometry.

    Top-level metadata uses an explicit allowlist. Nested source attributes and
    component records are provenance containers, so only geometry-bearing keys
    invalidate the screen certificate.
    """
    if any(key not in _SAFE_METADATA_KEYS for key in metadata):
        return True

    component_keys = {
        "feature_id",
        "chart_id",
        "scale_band",
        "service_layer",
        "longitude_deg",
        "latitude_deg",
        "source_epoch_sordat",
    }
    components = metadata.get("source_components")
    if components is not None:
        if not isinstance(components, (tuple, list)):
            return True
        for component in components:
            if not isinstance(component, dict) or any(
                key not in component_keys for key in component
            ):
                return True

    def source_attributes_have_model_key(value: object) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).casefold()
                if any(token in normalized for token in _MODEL_KEY_TOKENS):
                    # The known ENC source attribute height is vertical and
                    # inert under the scorer's declared 2D point-geometry model.
                    if normalized == "height":
                        continue
                    return True
                if source_attributes_have_model_key(child):
                    return True
        elif isinstance(value, (tuple, list)):
            return any(source_attributes_have_model_key(child) for child in value)
        return False

    return source_attributes_have_model_key(metadata.get("source_attributes"))


__all__ = ["ComponentEvidence", "VisibilityScreenResult", "screen_candidate_visibility"]

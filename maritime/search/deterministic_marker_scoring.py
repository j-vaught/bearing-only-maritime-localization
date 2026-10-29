"""Hard positive-evidence scoring for chart-marker detections.

Only positive marker classifications create obligations. Every such detection
must map injectively to a distinct, visible physical chart-aid identity inside
its calibrated bearing tolerance. Unknown geometry remains unresolved.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import dataclass
from importlib import import_module
from math import asin, degrees, isfinite
from typing import Any, Literal

import numpy as np
from scipy.optimize import linear_sum_assignment

from maritime.catalog_identity import canonicalize_chart_aids
from maritime.schemas.inputs_v2 import CatalogRecordV2, CatalogV2
from maritime.schemas.object_types import normalize_object_type

from .prediction import PredictedGroupSet

MapStatus = Literal["known", "land", "unknown"]
MarkerOutcome = Literal["matched", "no_compatible_group", "unresolved", "not_positive"]
OverallStatus = Literal["consistent", "inconsistent", "unresolved", "invalid_candidate"]


@dataclass(frozen=True, slots=True)
class MarkerObservation:
    """One detector output; only ``positive`` outputs impose a chart obligation."""

    marker_id: str
    bearing_deg: float
    tolerance_deg: float
    positive: bool = True
    identity_status: Literal["resolved", "unresolved"] = "resolved"
    tolerance_calibrated: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.marker_id, str) or not self.marker_id.strip():
            raise ValueError("marker_id must be a nonempty string.")
        if not isfinite(self.bearing_deg) or not 0 <= self.bearing_deg < 360:
            raise ValueError("bearing_deg must be finite and in [0, 360).")
        if not isfinite(self.tolerance_deg) or not 0 <= self.tolerance_deg <= 180:
            raise ValueError("tolerance_deg must be finite and in [0, 180].")
        if type(self.positive) is not bool or type(self.tolerance_calibrated) is not bool:
            raise TypeError("positive and tolerance_calibrated must be bool values.")
        if self.identity_status not in ("resolved", "unresolved"):
            raise ValueError("identity_status must be resolved or unresolved.")


@dataclass(frozen=True, slots=True)
class MarkerMatch:
    marker_id: str
    outcome: MarkerOutcome
    physical_entity_id: str | None = None
    reason: str | None = None
    residual_deg: float | None = None


@dataclass(frozen=True, slots=True)
class MarkerScore:
    status: OverallStatus
    matches: tuple[MarkerMatch, ...]
    association_ambiguous: bool = False
    alternative_assignment: tuple[tuple[str, str], ...] = ()
    reason: str | None = None
    total_abs_residual_deg: float | None = None
    matching_backend: Literal["cpu", "gpu"] = "cpu"

    @property
    def matched_count(self) -> int:
        return sum(item.outcome == "matched" for item in self.matches)

    @property
    def unresolved_count(self) -> int:
        return sum(item.outcome == "unresolved" for item in self.matches)


def score_marker_candidates(
    markers: Iterable[MarkerObservation],
    predicted: PredictedGroupSet,
    catalog: CatalogV2,
    *,
    map_status: MapStatus = "known",
    chart_complete: bool | None = None,
    catalog_position_uncertainty_m: float | None = None,
    catalog_exact_positions_asserted: bool = False,
    catalog_nominal_visible_offsets_asserted: bool = False,
    catalog_object_identities_proven: bool = False,
    matching_backend: Literal["cpu", "gpu"] = "cpu",
    device_id: int | None = None,
    cupy_module: Any | None = None,
) -> MarkerScore:
    """Score positive marker detections against exact visible chart-aid groups.

    ``predicted`` must come from ``predict_visible_groups`` for this candidate,
    so its groups already reflect range, land occlusion, and map coverage. Marker
    matching is deterministic and injective. A complete matching proves the
    detections are supported; alternative complete assignments are preserved.
    """
    observations = tuple(markers)
    if any(not isinstance(marker, MarkerObservation) for marker in observations):
        raise TypeError("markers must contain MarkerObservation records.")
    if len({marker.marker_id for marker in observations}) != len(observations):
        raise ValueError("marker_id values must be unique within a candidate.")
    if not isinstance(predicted, PredictedGroupSet) or not isinstance(catalog, CatalogV2):
        raise TypeError("predicted and catalog must use their shared V2 types.")
    catalog = canonicalize_chart_aids(catalog)
    if map_status not in ("known", "land", "unknown"):
        raise ValueError("map_status must be known, land, or unknown.")
    if matching_backend not in ("cpu", "gpu"):
        raise ValueError("matching_backend must be cpu or gpu.")
    if device_id is not None and (type(device_id) is not int or device_id < 0):
        raise ValueError("device_id must be a nonnegative integer or None.")
    if matching_backend == "cpu" and device_id is not None:
        raise ValueError("device_id is only valid for the GPU matching backend.")
    if chart_complete is not None and type(chart_complete) is not bool:
        raise TypeError("chart_complete must be bool or None.")
    if catalog_position_uncertainty_m is not None and (
        isinstance(catalog_position_uncertainty_m, bool)
        or not isinstance(catalog_position_uncertainty_m, (int, float))
        or not isfinite(catalog_position_uncertainty_m)
        or catalog_position_uncertainty_m < 0
    ):
        raise ValueError(
            "catalog_position_uncertainty_m must be a nonnegative finite bound or None."
        )
    for name, value in (
        ("catalog_exact_positions_asserted", catalog_exact_positions_asserted),
        ("catalog_nominal_visible_offsets_asserted", catalog_nominal_visible_offsets_asserted),
        ("catalog_object_identities_proven", catalog_object_identities_proven),
    ):
        if type(value) is not bool:
            raise TypeError(f"{name} must be bool.")
    if catalog_exact_positions_asserted and catalog_position_uncertainty_m not in (None, 0, 0.0):
        raise ValueError("exact positions cannot have a positive uncertainty bound.")

    positives = [marker for marker in observations if marker.positive]
    negative_rows = tuple(
        MarkerMatch(m.marker_id, "not_positive") for m in observations if not m.positive
    )
    if map_status == "land":
        rows = tuple(
            MarkerMatch(m.marker_id, "unresolved", reason="candidate is on land") for m in positives
        )
        return MarkerScore(
            "invalid_candidate",
            _ordered_rows(observations, (*rows, *negative_rows)),
            reason="candidate is on land",
        )
    if map_status == "unknown" or predicted.coverage_status != "known":
        reason = "map or visibility coverage is unknown"
        rows = tuple(MarkerMatch(m.marker_id, "unresolved", reason=reason) for m in positives)
        return MarkerScore(
            "unresolved",
            _ordered_rows(observations, (*rows, *negative_rows)),
            reason=reason,
        )
    if predicted.unresolved_member_keys:
        reason = "one or more visible chart features have unresolved map visibility"
        rows = tuple(MarkerMatch(m.marker_id, "unresolved", reason=reason) for m in positives)
        return MarkerScore(
            "unresolved",
            _ordered_rows(observations, (*rows, *negative_rows)),
            reason=reason,
        )
    if predicted.unresolved_model:
        rows = tuple(
            MarkerMatch(
                m.marker_id, "unresolved", reason="visible chart geometry model is unsupported"
            )
            for m in positives
        )
        return MarkerScore(
            "unresolved",
            _ordered_rows(observations, (*rows, *negative_rows)),
            reason="visible chart geometry model is unsupported",
        )
    if not positives:
        return MarkerScore(
            "consistent",
            negative_rows,
            total_abs_residual_deg=0.0,
            matching_backend=matching_backend,
        )

    by_key = {record.key: record for record in catalog.records}
    identity_limited = set(predicted.identity_limited_keys)
    # Prediction groups include nominally visible records only. Any chart aid
    # with unresolved identity/location could move across the nominal range or
    # land-occlusion boundary, so it remains a possible (unbounded) marker group.
    unresolved_catalog_geometry = any(
        not _record_geometry_is_exact(
            record,
            position_uncertainty_m=catalog_position_uncertainty_m,
            exact_positions_asserted=catalog_exact_positions_asserted,
            nominal_visible_offsets_asserted=catalog_nominal_visible_offsets_asserted,
            identities_proven=catalog_object_identities_proven,
        )
        for record in catalog.records
        if _is_possible_chart_aid(record)
    )
    # Build conservative physical-aid candidates. Each visible group can name
    # exactly one physical entity; source components sharing that entity collapse.
    aid_groups: dict[str, tuple[float, float, float, float]] = {}
    ambiguous_entity_ids: set[str] = set()
    uncertain_keys: set[str] = set()
    for group in predicted.groups:
        missing_keys = [key for key in group.member_keys if key not in by_key]
        aids = [
            by_key[key]
            for key in group.member_keys
            if key in by_key and _is_possible_chart_aid(by_key[key])
        ]
        if missing_keys:
            uncertain_keys.update(missing_keys)
        if not aids:
            continue
        entity_ids: set[str] = set()
        for record in aids:
            metadata = dict(record.metadata)
            if not _is_chart_physical_aid(record):
                uncertain_keys.add(record.key)
            entity = metadata.get("physical_entity_id")
            linkage = metadata.get("physical_linkage_status")
            identity_proven = (
                metadata.get("identity_ambiguous") is not True
                and linkage != "unresolved"
                and (
                    linkage in ("resolved", "resolved_by_chart_heuristic")
                    and metadata.get("identity_ambiguous") is False
                    or (
                        isinstance(entity, str)
                        and bool(entity.strip())
                        and catalog_object_identities_proven
                    )
                )
            )
            if entity is None and identity_proven:
                entity = record.key
            if (
                not identity_proven
                or not isinstance(entity, str)
                or not entity.strip()
                or metadata.get("identity_ambiguous") is True
                or linkage == "unresolved"
            ):
                uncertain_keys.add(record.key)
            else:
                entity_ids.add(entity)
        if len(entity_ids) != 1 or any(record.key in identity_limited for record in aids):
            uncertain_keys.update(record.key for record in aids)
            ambiguous_entity_ids.update(entity_ids)
            continue
        entity = next(iter(entity_ids))
        # Angular grouping may have merged distinct physical objects. Never
        # count one merged bearing group as multiple independently visible aids.
        if entity in aid_groups:
            ambiguous_entity_ids.add(entity)
            uncertain_keys.update(record.key for record in aids)
            continue
        if len({dict(record.metadata).get("physical_entity_id") for record in aids}) != 1:
            uncertain_keys.update(record.key for record in aids)
            continue
        record_uncertainties = [
            _effective_position_uncertainty(
                record,
                bound_m=catalog_position_uncertainty_m,
                exact_asserted=catalog_exact_positions_asserted,
            )
            for record in aids
        ]
        predicted_uncertainty = group.catalog_uncertainty_m
        known_uncertainties = [value for value in record_uncertainties if value is not None]
        uncertainty_values = list(known_uncertainties)
        if predicted_uncertainty is not None:
            uncertainty_values.append(predicted_uncertainty)
        uncertainty = (
            None
            if any(value is None for value in record_uncertainties)
            else max(uncertainty_values, default=None)
        )
        calibrated_offsets = all(
            _offset_is_certified(record, catalog_nominal_visible_offsets_asserted)
            for record in aids
        )
        if (
            uncertainty is None
            or any(value is None for value in record_uncertainties)
            or not calibrated_offsets
        ):
            uncertain_keys.update(record.key for record in aids)
        else:
            # The group summary must conservatively represent its source records.
            # A mismatch is unsupported input, not permission to narrow a bearing.
            expected_uncertainty = max(
                float(value) for value in record_uncertainties if value is not None
            )
            if uncertainty + 1e-9 < expected_uncertainty:
                uncertain_keys.update(record.key for record in aids)
            else:
                aid_groups[entity] = (
                    group.bearing_deg,
                    group.range_m,
                    uncertainty,
                    group.extent_half_width_deg,
                )

    # Unknown/ambiguous chart aid geometries close enough to a detection could
    # change the answer, so they cannot be silently ignored as a non-match.
    edges: list[list[str]] = []
    residual_by_edge: list[dict[str, float]] = []
    marker_uncertain: list[bool] = []
    for marker in positives:
        if marker.identity_status != "resolved" or not marker.tolerance_calibrated:
            edges.append([])
            residual_by_edge.append({})
            marker_uncertain.append(True)
            continue
        valid: list[str] = []
        edge_residuals: dict[str, float] = {}
        possible_unknown = False
        for entity, (bearing, range_m, uncertainty, extent) in aid_groups.items():
            angular_uncertainty = (
                180.0
                if range_m <= 0 or uncertainty >= range_m
                else degrees(asin(uncertainty / range_m))
            )
            separation = _separation(marker.bearing_deg, bearing)
            if uncertainty == 0.0 and separation <= marker.tolerance_deg:
                valid.append(entity)
                edge_residuals[entity] = separation
            elif separation <= marker.tolerance_deg + angular_uncertainty + extent:
                # Extent and nonzero location uncertainty establish only a
                # possible match; they cannot prove that the target lies inside
                # the calibrated detection tolerance.
                possible_unknown = True
        for group in predicted.groups:
            if not any(key in uncertain_keys for key in group.member_keys) and not any(
                (
                    dict(by_key[key].metadata).get("physical_entity_id") in ambiguous_entity_ids
                    or key in ambiguous_entity_ids
                )
                for key in group.member_keys
                if key in by_key
            ):
                continue
            # Missing location or identity bounds can place the feature at any bearing.
            possible_unknown = True
        edges.append(sorted(set(valid)))
        residual_by_edge.append(edge_residuals)
        marker_uncertain.append(possible_unknown or unresolved_catalog_geometry)

    assignment = _matching_assignment(
        edges,
        residual_by_edge,
        marker_rows=positives,
        aid_groups=aid_groups,
        backend=matching_backend,
        device_id=device_id,
        cupy_module=cupy_module,
    )
    if assignment is None:
        rows: list[MarkerMatch] = []
        for idx, marker in enumerate(positives):
            if marker_uncertain[idx]:
                rows.append(
                    MarkerMatch(
                        marker.marker_id,
                        "unresolved",
                        reason="compatible chart identity or geometry is uncertain",
                    )
                )
            elif not edges[idx] and chart_complete is True and not any(marker_uncertain):
                rows.append(
                    MarkerMatch(
                        marker.marker_id,
                        "no_compatible_group",
                        reason="no visible aid matches in complete chart",
                    )
                )
            elif not edges[idx]:
                rows.append(
                    MarkerMatch(
                        marker.marker_id,
                        "unresolved",
                        reason="no match is established and chart completeness is unknown",
                    )
                )
            else:
                rows.append(
                    MarkerMatch(
                        marker.marker_id,
                        "unresolved",
                        reason="positive detections cannot be assigned injectively",
                    )
                )
        overall: OverallStatus = (
            "unresolved" if chart_complete is not True or any(marker_uncertain) else "inconsistent"
        )
        return MarkerScore(
            overall,
            _ordered_rows(observations, (*rows, *negative_rows)),
            reason="no complete injective marker assignment",
            matching_backend=matching_backend,
        )
    rows = tuple(
        MarkerMatch(
            marker.marker_id,
            "matched",
            assignment[i],
            residual_deg=residual_by_edge[i][assignment[i]],
        )
        for i, marker in enumerate(positives)
    )
    alternative = _find_alternative_assignment(edges, assignment)
    alternative_rows = (
        tuple((marker.marker_id, alternative[i]) for i, marker in enumerate(positives))
        if alternative
        else ()
    )
    return MarkerScore(
        "consistent",
        _ordered_rows(observations, (*rows, *negative_rows)),
        association_ambiguous=alternative is not None or any(marker_uncertain),
        alternative_assignment=alternative_rows,
        total_abs_residual_deg=sum(
            residual_by_edge[i][entity] for i, entity in enumerate(assignment)
        ),
        matching_backend=matching_backend,
    )


def _matching_assignment(
    edges: list[list[str]],
    residual_by_edge: list[dict[str, float]],
    *,
    marker_rows: list[MarkerObservation],
    aid_groups: dict[str, tuple[float, float, float, float]],
    backend: Literal["cpu", "gpu"],
    device_id: int | None,
    cupy_module: Any | None,
) -> list[str] | None:
    if backend == "cpu":
        return _minimum_residual_assignment(edges, residual_by_edge)
    if not edges:
        return []
    entities = sorted({entity for row in edges for entity in row})
    if not entities:
        return None
    entity_index = {entity: index for index, entity in enumerate(entities)}
    valid_edges = np.zeros((1, len(marker_rows), len(entities)), dtype=bool)
    for marker_index, choices in enumerate(residual_by_edge):
        for entity in choices:
            valid_edges[0, marker_index, entity_index[entity]] = True
    cp = cupy_module if cupy_module is not None else import_module("cupy")
    device_context = cp.cuda.Device(device_id) if device_id is not None else nullcontext()
    from maritime.search.gpu.deterministic_marker_matching import (
        match_positive_markers_cupy,
    )

    with device_context:
        result = match_positive_markers_cupy(
            np.asarray([[marker.bearing_deg for marker in marker_rows]], dtype=float),
            np.asarray([[aid_groups[entity][0] for entity in entities]], dtype=float),
            np.asarray([[marker.tolerance_deg for marker in marker_rows]], dtype=float),
            valid_edges=valid_edges,
            cupy_module=cp,
        )
    if not result.complete[0]:
        return None
    assignment: list[str | None] = [None] * len(marker_rows)
    for marker_index, group_index in result.matches[0]:
        assignment[marker_index] = entities[group_index]
    if any(entity is None for entity in assignment):
        return None
    selected = [str(entity) for entity in assignment]
    # Guard against backend/edge-contract drift before exposing scores.
    expected = sum(residual_by_edge[i][entity] for i, entity in enumerate(selected))
    reported = float(result.residuals_deg[0].item())
    if not np.isclose(expected, reported, rtol=1e-10, atol=1e-10):
        raise RuntimeError("GPU matcher residual differs from the definitive-edge reference.")
    return selected


def _minimum_residual_assignment(
    edges: list[list[str]], residual_by_edge: list[dict[str, float]]
) -> list[str] | None:
    """Return the feasible injective matching with minimum absolute residual sum."""
    if not edges:
        return []
    entities = sorted({entity for row in edges for entity in row})
    if len(entities) < len(edges):
        return None
    entity_index = {entity: index for index, entity in enumerate(entities)}
    costs = np.full((len(edges), len(entities)), np.inf, dtype=float)
    for row_index, choices in enumerate(residual_by_edge):
        for entity, residual in choices.items():
            costs[row_index, entity_index[entity]] = residual
    try:
        row_indices, column_indices = linear_sum_assignment(costs)
    except ValueError:
        return None
    if len(row_indices) != len(edges) or not np.isfinite(costs[row_indices, column_indices]).all():
        return None
    result: list[str | None] = [None] * len(edges)
    for row_index, column_index in zip(row_indices, column_indices, strict=True):
        result[int(row_index)] = entities[int(column_index)]
    return [entity for entity in result if entity is not None]


def _maximum_matching(
    edges: list[list[str]], forbidden: tuple[int, str] | None = None
) -> list[str | None]:
    """Kuhn augmenting-path maximum bipartite matching in polynomial time."""
    owner: dict[str, int] = {}

    def augment(marker_index: int, visited: set[str]) -> bool:
        for entity in edges[marker_index]:
            if forbidden == (marker_index, entity) or entity in visited:
                continue
            visited.add(entity)
            previous = owner.get(entity)
            if previous is None or augment(previous, visited):
                owner[entity] = marker_index
                return True
        return False

    for marker_index in sorted(range(len(edges)), key=lambda i: len(edges[i])):
        augment(marker_index, set())
    result: list[str | None] = [None] * len(edges)
    for entity, marker_index in owner.items():
        result[marker_index] = entity
    return result


def _unique_injective_assignment(edges: list[list[str]]) -> tuple[list[str] | None, bool]:
    """Find a full matching and report whether any alternate full matching exists."""
    matching = _maximum_matching(edges)
    if any(entity is None for entity in matching):
        return None, False
    chosen = [str(entity) for entity in matching]
    return chosen, _find_alternative_assignment(edges, chosen) is not None


def _find_alternative_assignment(edges: list[list[str]], chosen: list[str]) -> list[str] | None:
    """Find one alternative full matching by excluding each chosen edge in turn."""
    for index, entity in enumerate(chosen):
        matching = _maximum_matching(edges, (index, entity))
        if all(value is not None for value in matching):
            alternate = [str(value) for value in matching]
            if alternate != chosen:
                return alternate
    return None


def _is_chart_physical_aid(record: CatalogRecordV2) -> bool:
    label = record.normalized_type or record.raw_type
    if normalize_object_type(label) == "physical_aid":
        return True
    if not _is_possible_chart_aid(record):
        return False
    normalized_label = "".join(char for char in str(label or "").casefold() if char.isalnum())
    return normalized_label in {
        "daymark",
        "topmark",
        "buoylateralpoint",
        "buoycardinalpoint",
        "buoyisolateddangerpoint",
        "buoysafewaterpoint",
        "buoyspecialpurposepoint",
    } or normalized_label.startswith("buoy")


def _is_possible_chart_aid(record: CatalogRecordV2) -> bool:
    label = record.normalized_type or record.raw_type
    normalized = normalize_object_type(label)
    if normalized == "physical_aid":
        return True
    metadata = dict(record.metadata)
    provenance = dict(record.provenance)
    chart_feature = (
        any(
            name in metadata
            for name in (
                "chart_id",
                "source_feature_id",
                "service_layer",
                "physical_linkage_status",
            )
        )
        or any(
            name in provenance
            for name in ("source_chart_ids", "source_feature_ids", "source_layers")
        )
        or record.key.startswith("enc-component:")
    )
    return chart_feature and normalized not in ("vessel", "virtual_aid", "shoreline")


def _record_geometry_is_exact(
    record: CatalogRecordV2,
    *,
    position_uncertainty_m: float | None,
    exact_positions_asserted: bool,
    nominal_visible_offsets_asserted: bool,
    identities_proven: bool,
) -> bool:
    metadata = dict(record.metadata)
    entity = metadata.get("physical_entity_id")
    linkage = metadata.get("physical_linkage_status")
    identity_known = (
        metadata.get("identity_ambiguous") is not True
        and linkage != "unresolved"
        and (
            linkage in ("resolved", "resolved_by_chart_heuristic")
            and metadata.get("identity_ambiguous") is False
            or isinstance(entity, str)
            and bool(entity.strip())
            and identities_proven
        )
    )
    uncertainty = _effective_position_uncertainty(
        record,
        bound_m=position_uncertainty_m,
        exact_asserted=exact_positions_asserted,
    )
    return (
        identity_known
        and uncertainty == 0.0
        and _offset_is_certified(record, nominal_visible_offsets_asserted)
        and _is_chart_physical_aid(record)
    )


def _effective_position_uncertainty(
    record: CatalogRecordV2, *, bound_m: float | None, exact_asserted: bool
) -> float | None:
    metadata_uncertainty = _position_uncertainty(record)
    if exact_asserted:
        if metadata_uncertainty not in (None, 0.0):
            return metadata_uncertainty
        return 0.0
    if metadata_uncertainty is None:
        return None if bound_m is None else float(bound_m)
    return max(metadata_uncertainty, float(bound_m or 0.0))


def _offset_is_certified(record: CatalogRecordV2, nominal_asserted: bool) -> bool:
    metadata = dict(record.metadata)
    east_key, north_key = "visible_feature_offset_east_m", "visible_feature_offset_north_m"
    present = {east_key, north_key}.intersection(metadata)
    if not present:
        unsupported_offset = any(key.startswith("visible_feature_offset_") for key in metadata)
        return nominal_asserted and not unsupported_offset
    return _calibrated_offset(record)


def _position_uncertainty(record: CatalogRecordV2) -> float | None:
    metadata = dict(record.metadata)
    for name in ("position_uncertainty_m", "horizontal_uncertainty_m", "uncertainty_m"):
        value = metadata.get(name)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(value)
            and value >= 0
        ):
            return float(value)
    return None


def _calibrated_offset(record: CatalogRecordV2) -> bool:
    metadata = dict(record.metadata)
    east, north = (
        metadata.get("visible_feature_offset_east_m"),
        metadata.get("visible_feature_offset_north_m"),
    )
    return all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
        for value in (east, north)
    )


def _separation(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _ordered_rows(
    observations: tuple[MarkerObservation, ...], rows: Iterable[MarkerMatch]
) -> tuple[MarkerMatch, ...]:
    by_id = {row.marker_id: row for row in rows}
    return tuple(by_id[marker.marker_id] for marker in observations)


__all__ = ["MarkerMatch", "MarkerObservation", "MarkerScore", "score_marker_candidates"]

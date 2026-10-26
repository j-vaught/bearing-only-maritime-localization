"""Experimental catalog-constrained row-min bound before LOS scoring."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import atan2, degrees, fsum, inf, isfinite, log, nextafter
from typing import TYPE_CHECKING

import numpy as np

from maritime_localization_shared.contracts.inputs_v2 import MeasurementSetV2
from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)

from .config import SearchConfig
from .likelihood import build_likelihood_costs

if TYPE_CHECKING:
    from .pre_los_preparation import PreparedPreLOSTargets


@dataclass(frozen=True, slots=True)
class PreLOSRowMinBoundResult:
    """Complete row-min decomposition or an explicit unresolved result."""

    bound: float | None
    supported: bool
    reason: str | None
    raw_row_min_bound: float | None
    numerical_margin: float
    applied_downward_margin: float | None
    baseline_constant: float | None
    report_clutter_cost: float | None
    edge_floor_matrix: tuple[tuple[float, ...], ...]
    row_terms: tuple[float, ...]
    row_sources: tuple[str, ...]
    component_keys: tuple[tuple[str, ...], ...]
    assumptions: tuple[str, ...]


def pre_los_catalog_row_min_bound(
    measurements: MeasurementSetV2 | Sequence[float] | np.ndarray,
    prepared_targets: PreparedPreLOSTargets,
    config: SearchConfig,
    *,
    numerical_margin: float = 1e-10,
) -> PreLOSRowMinBoundResult:
    """Return a candidate-specific pre-LOS floor or unresolved result.

    The factory-minted bundle binds the complete pre-LOS catalog to one scorer,
    candidate, measurements, configuration, and map-coverage context. Raw
    records are deliberately not accepted by this public interface. This
    remains an experimental, unwired component.
    """
    from .pre_los_preparation import PreLOSTargetRecord, PreparedPreLOSTargets

    if not isinstance(config, SearchConfig):
        raise TypeError("config must be SearchConfig.")
    if isinstance(numerical_margin, bool) or not isfinite(numerical_margin) or numerical_margin < 0:
        raise ValueError("numerical_margin must be finite and nonnegative.")
    if not isinstance(prepared_targets, PreparedPreLOSTargets):
        return _unresolved(
            "A factory-minted complete pre-LOS target bundle is required.", numerical_margin
        )
    try:
        scorer_binding = prepared_targets.matches_scorer(prepared_targets._scorer)
    except (AttributeError, TypeError, ValueError):
        scorer_binding = False
    if scorer_binding is not True:
        return _unresolved("Prepared target scorer binding is invalid.", numerical_margin)
    if prepared_targets.measurements is not measurements or prepared_targets.config is not config:
        return _unresolved(
            "Prepared targets are bound to different measurements or configuration.",
            numerical_margin,
        )
    if prepared_targets.supported is not True:
        return _unresolved(
            prepared_targets.reason or "Pre-LOS target preparation is unresolved.",
            numerical_margin,
        )
    if prepared_targets.reason is not None or prepared_targets.unresolved_model_keys:
        return _unresolved(
            prepared_targets.reason or "Nearby physical target models are unresolved.",
            numerical_margin,
        )
    prepared = tuple(prepared_targets.records)
    if any(not isinstance(r, PreLOSTargetRecord) for r in prepared):
        return _unresolved("Prepared target bundle contains invalid records.", numerical_margin)
    candidate_xy_m = prepared_targets.candidate_xy_m
    if len(candidate_xy_m) != 2 or not all(
        not isinstance(v, bool) and isfinite(v) for v in candidate_xy_m
    ):
        return _unresolved("Prepared candidate coordinates are invalid.", numerical_margin)
    keys = tuple(r.key for r in prepared)
    if len(set(keys)) != len(keys):
        return _unresolved("Prepared target keys are not unique.", numerical_margin)
    if any(r.offset_quality == "unsupported" for r in prepared):
        return _unresolved("A target feature offset is unsupported.", numerical_margin)
    if config.objective_version != "bearing_set_map_v1":
        return _unresolved("Only bearing_set_map_v1 is supported.", numerical_margin)
    if config.common_bearing_bias_prior_deg is not None:
        return _unresolved("Common-bearing-bias likelihood is unsupported.", numerical_margin)
    if config.expected_unmatched_reports <= 0:
        return _unresolved(
            "Zero clutter intensity is unsupported by the exact scorer.", numerical_margin
        )
    coverage_certificate = prepared_targets.coverage_certificate
    map_context = prepared_targets.coverage_map_context
    if coverage_certificate is None or map_context is None:
        return _unresolved("A typed candidate coverage certificate is required.", numerical_margin)
    targets = tuple(sorted((r.key, r.target_map_xy_m) for r in prepared))
    try:
        covered = coverage_certificate.covers(candidate_xy_m, targets)
        same_map = coverage_certificate.matches_map(map_context)
    except (TypeError, ValueError, AttributeError):
        covered, same_map = False, False
    if not covered or not same_map:
        return _unresolved(
            "Coverage certificate does not bind exact scorer map, candidate, and targets.",
            numerical_margin,
        )

    detections = [DetectedObject(r.key, "unknown", r.bearing_deg, r.range_m) for r in prepared]
    grouped = group_angular_detections(
        detections, config.circular_merge_tolerance_deg, report_type_labels=False
    )
    components = grouped.truth_groups
    by_key = {r.key: r for r in prepared}
    component_by_key = {
        key: component_index
        for component_index, component in enumerate(components)
        for key in component
    }
    known_uncertainties_by_component = tuple(
        tuple(
            sorted(
                {
                    float(uncertainty)
                    for key in component
                    if (uncertainty := by_key[key].uncertainty_m) is not None
                }
            )
        )
        for component in components
    )
    try:
        empty = build_likelihood_costs(measurements, [], [], config)
        baseline = float(empty.baseline_constant)
        nr = _report_count(measurements)
        clutter = (
            float(empty.report_clutter_costs[0])
            if nr
            else -log(config.expected_unmatched_reports / 360.0)
        )
    except NotImplementedError as exc:
        return _unresolved(f"Unsupported likelihood model: {exc}", numerical_margin)
    except (TypeError, ValueError) as exc:
        return _unresolved(f"Unsupported likelihood inputs: {exc}", numerical_margin)

    variant_bearings: list[float] = []
    variant_types: list[str | int | None] = []
    variant_sigmas: list[float] = []
    owner_columns: list[list[int]] = [[] for _ in prepared]
    for record_index, record in enumerate(prepared):
        component_index = component_by_key[record.key]
        possible_u = _possible_uncertainties(
            record, known_uncertainties_by_component[component_index]
        )
        for uncertainty in possible_u:
            owner_columns[record_index].append(len(variant_bearings))
            variant_bearings.append(record.bearing_deg)
            variant_types.append(record.object_type)
            variant_sigmas.append(degrees(atan2(uncertainty, record.range_m)))

    if variant_bearings and nr:
        try:
            batched = build_likelihood_costs(
                measurements,
                variant_bearings,
                variant_types,
                config,
                variant_sigmas,
            )
        except NotImplementedError as exc:
            return _unresolved(f"Unsupported likelihood model: {exc}", numerical_margin)
        except (TypeError, ValueError) as exc:
            return _unresolved(f"Unsupported likelihood inputs: {exc}", numerical_margin)
        columns = [
            tuple(
                float(min(batched.matched_costs[i, column] for column in member_columns))
                for i in range(nr)
            )
            for member_columns in owner_columns
        ]
    else:
        columns = [tuple() for _ in prepared]
    edges = tuple(tuple(columns[j][i] for j in range(len(prepared))) for i in range(nr))
    terms, sources = [], []
    for row in edges:
        edge = min(row, default=inf)
        terms.append(min(clutter, edge))
        sources.append("clutter" if clutter <= edge else "catalog_edge")
    raw = fsum((baseline, *terms))
    scale = 1 + abs(baseline) + fsum(abs(v) for v in terms)
    downward = numerical_margin * scale
    bound = nextafter(raw - downward, -inf)
    if not isfinite(bound):
        return _unresolved("Bound is outside the finite score domain.", numerical_margin)
    return PreLOSRowMinBoundResult(
        bound,
        True,
        None,
        raw,
        numerical_margin,
        downward,
        baseline,
        clutter,
        edges,
        tuple(terms),
        tuple(sources),
        tuple(tuple(g) for g in components),
        (
            "objective is bearing_set_map_v1",
            "records are the complete supported in-range set from exact prediction preparation",
            "typed coverage certificate binds candidate and every target segment",
            "circular pre-LOS components are recomputed at configured angular resolution",
            "each exact visible group is a subset of one pre-LOS component",
            "all possible component-max uncertainties are included per representative edge",
            "nonnegative unused-group costs are omitted",
            "report rows may reuse a catalog record in this relaxation",
        ),
    )


def _possible_uncertainties(rep, known: tuple[float, ...]) -> tuple[float, ...]:
    rep_u = rep.uncertainty_m
    if rep_u is None:
        return tuple(sorted({0.0, *known}))
    return tuple(value for value in known if value >= rep_u)


def _report_count(measurements) -> int:
    if isinstance(measurements, MeasurementSetV2):
        return len(measurements.measurements)
    values = np.asarray(measurements, dtype=float)
    if values.ndim != 1:
        raise ValueError("measurements must be a one-dimensional sequence.")
    return int(values.size)


def _unresolved(reason: str, margin: float) -> PreLOSRowMinBoundResult:
    return PreLOSRowMinBoundResult(
        None, False, reason, None, margin, None, None, None, (), (), (), (), ()
    )


__all__ = ["PreLOSRowMinBoundResult", "pre_los_catalog_row_min_bound"]

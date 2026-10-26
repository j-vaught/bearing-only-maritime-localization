"""Objective-aware lower bounds for one candidate's visible-group count.

This bound uses no bearing-region geometry. It needs only a certified lower
bound on the number of distinct groups that the candidate predicts visible.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from math import fsum, inf, isfinite, log, nextafter

import numpy as np

from maritime_localization_shared.contracts.inputs_v2 import MeasurementSetV2

from .config import SearchConfig
from .likelihood import build_likelihood_costs


@dataclass(frozen=True, slots=True)
class CandidateLowerBoundResult:
    """Supported objective floor or an explicit unresolved result."""

    bound: float | None
    supported: bool
    reason: str | None
    minimum_visible_group_count: int | None
    per_report_match_floors: tuple[float, ...]
    cardinality_bounds: tuple[float, ...]
    assumptions: tuple[str, ...]


def candidate_objective_lower_bound(
    measurements: MeasurementSetV2 | Sequence[float] | np.ndarray,
    minimum_visible_group_count: int | None,
    config: SearchConfig,
    *,
    visibility_count_certified: bool = False,
    variance_model_supported: bool = True,
    observation_types: Sequence[str | int | None] | None = None,
    observation_sigmas_deg: Sequence[float | None] | np.ndarray | None = None,
    numerical_margin: float = 1e-10,
) -> CandidateLowerBoundResult:
    """Lower-bound the full objective using only a certified visible-group minimum.

    The count K may be smaller than the final visible-group count. The bound
    therefore allows any number of matched reports up to the report count and
    minimizes the group-miss term over all possible group counts n >= K.
    """
    if not isinstance(config, SearchConfig):
        raise TypeError("config must be SearchConfig.")
    if not isfinite(numerical_margin) or numerical_margin < 0.0:
        raise ValueError("numerical_margin must be finite and nonnegative.")
    if config.objective_version != "bearing_set_map_v1":
        return _unresolved(
            "Only bearing_set_map_v1 has a certified objective floor.",
            minimum_visible_group_count,
        )
    if not visibility_count_certified:
        return _unresolved(
            "The supplied visible-group minimum is not certified.",
            minimum_visible_group_count,
        )
    if type(minimum_visible_group_count) is not int or minimum_visible_group_count < 0:
        return _unresolved(
            "A nonnegative integer minimum visible-group count K is required.",
            minimum_visible_group_count,
        )
    if not variance_model_supported:
        return _unresolved(
            "The candidate variance or uncertainty model is unsupported.",
            minimum_visible_group_count,
        )

    try:
        objective_reference = build_likelihood_costs(
            measurements,
            _report_bearings(measurements),
            [None] * len(_report_bearings(measurements)),
            config,
            np.zeros(len(_report_bearings(measurements))),
            observation_types=observation_types,
            observation_sigmas_deg=observation_sigmas_deg,
        )
    except NotImplementedError as exc:
        return _unresolved(
            f"Unsupported likelihood or variance model: {exc}",
            minimum_visible_group_count,
        )

    report_count = objective_reference.matched_costs.shape[0]
    clutter_cost = (
        float(objective_reference.report_clutter_costs[0])
        if report_count
        else _report_clutter_cost(config.expected_unmatched_reports)
    )
    group_miss_cost = (
        float(objective_reference.group_miss_costs[0])
        if len(objective_reference.group_miss_costs)
        else -log(1.0 - config.detection_probability)
    )
    match_floors = _global_report_match_floors(
        measurements,
        config,
        observation_types=observation_types,
        observation_sigmas_deg=observation_sigmas_deg,
    )
    if len(match_floors) != report_count or not np.isfinite(match_floors).all():
        return _unresolved(
            "The objective did not yield finite report-wise global match floors.",
            minimum_visible_group_count,
        )

    cardinality_scores: list[float] = []
    cumulative_matches = 0.0
    sorted_floors = sorted(match_floors)
    for cardinality in range(report_count + 1):
        if cardinality:
            cumulative_matches = fsum((cumulative_matches, sorted_floors[cardinality - 1]))
        unmatched_reports = report_count - cardinality
        if unmatched_reports == 0:
            report_term = 0.0
        elif np.isinf(clutter_cost):
            report_term = inf
        else:
            report_term = unmatched_reports * clutter_cost
        unmatched_groups = max(minimum_visible_group_count - cardinality, 0)
        group_term = unmatched_groups * group_miss_cost
        score = fsum(
            (
                float(objective_reference.baseline_constant),
                report_term,
                group_term,
                cumulative_matches,
            )
        )
        cardinality_scores.append(score)

    raw_bound = min(cardinality_scores)
    scale = 1.0 + abs(float(objective_reference.baseline_constant))
    if isfinite(clutter_cost):
        scale += report_count * abs(clutter_cost)
    scale += minimum_visible_group_count * abs(group_miss_cost)
    scale += fsum(abs(value) for value in match_floors)
    # The scaled downward slack absorbs floating summation and libm rounding.
    slack = numerical_margin * scale
    bound = nextafter(raw_bound - slack, -inf)
    if not isfinite(bound):
        return _unresolved(
            "The numerical lower bound is outside the finite score domain.",
            minimum_visible_group_count,
        )
    return CandidateLowerBoundResult(
        bound=bound,
        supported=True,
        reason=None,
        minimum_visible_group_count=minimum_visible_group_count,
        per_report_match_floors=tuple(float(value) for value in match_floors),
        cardinality_bounds=tuple(float(value) for value in cardinality_scores),
        assumptions=(
            "objective is bearing_set_map_v1",
            "visible-group count certificate is valid",
            "all predicted type factors are at most one",
            "predicted angular uncertainty is nonnegative",
            "unknown predicted-group bearings may attain zero residual",
            "predicted group count may exceed K",
            "all unmatched-group costs use the configured detection probability",
        ),
    )


def should_prune_candidate(
    result: CandidateLowerBoundResult,
    feasible_incumbent_score: float,
    *,
    comparison_margin: float = 1e-10,
) -> bool:
    """Prune only when a supported lower bound exceeds a feasible score plus margin."""
    if not isinstance(result, CandidateLowerBoundResult):
        raise TypeError("result must be CandidateLowerBoundResult.")
    if not isfinite(feasible_incumbent_score):
        raise ValueError("feasible_incumbent_score must be finite.")
    if not isfinite(comparison_margin) or comparison_margin < 0.0:
        raise ValueError("comparison_margin must be finite and nonnegative.")
    return bool(
        result.supported
        and result.bound is not None
        and result.bound > feasible_incumbent_score + comparison_margin
    )


def _global_report_match_floors(
    measurements: MeasurementSetV2 | Sequence[float] | np.ndarray,
    config: SearchConfig,
    *,
    observation_types: Sequence[str | int | None] | None,
    observation_sigmas_deg: Sequence[float | None] | np.ndarray | None,
) -> np.ndarray:
    """Use the canonical likelihood at zero residual with q_type fixed to one."""
    bearings = _report_bearings(measurements)
    type_neutral_config = replace(config, use_types=False)
    floors = np.empty(len(bearings), dtype=float)
    for index, bearing in enumerate(bearings):
        if isinstance(measurements, MeasurementSetV2):
            item = measurements.measurements[index]
            covariance = measurements.covariance_deg2
            one_covariance = None if covariance is None else ((float(covariance[index][index]),),)
            single_measurement = MeasurementSetV2(
                (item,),
                trial_id=measurements.trial_id,
                covariance_deg2=one_covariance,
                calibrated_common_bias_mean_deg=measurements.calibrated_common_bias_mean_deg,
                calibrated_common_bias_sigma_deg=measurements.calibrated_common_bias_sigma_deg,
            )
            costs = build_likelihood_costs(
                single_measurement,
                [float(bearing)],
                [None],
                type_neutral_config,
                [0.0],
            )
        else:
            one_types = None if observation_types is None else [observation_types[index]]
            one_sigmas = None if observation_sigmas_deg is None else [observation_sigmas_deg[index]]
            costs = build_likelihood_costs(
                [float(bearing)],
                [float(bearing)],
                [None],
                type_neutral_config,
                [0.0],
                observation_types=one_types,
                observation_sigmas_deg=one_sigmas,
            )
        floors[index] = float(costs.matched_costs[0, 0])
    return floors


def _report_bearings(
    measurements: MeasurementSetV2 | Sequence[float] | np.ndarray,
) -> np.ndarray:
    if isinstance(measurements, MeasurementSetV2):
        return np.asarray([item.bearing_deg for item in measurements.measurements], dtype=float)
    return np.asarray(measurements, dtype=float)


def _report_clutter_cost(expected_unmatched_reports: float) -> float:
    intensity = expected_unmatched_reports / 360.0
    return inf if intensity == 0.0 else -log(intensity)


def _unresolved(
    reason: str,
    minimum_visible_group_count: int | None,
) -> CandidateLowerBoundResult:
    return CandidateLowerBoundResult(
        bound=None,
        supported=False,
        reason=reason,
        minimum_visible_group_count=minimum_visible_group_count,
        per_report_match_floors=(),
        cardinality_bounds=(),
        assumptions=(),
    )


__all__ = [
    "CandidateLowerBoundResult",
    "candidate_objective_lower_bound",
    "should_prune_candidate",
]

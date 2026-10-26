"""Conservative region bounds for the fixed bearing-set objective.

Bearing arcs are full-circle enclosures. This remains valid for true-north
WGS84 bearings without treating projected-plane angles as geodesic bearings.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

import numpy as np

from maritime.schemas.inputs_v2 import MeasurementSetV2

from .assignment import assign_partial_cost_matrix
from .config import SearchConfig
from .likelihood import LikelihoodCostMatrix, PredictedType, build_likelihood_costs


@dataclass(frozen=True, slots=True)
class CircularBearingEnclosure:
    start_deg: float
    end_deg: float
    full_circle: bool
    reason: str


@dataclass(frozen=True, slots=True)
class BoundsResult:
    bound: float | None
    valid_scope: tuple[str, ...]
    reason: str | None
    row_min_bound: float | None
    assignment_bound: float | None


def rectangular_bearing_enclosures(
    bounds_xy_m: tuple[float, float, float, float],
    landmarks_xy_m: Sequence[tuple[float, float]],
) -> tuple[CircularBearingEnclosure, ...]:
    """Enclose each landmark bearing over a projected rectangle conservatively.

    The full circle is exact when a rectangle contains its landmark and safely
    encloses bearings otherwise. A tighter true-north geodesic arc is not
    derived by this implementation.
    """
    xmin, xmax, ymin, ymax = _validate_rectangle(bounds_xy_m)
    output = []
    for point in landmarks_xy_m:
        if len(point) != 2 or not all(isfinite(float(value)) for value in point):
            raise ValueError("Each landmark must contain two finite coordinates.")
        east, north = map(float, point)
        contains = xmin <= east <= xmax and ymin <= north <= ymax
        reason = (
            "rectangle contains landmark; bearing singularity requires full circle"
            if contains
            else "true-north geodesic arc not derived; conservative full circle used"
        )
        output.append(CircularBearingEnclosure(0.0, 360.0, True, reason))
    return tuple(output)


def full_circle_edge_lower_bounds(
    measurements: MeasurementSetV2 | Sequence[float] | np.ndarray,
    predicted_types: Sequence[PredictedType] | None,
    config: SearchConfig,
    predicted_uncertainty_deg: Sequence[float] | np.ndarray | None = None,
    *,
    observation_types: Sequence[str | int | None] | None = None,
    observation_sigmas_deg: Sequence[float | None] | np.ndarray | None = None,
) -> np.ndarray:
    """Compute per-edge objective minima over a full bearing circle.

    Each report is paired at zero residual with each group in turn. The
    wrapped-mixture likelihood has its minimum matched cost at zero residual,
    as does the legacy absolute-residual objective. Type and variance inputs
    remain pair-specific and fixed over the region.
    """
    if predicted_types is None:
        raise ValueError(
            "predicted_types must have one entry per group; use None for unknown types."
        )
    group_count = len(predicted_types)
    if isinstance(measurements, MeasurementSetV2):
        bearings = np.asarray([item.bearing_deg for item in measurements.measurements])
        # Validate covariance and common-bias support using the objective API.
        build_likelihood_costs(
            measurements,
            np.zeros(group_count),
            predicted_types,
            config,
            predicted_uncertainty_deg,
        )
    else:
        bearings = np.asarray(measurements, dtype=float)
        report_types = (
            list(observation_types) if observation_types is not None else [None] * len(bearings)
        )
        report_sigmas = (
            list(observation_sigmas_deg)
            if observation_sigmas_deg is not None
            else [None] * len(bearings)
        )
        # Run the canonical validator before constructing pairwise zero-residual rows.
        build_likelihood_costs(
            measurements,
            np.zeros(group_count),
            predicted_types,
            config,
            predicted_uncertainty_deg,
            observation_types=observation_types,
            observation_sigmas_deg=observation_sigmas_deg,
        )
    if predicted_uncertainty_deg is None:
        group_uncertainties = np.zeros(group_count)
    else:
        group_uncertainties = np.asarray(predicted_uncertainty_deg, dtype=float)
        if group_uncertainties.ndim == 0:
            group_uncertainties = np.full(group_count, float(group_uncertainties))
    output = np.empty((len(bearings), group_count), dtype=float)
    for report_index, bearing in enumerate(bearings):
        if isinstance(measurements, MeasurementSetV2):
            item = measurements.measurements[report_index]
            covariance = measurements.covariance_deg2
            one_covariance = (
                None if covariance is None else ((float(covariance[report_index][report_index]),),)
            )
            one_measurement = MeasurementSetV2(
                (item,),
                trial_id=measurements.trial_id,
                covariance_deg2=one_covariance,
                calibrated_common_bias_mean_deg=measurements.calibrated_common_bias_mean_deg,
                calibrated_common_bias_sigma_deg=measurements.calibrated_common_bias_sigma_deg,
            )
            row_costs = build_likelihood_costs(
                one_measurement,
                np.full(group_count, float(bearing)),
                predicted_types,
                config,
                group_uncertainties,
            )
        else:
            one_sigmas = None if observation_sigmas_deg is None else [report_sigmas[report_index]]
            one_types = None if observation_types is None else [report_types[report_index]]
            row_costs = build_likelihood_costs(
                np.asarray([bearing]),
                np.full(group_count, float(bearing)),
                predicted_types,
                config,
                group_uncertainties,
                observation_types=one_types,
                observation_sigmas_deg=one_sigmas,
            )
        output[report_index, :] = row_costs.matched_costs[0, :]
    return output


def lower_bound_from_edge_floors(
    costs: LikelihoodCostMatrix,
    matched_cost_lower_bounds: Sequence[Sequence[float]] | np.ndarray,
    *,
    fixed_visibility: bool = True,
    fixed_grouping: bool = True,
    fixed_variance: bool = True,
    numerical_margin: float = 1e-10,
) -> BoundsResult:
    """Bound the full assignment objective with row and injective relaxations.

    Edge floors must be no greater than each report/group matched cost anywhere
    in the region. The row relaxation independently chooses each report's
    cheapest edge or clutter alternative. The tighter assignment relaxation
    enforces one-to-one group use. Both retain baseline, report miss, and
    unused-group miss costs, including negative edge costs.
    """
    if not isinstance(costs, LikelihoodCostMatrix):
        raise TypeError("costs must be a LikelihoodCostMatrix.")
    if not isfinite(numerical_margin) or numerical_margin < 0.0:
        raise ValueError("numerical_margin must be finite and nonnegative.")
    if not (fixed_visibility and fixed_grouping and fixed_variance):
        reasons = []
        if not fixed_visibility:
            reasons.append("visibility can change within the region")
        if not fixed_grouping:
            reasons.append("angular grouping can change within the region")
        if not fixed_variance:
            reasons.append("bearing variance can change within the region")
        return BoundsResult(None, (), "; ".join(reasons), None, None)

    lower = np.asarray(matched_cost_lower_bounds, dtype=float)
    matched = np.asarray(costs.matched_costs, dtype=float)
    report_miss = np.asarray(costs.report_clutter_costs, dtype=float)
    group_miss = np.asarray(costs.group_miss_costs, dtype=float)
    if lower.shape != matched.shape:
        raise ValueError("matched_cost_lower_bounds must match the cost matrix.")
    if np.isnan(lower).any() or np.isneginf(lower).any():
        raise ValueError("Edge floors must be finite or positive infinity.")
    if np.isnan(matched).any() or np.isneginf(matched).any():
        raise ValueError("Matched costs must be finite or positive infinity.")
    if not np.isfinite(report_miss).all() or not np.isfinite(group_miss).all():
        raise ValueError("Miss costs must be finite.")
    baseline = float(costs.baseline_constant)
    if not isfinite(baseline):
        raise ValueError("Objective baseline must be finite.")
    if np.any(lower > matched):
        raise ValueError("An edge floor cannot exceed the supplied matched cost.")

    safe_lower = lower - np.where(np.isfinite(lower), numerical_margin, 0.0)
    base = baseline + float(np.sum(report_miss)) + float(np.sum(group_miss))
    row_value = base
    if len(report_miss):
        delta = safe_lower - report_miss[:, None] - group_miss[None, :]
        choices = (
            np.minimum(0.0, np.min(delta, axis=1)) if delta.shape[1] else np.zeros(len(report_miss))
        )
        row_value += float(np.sum(choices))
    exact = assign_partial_cost_matrix(
        safe_lower, report_miss, group_miss, baseline_constant=baseline
    )
    scale = (
        1.0
        + abs(base)
        + float(np.sum(np.abs(report_miss)))
        + float(np.sum(np.abs(group_miss)))
        + float(np.sum(np.abs(safe_lower[np.isfinite(safe_lower)])))
    )
    slack = numerical_margin * scale
    row_bound = float(row_value - slack)
    assignment_bound = float(exact.cost - slack)
    return BoundsResult(
        assignment_bound,
        (
            "fixed_visibility",
            "fixed_angular_grouping",
            "fixed_bearing_variance",
            "full_true_north_bearing_circle",
            "same_partial_assignment_objective",
        ),
        None,
        row_bound,
        assignment_bound,
    )


def rectangular_region_lower_bound(
    bounds_xy_m: tuple[float, float, float, float],
    landmarks_xy_m: Sequence[tuple[float, float]],
    costs: LikelihoodCostMatrix,
    matched_cost_lower_bounds: Sequence[Sequence[float]] | np.ndarray,
    *,
    fixed_visibility: bool = True,
    fixed_grouping: bool = True,
    fixed_variance: bool = True,
    numerical_margin: float = 1e-10,
) -> BoundsResult:
    """Compute a region bound from conservative circular bearing enclosures."""
    enclosures = rectangular_bearing_enclosures(bounds_xy_m, landmarks_xy_m)
    if len(enclosures) != costs.matched_costs.shape[1]:
        raise ValueError("Landmark count must match predicted group count.")
    return lower_bound_from_edge_floors(
        costs,
        matched_cost_lower_bounds,
        fixed_visibility=fixed_visibility,
        fixed_grouping=fixed_grouping,
        fixed_variance=fixed_variance,
        numerical_margin=numerical_margin,
    )


def _validate_rectangle(
    bounds_xy_m: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    if len(bounds_xy_m) != 4:
        raise ValueError("bounds_xy_m must contain xmin, xmax, ymin, ymax.")
    values = tuple(map(float, bounds_xy_m))
    if not all(map(isfinite, values)):
        raise ValueError("Rectangle coordinates must be finite.")
    xmin, xmax, ymin, ymax = values
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("Rectangle must have positive width and height.")
    return xmin, xmax, ymin, ymax


__all__ = [
    "BoundsResult",
    "CircularBearingEnclosure",
    "full_circle_edge_lower_bounds",
    "lower_bound_from_edge_floors",
    "rectangular_bearing_enclosures",
    "rectangular_region_lower_bound",
]

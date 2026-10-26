"""Continuous local refinement of separately seeded spatial modes.

Every trial is evaluated through CandidateScorer, which rebuilds visible
range-limited groups and solves the exact configured assignment objective.
This module makes no global-optimality claim.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import atan2, degrees, hypot, isfinite, sqrt
from time import perf_counter
from typing import Literal

import numpy as np

from maritime_localization_shared.contracts.inputs_v2 import CatalogV2, MeasurementSetV2
from maritime_localization_shared.contracts.localization import (
    Hypothesis,
    UncertaintyEllipse,
)
from maritime_localization_shared.geospatial.vector_map import VectorMap

from .config import SearchConfig
from .prediction import PredictedVisibleGroup
from .scoring import CandidateScorer

StopReason = Literal[
    "relative_improvement_below_1e-8",
    "displacement_below_0_01m",
    "iteration_limit",
    "unsupported_seed",
]


@dataclass(frozen=True, slots=True)
class RefinementHistory:
    """One accepted pose or one rejected neighborhood contraction."""

    iteration: int
    east_m: float
    north_m: float
    score: float
    step_m: float
    scored_trials: int


@dataclass(frozen=True, slots=True)
class RefinedMode:
    """Refinement details for one input mode, including its local uncertainty."""

    hypothesis: Hypothesis | None
    iterations: int
    stop_reason: StopReason
    improved_hypothesis: bool
    history: tuple[RefinementHistory, ...]
    uncertainty_rank: int | None
    uncertainty_singular: bool
    uncertainty_reason: str | None

    @property
    def estimated_95_radius_m(self) -> float | None:
        """Return the shared conditional 95% ellipse major radius when available."""
        if self.hypothesis is None or self.hypothesis.uncertainty is None:
            return None
        return self.hypothesis.uncertainty.major_radius_m


@dataclass(frozen=True, slots=True)
class RefinementResult:
    """Ranked independently refined modes and execution duration."""

    modes: tuple[RefinedMode, ...]
    elapsed_s: float
    objective_version: str

    @property
    def hypotheses(self) -> tuple[Hypothesis, ...]:
        return tuple(mode.hypothesis for mode in self.modes if mode.hypothesis is not None)


def _position(mode: object) -> tuple[float, float]:
    hypothesis = getattr(mode, "hypothesis", mode)
    if not isinstance(hypothesis, Hypothesis):
        raise TypeError("Each mode must be a Hypothesis or expose a Hypothesis field.")
    return hypothesis.east_m, hypothesis.north_m


def _inside_bounds(
    position: tuple[float, float], bounds: tuple[float, float, float, float]
) -> bool:
    east, north = position
    xmin, xmax, ymin, ymax = bounds
    return xmin <= east <= xmax and ymin <= north <= ymax


def _bearing_for_key(evaluation, key: str) -> tuple[float, PredictedVisibleGroup] | None:
    if evaluation.status != "scored" or evaluation.predicted is None:
        return None
    for group in evaluation.predicted.groups:
        if group.representative_key == key:
            return group.bearing_deg, group
    return None


def _conditional_uncertainty(
    scorer: CandidateScorer,
    hypothesis: Hypothesis,
    *,
    finite_difference_m: float = 0.5,
) -> tuple[UncertaintyEllipse | None, int, bool, str | None]:
    """Estimate pose covariance from bearing Jacobians with fixed assignments."""
    assignments = hypothesis.assignments
    if len(assignments) < 2:
        return None, 0, True, "fewer than two assigned bearings"
    center = (hypothesis.east_m, hypothesis.north_m)
    base = scorer.evaluate(center)
    if base.status != "scored":
        return None, 0, True, "refined pose is unsupported"
    rows: list[tuple[int, str, float]] = []
    for assignment in assignments:
        entry = _bearing_for_key(base, assignment.catalog_key)
        if entry is None:
            return None, 0, True, "an assigned visible group is unavailable"
        _bearing, group = entry
        measurement = scorer.measurements.measurements[assignment.report_index]
        report_sigma = (
            scorer.config.generated_sigma_deg
            if measurement.sigma_deg is None
            else measurement.sigma_deg
        )
        group_sigma = degrees(atan2(group.catalog_uncertainty_m or 0.0, group.range_m))
        sigma = max(scorer.config.sigma_floor_deg, hypot(report_sigma, group_sigma))
        rows.append((assignment.report_index, assignment.catalog_key, sigma))

    jacobian = np.empty((len(rows), 2), dtype=np.float64)
    for axis in range(2):
        offsets: list[tuple[float, float]] = []
        for sign in (1.0, -1.0):
            point = list(center)
            point[axis] += sign * finite_difference_m
            offsets.append((point[0], point[1]))
        sampled: list[dict[str, float] | None] = []
        for point in offsets:
            evaluation = scorer.evaluate(point)
            bearing_map: dict[str, float] = {}
            if evaluation.status == "scored" and evaluation.predicted is not None:
                bearing_map = {
                    group.representative_key: group.bearing_deg
                    for group in evaluation.predicted.groups
                }
            sampled.append(bearing_map)
        plus, minus = sampled
        for row_index, (_report_index, key, _sigma) in enumerate(rows):
            base_bearing = _bearing_for_key(base, key)
            assert base_bearing is not None
            base_value = base_bearing[0]
            if plus is not None and minus is not None and key in plus and key in minus:
                delta = (plus[key] - minus[key] + 180.0) % 360.0 - 180.0
                jacobian[row_index, axis] = delta / (2.0 * finite_difference_m)
            elif plus is not None and key in plus:
                delta = (plus[key] - base_value + 180.0) % 360.0 - 180.0
                jacobian[row_index, axis] = delta / finite_difference_m
            elif minus is not None and key in minus:
                delta = (base_value - minus[key] + 180.0) % 360.0 - 180.0
                jacobian[row_index, axis] = delta / finite_difference_m
            else:
                return None, 0, True, "bearing derivative is unresolved at map boundary"

    scales = np.asarray([1.0 / sigma for _report, _key, sigma in rows], dtype=np.float64)
    weighted = jacobian * scales[:, None]
    information = weighted.T @ weighted
    eigenvalues, eigenvectors = np.linalg.eigh(information)
    threshold = max(float(eigenvalues[-1]) * 1e-12, 1e-18)
    rank = int(np.count_nonzero(eigenvalues > threshold))
    if rank < 2:
        return None, rank, True, "bearing geometry has rank below two"
    covariance = np.linalg.inv(information)
    covariance_values, covariance_vectors = np.linalg.eigh(covariance)
    if not np.isfinite(covariance_values).all() or np.any(covariance_values <= 0.0):
        return None, rank, True, "conditional covariance is not positive definite"
    confidence_scale = sqrt(5.991464547107979)
    order = np.argsort(covariance_values)[::-1]
    major_index, minor_index = int(order[0]), int(order[1])
    major_vector = covariance_vectors[:, major_index]
    ellipse = UncertaintyEllipse(
        major_radius_m=confidence_scale * sqrt(float(covariance_values[major_index])),
        minor_radius_m=confidence_scale * sqrt(float(covariance_values[minor_index])),
        orientation_deg=degrees(atan2(float(major_vector[1]), float(major_vector[0]))),
        confidence_level=0.95,
    )
    return ellipse, rank, False, None


def refine_modes(
    measurements: MeasurementSetV2,
    catalog: CatalogV2,
    vector_map: VectorMap,
    config: SearchConfig,
    modes: Iterable[object],
    *,
    initial_step_m: float = 100.0,
    max_iterations: int = 50,
) -> RefinementResult:
    """Refine each supplied spatial mode independently using exact scores.

    ``modes`` may be A18 ``SpatialMode`` records or hypotheses. Neighbors are
    evaluated with CandidateScorer, so every accepted pose has a fresh range,
    visibility, circular merge, and full optional assignment evaluation.
    Equal-cost trials never replace the current pose.
    """
    if not isfinite(initial_step_m) or initial_step_m <= 0.0:
        raise ValueError("initial_step_m must be finite and positive.")
    if type(max_iterations) is not int or not 1 <= max_iterations <= 50:
        raise ValueError("max_iterations must be an integer from one through fifty.")
    if config.bounds_xy is None:
        raise ValueError("Continuous refinement requires finite config.bounds_xy.")
    scorer = CandidateScorer(measurements, catalog, vector_map, config)
    started = perf_counter()
    results: list[RefinedMode] = []
    xmin, xmax, ymin, ymax = config.bounds_xy
    directions = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))

    for mode in modes:
        seed = _position(mode)
        if not _inside_bounds(seed, config.bounds_xy):
            raise ValueError("Every input mode must lie within config.bounds_xy.")
        start = scorer.evaluate(seed)
        if start.status != "scored":
            results.append(
                RefinedMode(None, 0, "unsupported_seed", False, (), None, True, start.status)
            )
            continue
        assert start.hypothesis is not None
        current = start.hypothesis
        original_score = current.score
        improved = False
        step = initial_step_m
        history: list[RefinementHistory] = [
            RefinementHistory(0, current.east_m, current.north_m, current.score, step, 1)
        ]
        stop: StopReason = "iteration_limit"
        iterations = 0

        for iteration in range(1, max_iterations + 1):
            iterations = iteration
            if step < 0.01:
                stop = "displacement_below_0_01m"
                break
            trials = 0
            best = current
            best_displacement = 0.0
            for de, dn in directions:
                east = current.east_m + de * step
                north = current.north_m + dn * step
                if not (xmin <= east <= xmax and ymin <= north <= ymax):
                    continue
                evaluation = scorer.evaluate((east, north))
                trials += 1
                if (
                    evaluation.status == "scored"
                    and evaluation.hypothesis is not None
                    and evaluation.hypothesis.score < best.score
                ):
                    best = evaluation.hypothesis
                    best_displacement = hypot(east - current.east_m, north - current.north_m)
            if best.score < current.score:
                relative_improvement = (current.score - best.score) / max(abs(current.score), 1.0)
                current = best
                improved = True
                history.append(
                    RefinementHistory(
                        iteration, current.east_m, current.north_m, current.score, step, trials
                    )
                )
                if relative_improvement < 1e-8:
                    stop = "relative_improvement_below_1e-8"
                    break
                if best_displacement < 0.01:
                    stop = "displacement_below_0_01m"
                    break
            else:
                step *= 0.5
                history.append(
                    RefinementHistory(
                        iteration, current.east_m, current.north_m, current.score, step, trials
                    )
                )
                if step < 0.01:
                    stop = "displacement_below_0_01m"
                    break
        ellipse, rank, singular, reason = _conditional_uncertainty(scorer, current)
        final = Hypothesis(
            east_m=current.east_m,
            north_m=current.north_m,
            score=current.score,
            assignments=current.assignments,
            uncertainty=ellipse,
            score_components=current.score_components,
        )
        results.append(
            RefinedMode(
                final,
                iterations,
                stop,
                improved and current.score < original_score,
                tuple(history),
                rank,
                singular,
                reason,
            )
        )

    results.sort(
        key=lambda item: (
            float("inf") if item.hypothesis is None else item.hypothesis.score,
            float("inf") if item.hypothesis is None else item.hypothesis.east_m,
            float("inf") if item.hypothesis is None else item.hypothesis.north_m,
        )
    )
    return RefinementResult(tuple(results), perf_counter() - started, config.objective_version)


__all__ = [
    "RefinedMode",
    "RefinementHistory",
    "RefinementResult",
    "refine_modes",
]

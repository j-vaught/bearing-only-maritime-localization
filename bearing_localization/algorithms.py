"""Baseline search algorithms for global-bearing localization with unknown identities.

The module deliberately keeps the association model explicit.  A detection is a
``(type, bearing_from_north_deg)`` pair; AIS candidates are ``(x, y, type)``
records.  Detections may be unmatched (objects absent from AIS), while AIS
objects with no detection are simply unused.  This is a baseline for simulation
benchmarks, not a production tracker.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import numpy as np
from scipy.optimize import least_squares, linear_sum_assignment


@dataclass(frozen=True)
class SearchResult:
    east_m: float
    north_m: float
    score_sigma: float
    matches: tuple[tuple[int, int], ...]
    elapsed_s: float
    evaluations: int


def wrap_deg(angle: np.ndarray | float) -> np.ndarray | float:
    """Wrap angle(s) to [-180, 180)."""
    return (np.asarray(angle) + 180.0) % 360.0 - 180.0


def associate(
    position: np.ndarray,
    observations: list[tuple[str | None, float]],
    ais_xy: np.ndarray,
    ais_types: list[str | None],
    sigma_deg: float = 0.5,
    unmatched_penalty_deg: float = 12.0,
    use_types: bool = True,
    visibility_radius_m: float = float("inf"),
    penalize_missing_ais: bool = False,
) -> tuple[float, tuple[tuple[int, int], ...]]:
    """Return minimum assignment cost and matched detection/AIS indices.

    The Hungarian assignment includes one private dummy column per detection,
    allowing observations of non-AIS objects.  Costs are absolute wrapped
    bearing errors, normalized by ``sigma_deg`` and with a configurable dummy
    penalty. Bearings and heading are degrees clockwise from north. The visibility
    radius gates impossible AIS matches; detections may remain unmatched. When the
    complete sensor report is available, ``penalize_missing_ais`` also charges for
    AIS objects predicted in range but absent from the report.
    """
    if sigma_deg <= 0 or unmatched_penalty_deg <= 0 or visibility_radius_m <= 0:
        raise ValueError("sigma, unmatched penalty, and visibility radius must be positive.")
    xy = np.asarray(ais_xy, dtype=float).reshape((-1, 2))
    if len(ais_types) != len(xy):
        raise ValueError("ais_types must have one entry per AIS coordinate.")
    if xy.size == 0:
        return float(len(observations) * unmatched_penalty_deg / sigma_deg), ()
    distance = np.linalg.norm(xy - np.asarray(position, dtype=float)[None, :], axis=1)
    candidate_indices = np.flatnonzero(distance <= visibility_radius_m)
    if not len(candidate_indices):
        return float(len(observations) * unmatched_penalty_deg / sigma_deg), ()
    if not observations:
        missing = len(candidate_indices) if penalize_missing_ais else 0
        return float(missing * unmatched_penalty_deg / sigma_deg), ()
    xy = xy[candidate_indices]
    candidate_types = [ais_types[i] for i in candidate_indices]
    obs_b = np.asarray([o[1] for o in observations], dtype=float)
    abs_b = np.rad2deg(np.arctan2(xy[:, 0] - position[0], xy[:, 1] - position[1]))
    # Sensor bearings are globally referenced to north, so ship heading is not
    # an unknown in the localization problem.
    predicted = np.asarray(wrap_deg(abs_b))
    unmatched = unmatched_penalty_deg / sigma_deg

    def solve_group(
        observation_indices: np.ndarray, candidate_local_indices: np.ndarray
    ) -> tuple[float, list[tuple[int, int]]]:
        costs = (
            np.abs(
                wrap_deg(
                    predicted[candidate_local_indices][None, :] - obs_b[observation_indices, None]
                )
            )
            / sigma_deg
        )
        if not penalize_missing_ais:
            dummy = np.full((len(observation_indices), len(observation_indices)), unmatched)
            matrix = np.concatenate((costs, dummy), axis=1)
            rows, cols = linear_sum_assignment(matrix)
            pairs = [
                (
                    int(observation_indices[r]),
                    int(candidate_indices[candidate_local_indices[c]]),
                )
                for r, c in zip(rows, cols)
                if c < len(candidate_local_indices) and costs[r, c] < unmatched
            ]
        else:
            detection_dummy = np.full(
                (len(observation_indices), len(observation_indices)), unmatched
            )
            ais_dummy = np.full(
                (len(candidate_local_indices), len(candidate_local_indices)), unmatched
            )
            fill = np.zeros((len(candidate_local_indices), len(observation_indices)))
            matrix = np.block([[costs, detection_dummy], [ais_dummy, fill]])
            rows, cols = linear_sum_assignment(matrix)
            pairs = [
                (
                    int(observation_indices[r]),
                    int(candidate_indices[candidate_local_indices[c]]),
                )
                for r, c in zip(rows, cols)
                if r < len(observation_indices)
                and c < len(candidate_local_indices)
                and costs[r, c] < 2.0 * unmatched
            ]
        return float(matrix[rows, cols].sum()), pairs

    if use_types and all(kind is not None for kind, _ in observations):
        # Independent type groups eliminate impossible pairs before the
        # cubic-cost assignment, so labels can reduce search time as well as
        # ambiguity. Unobserved AIS types count as missing only in full-report mode.
        total_cost = 0.0
        all_matches: list[tuple[int, int]] = []
        observed_kinds = {kind for kind, _ in observations}
        for kind in observed_kinds:
            observation_indices = np.asarray(
                [i for i, (observed_kind, _) in enumerate(observations) if observed_kind == kind],
                dtype=int,
            )
            candidate_local_indices = np.asarray(
                [i for i, candidate_kind in enumerate(candidate_types) if candidate_kind == kind],
                dtype=int,
            )
            if not len(candidate_local_indices) and penalize_missing_ais:
                total_cost += len(observation_indices) * unmatched
            else:
                cost, matches = solve_group(observation_indices, candidate_local_indices)
                total_cost += cost
                all_matches.extend(matches)
        if penalize_missing_ais:
            missing_type_count = sum(kind not in observed_kinds for kind in candidate_types)
            total_cost += missing_type_count * unmatched
        return total_cost, tuple(all_matches)

    if use_types:
        all_costs = np.abs(wrap_deg(predicted[None, :] - obs_b[:, None])) / sigma_deg
        for i, (kind, _) in enumerate(observations):
            if kind is not None:
                all_costs[i, np.asarray([t != kind for t in candidate_types])] = 2.0 * unmatched
    score, matches = solve_group(np.arange(len(observations)), np.arange(len(candidate_indices)))
    return score, tuple(matches)


def grid_search(
    observations: list[tuple[str | None, float]],
    ais_xy: np.ndarray,
    ais_types: list[str | None],
    bounds_xy: tuple[float, float, float, float],
    sigma_deg: float = 0.5,
    grid_spacing_m: float = 250.0,
    use_types: bool = True,
    unmatched_penalty_deg: float = 12.0,
    refine: bool = True,
    visibility_radius_m: float = float("inf"),
    penalize_missing_ais: bool = False,
    refine_starts: int = 16,
) -> SearchResult:
    """Exhaustively search a position grid, then optionally refine.

    ``bounds_xy`` is ``(xmin, xmax, ymin, ymax)``.  The grid is intentionally
    simple and deterministic, making runtime and parallelization easy to
    benchmark.  Bearings are referenced to north, so only position is searched.
    """
    started = perf_counter()
    if sigma_deg <= 0 or grid_spacing_m <= 0:
        raise ValueError("sigma and grid spacing must be positive.")
    xmin, xmax, ymin, ymax = bounds_xy
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("Search bounds must have positive width and height.")
    xs = np.arange(xmin, xmax + grid_spacing_m * 0.5, grid_spacing_m)
    ys = np.arange(ymin, ymax + grid_spacing_m * 0.5, grid_spacing_m)
    if refine_starts < 1:
        raise ValueError("refine_starts must be positive.")
    candidates: list[tuple[float, np.ndarray, float, tuple[tuple[int, int], ...]]] = []
    best: tuple[float, np.ndarray, float, tuple[tuple[int, int], ...]] | None = None
    evaluations = 0
    coarse_penalty = max(unmatched_penalty_deg, 45.0) if refine else unmatched_penalty_deg
    for x in xs:
        for y in ys:
            score, matches = associate(
                np.array([x, y]),
                observations,
                ais_xy,
                ais_types,
                sigma_deg,
                coarse_penalty,
                use_types,
                visibility_radius_m,
                penalize_missing_ais and not refine,
            )
            evaluations += 1
            candidate = (score, np.array([x, y], dtype=float), 0.0, matches)
            candidates.append(candidate)
            if best is None or score < best[0]:
                best = candidate
    assert best is not None
    if refine:
        refined_best: tuple[float, np.ndarray, float, tuple[tuple[int, int], ...]] | None = None
        for _, coarse_pos, coarse_heading, coarse_matches in sorted(
            candidates, key=lambda item: item[0]
        )[:refine_starts]:
            pos, heading = coarse_pos, coarse_heading
            if coarse_matches:

                def residual(v: np.ndarray) -> np.ndarray:
                    vals = []
                    for observation_index, ais_index in coarse_matches:
                        dx, dy = np.asarray(ais_xy)[ais_index] - v[:2]
                        predicted = np.rad2deg(np.arctan2(dx, dy))
                        vals.append(
                            float(wrap_deg(predicted - observations[observation_index][1]))
                            / sigma_deg
                        )
                    return np.asarray(vals)

                fit = least_squares(residual, pos.copy(), max_nfev=100)
                pos = fit.x
            score, matches = associate(
                pos,
                observations,
                ais_xy,
                ais_types,
                sigma_deg,
                unmatched_penalty_deg,
                use_types,
                visibility_radius_m,
                penalize_missing_ais,
            )
            refined = (score, pos, heading, matches)
            if refined_best is None or score < refined_best[0]:
                refined_best = refined
        assert refined_best is not None
        score, pos, heading, matches = refined_best
    else:
        score, pos, heading, matches = best
    return SearchResult(
        float(pos[0]), float(pos[1]), float(score), matches, perf_counter() - started, evaluations
    )


__all__ = ["SearchResult", "associate", "grid_search", "wrap_deg"]

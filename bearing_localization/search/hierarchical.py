"""Coarse-to-fine grid search for global-bearing localization.

The search scores a sparse grid over the full region, retains a configurable
beam of promising water-valid candidates, and successively samples finer grids
around them. It uses the shared baseline association cost so its measurements
and matching semantics stay comparable with :mod:`bearing_localization.algorithms`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from time import perf_counter
from typing import Literal

import numpy as np

from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime_localization_shared.geospatial.spatial_index import SpatialIndex2D

from ..algorithms import SearchResult, associate
from .scoring import CandidateScorer, CandidateSearchRun, score_candidate_positions

WaterPredicate = Callable[[float, float], bool]
LineOfSightPredicate = Callable[[tuple[float, float], tuple[float, float]], bool]


def hierarchical_search(
    observations: Sequence[tuple[str | None, float]],
    ais_xy: np.ndarray,
    ais_types: Sequence[str | None],
    bounds_xy: tuple[float, float, float, float],
    sigma_deg: float | Sequence[float] | np.ndarray = 0.5,
    coarse_spacing_m: float = 5_000.0,
    refinement_levels: int = 4,
    refinement_factor: float = 2.0,
    beam_width: int = 8,
    use_types: bool = True,
    unmatched_penalty_deg: float = 12.0,
    visibility_radius_m: float = float("inf"),
    penalize_missing_ais: bool = False,
    angular_resolution_deg: float | None = None,
    water_predicate: WaterPredicate | None = None,
    line_of_sight_predicate: LineOfSightPredicate | None = None,
) -> SearchResult:
    """Estimate position with a bounded coarse-to-fine search.

    ``bounds_xy`` is ``(xmin, xmax, ymin, ymax)`` in projected metres.
    ``water_predicate`` receives ``(east_m, north_m)`` and returns whether a
    candidate is water. It is optional so callers without a water mask can use
    the same search interface.

    ``line_of_sight_predicate`` receives candidate observer and AIS target
    ``(east_m, north_m)`` pairs. When supplied, AIS targets outside the range
    limit or behind land are removed before association at each candidate.

    ``coarse_spacing_m`` sets the initial lattice spacing. Each refinement
    level divides the spacing by ``refinement_factor`` and evaluates a 3-by-3
    neighborhood around each retained candidate. Duplicate coordinates are
    evaluated once. ``beam_width`` controls the number of candidates retained
    between levels, trading compute for resilience to local score minima.
    """
    started = perf_counter()
    if np.any(np.asarray(sigma_deg) <= 0) or unmatched_penalty_deg <= 0 or visibility_radius_m <= 0:
        raise ValueError("sigma, unmatched penalty, and visibility radius must be positive.")
    if coarse_spacing_m <= 0 or not np.isfinite(coarse_spacing_m):
        raise ValueError("coarse_spacing_m must be finite and positive.")
    if refinement_levels < 0:
        raise ValueError("refinement_levels must be nonnegative.")
    if refinement_factor <= 1 or not np.isfinite(refinement_factor):
        raise ValueError("refinement_factor must be finite and greater than one.")
    if beam_width < 1:
        raise ValueError("beam_width must be positive.")
    if angular_resolution_deg is not None and (
        not np.isfinite(angular_resolution_deg) or not 0.0 <= angular_resolution_deg < 180.0
    ):
        raise ValueError("angular_resolution_deg must be finite and in [0, 180).")

    xmin, xmax, ymin, ymax = bounds_xy
    if not all(np.isfinite(bounds_xy)) or xmin >= xmax or ymin >= ymax:
        raise ValueError("Search bounds must be finite and have positive width and height.")
    observation_list = list(observations)
    ais_type_list = list(ais_types)
    xy = np.asarray(ais_xy, dtype=float).reshape((-1, 2))
    if len(ais_type_list) != len(xy):
        raise ValueError("ais_types must have one entry per AIS coordinate.")

    spatial_index = (
        SpatialIndex2D(
            [(float(point[0]), float(point[1])) for point in xy],
            cell_size_m=max(1_000.0, visibility_radius_m),
        )
        if np.isfinite(visibility_radius_m) and len(xy)
        else None
    )

    # Candidate -> score and assignment. Rounding suppresses floating point
    # duplicates when neighboring beam cells generate the same point.
    scored: dict[tuple[float, float], tuple[float, tuple[tuple[int, int], ...]]] = {}
    evaluations = 0

    def evaluate(points: set[tuple[float, float]]) -> None:
        nonlocal evaluations
        for x, y in sorted(points):
            key = (round(x, 9), round(y, 9))
            if key in scored:
                continue
            if water_predicate is not None and not water_predicate(x, y):
                continue
            candidate_indices = np.arange(len(xy))
            if spatial_index is not None:
                candidate_indices = np.asarray(
                    spatial_index.query_radius(x, y, visibility_radius_m), dtype=int
                )
            if line_of_sight_predicate is not None and len(candidate_indices):
                observer_xy = (x, y)
                candidate_indices = np.asarray(
                    [
                        index
                        for index in candidate_indices
                        if line_of_sight_predicate(
                            observer_xy,
                            (float(xy[index, 0]), float(xy[index, 1])),
                        )
                    ],
                    dtype=int,
                )
            candidate_xy = xy[candidate_indices]
            candidate_types = [ais_type_list[int(index)] for index in candidate_indices]
            candidate_observations = observation_list
            if (
                penalize_missing_ais
                and angular_resolution_deg is not None
                and len(candidate_indices)
            ):
                delta = candidate_xy - np.asarray([x, y])
                ranges = np.hypot(delta[:, 0], delta[:, 1])
                bearings = np.degrees(np.arctan2(delta[:, 0], delta[:, 1]))
                expected = [
                    DetectedObject(
                        object_id=str(int(index)),
                        object_type=candidate_types[local_index] or "unknown",
                        bearing_deg=float(bearings[local_index]),
                        range_m=float(ranges[local_index]),
                    )
                    for local_index, index in enumerate(candidate_indices)
                ]
                grouped = group_angular_detections(expected, angular_resolution_deg)
                index_to_local = {
                    int(index): local for local, index in enumerate(candidate_indices)
                }
                representative_local = [
                    min(
                        (index_to_local[int(member)] for member in group),
                        key=lambda local: (float(ranges[local]), int(candidate_indices[local])),
                    )
                    for group in grouped.truth_groups
                ]
                candidate_indices = candidate_indices[representative_local]
                candidate_xy = xy[candidate_indices]
                candidate_types = [ais_type_list[int(index)] for index in candidate_indices]
            score, matches = associate(
                np.asarray([x, y]),
                candidate_observations,
                candidate_xy,
                candidate_types,
                sigma_deg,
                unmatched_penalty_deg,
                use_types,
                float("inf"),
                penalize_missing_ais,
            )
            matches = tuple(
                (observation_index, int(candidate_indices[local_ais_index]))
                for observation_index, local_ais_index in matches
            )
            scored[key] = (score, matches)
            evaluations += 1

    def lattice(step: float) -> set[tuple[float, float]]:
        xs = np.arange(xmin, xmax + step * 0.5, step)
        ys = np.arange(ymin, ymax + step * 0.5, step)
        return {
            (float(np.clip(x, xmin, xmax)), float(np.clip(y, ymin, ymax))) for x in xs for y in ys
        }

    evaluate(lattice(coarse_spacing_m))
    if not scored:
        raise ValueError("No water-valid search candidates fall within the search bounds.")

    step = coarse_spacing_m
    for _ in range(refinement_levels):
        step /= refinement_factor
        leader_cell_m = max(coarse_spacing_m * 4.0, 50_000.0)
        leaders = _select_diverse_leaders(
            scored,
            beam_width,
            xmin=xmin,
            ymin=ymin,
            cell_size_m=leader_cell_m,
        )
        next_points: set[tuple[float, float]] = set()
        for (x, y), _ in leaders:
            for dx in (-step, 0.0, step):
                for dy in (-step, 0.0, step):
                    candidate_x, candidate_y = x + dx, y + dy
                    if xmin <= candidate_x <= xmax and ymin <= candidate_y <= ymax:
                        next_points.add((candidate_x, candidate_y))
        evaluate(next_points)

    (best_x, best_y), (best_score, best_matches) = min(scored.items(), key=lambda item: item[1][0])
    return SearchResult(
        east_m=best_x,
        north_m=best_y,
        score_sigma=float(best_score),
        matches=best_matches,
        elapsed_s=perf_counter() - started,
        evaluations=evaluations,
    )


def _select_diverse_leaders(
    scored: dict[tuple[float, float], tuple[float, tuple[tuple[int, int], ...]]],
    beam_width: int,
    *,
    xmin: float,
    ymin: float,
    cell_size_m: float,
) -> list[tuple[tuple[float, float], tuple[float, tuple[tuple[int, int], ...]]]]:
    """Retain low-cost seeds across the map before filling remaining beam slots."""
    ordered = sorted(scored.items(), key=lambda item: (item[1][0], item[0]))
    global_count = max(1, (beam_width * 3) // 4)
    leaders = ordered[:global_count]
    selected = {item[0] for item in leaders}
    occupied = {
        (int((x - xmin) // cell_size_m), int((y - ymin) // cell_size_m)) for (x, y), _ in leaders
    }
    for item in ordered[global_count:]:
        x, y = item[0]
        cell = (int((x - xmin) // cell_size_m), int((y - ymin) // cell_size_m))
        if item[0] in selected or cell in occupied:
            continue
        leaders.append(item)
        selected.add(item[0])
        occupied.add(cell)
        if len(leaders) == beam_width:
            return leaders
    if len(leaders) < beam_width:
        remaining = [item for item in ordered if item[0] not in selected]
        leaders.extend(remaining[: beam_width - len(leaders)])
    return leaders


def run_hierarchical_search_v2(
    scorer: CandidateScorer,
    candidate_positions: Iterable[tuple[float, float]],
    *,
    search_status: Literal["completed", "budget_exhausted"] = "completed",
    budget_limit: int | None = None,
    budget_used: int | None = None,
) -> CandidateSearchRun:
    """Dispatch caller-generated version-two candidates through the common scorer.

    This adapter never converts a finite candidate minimum into a global
    optimality certificate or an accepted fix. Candidate generation remains
    owned by the associated grid, hierarchical, or randomized driver.
    """
    return score_candidate_positions(
        scorer,
        candidate_positions,
        search_status=search_status,
        budget_limit=budget_limit,
        budget_used=budget_used,
    )


__all__ = [
    "LineOfSightPredicate",
    "WaterPredicate",
    "hierarchical_search",
    "run_hierarchical_search_v2",
]

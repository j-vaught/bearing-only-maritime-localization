"""Coarse-to-fine grid search for global-bearing localization.

The search scores a sparse grid over the full region, retains a configurable
beam of promising water-valid candidates, and successively samples finer grids
around them. It uses the shared baseline association cost so its measurements
and matching semantics stay comparable with :mod:`bearing_localization.algorithms`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from time import perf_counter

import numpy as np

from maritime_localization_shared.geometry.angular_grouping import (
    DetectedObject,
    group_angular_detections,
)
from maritime_localization_shared.geospatial.spatial_index import SpatialIndex2D

from ..algorithms import SearchResult, associate
from .candidate_order import order_candidate_positions
from .scoring import (
    CandidateScorer,
    CandidateSearchAccumulator,
    CandidateSearchRun,
    score_candidate_positions,
)

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
    *,
    coarse_spacing_m: float = 5_000.0,
    refinement_levels: int = 4,
    refinement_factor: float = 2.0,
    beam_width: int = 8,
    candidate_positions: Iterable[tuple[float, float]] | None = None,
) -> CandidateSearchRun:
    """Run a coarse-to-fine V2 search over the complete declared bounds.

    Spatially diverse scored leaders seed each refinement level.  The remaining
    candidate budget is allocated with ceil(remaining/stages_left) at each
    stage, which reserves work for refinement instead of allowing the initial
    coarse prefix to consume the full budget.  Candidate ordering is
    deterministic and status-aware; it does not remove candidates or weaken
    acceptance criteria.  Completion is heuristic and carries no global
    certificate.  An explicit position iterable provides fixed-candidate
    parity mode.
    """
    if candidate_positions is not None:
        return score_candidate_positions(
            scorer,
            candidate_positions,
            candidate_limit=scorer.config.candidate_budget,
        )
    bounds = scorer.config.bounds_xy
    if bounds is None:
        raise ValueError("V2 hierarchical search requires config.bounds_xy.")
    if not np.isfinite(coarse_spacing_m) or coarse_spacing_m <= 0.0:
        raise ValueError("coarse_spacing_m must be finite and positive.")
    if refinement_levels < 0:
        raise ValueError("refinement_levels must be nonnegative.")
    if not np.isfinite(refinement_factor) or refinement_factor <= 1.0:
        raise ValueError("refinement_factor must be finite and greater than one.")
    if beam_width < 1:
        raise ValueError("beam_width must be positive.")

    xmin, xmax, ymin, ymax = bounds
    budget = scorer.config.candidate_budget
    accumulator = CandidateSearchAccumulator(scorer)
    budget_exhausted = False
    any_stage_truncated = False
    pending_truncated: list[tuple[float, float]] = []
    pending_keys: set[tuple[float, float]] = set()
    total_stages = refinement_levels + 1

    def axis(low: float, high: float, step: float) -> Iterable[float]:
        index = 0
        last = low
        while True:
            value = low + index * step
            if value > high + 1e-9:
                break
            last = min(value, high)
            yield last
            index += 1
        if high - last > 1e-9:
            yield high

    def lattice(step: float) -> Iterable[tuple[float, float]]:
        for east_m in axis(xmin, xmax, step):
            for north_m in axis(ymin, ymax, step):
                yield east_m, north_m

    def evaluate_stage(positions: Iterable[tuple[float, float]], quota: int) -> None:
        nonlocal any_stage_truncated, budget_exhausted
        ordered = order_candidate_positions(
            positions,
            water_status=scorer.vector_map.point_water_status,
            bounds=bounds,
        )
        used = 0
        for position_index, position in enumerate(ordered):
            if not accumulator.is_new(position):
                continue
            if accumulator.candidate_count >= budget:
                budget_exhausted = True
                return
            if used >= quota:
                any_stage_truncated = True
                for pending in ordered[position_index:]:
                    if accumulator.is_new(pending):
                        key = accumulator.position_key(pending)
                        if key not in pending_keys:
                            pending_keys.add(key)
                            pending_truncated.append(pending)
                return
            accumulator.add(position)
            used += 1

    for stage_index in range(total_stages):
        if budget_exhausted:
            break
        remaining_budget = budget - accumulator.candidate_count
        if remaining_budget <= 0:
            remaining_budget = 1
        stages_left = total_stages - stage_index
        quota = max(1, (remaining_budget + stages_left - 1) // stages_left)
        if stage_index == 0:
            evaluate_stage(lattice(coarse_spacing_m), quota)
            continue

        step = coarse_spacing_m / (refinement_factor**stage_index)
        cell_size = max(coarse_spacing_m * 4.0, 50_000.0)
        ranked = sorted(
            accumulator.scored_by_position.items(),
            key=lambda item: (item[1].score, item[0]),
        )
        leaders: list[tuple[float, float]] = []
        occupied: set[tuple[int, int]] = set()
        for (east_m, north_m), _hypothesis in ranked:
            cell = (
                int(np.floor((east_m - xmin) / cell_size)),
                int(np.floor((north_m - ymin) / cell_size)),
            )
            if cell in occupied:
                continue
            occupied.add(cell)
            leaders.append((east_m, north_m))
            if len(leaders) >= beam_width:
                break
        if len(leaders) < beam_width:
            selected = set(leaders)
            for (east_m, north_m), _hypothesis in ranked:
                if (east_m, north_m) not in selected:
                    leaders.append((east_m, north_m))
                    selected.add((east_m, north_m))
                if len(leaders) >= beam_width:
                    break
        next_positions = {
            (x + dx, y + dy)
            for x, y in leaders
            for dx in (-step, 0.0, step)
            for dy in (-step, 0.0, step)
            if xmin <= x + dx <= xmax and ymin <= y + dy <= ymax
        }
        evaluate_stage(next_positions, quota)

    # A stage quota is an internal reservation.  Continue all planned
    # refinement stages first, then consume the omitted unique positions in
    # their deterministic order so the declared candidate budget and outcome
    # counts remain aligned with the budget-exhausted contract.
    if any_stage_truncated and not budget_exhausted:
        evaluate_stage(pending_truncated, budget - accumulator.candidate_count)
        if accumulator.candidate_count == budget:
            budget_exhausted = True

    if budget_exhausted:
        return accumulator.finish(
            search_status="budget_exhausted",
            budget_limit=budget,
            budget_used=budget,
        )
    return accumulator.finish(
        budget_limit=budget,
        budget_used=accumulator.candidate_count,
    )


__all__ = [
    "LineOfSightPredicate",
    "WaterPredicate",
    "hierarchical_search",
    "run_hierarchical_search_v2",
]

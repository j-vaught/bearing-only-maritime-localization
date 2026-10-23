"""Coarse-to-fine grid search for global-bearing localization.

The search scores a sparse grid over the full region, retains a configurable
beam of promising water-valid candidates, and successively samples finer grids
around them. It uses the shared baseline association cost so its measurements
and matching semantics stay comparable with :mod:`bearing_localization.algorithms`.
"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

import numpy as np

from ..algorithms import SearchResult, associate

WaterPredicate = Callable[[float, float], bool]


def hierarchical_search(
    observations: list[tuple[str | None, float]],
    ais_xy: np.ndarray,
    ais_types: list[str | None],
    bounds_xy: tuple[float, float, float, float],
    sigma_deg: float = 0.5,
    coarse_spacing_m: float = 5_000.0,
    refinement_levels: int = 4,
    refinement_factor: float = 2.0,
    beam_width: int = 8,
    use_types: bool = True,
    unmatched_penalty_deg: float = 12.0,
    visibility_radius_m: float = float("inf"),
    penalize_missing_ais: bool = False,
    water_predicate: WaterPredicate | None = None,
) -> SearchResult:
    """Estimate position with a bounded coarse-to-fine search.

    ``bounds_xy`` is ``(xmin, xmax, ymin, ymax)`` in projected metres.
    ``water_predicate`` receives ``(east_m, north_m)`` and returns whether a
    candidate is water. It is optional so callers without a water mask can use
    the same search interface.

    ``coarse_spacing_m`` sets the initial lattice spacing. Each refinement
    level divides the spacing by ``refinement_factor`` and evaluates a 3-by-3
    neighborhood around each retained candidate. Duplicate coordinates are
    evaluated once. ``beam_width`` controls the number of candidates retained
    between levels, trading compute for resilience to local score minima.
    """
    started = perf_counter()
    if sigma_deg <= 0 or unmatched_penalty_deg <= 0 or visibility_radius_m <= 0:
        raise ValueError("sigma, unmatched penalty, and visibility radius must be positive.")
    if coarse_spacing_m <= 0 or not np.isfinite(coarse_spacing_m):
        raise ValueError("coarse_spacing_m must be finite and positive.")
    if refinement_levels < 0:
        raise ValueError("refinement_levels must be nonnegative.")
    if refinement_factor <= 1 or not np.isfinite(refinement_factor):
        raise ValueError("refinement_factor must be finite and greater than one.")
    if beam_width < 1:
        raise ValueError("beam_width must be positive.")

    xmin, xmax, ymin, ymax = bounds_xy
    if not all(np.isfinite(bounds_xy)) or xmin >= xmax or ymin >= ymax:
        raise ValueError("Search bounds must be finite and have positive width and height.")
    xy = np.asarray(ais_xy, dtype=float).reshape((-1, 2))
    if len(ais_types) != len(xy):
        raise ValueError("ais_types must have one entry per AIS coordinate.")

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
            score, matches = associate(
                np.asarray([x, y]),
                observations,
                xy,
                ais_types,
                sigma_deg,
                unmatched_penalty_deg,
                use_types,
                visibility_radius_m,
                penalize_missing_ais,
            )
            scored[key] = (score, matches)
            evaluations += 1

    def lattice(step: float) -> set[tuple[float, float]]:
        xs = np.arange(xmin, xmax + step * 0.5, step)
        ys = np.arange(ymin, ymax + step * 0.5, step)
        return {
            (float(np.clip(x, xmin, xmax)), float(np.clip(y, ymin, ymax)))
            for x in xs
            for y in ys
        }

    evaluate(lattice(coarse_spacing_m))
    if not scored:
        raise ValueError("No water-valid search candidates fall within the search bounds.")

    step = coarse_spacing_m
    for _ in range(refinement_levels):
        step /= refinement_factor
        leaders = sorted(scored.items(), key=lambda item: item[1][0])[:beam_width]
        next_points: set[tuple[float, float]] = set()
        for (x, y), _ in leaders:
            for dx in (-step, 0.0, step):
                for dy in (-step, 0.0, step):
                    candidate_x, candidate_y = x + dx, y + dy
                    if xmin <= candidate_x <= xmax and ymin <= candidate_y <= ymax:
                        next_points.add((candidate_x, candidate_y))
        evaluate(next_points)

    (best_x, best_y), (best_score, best_matches) = min(
        scored.items(), key=lambda item: item[1][0]
    )
    return SearchResult(
        east_m=best_x,
        north_m=best_y,
        score_sigma=float(best_score),
        matches=best_matches,
        elapsed_s=perf_counter() - started,
        evaluations=evaluations,
    )


__all__ = ["WaterPredicate", "hierarchical_search"]

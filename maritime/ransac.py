"""Randomized position-hypothesis search for global-bearing localization.

This is a RANSAC-like multi-start method rather than an exact RANSAC
implementation.  Each trial samples three observations and three AIS records,
fits a pose to that tentative correspondence with nonlinear least squares, and
then evaluates the full report using :func:`maritime.algorithms.associate`.
The full scoring step permits observations from objects absent from AIS and can
optionally penalize AIS objects that should have been visible but were not.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import permutations
from time import perf_counter

import numpy as np
from scipy.optimize import least_squares

from .algorithms import SearchResult, associate, wrap_deg

WaterPredicate = Callable[[float, float], bool]
LineOfSightPredicate = Callable[[tuple[float, float], tuple[float, float]], bool]


def randomized_hypothesis_search(
    observations: list[tuple[str | None, float]],
    ais_xy: np.ndarray,
    ais_types: list[str | None],
    bounds_xy: tuple[float, float, float, float],
    sigma_deg: float | Sequence[float] | np.ndarray = 0.5,
    visibility_radius_m: float = float("inf"),
    unmatched_penalty_deg: float = 12.0,
    use_types: bool = True,
    penalize_missing_ais: bool = False,
    iterations: int = 500,
    seed: int | None = 0,
    water_predicate: WaterPredicate | None = None,
    line_of_sight_predicate: LineOfSightPredicate | None = None,
    report_sigmas_deg: Sequence[float] | np.ndarray | None = None,
) -> SearchResult:
    """Estimate position with randomized three-correspondence hypotheses.

    ``observations`` are ``(type, bearing_from_north_deg)`` pairs and ``ais_xy``
    uses ``(east, north)`` coordinates.  The returned result is compatible
    with :class:`~maritime.algorithms.SearchResult`.  A trial is
    counted in ``evaluations`` even when it cannot form a valid hypothesis.
    Runtime is approximately linear in ``iterations`` times the assignment
    cost, with a small nonlinear fit per valid trial.  Trials are independent
    and therefore suitable for CPU process or GPU batch parallelization, while
    the Hungarian association itself remains the main serial scoring step.
    """
    started = perf_counter()
    sigmas = np.asarray(
        report_sigmas_deg if report_sigmas_deg is not None else sigma_deg, dtype=float
    )
    if sigmas.ndim == 0:
        sigmas = np.full(len(observations), float(sigmas))
    if (
        sigmas.shape != (len(observations),)
        or not np.isfinite(sigmas).all()
        or np.any(sigmas <= 0.0)
        or visibility_radius_m <= 0
        or unmatched_penalty_deg <= 0
    ):
        raise ValueError("sigma, visibility radius, and unmatched penalty must be positive.")
    if iterations < 1:
        raise ValueError("iterations must be positive.")
    xmin, xmax, ymin, ymax = bounds_xy
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("Search bounds must have positive width and height.")
    xy = np.asarray(ais_xy, dtype=float).reshape((-1, 2))
    if len(ais_types) != len(xy):
        raise ValueError("ais_types must have one entry per AIS coordinate.")

    rng = np.random.default_rng(seed)
    best: tuple[float, np.ndarray, float, tuple[tuple[int, int], ...]] | None = None
    obs_count = len(observations)
    candidate_count = len(xy)

    def compatible(obs_i: int, ais_i: int) -> bool:
        return (
            not use_types
            or observations[obs_i][0] is None
            or ais_types[ais_i] is None
            or observations[obs_i][0] == ais_types[ais_i]
        )

    def score_position(
        position: np.ndarray,
    ) -> tuple[float, tuple[tuple[int, int], ...]] | None:
        if water_predicate is not None and not water_predicate(
            float(position[0]), float(position[1])
        ):
            return None
        distances = np.linalg.norm(xy - position[None, :], axis=1)
        candidate_indices = np.flatnonzero(distances <= visibility_radius_m)
        if line_of_sight_predicate is not None and len(candidate_indices):
            observer = (float(position[0]), float(position[1]))
            candidate_indices = np.asarray(
                [
                    index
                    for index in candidate_indices
                    if line_of_sight_predicate(
                        observer,
                        (float(xy[index, 0]), float(xy[index, 1])),
                    )
                ],
                dtype=int,
            )
        score, local_matches = associate(
            position,
            observations,
            xy[candidate_indices],
            [ais_types[int(index)] for index in candidate_indices],
            sigmas,
            unmatched_penalty_deg,
            use_types,
            float("inf"),
            penalize_missing_ais,
        )
        matches = tuple(
            (observation_index, int(candidate_indices[local_ais_index]))
            for observation_index, local_ais_index in local_matches
        )
        return score, matches

    for _ in range(iterations):
        if obs_count < 3 or candidate_count < 3:
            continue
        obs_indices = rng.choice(obs_count, size=3, replace=False)
        # Sampling candidate triples avoids enumerating the full AIS catalog.
        candidate_pool = rng.choice(candidate_count, size=min(candidate_count, 24), replace=False)
        valid = [i for i in candidate_pool if any(compatible(int(o), int(i)) for o in obs_indices)]
        if len(valid) < 3:
            continue
        candidate_indices = rng.choice(valid, size=3, replace=False)
        if not all(compatible(int(o), int(a)) for o, a in zip(obs_indices, candidate_indices)):
            # Try a few permutations before discarding this draw.
            pairing = next(
                (
                    p
                    for p in permutations(candidate_indices)
                    if all(compatible(int(o), int(a)) for o, a in zip(obs_indices, p))
                ),
                None,
            )
            if pairing is None:
                continue
            candidate_indices = np.asarray(pairing, dtype=int)

        init = np.array([rng.uniform(xmin, xmax), rng.uniform(ymin, ymax)])

        def residual(v: np.ndarray) -> np.ndarray:
            dx = xy[candidate_indices, 0] - v[0]
            dy = xy[candidate_indices, 1] - v[1]
            predicted = np.degrees(np.arctan2(dx, dy))
            measured = np.asarray([observations[int(i)][1] for i in obs_indices])
            return np.asarray(wrap_deg(predicted - measured), dtype=float) / sigmas[obs_indices]

        fit = least_squares(
            residual,
            init,
            bounds=([xmin, ymin], [xmax, ymax]),
            max_nfev=80,
        )
        position = fit.x[:2]
        scored = score_position(position)
        if scored is None:
            continue
        score, matches = scored
        candidate = (score, position.copy(), 0.0, matches)
        if best is None or candidate[0] < best[0]:
            best = candidate

    if best is None:
        # No three-point hypothesis was possible. Return a valid, explicitly
        # uninformative result so benchmark sweeps can continue.
        position: np.ndarray | None = None
        for _ in range(10_000):
            candidate_position = np.array(
                [rng.uniform(xmin, xmax), rng.uniform(ymin, ymax)], dtype=float
            )
            scored = score_position(candidate_position)
            if scored is not None:
                position = candidate_position
                score, matches = scored
                break
        if position is None:
            raise ValueError("No water-valid randomized candidate falls within the search bounds.")
        best = (score, position, 0.0, matches)

    score, position, heading, matches = best
    return SearchResult(
        float(position[0]),
        float(position[1]),
        float(score),
        matches,
        perf_counter() - started,
        iterations,
    )


__all__ = ["randomized_hypothesis_search"]

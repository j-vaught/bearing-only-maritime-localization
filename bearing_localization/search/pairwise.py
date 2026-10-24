"""Deterministic pairwise geometric hypotheses for bearing-only localization.

Each observation / catalogue-object pairing defines a line on which the
observer must lie. Intersections of two such lines generate position
hypotheses; the complete observation set then scores each candidate using the
same assignment likelihood as the other search methods.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from math import hypot, isfinite, radians
from time import perf_counter

import numpy as np
from scipy.optimize import least_squares

from maritime_localization_shared.contracts import AISSnapshot, MeasurementSet, PositionEstimate

from ..algorithms import SearchResult, associate, wrap_deg
from .association import compatible_types


@dataclass(frozen=True, slots=True)
class PairwiseSearchConfig:
    """Controls deterministic intersection generation and candidate refinement."""

    sigma_deg: float = 0.5
    unmatched_penalty_deg: float = 12.0
    visibility_radius_m: float = float("inf")
    use_types: bool = True
    penalize_missing_ais: bool = False
    min_crossing_angle_deg: float = 1.0
    max_hypotheses: int = 20_000
    max_visibility_hypotheses: int = 128
    refine_starts: int = 32

    def __post_init__(self) -> None:
        if not isfinite(self.sigma_deg) or self.sigma_deg <= 0:
            raise ValueError("sigma_deg must be finite and positive.")
        if not isfinite(self.unmatched_penalty_deg) or self.unmatched_penalty_deg <= 0:
            raise ValueError("unmatched_penalty_deg must be finite and positive.")
        if self.visibility_radius_m <= 0 or np.isnan(self.visibility_radius_m):
            raise ValueError("visibility_radius_m must be positive.")
        if not isfinite(self.min_crossing_angle_deg) or not 0 <= self.min_crossing_angle_deg < 90:
            raise ValueError("min_crossing_angle_deg must be in [0, 90).")
        if min(self.max_hypotheses, self.max_visibility_hypotheses, self.refine_starts) < 1:
            raise ValueError("Hypothesis limits and refine_starts must be positive.")


def pairwise_hypothesis_search(
    observations: Sequence[tuple[str | None, float]],
    ais_xy: np.ndarray,
    ais_types: Sequence[str | None],
    bounds_xy: tuple[float, float, float, float],
    config: PairwiseSearchConfig | None = None,
    *,
    water_predicate=None,
    line_of_sight_predicate=None,
) -> SearchResult:
    """Estimate position by enumerating intersections of paired bearing lines.

    Bearings are clockwise from north. Every hypothesis comes from two
    distinct observations paired with two distinct catalogue records. Type
    filtering is applied before intersection generation, while final candidate
    scoring uses the shared one-to-one assignment likelihood. ``max_hypotheses``
    bounds the deterministic candidate pool. That pool is spread across report
    pairs and ranked without ray tracing; ``max_visibility_hypotheses`` bounds
    candidates passed to the expensive map-aware score. Water and line-of-sight
    callbacks follow the signatures used by hierarchical search.
    """
    started = perf_counter()
    cfg = config or PairwiseSearchConfig()
    if len(observations) < 2:
        raise ValueError("Pairwise search requires at least two observations.")
    if len(bounds_xy) != 4 or not all(isfinite(float(v)) for v in bounds_xy):
        raise ValueError("bounds_xy must contain four finite values.")
    xmin, xmax, ymin, ymax = map(float, bounds_xy)
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("bounds_xy must have positive width and height.")

    points = np.asarray(ais_xy, dtype=float).reshape((-1, 2))
    kinds = list(ais_types)
    if len(kinds) != len(points):
        raise ValueError("ais_types must have one entry per AIS coordinate.")
    if not np.isfinite(points).all():
        raise ValueError("ais_xy must contain finite coordinates.")
    obs = [(kind, float(bearing)) for kind, bearing in observations]
    if any(not isfinite(bearing) for _, bearing in obs):
        raise ValueError("Observation bearings must be finite.")

    half_diagonal = hypot((xmax - xmin) / 2.0, (ymax - ymin) / 2.0)
    center = np.asarray([(xmin + xmax) / 2.0, (ymin + ymax) / 2.0])
    eligible: list[np.ndarray] = []
    for kind, bearing in obs:
        distances = np.linalg.norm(points - center, axis=1)
        possible = distances <= half_diagonal + cfg.visibility_radius_m
        # A target's bearing varies across the search rectangle by at most the
        # angular radius subtended by its half-diagonal. Keep a conservative
        # sigma margin so this is a safe geometric prefilter.
        away = distances > half_diagonal
        angular_radius = np.full(len(points), 180.0)
        angular_radius[away] = np.degrees(
            np.arcsin(np.clip(half_diagonal / distances[away], 0.0, 1.0))
        )
        center_bearing = np.degrees(np.arctan2(points[:, 0] - center[0], points[:, 1] - center[1]))
        bearing_delta = np.abs(wrap_deg(center_bearing - bearing))
        possible &= bearing_delta <= angular_radius + 6.0 * cfg.sigma_deg
        possible &= np.asarray(
            [
                ais_kind is not None and compatible_types(kind, ais_kind, use_types=cfg.use_types)
                for ais_kind in kinds
            ],
            dtype=bool,
        )
        eligible.append(np.flatnonzero(possible))

    # Build a spatially pruned, vectorized list of target pairs. If both targets
    # are within range of a common observer, their separation cannot exceed
    # twice the visibility radius.
    upper_i, upper_j = np.triu_indices(len(points), 1)
    if isfinite(cfg.visibility_radius_m):
        close = (
            np.sum((points[upper_i] - points[upper_j]) ** 2, axis=1)
            <= (2.0 * cfg.visibility_radius_m) ** 2
        )
        upper_i, upper_j = upper_i[close], upper_j[close]

    observation_pairs = list(combinations(range(len(obs)), 2))
    pair_quota = max(1, cfg.max_hypotheses // len(observation_pairs))
    extra_quota = cfg.max_hypotheses % len(observation_pairs)
    hypotheses: list[np.ndarray] = []
    minimum_sine = np.sin(np.deg2rad(cfg.min_crossing_angle_deg))
    directions = np.asarray(
        [[np.sin(radians(bearing)), np.cos(radians(bearing))] for _, bearing in obs],
        dtype=float,
    )

    for pair_number, (obs_i, obs_j) in enumerate(observation_pairs):
        quota = pair_quota + (pair_number < extra_quota)
        u_i, u_j = directions[obs_i], directions[obs_j]
        determinant = u_i[1] * u_j[0] - u_i[0] * u_j[1]
        if abs(determinant) < minimum_sine:
            continue
        allowed_i = np.zeros(len(points), dtype=bool)
        allowed_j = np.zeros(len(points), dtype=bool)
        allowed_i[eligible[obs_i]] = True
        allowed_j[eligible[obs_j]] = True
        order_ij = allowed_i[upper_i] & allowed_j[upper_j]
        order_ji = allowed_i[upper_j] & allowed_j[upper_i]
        if not np.any(order_ij) and not np.any(order_ji):
            continue
        ais_i = np.concatenate((upper_i[order_ij], upper_j[order_ji]))
        ais_j = np.concatenate((upper_j[order_ij], upper_i[order_ji]))
        delta = points[ais_i] - points[ais_j]
        range_i = (-delta[:, 0] * u_j[1] + u_j[0] * delta[:, 1]) / determinant
        range_j = (u_i[0] * delta[:, 1] - delta[:, 0] * u_i[1]) / determinant
        valid = (range_i > 0.0) & (range_j > 0.0)
        if isfinite(cfg.visibility_radius_m):
            valid &= (range_i <= cfg.visibility_radius_m) & (range_j <= cfg.visibility_radius_m)
        positions = points[ais_i] - range_i[:, None] * u_i[None, :]
        valid &= (
            (positions[:, 0] >= xmin)
            & (positions[:, 0] <= xmax)
            & (positions[:, 1] >= ymin)
            & (positions[:, 1] <= ymax)
        )
        positions = positions[valid]
        if len(positions) > quota:
            # Spread the deterministic sample over the complete ordered-pair
            # list so low record IDs do not monopolize the candidate budget.
            positions = positions[np.linspace(0, len(positions) - 1, quota, dtype=np.int64)]
        if len(positions):
            hypotheses.extend(positions)

    if not hypotheses:
        raise ValueError("No valid pairwise intersections fall within the supplied bounds.")
    candidates = np.unique(np.round(np.asarray(hypotheses, dtype=float), decimals=0), axis=0)
    if water_predicate is not None:
        wet = np.asarray([water_predicate(float(x), float(y)) for x, y in candidates], dtype=bool)
        candidates = candidates[wet]
    if not len(candidates):
        raise ValueError("No water-valid pairwise hypotheses fall within the supplied bounds.")

    def visible_indices(candidate: np.ndarray) -> np.ndarray:
        indices = np.flatnonzero(
            np.linalg.norm(points - candidate, axis=1) <= cfg.visibility_radius_m
        )
        if line_of_sight_predicate is not None:
            indices = np.asarray(
                [
                    index
                    for index in indices
                    if line_of_sight_predicate(
                        (float(candidate[0]), float(candidate[1])),
                        (float(points[index, 0]), float(points[index, 1])),
                    )
                ],
                dtype=int,
            )
        return indices

    def score_candidate(
        candidate: np.ndarray,
        *,
        use_line_of_sight: bool,
        penalize_missing_ais: bool = cfg.penalize_missing_ais,
    ) -> tuple[float, tuple[tuple[int, int], ...]]:
        indices = np.flatnonzero(
            np.linalg.norm(points - candidate[None, :], axis=1) <= cfg.visibility_radius_m
        )
        if use_line_of_sight and line_of_sight_predicate is not None and len(indices):
            indices = np.asarray(
                [
                    index
                    for index in indices
                    if line_of_sight_predicate(
                        (float(candidate[0]), float(candidate[1])),
                        (float(points[index, 0]), float(points[index, 1])),
                    )
                ],
                dtype=int,
            )
        score, matches = associate(
            candidate,
            obs,
            points[indices],
            [kinds[index] for index in indices],
            sigma_deg=cfg.sigma_deg,
            unmatched_penalty_deg=cfg.unmatched_penalty_deg,
            use_types=cfg.use_types,
            penalize_missing_ais=penalize_missing_ais,
        )
        global_matches = tuple((oi, int(indices[local])) for oi, local in matches)
        return score, global_matches

    # First rank the bounded candidate pool without raster ray tracing. Only a
    # small set of leaders incurs per-object line-of-sight queries and final
    # one-to-one scoring.
    prelim: list[tuple[float, tuple[tuple[int, int], ...], np.ndarray]] = []
    for candidate in candidates:
        score, matches = score_candidate(
            candidate,
            use_line_of_sight=False,
            penalize_missing_ais=cfg.penalize_missing_ais and line_of_sight_predicate is None,
        )
        prelim.append((score, matches, candidate))
    prelim.sort(key=lambda value: value[0])
    shortlist = prelim[: cfg.max_visibility_hypotheses]
    scored: list[tuple[float, tuple[tuple[int, int], ...], np.ndarray]] = []
    for _, _, candidate in shortlist:
        score, matches = score_candidate(candidate, use_line_of_sight=True)
        scored.append((score, matches, candidate))
    if not scored:
        raise ValueError("No water-valid pairwise hypotheses fall within the supplied bounds.")

    leaders = sorted(scored, key=lambda value: value[0])[: cfg.refine_starts]
    best_score = float("inf")
    best_position: np.ndarray | None = None
    best_matches: tuple[tuple[int, int], ...] = ()
    for initial_score, matches, initial_position in leaders:
        position = initial_position
        if matches:
            matched_observations = [obs[oi][1] for oi, _ in matches]
            matched_targets = points[[ai for _, ai in matches]]

            def residual(position_xy: np.ndarray) -> np.ndarray:
                delta = matched_targets - position_xy[None, :]
                predicted = np.degrees(np.arctan2(delta[:, 0], delta[:, 1]))
                return np.asarray(wrap_deg(predicted - matched_observations)) / cfg.sigma_deg

            fit = least_squares(residual, initial_position.copy(), max_nfev=100)
            proposed = fit.x
            if xmin <= proposed[0] <= xmax and ymin <= proposed[1] <= ymax:
                if water_predicate is None or water_predicate(
                    float(proposed[0]), float(proposed[1])
                ):
                    proposed_indices = visible_indices(proposed)
                    proposed_score, proposed_local_matches = associate(
                        proposed,
                        obs,
                        points[proposed_indices],
                        [kinds[index] for index in proposed_indices],
                        sigma_deg=cfg.sigma_deg,
                        unmatched_penalty_deg=cfg.unmatched_penalty_deg,
                        use_types=cfg.use_types,
                        penalize_missing_ais=cfg.penalize_missing_ais,
                    )
                    proposed_matches = tuple(
                        (oi, int(proposed_indices[local])) for oi, local in proposed_local_matches
                    )
                    if proposed_score < initial_score:
                        initial_score, matches, position = (
                            proposed_score,
                            proposed_matches,
                            proposed,
                        )
        if initial_score < best_score:
            best_score = float(initial_score)
            best_position = position
            best_matches = matches
    assert best_position is not None
    return SearchResult(
        east_m=float(best_position[0]),
        north_m=float(best_position[1]),
        score_sigma=best_score,
        matches=best_matches,
        elapsed_s=perf_counter() - started,
        evaluations=len(candidates),
    )


def run_pairwise_search(
    measurements: MeasurementSet,
    ais: AISSnapshot,
    bounds_xy: tuple[float, float, float, float],
    config: PairwiseSearchConfig | None = None,
    *,
    visibility=None,
) -> PositionEstimate:
    """Contract adapter for deterministic pairwise hypothesis localization."""
    if not isinstance(measurements, MeasurementSet):
        raise TypeError("measurements must be a MeasurementSet.")
    if not isinstance(ais, AISSnapshot):
        raise TypeError("ais must be an AISSnapshot.")
    cfg = config or PairwiseSearchConfig()
    observations = [(item.object_type, item.bearing_deg) for item in measurements.observations]
    points = np.asarray([(record.east_m, record.north_m) for record in ais.records], dtype=float)
    if not ais.records:
        points = np.empty((0, 2), dtype=float)
    types = [record.ais_class for record in ais.records]
    positive_sigmas = [
        item.bearing_sigma_deg for item in measurements.observations if item.bearing_sigma_deg > 0.0
    ]
    sigma = (
        float(np.sqrt(np.mean(np.square(positive_sigmas)))) if positive_sigmas else cfg.sigma_deg
    )
    effective = PairwiseSearchConfig(
        sigma_deg=sigma,
        unmatched_penalty_deg=cfg.unmatched_penalty_deg,
        visibility_radius_m=cfg.visibility_radius_m,
        use_types=cfg.use_types,
        penalize_missing_ais=cfg.penalize_missing_ais,
        min_crossing_angle_deg=cfg.min_crossing_angle_deg,
        max_hypotheses=cfg.max_hypotheses,
        max_visibility_hypotheses=cfg.max_visibility_hypotheses,
        refine_starts=cfg.refine_starts,
    )
    water_predicate = visibility.is_water if visibility is not None else None
    los_predicate = None
    if visibility is not None:

        def los_predicate(observer: tuple[float, float], target: tuple[float, float]) -> bool:
            return visibility.has_line_of_sight(observer[0], observer[1], target[0], target[1])

    result = pairwise_hypothesis_search(
        observations,
        points,
        types,
        bounds_xy,
        effective,
        water_predicate=water_predicate,
        line_of_sight_predicate=los_predicate,
    )
    return PositionEstimate(
        east_m=result.east_m,
        north_m=result.north_m,
        score=result.score_sigma,
        elapsed_s=result.elapsed_s,
        algorithm="pairwise_hypothesis",
        matched_pairs=tuple(
            (observation_index, ais.records[ais_index].record_id)
            for observation_index, ais_index in result.matches
        ),
    )


__all__ = ["PairwiseSearchConfig", "pairwise_hypothesis_search", "run_pairwise_search"]

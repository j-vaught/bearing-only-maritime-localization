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
from typing import Literal

import numpy as np
from pyproj import Transformer
from scipy.optimize import least_squares

from maritime.geometry.coordinates import LocalFrame
from maritime.geometry.spatial_index import SpatialIndex2D
from maritime.schemas import AISSnapshot, MeasurementSet, PositionEstimate

from ..algorithms import SearchResult, associate, wrap_deg
from .proposals.pairwise import generate_geographic_pairwise_proposals
from .scoring import CandidateScorer, CandidateSearchRun, score_candidate_positions


def _spread_sources(
    source_indices: np.ndarray,
    points: np.ndarray,
    bounds: tuple[float, float, float, float],
    limit: int,
) -> np.ndarray:
    """Choose a stable sample spread across the search rectangle."""
    if len(source_indices) <= limit:
        return source_indices
    xmin, xmax, ymin, ymax = bounds
    aspect = (xmax - xmin) / (ymax - ymin)
    grid_x = max(1, int(np.sqrt(limit * aspect)))
    grid_y = max(1, int(np.ceil(limit / grid_x)))
    coordinates = points[source_indices]
    cell_x = np.clip(
        ((coordinates[:, 0] - xmin) / (xmax - xmin) * grid_x).astype(int), 0, grid_x - 1
    )
    cell_y = np.clip(
        ((coordinates[:, 1] - ymin) / (ymax - ymin) * grid_y).astype(int), 0, grid_y - 1
    )
    cell_id = cell_x * grid_y + cell_y
    centers_x = xmin + (cell_x + 0.5) * (xmax - xmin) / grid_x
    centers_y = ymin + (cell_y + 0.5) * (ymax - ymin) / grid_y
    center_distance = (coordinates[:, 0] - centers_x) ** 2 + (coordinates[:, 1] - centers_y) ** 2
    order = np.lexsort((source_indices, center_distance, cell_id))
    ordered_cells = cell_id[order]
    first_in_cell = np.concatenate(([True], ordered_cells[1:] != ordered_cells[:-1]))
    selected = source_indices[order[first_in_cell]]
    if len(selected) > limit:
        selected = selected[np.linspace(0, len(selected) - 1, limit, dtype=np.int64)]
    elif len(selected) < limit:
        chosen = set(selected.tolist())
        remaining = np.asarray([index for index in source_indices if index not in chosen])
        needed = min(limit - len(selected), len(remaining))
        if needed:
            fill = remaining[np.linspace(0, len(remaining) - 1, needed, dtype=np.int64)]
            selected = np.concatenate((selected, fill))
    return selected


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
    max_pair_sources: int = 5_000
    max_neighbors_per_source: int = 64

    def __post_init__(self) -> None:
        if not isfinite(self.sigma_deg) or self.sigma_deg <= 0:
            raise ValueError("sigma_deg must be finite and positive.")
        if not isfinite(self.unmatched_penalty_deg) or self.unmatched_penalty_deg <= 0:
            raise ValueError("unmatched_penalty_deg must be finite and positive.")
        if self.visibility_radius_m <= 0 or np.isnan(self.visibility_radius_m):
            raise ValueError("visibility_radius_m must be positive.")
        if not isfinite(self.min_crossing_angle_deg) or not 0 <= self.min_crossing_angle_deg < 90:
            raise ValueError("min_crossing_angle_deg must be in [0, 90).")
        if (
            min(
                self.max_hypotheses,
                self.max_visibility_hypotheses,
                self.refine_starts,
                self.max_pair_sources,
                self.max_neighbors_per_source,
            )
            < 1
        ):
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
    report_sigmas_deg: Sequence[float] | np.ndarray | None = None,
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
    report_sigmas = np.asarray(
        report_sigmas_deg if report_sigmas_deg is not None else cfg.sigma_deg, dtype=float
    )
    if report_sigmas.ndim == 0:
        report_sigmas = np.full(len(obs), float(report_sigmas))
    if (
        report_sigmas.shape != (len(obs),)
        or not np.isfinite(report_sigmas).all()
        or np.any(report_sigmas <= 0)
    ):
        raise ValueError(
            "report_sigmas_deg must contain one finite positive value per observation."
        )

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
                ais_kind is not None and (not cfg.use_types or ais_kind == kind)
                for ais_kind in kinds
            ],
            dtype=bool,
        )
        eligible.append(np.flatnonzero(possible))

    # A finite sensor range bounds the separation of two targets that can share
    # an observer position. Use a spatial index so the algorithm never allocates
    # the full O(N^2) catalog pair matrix for a coast-wide catalog.
    if isfinite(cfg.visibility_radius_m):
        target_index = SpatialIndex2D(points, cell_size_m=max(cfg.visibility_radius_m, 1.0))
        pair_radius_m = 2.0 * cfg.visibility_radius_m
    else:
        if len(points) > 5_000:
            raise ValueError(
                "Pairwise search on more than 5,000 catalog objects requires a finite "
                "visibility_radius_m to prune target pairs."
            )
        target_index = None
        pair_radius_m = float("inf")

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
        allowed_j = np.zeros(len(points), dtype=bool)
        allowed_j[eligible[obs_j]] = True
        source_indices = eligible[obs_i]
        if len(source_indices) > cfg.max_pair_sources:
            source_indices = _spread_sources(
                source_indices, points, (xmin, xmax, ymin, ymax), cfg.max_pair_sources
            )
        pair_i_parts: list[np.ndarray] = []
        pair_j_parts: list[np.ndarray] = []
        for ais_i in source_indices:
            if target_index is None:
                nearby = np.arange(len(points), dtype=np.int64)
            else:
                nearby = np.asarray(
                    target_index.query_radius(
                        float(points[ais_i, 0]), float(points[ais_i, 1]), pair_radius_m
                    ),
                    dtype=np.int64,
                )
            neighbors = nearby[allowed_j[nearby] & (nearby != ais_i)]
            if len(neighbors) > cfg.max_neighbors_per_source:
                offsets = points[neighbors] - points[ais_i]
                angles = np.arctan2(offsets[:, 0], offsets[:, 1])
                distance_squared = np.einsum("ij,ij->i", offsets, offsets)
                neighbor_order = np.lexsort((neighbors, distance_squared, angles))
                neighbors = neighbors[neighbor_order]
                neighbors = neighbors[
                    np.linspace(
                        0,
                        len(neighbors) - 1,
                        cfg.max_neighbors_per_source,
                        dtype=np.int64,
                    )
                ]
            if len(neighbors):
                pair_i_parts.append(np.full(len(neighbors), ais_i, dtype=np.int64))
                pair_j_parts.append(neighbors)
        if not pair_i_parts:
            continue
        ais_i = np.concatenate(pair_i_parts)
        ais_j = np.concatenate(pair_j_parts)
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
            sigma_deg=report_sigmas,
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
                return (
                    np.asarray(wrap_deg(predicted - matched_observations))
                    / report_sigmas[[oi for oi, _ in matches]]
                )

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
                        sigma_deg=report_sigmas,
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
    report_sigmas = np.asarray(
        [
            item.bearing_sigma_deg if item.bearing_sigma_deg > 0.0 else cfg.sigma_deg
            for item in measurements.observations
        ],
        dtype=float,
    )
    sigma = cfg.sigma_deg
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
        max_pair_sources=cfg.max_pair_sources,
        max_neighbors_per_source=cfg.max_neighbors_per_source,
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
        report_sigmas_deg=report_sigmas,
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


def run_pairwise_search_v2(
    scorer: CandidateScorer,
    *,
    batch_size: int = 1_024,
    max_proposals: int | None = None,
    min_crossing_angle_deg: float = 0.1,
) -> CandidateSearchRun:
    """Score A19's streaming geographic proposals with the common V2 objective.

    Proposal generation has no source or neighbor caps. The declared search
    candidate budget is the default proposal limit; the terminal A19 stream
    status is carried into the result. Local AEQD proposal coordinates are
    converted to the VectorMap's projected metre CRS before scoring.
    """
    if not isinstance(scorer, CandidateScorer):
        raise TypeError("scorer must be a CandidateScorer instance.")
    bounds = scorer.config.bounds_xy
    if bounds is None:
        raise ValueError("V2 pairwise search requires config.bounds_xy.")
    budget = scorer.config.candidate_budget if max_proposals is None else max_proposals
    if type(budget) is not int or budget < 1 or budget > scorer.config.candidate_budget:
        raise ValueError("max_proposals must be positive and no greater than candidate_budget.")
    xmin, xmax, ymin, ymax = bounds
    to_geographic = Transformer.from_crs(scorer.vector_map.crs, "EPSG:4326", always_xy=True)
    origin_lon, origin_lat = to_geographic.transform((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
    origin = (float(origin_lon), float(origin_lat))
    proposal_frame = LocalFrame(origin)
    to_map = Transformer.from_crs("EPSG:4326", scorer.vector_map.crs, always_xy=True)
    batches = generate_geographic_pairwise_proposals(
        scorer.measurements,
        scorer.catalog,
        output_origin_lonlat=origin,
        max_range_m=scorer.config.max_range_m,
        batch_size=batch_size,
        max_proposals=budget,
        min_crossing_angle_deg=min_crossing_angle_deg,
    )
    positions: list[tuple[float, float]] = []
    terminal_status: Literal["generation_incomplete", "completed", "budget_exhausted"] = (
        "generation_incomplete"
    )
    generated_count = 0
    for batch in batches:
        generated_count = batch.generated_count
        terminal_status = batch.generation_status
        for proposal in batch.proposals:
            lon, lat = proposal_frame.to_geographic(proposal.east_m, proposal.north_m)
            east_m, north_m = to_map.transform(lon, lat)
            positions.append((float(east_m), float(north_m)))
    if terminal_status == "generation_incomplete":
        raise RuntimeError("The A19 proposal stream ended without a terminal status.")
    search_status = "budget_exhausted" if terminal_status == "budget_exhausted" else "completed"
    return score_candidate_positions(
        scorer,
        positions,
        search_status=search_status,
        budget_limit=budget,
        budget_used=generated_count,
    )


__all__ = [
    "PairwiseSearchConfig",
    "pairwise_hypothesis_search",
    "run_pairwise_search",
    "run_pairwise_search_v2",
]

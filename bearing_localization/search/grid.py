"""Adapter from shared program contracts to the deterministic grid baseline.

The adapter keeps the estimator boundary explicit: inputs are measurements,
AIS, bounds, and optional map-visibility behavior. It never accepts simulation
truth. Map objects are structural so the shared contract package need not
depend on a particular raster or vector implementation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Protocol

import numpy as np

from bearing_localization.algorithms import associate, grid_search
from maritime_localization_shared.contracts import AISSnapshot, MeasurementSet, PositionEstimate

from .scoring import CandidateScorer, CandidateSearchRun, score_candidate_positions


class MapVisibility(Protocol):
    """Map behavior required to constrain grid candidates and AIS visibility.

    ``has_line_of_sight`` should return ``False`` when land intersects the
    segment between the observer and target. Coordinates are projected east
    and north meters in the same CRS as the AIS snapshot.
    """

    def is_water(self, east_m: float, north_m: float) -> bool: ...

    def has_line_of_sight(
        self,
        observer_east_m: float,
        observer_north_m: float,
        target_east_m: float,
        target_north_m: float,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class GridSearchConfig:
    """Controls the shared-contract grid-search adapter."""

    grid_spacing_m: float = 250.0
    bearing_sigma_deg: float = 0.5
    unmatched_penalty_deg: float = 12.0
    visibility_radius_m: float = float("inf")
    use_types: bool = True
    refine: bool = True
    refine_starts: int = 16
    penalize_missing_ais: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.grid_spacing_m) or self.grid_spacing_m <= 0:
            raise ValueError("grid_spacing_m must be finite and positive.")
        if not isfinite(self.bearing_sigma_deg) or self.bearing_sigma_deg <= 0:
            raise ValueError("bearing_sigma_deg must be finite and positive.")
        if not isfinite(self.unmatched_penalty_deg) or self.unmatched_penalty_deg <= 0:
            raise ValueError("unmatched_penalty_deg must be finite and positive.")
        if self.visibility_radius_m <= 0 or np.isnan(self.visibility_radius_m):
            raise ValueError("visibility_radius_m must be positive.")
        if self.refine_starts < 1:
            raise ValueError("refine_starts must be positive.")


def _inputs(
    measurements: MeasurementSet,
    ais: AISSnapshot,
    config: GridSearchConfig,
) -> tuple[list[tuple[str | None, float]], np.ndarray, list[str | None], np.ndarray]:
    observations = [
        (observation.object_type, observation.bearing_deg)
        for observation in measurements.observations
    ]
    ais_xy = np.asarray([(record.east_m, record.north_m) for record in ais.records], dtype=float)
    if not ais.records:
        ais_xy = np.empty((0, 2), dtype=float)
    ais_types: list[str | None] = [record.ais_class for record in ais.records]

    # Retain each report's own sigma. The configured value is used only when
    # a legacy measurement does not provide a positive report uncertainty.
    sigma_deg = np.asarray(
        [
            observation.bearing_sigma_deg
            if observation.bearing_sigma_deg > 0.0
            else config.bearing_sigma_deg
            for observation in measurements.observations
        ],
        dtype=float,
    )
    return observations, ais_xy, ais_types, sigma_deg


def run_grid_search(
    measurements: MeasurementSet,
    ais: AISSnapshot,
    bounds_xy: tuple[float, float, float, float],
    config: GridSearchConfig | None = None,
    visibility: MapVisibility | None = None,
) -> PositionEstimate:
    """Estimate observer position from contracts using the grid baseline.

    Bounds use ``(xmin, xmax, ymin, ymax)`` projected meters. With no map
    behavior, this delegates to the existing deterministic grid search. When
    a visibility implementation is supplied, land candidates are skipped and
    only in-range, unobstructed AIS records are associated at each candidate.
    """
    config = config or GridSearchConfig()
    if not measurements.observations:
        raise ValueError("Grid search requires at least one observation.")
    xmin, xmax, ymin, ymax = bounds_xy
    if not all(isfinite(value) for value in bounds_xy) or xmin >= xmax or ymin >= ymax:
        raise ValueError("Search bounds must be finite and have positive width and height.")
    observations, ais_xy, ais_types, sigma_deg = _inputs(measurements, ais, config)

    if visibility is None:
        result = grid_search(
            observations,
            ais_xy,
            ais_types,
            bounds_xy,
            sigma_deg=sigma_deg,
            grid_spacing_m=config.grid_spacing_m,
            use_types=config.use_types,
            unmatched_penalty_deg=config.unmatched_penalty_deg,
            refine=config.refine,
            visibility_radius_m=config.visibility_radius_m,
            penalize_missing_ais=config.penalize_missing_ais,
            refine_starts=config.refine_starts,
        )
        matched_pairs = tuple(
            (observation_index, ais.records[ais_index].record_id)
            for observation_index, ais_index in result.matches
        )
        return PositionEstimate(
            east_m=result.east_m,
            north_m=result.north_m,
            score=result.score_sigma,
            elapsed_s=result.elapsed_s,
            algorithm="grid",
            matched_pairs=matched_pairs,
        )

    started = perf_counter()
    xs = np.arange(xmin, xmax + config.grid_spacing_m * 0.5, config.grid_spacing_m)
    ys = np.arange(ymin, ymax + config.grid_spacing_m * 0.5, config.grid_spacing_m)
    best_score = float("inf")
    best_xy: tuple[float, float] | None = None
    best_matches: tuple[tuple[int, int], ...] = ()
    for east_m in xs:
        for north_m in ys:
            if not visibility.is_water(float(east_m), float(north_m)):
                continue
            delta = ais_xy - np.asarray([east_m, north_m])
            distances = np.linalg.norm(delta, axis=1)
            candidate_indices = [
                index
                for index, distance in enumerate(distances)
                if distance <= config.visibility_radius_m
                and visibility.has_line_of_sight(
                    float(east_m),
                    float(north_m),
                    float(ais_xy[index, 0]),
                    float(ais_xy[index, 1]),
                )
            ]
            candidate_xy = ais_xy[candidate_indices]
            candidate_types: list[str | None] = [ais_types[index] for index in candidate_indices]
            score, local_matches = associate(
                np.asarray([east_m, north_m]),
                observations,
                candidate_xy,
                candidate_types,
                sigma_deg=sigma_deg,
                unmatched_penalty_deg=config.unmatched_penalty_deg,
                use_types=config.use_types,
                visibility_radius_m=float("inf"),
                penalize_missing_ais=config.penalize_missing_ais,
            )
            if score < best_score:
                best_score = score
                best_xy = (float(east_m), float(north_m))
                best_matches = tuple(
                    (observation_index, candidate_indices[local_ais_index])
                    for observation_index, local_ais_index in local_matches
                )
    if best_xy is None:
        raise ValueError("No water grid candidates fall within the supplied search bounds.")

    matched_pairs = tuple(
        (observation_index, ais.records[ais_index].record_id)
        for observation_index, ais_index in best_matches
    )
    return PositionEstimate(
        east_m=best_xy[0],
        north_m=best_xy[1],
        score=best_score,
        elapsed_s=perf_counter() - started,
        algorithm="grid",
        matched_pairs=matched_pairs,
    )


def _grid_positions_v2(
    bounds: tuple[float, float, float, float], spacing_m: float
) -> Iterable[tuple[float, float]]:
    xmin, xmax, ymin, ymax = bounds

    def axis(low: float, high: float) -> Iterable[float]:
        index = 0
        last = low
        while True:
            value = low + index * spacing_m
            if value > high + 1e-9:
                break
            last = min(value, high)
            yield last
            index += 1
        if high - last > 1e-9:
            yield high

    for east_m in axis(xmin, xmax):
        for north_m in axis(ymin, ymax):
            yield east_m, north_m


def run_grid_search_v2(
    scorer: CandidateScorer,
    *,
    grid_spacing_m: float = 250.0,
    candidate_positions: Iterable[tuple[float, float]] | None = None,
) -> CandidateSearchRun:
    """Score a uniform grid over all declared bounds with the V2 objective.

    An explicit position iterable provides fixed-candidate mode for parity
    tests and externally generated exact grids. No port prior or map subset is
    inferred when the full bounds are available.
    """
    if not isfinite(grid_spacing_m) or grid_spacing_m <= 0.0:
        raise ValueError("grid_spacing_m must be finite and positive.")
    if candidate_positions is None:
        bounds = scorer.config.bounds_xy
        if bounds is None:
            raise ValueError("V2 grid search requires config.bounds_xy.")
        positions = _grid_positions_v2(bounds, grid_spacing_m)
    else:
        positions = candidate_positions
    return score_candidate_positions(
        scorer,
        positions,
        candidate_limit=scorer.config.candidate_budget,
        certificate_scope="finite_candidate_set",
    )


__all__ = ["GridSearchConfig", "MapVisibility", "run_grid_search", "run_grid_search_v2"]

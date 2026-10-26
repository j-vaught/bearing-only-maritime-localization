"""Shared-contract adapter for randomized-hypothesis localization."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from maritime_localization_shared.contracts.ais import AISSnapshot
from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.observation import MeasurementSet

from ..ransac import randomized_hypothesis_search
from .scoring import CandidateScorer, CandidateSearchRun, score_candidate_positions


class VisibilityProtocol(Protocol):
    """Map queries needed to constrain randomized candidate hypotheses."""

    def is_water(self, east_m: float, north_m: float) -> bool: ...

    def has_line_of_sight(
        self,
        observer_east_m: float,
        observer_north_m: float,
        target_east_m: float,
        target_north_m: float,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class RandomizedSearchConfig:
    """Configuration for the randomized search adapter.

    If ``sigma_deg`` is omitted, each observation retains its own reported
    standard deviation. A small positive floor handles exact-zero uncertainty.
    """

    bounds_xy: tuple[float, float, float, float]
    sigma_deg: float | None = None
    min_sigma_deg: float = 1e-3
    visibility_radius_m: float = float("inf")
    unmatched_penalty_deg: float = 12.0
    use_types: bool = True
    penalize_missing_ais: bool = False
    iterations: int = 500
    seed: int | None = 0
    visibility: VisibilityProtocol | None = None

    def __post_init__(self) -> None:
        if len(self.bounds_xy) != 4:
            raise ValueError("bounds_xy must be (xmin, xmax, ymin, ymax).")
        xmin, xmax, ymin, ymax = self.bounds_xy
        if not all(np.isfinite(v) for v in self.bounds_xy) or xmin >= xmax or ymin >= ymax:
            raise ValueError("bounds_xy must contain finite, positive-width bounds.")
        if self.sigma_deg is not None and (not np.isfinite(self.sigma_deg) or self.sigma_deg <= 0):
            raise ValueError("sigma_deg must be finite and positive when provided.")
        if not np.isfinite(self.min_sigma_deg) or self.min_sigma_deg <= 0:
            raise ValueError("min_sigma_deg must be finite and positive.")


def randomized_search(
    measurements: MeasurementSet,
    ais: AISSnapshot,
    config: RandomizedSearchConfig,
) -> PositionEstimate:
    """Estimate observer position using only measurements and AIS contracts.

    Returned matches pair measurement indices with opaque AIS record IDs. The
    evaluator-only truth contract is intentionally not accepted by this API.
    """
    if not isinstance(measurements, MeasurementSet):
        raise TypeError("measurements must be a MeasurementSet.")
    if not isinstance(ais, AISSnapshot):
        raise TypeError("ais must be an AISSnapshot.")
    if not isinstance(config, RandomizedSearchConfig):
        raise TypeError("config must be a RandomizedSearchConfig.")

    observations = [(item.object_type, item.bearing_deg) for item in measurements.observations]
    ais_types: list[str | None] = [record.ais_class for record in ais.records]
    ais_xy = np.asarray(
        [(record.east_m, record.north_m) for record in ais.records], dtype=float
    ).reshape((-1, 2))
    if config.sigma_deg is None:
        report_sigmas_deg = np.asarray(
            [
                max(item.bearing_sigma_deg, config.min_sigma_deg)
                if item.bearing_sigma_deg > 0.0
                else config.min_sigma_deg
                for item in measurements.observations
            ],
            dtype=float,
        )
        sigma_deg = config.min_sigma_deg
    else:
        sigma_deg = config.sigma_deg
        report_sigmas_deg = np.full(len(observations), sigma_deg, dtype=float)

    water_predicate = config.visibility.is_water if config.visibility is not None else None
    line_of_sight_predicate = None
    if config.visibility is not None:

        def line_of_sight_predicate(
            observer: tuple[float, float], target: tuple[float, float]
        ) -> bool:
            assert config.visibility is not None
            return config.visibility.has_line_of_sight(
                observer[0], observer[1], target[0], target[1]
            )

    result = randomized_hypothesis_search(
        observations=observations,
        ais_xy=ais_xy,
        ais_types=ais_types,
        bounds_xy=config.bounds_xy,
        sigma_deg=sigma_deg,
        visibility_radius_m=config.visibility_radius_m,
        unmatched_penalty_deg=config.unmatched_penalty_deg,
        use_types=config.use_types,
        penalize_missing_ais=config.penalize_missing_ais,
        iterations=config.iterations,
        seed=config.seed,
        water_predicate=water_predicate,
        line_of_sight_predicate=line_of_sight_predicate,
        report_sigmas_deg=report_sigmas_deg,
    )
    return PositionEstimate(
        east_m=result.east_m,
        north_m=result.north_m,
        score=result.score_sigma,
        elapsed_s=result.elapsed_s,
        algorithm="randomized_hypothesis",
        matched_pairs=tuple(
            (observation_index, ais.records[ais_index].record_id)
            for observation_index, ais_index in result.matches
        ),
    )


def run_randomized_search_v2(
    scorer: CandidateScorer,
    *,
    seed: int | None = 0,
    candidate_positions: Iterable[tuple[float, float]] | None = None,
) -> CandidateSearchRun:
    """Score seeded uniform candidates across all declared bounds.

    The candidate budget is the number of independent bounded samples. Water
    validity is decided by CandidateScorer, and the result carries no global
    certificate. An explicit position iterable supports fixed-candidate parity.
    """
    if candidate_positions is None:
        bounds = scorer.config.bounds_xy
        if bounds is None:
            raise ValueError("V2 randomized search requires config.bounds_xy.")
        xmin, xmax, ymin, ymax = bounds
        rng = np.random.default_rng(seed)

        def sampled_positions() -> Iterable[tuple[float, float]]:
            for _ in range(scorer.config.candidate_budget):
                yield float(rng.uniform(xmin, xmax)), float(rng.uniform(ymin, ymax))

        positions = sampled_positions()
    else:
        positions = candidate_positions
    return score_candidate_positions(
        scorer,
        positions,
        candidate_limit=scorer.config.candidate_budget,
    )


__all__ = ["RandomizedSearchConfig", "randomized_search", "run_randomized_search_v2"]

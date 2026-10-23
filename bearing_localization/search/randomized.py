"""Shared-contract adapter for randomized-hypothesis localization."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from maritime_localization_shared.contracts.ais import AISSnapshot
from maritime_localization_shared.contracts.estimate import PositionEstimate
from maritime_localization_shared.contracts.observation import MeasurementSet

from ..ransac import randomized_hypothesis_search


@dataclass(frozen=True, slots=True)
class RandomizedSearchConfig:
    """Configuration for the randomized search adapter.

    If ``sigma_deg`` is omitted, the median positive per-observation standard
    deviation is used. A small positive floor handles exact-zero contract
    uncertainty because the underlying normalized residual requires sigma > 0.
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
        reported_sigmas = [
            item.bearing_sigma_deg
            for item in measurements.observations
            if item.bearing_sigma_deg > 0.0
        ]
        sigma_deg = max(
            float(np.median(reported_sigmas)) if reported_sigmas else config.min_sigma_deg,
            config.min_sigma_deg,
        )
    else:
        sigma_deg = config.sigma_deg

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


__all__ = ["RandomizedSearchConfig", "randomized_search"]

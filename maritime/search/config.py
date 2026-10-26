"""Versioned objective and numerical controls for localization search."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

ObjectiveVersion = Literal["bearing_set_map_v1", "legacy_corrected_v0"]
Backend = Literal["cpu", "gpu"]


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Explicit search configuration shared by independent search programs.

    bounds_xy uses projected meters as (xmin, xmax, ymin, ymax). The default
    objective is the v2 bearing-set map likelihood. Legacy scoring is available
    only through its named versioned adapter.
    """

    objective_version: ObjectiveVersion = "bearing_set_map_v1"
    algorithm: str = "pairwise"
    backend: Backend = "cpu"
    bounds_xy: tuple[float, float, float, float] | None = None
    min_range_m: float = 0.0
    max_range_m: float = 10_000.0
    range_inclusive: bool = True
    candidate_budget: int = 20_000
    proposal_source_pair_budget: int | None = None
    proposal_cell_budget: int = 1_000_000
    proposal_chunk_size: int = 4_096
    proposal_solver_tolerance_deg: float = 1e-7
    absolute_score_tolerance: float = 1e-8
    relative_score_tolerance: float = 1e-10
    generated_sigma_deg: float = 0.5
    sigma_floor_deg: float = 0.05
    circular_merge_tolerance_deg: float = 0.1
    detection_probability: float = 0.9
    bearing_outlier_probability: float = 0.05
    expected_unmatched_reports: float = 1.0
    correct_type_probability: float = 0.95
    use_types: bool = True
    common_bearing_bias_prior_deg: tuple[float, float] | None = None
    uniform_eligible_water_prior: bool = True
    seed_cell_budget: int = 256
    seed_pair_budget: int = 100_000
    seed_candidate_budget: int = 4_096
    candidate_workers: int = 1
    candidate_pool_memory_budget_bytes: int = 8 * 1024**3
    proposal_cache_path: str | None = None

    def __post_init__(self) -> None:
        if self.objective_version not in ("bearing_set_map_v1", "legacy_corrected_v0"):
            raise ValueError("objective_version must identify a supported objective explicitly.")
        if not isinstance(self.algorithm, str) or not self.algorithm.strip():
            raise ValueError("algorithm must be a nonempty string.")
        if self.backend not in ("cpu", "gpu"):
            raise ValueError("backend must be cpu or gpu.")
        if self.bounds_xy is not None:
            if len(self.bounds_xy) != 4 or not all(isfinite(v) for v in self.bounds_xy):
                raise ValueError("bounds_xy must contain four finite values or be None.")
            xmin, xmax, ymin, ymax = self.bounds_xy
            if xmin >= xmax or ymin >= ymax:
                raise ValueError("bounds_xy must have positive width and height.")
        if not isfinite(self.min_range_m) or self.min_range_m < 0.0:
            raise ValueError("min_range_m must be finite and nonnegative.")
        if not isfinite(self.max_range_m) or self.max_range_m <= self.min_range_m:
            raise ValueError("max_range_m must be finite and greater than min_range_m.")
        if type(self.range_inclusive) is not bool or type(self.use_types) is not bool:
            raise TypeError("range_inclusive and use_types must be bool values.")
        if type(self.uniform_eligible_water_prior) is not bool:
            raise TypeError("uniform_eligible_water_prior must be bool.")
        if type(self.candidate_budget) is not int or self.candidate_budget < 1:
            raise ValueError("candidate_budget must be a positive integer.")
        if self.proposal_source_pair_budget is not None and (
            type(self.proposal_source_pair_budget) is not int
            or self.proposal_source_pair_budget < 1
        ):
            raise ValueError("proposal_source_pair_budget must be a positive integer or None.")
        if self.proposal_cache_path is not None and (
            not isinstance(self.proposal_cache_path, str) or not self.proposal_cache_path.strip()
        ):
            raise ValueError("proposal_cache_path must be a nonempty path string or None.")
        if type(self.proposal_cell_budget) is not int or self.proposal_cell_budget < 1:
            raise ValueError("proposal_cell_budget must be a positive integer.")
        if type(self.proposal_chunk_size) is not int or self.proposal_chunk_size < 1:
            raise ValueError("proposal_chunk_size must be a positive integer.")
        for name, value in (
            ("seed_cell_budget", self.seed_cell_budget),
            ("seed_pair_budget", self.seed_pair_budget),
            ("seed_candidate_budget", self.seed_candidate_budget),
            ("candidate_workers", self.candidate_workers),
            ("candidate_pool_memory_budget_bytes", self.candidate_pool_memory_budget_bytes),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        for name, value in (
            ("absolute_score_tolerance", self.absolute_score_tolerance),
            ("relative_score_tolerance", self.relative_score_tolerance),
            ("generated_sigma_deg", self.generated_sigma_deg),
            ("sigma_floor_deg", self.sigma_floor_deg),
            ("circular_merge_tolerance_deg", self.circular_merge_tolerance_deg),
            ("proposal_solver_tolerance_deg", self.proposal_solver_tolerance_deg),
        ):
            if not isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive.")
        if self.sigma_floor_deg > self.generated_sigma_deg:
            raise ValueError("sigma_floor_deg cannot exceed generated_sigma_deg.")
        for name, value in (
            ("detection_probability", self.detection_probability),
            ("bearing_outlier_probability", self.bearing_outlier_probability),
            ("correct_type_probability", self.correct_type_probability),
        ):
            if not isfinite(value) or not 0.0 < value < 1.0:
                raise ValueError(f"{name} must be strictly between zero and one.")
        if not isfinite(self.expected_unmatched_reports) or self.expected_unmatched_reports < 0.0:
            raise ValueError("expected_unmatched_reports must be finite and nonnegative.")
        if self.common_bearing_bias_prior_deg is not None:
            if len(self.common_bearing_bias_prior_deg) != 2:
                raise ValueError("common_bearing_bias_prior_deg must be (mean_deg, sigma_deg).")
            bias_mean_deg, bias_sigma_deg = self.common_bearing_bias_prior_deg
            if not isfinite(bias_mean_deg) or not isfinite(bias_sigma_deg) or bias_sigma_deg <= 0.0:
                raise ValueError(
                    "Common bearing bias prior requires finite mean and positive sigma."
                )

"""Public search-program APIs.

The three estimator entry points consume the same measurement and AIS
contracts and return :class:`PositionEstimate`. Lower-level association,
candidate-generation, and visibility helpers are also exposed for composition
and testing. None of these APIs accepts simulation truth.
"""

from dataclasses import dataclass

import numpy as np

from maritime.schemas import AISSnapshot, MeasurementSet, PositionEstimate
from maritime.search.association import (
    AssociationResult,
    associate_observations_to_ais,
    compatible_types,
)
from maritime.search.branch_bound import (
    BranchBoundContext,
    BranchBoundResult,
    RegionDiagnostic,
    branch_and_bound_search,
)
from maritime.search.candidates import WaterMaskProtocol, generate_candidates
from maritime.search.config import Backend, ObjectiveVersion, SearchConfig
from maritime.search.config_serialization import (
    dumps_search_config,
    loads_search_config,
    read_search_config,
    search_config_from_dict,
    search_config_to_dict,
    write_search_config,
)
from maritime.search.grid import GridSearchConfig, MapVisibility, run_grid_search
from maritime.search.hierarchical import (
    WaterPredicate,
)
from maritime.search.hierarchical import (
    hierarchical_search as hierarchical_search_core,
)
from maritime.search.pairwise import PairwiseSearchConfig, run_pairwise_search
from maritime.search.randomized import (
    RandomizedSearchConfig,
    randomized_search,
)
from maritime.search.visibility import (
    LineOfSightQuery,
    WaterQuery,
    filter_visible_candidates,
)


@dataclass(frozen=True, slots=True)
class HierarchicalSearchConfig:
    """Controls the shared-contract coarse-to-fine search adapter."""

    coarse_spacing_m: float = 5_000.0
    refinement_levels: int = 4
    refinement_factor: float = 2.0
    beam_width: int = 8
    bearing_sigma_deg: float = 0.5
    unmatched_penalty_deg: float = 12.0
    visibility_radius_m: float = float("inf")
    use_types: bool = True
    penalize_missing_ais: bool = False
    angular_resolution_deg: float | None = None


def run_hierarchical_search(
    measurements: MeasurementSet,
    ais: AISSnapshot,
    bounds_xy: tuple[float, float, float, float],
    config: HierarchicalSearchConfig | None = None,
    water_predicate: WaterPredicate | None = None,
    visibility: MapVisibility | None = None,
) -> PositionEstimate:
    """Estimate position from the shared contracts with coarse-to-fine search.

    The optional water predicate filters candidate observer positions. A map
    visibility object additionally applies range and line-of-sight checks per
    candidate. The lower-level :func:`hierarchical_search_core` remains
    available for callers that already have array-form inputs.
    """
    if not isinstance(measurements, MeasurementSet):
        raise TypeError("measurements must be a MeasurementSet.")
    if not isinstance(ais, AISSnapshot):
        raise TypeError("ais must be an AISSnapshot.")
    config = config or HierarchicalSearchConfig()
    if visibility is not None and water_predicate is not None:
        raise ValueError("Provide either visibility or water_predicate, not both.")
    if visibility is not None:
        water_predicate = visibility.is_water

        def line_of_sight_predicate(
            observer: tuple[float, float], target: tuple[float, float]
        ) -> bool:
            return visibility.has_line_of_sight(observer[0], observer[1], target[0], target[1])
    else:
        line_of_sight_predicate = None
    observations = [(item.object_type, item.bearing_deg) for item in measurements.observations]
    ais_xy = np.asarray(
        [(record.east_m, record.north_m) for record in ais.records], dtype=float
    ).reshape((-1, 2))
    ais_types: list[str | None] = [record.ais_class for record in ais.records]
    report_sigmas = [
        item.bearing_sigma_deg if item.bearing_sigma_deg > 0.0 else config.bearing_sigma_deg
        for item in measurements.observations
    ]
    result = hierarchical_search_core(
        observations,
        ais_xy,
        ais_types,
        bounds_xy,
        sigma_deg=report_sigmas,
        coarse_spacing_m=config.coarse_spacing_m,
        refinement_levels=config.refinement_levels,
        refinement_factor=config.refinement_factor,
        beam_width=config.beam_width,
        use_types=config.use_types,
        unmatched_penalty_deg=config.unmatched_penalty_deg,
        visibility_radius_m=config.visibility_radius_m,
        penalize_missing_ais=config.penalize_missing_ais,
        angular_resolution_deg=config.angular_resolution_deg,
        water_predicate=water_predicate,
        line_of_sight_predicate=line_of_sight_predicate,
    )
    return PositionEstimate(
        east_m=result.east_m,
        north_m=result.north_m,
        score=result.score_sigma,
        elapsed_s=result.elapsed_s,
        algorithm="hierarchical",
        matched_pairs=tuple(
            (observation_index, ais.records[ais_index].record_id)
            for observation_index, ais_index in result.matches
        ),
    )


__all__ = [
    "AssociationResult",
    "BranchBoundContext",
    "BranchBoundResult",
    "Backend",
    "GridSearchConfig",
    "HierarchicalSearchConfig",
    "LineOfSightQuery",
    "MapVisibility",
    "ObjectiveVersion",
    "PairwiseSearchConfig",
    "RandomizedSearchConfig",
    "RegionDiagnostic",
    "SearchConfig",
    "WaterMaskProtocol",
    "WaterPredicate",
    "WaterQuery",
    "associate_observations_to_ais",
    "branch_and_bound_search",
    "compatible_types",
    "dumps_search_config",
    "filter_visible_candidates",
    "generate_candidates",
    "hierarchical_search_core",
    "loads_search_config",
    "randomized_search",
    "read_search_config",
    "search_config_from_dict",
    "search_config_to_dict",
    "run_grid_search",
    "run_hierarchical_search",
    "run_pairwise_search",
    "write_search_config",
]

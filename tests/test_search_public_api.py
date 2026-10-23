from __future__ import annotations

import numpy as np

from bearing_localization.search import (
    GridSearchConfig,
    HierarchicalSearchConfig,
    RandomizedSearchConfig,
    associate_observations_to_ais,
    filter_visible_candidates,
    generate_candidates,
    randomized_search,
    run_grid_search,
    run_hierarchical_search,
)
from maritime_localization_shared.contracts import (
    AISRecord,
    AISSnapshot,
    MeasurementSet,
    Observation,
    PositionEstimate,
)


def _inputs() -> tuple[MeasurementSet, AISSnapshot]:
    observer = np.asarray([0.0, 0.0])
    targets = (
        ("vessel-1", "ship", (-1_000.0, 1_500.0)),
        ("vessel-2", "ship", (1_500.0, 1_000.0)),
        ("aid-1", "buoy", (1_000.0, -1_500.0)),
    )
    ais = AISSnapshot(
        records=tuple(AISRecord(record_id, *xy, kind) for record_id, kind, xy in targets),
        timestamp_utc="2026-10-23T12:00:00Z",
        crs="local-EN",
        snapshot_id="cross-method",
    )
    observations = tuple(
        Observation(
            float(np.degrees(np.arctan2(xy[0] - observer[0], xy[1] - observer[1]))),
            0.1,
            kind,
        )
        for _, kind, xy in targets
    )
    return MeasurementSet(observations=observations), ais


def test_contract_search_adapters_share_position_estimate_and_ais_id_semantics() -> None:
    measurements, ais = _inputs()
    estimates = (
        run_grid_search(
            measurements,
            ais,
            (-100.0, 100.0, -100.0, 100.0),
            GridSearchConfig(grid_spacing_m=100.0, refine=False),
        ),
        randomized_search(
            measurements,
            ais,
            RandomizedSearchConfig(
                bounds_xy=(-100.0, 100.0, -100.0, 100.0), iterations=100, seed=4
            ),
        ),
        run_hierarchical_search(
            measurements,
            ais,
            (-100.0, 100.0, -100.0, 100.0),
            HierarchicalSearchConfig(coarse_spacing_m=100.0, refinement_levels=0),
        ),
    )

    assert all(isinstance(estimate, PositionEstimate) for estimate in estimates)
    assert [estimate.algorithm for estimate in estimates] == [
        "grid",
        "randomized_hypothesis",
        "hierarchical",
    ]
    expected_ids = {record.record_id for record in ais.records}
    assert all(
        {record_id for _, record_id in estimate.matched_pairs} == expected_ids
        for estimate in estimates
    )
    assert all(estimate.elapsed_s >= 0.0 for estimate in estimates)


def test_hierarchical_contract_adapter_applies_candidate_specific_visibility() -> None:
    measurements, ais = _inputs()

    class Visibility:
        def is_water(self, east_m: float, north_m: float) -> bool:
            return east_m >= 0.0

        def has_line_of_sight(
            self,
            observer_east_m: float,
            observer_north_m: float,
            target_east_m: float,
            target_north_m: float,
        ) -> bool:
            return target_east_m >= 0.0

    estimate = run_hierarchical_search(
        measurements,
        ais,
        (-100.0, 100.0, -100.0, 100.0),
        HierarchicalSearchConfig(coarse_spacing_m=100.0, refinement_levels=0),
        visibility=Visibility(),
    )

    assert isinstance(estimate, PositionEstimate)
    assert estimate.east_m >= 0.0
    assert "vessel-1" not in {record_id for _, record_id in estimate.matched_pairs}


def test_search_helpers_are_reachable_from_public_package_surface() -> None:
    measurements, ais = _inputs()
    association = associate_observations_to_ais((0.0, 0.0), measurements.observations, ais.records)
    candidates = generate_candidates((-1.0, 1.0, -1.0, 1.0), 1.0)
    visible = filter_visible_candidates((0.0, 0.0), candidates, visibility_radius_m=1.0)

    assert len(association.matches) == len(measurements.observations)
    assert candidates.shape == (9, 2)
    assert visible.shape == (5,)
